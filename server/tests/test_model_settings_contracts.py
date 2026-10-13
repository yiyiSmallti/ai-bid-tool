"""DB-free safe projections and wire-shape contracts.

Failure inventory: persisted secrets or request overrides enter responses; model
identity is substituted; cursors cross actors/filters; metadata exceeds bounds;
read inputs accidentally require write preconditions.
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from app.api.main import create_app
from app.core.config import Settings
from app.core.errors import ServiceError
from app.core.security import TokenSigner
from app.schemas.management_pages import PageQuery
from app.schemas.management_providers import (
    ProviderRevisionMetadata,
)
from app.schemas.provider_contracts import ProviderConfigInput, ProviderConfigSet
from app.services import management_providers as service
from app.services.auth import Identity
from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

CANARY = "synthetic-private-model-settings-value"
FORBIDDEN = {
    "api_key",
    "encrypted_key",
    "key_last4",
    "fingerprint",
    "credential",
    "credential_id",
    "vendor_input_usd_per_mtok",
    "vendor_output_usd_per_mtok",
}


def actor():
    return Identity(scopes={"provider:read", "provider:write"})


def row(owner, **changes):
    return {
        "id": uuid4(),
        "revision": 1,
        "capability": "llm_extract",
        "created_at": datetime.now(UTC),
        "data": {
            "provider": "openai",
            "model": "Saved model",
            "base_url": "https://api.example.test/v1",
            "json_mode": "json_schema",
            "reasoning": [
                {"name": "low", "label": "Low", "request_options": {"reasoning_effort": "low"}}
            ],
            "default_reasoning": "low",
            **{key: CANARY for key in FORBIDDEN},
        },
        **{key: CANARY for key in FORBIDDEN},
        **changes,
    }


def assert_safe(value):
    assert CANARY not in json.dumps(value)
    if isinstance(value, dict):
        assert FORBIDDEN.isdisjoint(value)
        for child in value.values():
            assert_safe(child)
    elif isinstance(value, list):
        for child in value:
            assert_safe(child)


def test_projection_allowlist_disabled_identity_and_saved_terms():
    owner = actor()
    saved = row(owner)
    value = service.metadata(owner, saved).model_dump(mode="json")
    assert_safe(value)
    assert value["model"] == "Saved model"
    assert value["configuration"]["base_url"] == "https://api.example.test/v1"
    assert value["configuration"]["expected_revision"] is None
    assert value["reasoning"] == [{"name": "low", "label": "Low"}]


def test_byok_exposes_only_nonsecret_saved_input_and_official_choices():
    owner = actor()
    value = service.metadata(owner, row(owner)).model_dump(mode="json")
    assert_safe(value)
    assert value["credential_state"] == "configured"
    assert value["configuration"]["base_url"] == "https://api.example.test/v1"
    assert value["configuration"]["reasoning"][0]["request_options"] == {"reasoning_effort": "low"}


@pytest.mark.parametrize("field", sorted(FORBIDDEN))
def test_projection_types_reject_new_secret_fields(field):
    owner = actor()
    value = service.metadata(owner, row(owner)).model_dump(mode="json")
    with pytest.raises(ValidationError):
        ProviderRevisionMetadata.model_validate({**value, field: CANARY})


def test_secret_set_is_excluded_from_generic_serialization():
    body = ProviderConfigSet(provider="openai", model="Official", api_key=CANARY)
    assert CANARY not in body.model_dump_json()
    assert "api_key" not in body.model_dump()


@pytest.mark.parametrize(
    "name",
    [
        "api_key",
        "X-API-Key",
        "Authorization",
        "headers",
        "client_secret",
        "credential_id",
        "key_last4",
        "ciphertext",
        "fingerprint",
        "base_url",
        "vendor_input_usd_per_mtok",
    ],
)
def test_reasoning_rejects_nested_credential_transport_slots(name):
    body = {
        "provider": "openai",
        "model": "Official",
        "reasoning": [{"name": "low", "request_options": {"nested": [{name: CANARY}]}}],
        "default_reasoning": "low",
    }
    with pytest.raises(ValidationError):
        ProviderConfigInput.model_validate(body)
    owner = actor()
    saved = row(owner)
    saved["data"]["reasoning"] = body["reasoning"]
    with pytest.raises(ServiceError) as error:
        service.metadata(owner, saved)
    assert error.value.code == "management_integrity_error"
    assert CANARY not in str(error.value)


def test_reasoning_option_tree_bounds_and_official_options():
    base = {"provider": "openai", "model": "Official", "default_reasoning": "low"}
    options = {
        "reasoning_effort": "low",
        "thinking": {"type": "enabled", "budget_tokens": 4096},
        "temperature": 0.1,
    }
    value = ProviderConfigInput.model_validate(
        {**base, "reasoning": [{"name": "low", "request_options": options}]}
    )
    assert value.reasoning[0].request_options == options
    deep = {}
    for _ in range(25):
        deep = {"nested": deep}
    for oversized in (deep, {"choices": list(range(5000))}):
        with pytest.raises(ValidationError):
            ProviderConfigInput.model_validate(
                {**base, "reasoning": [{"name": "low", "request_options": oversized}]}
            )


def test_metadata_sql_excludes_credential_columns():
    owner = actor()
    compiled = str(service.config_statement(owner).compile(dialect=postgresql.dialect()))
    for field in FORBIDDEN:
        assert field not in compiled


def test_bad_stored_metadata_has_fixed_error_without_validation_input():
    owner = actor()
    saved = row(owner)
    saved["data"]["base_url"] = "https://example.test/?secret=" + CANARY
    with pytest.raises(ServiceError) as error:
        service.metadata(owner, saved)
    assert error.value.code == "management_integrity_error"
    assert CANARY not in str(error.value)


def test_cursor_is_authority_purpose_prefix_bound_and_expires():
    owner = actor()
    session = SimpleNamespace(
        info={
            "management_settings": Settings(
                database_url="postgresql+psycopg://fixture.invalid/bid_test",
                encryption_key=Fernet.generate_key().decode(),
                token_key=Fernet.generate_key().decode(),
            )
        }
    )
    signer = TokenSigner.for_tokens(service.settings_for(session))
    cursor = signer.issue(
        {**service.binding(owner, "history", {}), "anchor": [1, str(uuid4())]}, 900
    )
    query = PageQuery(cursor=cursor)
    assert service.open_cursor(session, owner, query, "history", {})[0] == 1
    for other, purpose, filters in [
        (Identity(scopes={"provider:read"}), "history", {}),
        (owner, "wrong-purpose", {}),
        (owner, "history", {"unexpected": True}),
    ]:
        with pytest.raises(ServiceError) as error:
            service.open_cursor(session, other, query, purpose, filters)
        assert error.value.code == "management_cursor_invalid"
    expired = signer.issue(
        {**service.binding(owner, "history", {}), "anchor": [1, str(uuid4())]}, -1
    )
    with pytest.raises(ServiceError) as error:
        service.open_cursor(session, owner, PageQuery(cursor=expired), "history", {})
    assert error.value.code == "management_cursor_expired"


@pytest.mark.parametrize(
    "path,body",
    [
        (
            "/providers",
            {
                "provider": "openai",
                "model": "Official",
                "api_key": CANARY,
                CANARY: "extra",
            },
        ),
        (
            "/v5/providers",
            {
                "provider": "openai",
                "model": "Official",
                "api_key": CANARY,
                CANARY: "extra",
            },
        ),
        ("/providers/test", {CANARY: "extra"}),
        ("/v5/providers/test", {CANARY: "extra"}),
        ("/management/providers/history/query", {CANARY: "extra"}),
        ("/v5/management/providers/history/query", {CANARY: "extra"}),
    ],
)
async def test_provider_validation_never_reflects_dynamic_field_names(path, body, caplog):
    settings = Settings(
        database_url="postgresql+psycopg://fixture.invalid/bid_test",
        encryption_key=Fernet.generate_key().decode(),
        token_key=Fernet.generate_key().decode(),
    )
    application = create_app(settings=settings)
    from app.api.management_providers import create_router as metadata_router
    from app.api.providers import create_router as provider_router

    async def offline_context():
        return SimpleNamespace(info={}), actor()

    # Exercise real request schemas and shared error handlers without opening a database.
    application.router.routes.clear()
    application.include_router(metadata_router(offline_context, settings))
    application.include_router(provider_router(offline_context, None, settings, None, None, None))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://fixture.test"
        ) as client:
            response = await client.post(path, json=body)
        assert response.status_code == 422, response.text
        assert response.json()["data"]["error"]["message"] == "Invalid provider input"
        assert CANARY not in response.text and CANARY not in caplog.text
        assert response.headers["Cache-Control"] == "no-store"
    finally:
        await application.state.db.engine.dispose()
