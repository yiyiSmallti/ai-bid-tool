"""Credential workflow through the real API and dedicated PostgreSQL roles.

Failure modes: unauthorized token authority, stale writes, ciphertext/value disclosure,
probe without a persisted authorization, partial imports and resurrection after removal.
The database suite is run by the owning integration session, never a local service helper.
"""

import json
import os
from uuid import uuid4

import httpx
import pytest
from app.api.main import create_app
from app.core.config import Settings
from app.schemas.platform_credentials import ServiceResolveTarget
from app.services.platform_credentials import PlatformCredentialResolver
from conftest import FakeQueue
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import text

KEY = "synthetic-credential-api-first-1234"
NEW_KEY = "synthetic-credential-api-second-5678"
BODY = {
    "name": "search_main",
    "purpose": "vendor_search",
    "provider": "perplexity",
    "endpoint": "https://api.perplexity.ai",
    "api_key": KEY,
    "active": True,
}


@pytest.fixture
async def credential_console(database, tmp_path, admin_engine):
    settings = Settings(data_dir=tmp_path).model_copy(
        update={
            "secrets_key": SecretStr(Fernet.generate_key().decode()),
        }
    )
    app = create_app(settings, queue=FakeQueue())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as api,
    ):
        api.headers["Authorization"] = "Bearer " + os.environ["BID_ACCESS_KEY"]
        yield api, settings, app


async def test_credential_lifecycle_and_live_resolution(credential_console, admin_engine, caplog):
    api, settings, app = credential_console
    created = await api.post("/platform/credentials", json=BODY)
    assert created.status_code == 200, created.text
    view = created.json()["data"]["credential"]
    target = ServiceResolveTarget(service="vendor_search", credential_id=view["id"])
    resolver = PlatformCredentialResolver(settings, app.state.db)
    assert (await resolver.resolve_for_call(target)).api_key.get_secret_value() == KEY
    path = f"/platform/credentials/{view['id']}"
    replaced = await api.post(
        path + "/replace",
        json={
            "api_key": NEW_KEY,
            "expected_revision": 1,
            "reason": "scheduled_rotation",
        },
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["data"]["credential"]["secret_version"] == 2
    assert (await resolver.resolve_for_call(target)).api_key.get_secret_value() == NEW_KEY
    conflict = await api.post(
        path + "/active",
        json={
            "active": False,
            "expected_revision": 1,
            "reason": "incident",
        },
    )
    assert conflict.status_code == 409
    disabled = await api.post(
        path + "/active",
        json={
            "active": False,
            "expected_revision": 2,
            "reason": "incident",
        },
    )
    assert disabled.status_code == 200
    with pytest.raises(Exception) as caught:
        await resolver.resolve_for_call(target)
    assert caught.value.code == "credential_disabled"
    probe = await api.post(path + "/test", json={"expected_revision": 3})
    assert probe.status_code == 422 and probe.json()["data"]["probe"]["outcome"] == "unsupported"
    removed = await api.post(path + "/remove", json={"expected_revision": 3, "reason": "retired"})
    assert removed.status_code == 200
    assert removed.json()["data"]["credential"]["state"] == "removed"
    refused = await api.post(
        path + "/active",
        json={
            "active": True,
            "expected_revision": 4,
            "reason": "setup",
        },
    )
    assert refused.status_code == 409
    duplicate = await api.post("/platform/credentials", json=BODY)
    assert duplicate.status_code == 409
    listed = await api.get("/platform/credentials")
    shown = await api.get(path)
    with admin_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT encrypted_key FROM platform_credentials WHERE id=:id"),
                {"id": view["id"]},
            )
            is None
        )
        audit = str(connection.execute(text("SELECT details FROM audit_logs")).all())
    output = "".join(
        r.text
        for r in [
            created,
            replaced,
            conflict,
            disabled,
            probe,
            removed,
            refused,
            duplicate,
            listed,
            shown,
        ]
    )
    assert KEY not in output + audit + caplog.text and NEW_KEY not in output + audit + caplog.text
    assert "encrypted_key" not in output
    assert shown.headers["Cache-Control"] == "no-store"


async def test_all_credential_routes_refuse_tokens_and_missing_identity(
    credential_console, database
):
    api, settings, app = credential_console
    token = await api.post(
        "/tokens",
        json={
            "name": "credential boundary",
            "scopes": ["task:read"],
            "expires_at": "2030-01-01T00:00:00Z",
        },
    )
    credentials = [
        {"Authorization": "Bearer " + token.json()["data"]["token"]},
        {"Authorization": ""},
    ]
    missing = str(uuid4())
    for header in credentials:
        for method, suffix, body in [
            ("GET", "", None),
            ("GET", "/" + missing, None),
            ("POST", "", BODY),
            (
                "POST",
                "/" + missing + "/replace",
                {"api_key": NEW_KEY, "expected_revision": 1, "reason": "incident"},
            ),
            (
                "POST",
                "/" + missing + "/active",
                {"active": False, "expected_revision": 1, "reason": "incident"},
            ),
            ("POST", "/" + missing + "/remove", {"expected_revision": 1, "reason": "retired"}),
            ("POST", "/" + missing + "/test", {"expected_revision": 1}),
            (
                "POST",
                "/import-env",
                {
                    "entries": [
                        {**BODY, "source_env": "BID_PERPLEXITY_API_KEY", "reason": "migration"}
                    ]
                },
            ),
        ]:
            response = await api.request(
                method, "/platform/credentials" + suffix, headers=header, json=body
            )
            assert response.status_code in {401, 403}, response.text
            assert response.json()["data"]["error"]["code"] in {"invalid_session", "forbidden"}


async def test_import_dry_run_replay_conflict_and_validation_redaction(
    credential_console, admin_engine
):
    api, _, _ = credential_console
    body = {"entries": [{**BODY, "source_env": "BID_PERPLEXITY_API_KEY", "reason": "migration"}]}
    with admin_engine.connect() as connection:
        initial = connection.scalar(text("SELECT count(*) FROM audit_logs"))
    dry = await api.post("/platform/credentials/import-env", json={**body, "dry_run": True})
    assert dry.status_code == 200 and dry.json()["data"]["would_create"] == 1
    with admin_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM audit_logs")) == initial
    first = await api.post("/platform/credentials/import-env", json=body)
    assert first.status_code == 200 and first.json()["data"]["created"] == 1
    replay = await api.post("/platform/credentials/import-env", json=body)
    assert replay.status_code == 200 and replay.json()["data"]["skipped"] == 1
    body["entries"][0]["api_key"] = NEW_KEY
    conflict = await api.post("/platform/credentials/import-env", json=body)
    assert conflict.status_code == 409
    invalid = await api.post("/platform/credentials", json={**BODY, KEY: KEY})
    assert invalid.status_code == 422 and KEY not in invalid.text
    assert KEY not in json.dumps([dry.json(), first.json(), replay.json(), conflict.json()])
