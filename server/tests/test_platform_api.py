"""Platform console routes end to end: access boundaries, provisioning, catalog and usage.

Output-limit failure mode: a reasoning level can bypass the adapter limit using an alias
or nested vendor configuration; both new and revised catalog entries must reject it.
"""

import json
import re
from urllib.parse import urlsplit

import httpx
import pytest
from app.api.main import create_app
from app.models.entities import UsageRecord
from conftest import PASSWORD, FakeQueue, seed_platform_credential
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from test_api import create_document, run_job
from test_llm_providers import GOOD_ITEMS, Vendor, anthropic_reply
from test_platform_auth import OPERATOR, platform_settings, sign_in

MODEL = {
    "id": "opus-standard",
    "capability": "llm_extract",
    "provider": "anthropic",
    "model": "claude-opus-5-5",
    "base_url": None,
    "credential": "main",
    "default": True,
    "enabled": True,
}
ROUTES = [
    ("GET", "/platform/orgs", None),
    ("POST", "/platform/orgs", {"name": "X", "admin_email": "x@example.test"}),
    ("POST", "/platform/orgs/00000000-0000-0000-0000-000000000001/active", {"active": False}),
    ("GET", "/platform/models", None),
    ("POST", "/platform/models", MODEL),
    (
        "POST",
        "/platform/models/opus-standard/test",
        {"test_org_id": "00000000-0000-0000-0000-000000000001"},
    ),
    ("GET", "/platform/usage", None),
    ("GET", "/platform/audit", None),
]


@pytest.fixture
async def console(operator, tmp_path, monkeypatch):
    vendor = Vendor()
    app = create_app(
        platform_settings(tmp_path), queue=FakeQueue(), llm_transport=vendor.transport()
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api,
    ):
        session = (await sign_in(api)).json()["data"]["session"]
        api.app, api.vendor = app, vendor  # pyright: ignore[reportAttributeAccessIssue]
        api.ops = {"Authorization": f"Bearer {session}"}  # pyright: ignore[reportAttributeAccessIssue]
        settings = app.state.processor.settings
        await seed_platform_credential(settings)
        await seed_platform_credential(settings, name="spare")
        await seed_platform_credential(
            settings, name="main_openai", provider="openai", endpoint="https://api.openai.com/v1"
        )
        yield api


async def org_header(api, org, email):
    body = {"email": email, "password": PASSWORD, "org_id": str(org)}
    session = (await api.post("/auth/login", json=body)).json()["data"]["session"]
    return {"Authorization": f"Bearer {session}", "X-Org-Id": str(org)}


async def test_platform_routes_reject_org_sessions_tokens_and_anonymous(console, tenants):
    header = await org_header(console, tenants["orgs"][0], "a@example.test")
    token = (
        await console.post(
            "/tokens",
            headers=header,
            json={"name": "T", "scopes": ["task:read"], "expires_at": "2030-01-01T00:00:00+00:00"},
        )
    ).json()["data"]["token"]
    for credentials in (header, {"Authorization": f"Bearer {token}"}, {}):
        for method, path, body in ROUTES:
            response = await console.request(method, path, headers=credentials, json=body)
            assert response.status_code == 401, (method, path, response.text)


async def test_provisioned_admin_sets_password_and_signs_in(console, tenants):
    created = await console.post(
        "/platform/orgs",
        headers=console.ops,
        json={"name": "New org", "admin_email": "Boss@Example.test"},
    )
    assert created.status_code == 200, created.text
    data = created.json()["data"]
    assert data["user_created"] is True and data["setup_expires_in"] == 86400
    link = urlsplit(data["setup_url"])
    assert link.path == "/app/setup-password" and link.query == ""
    token = link.fragment.removeprefix("token=")
    new_password = "synthetic-boss-password"
    assert (
        await console.post("/auth/setup-password", json={"token": token, "password": new_password})
    ).status_code == 200
    login = await console.post(
        "/auth/login",
        json={"email": "boss@example.test", "password": new_password, "org_id": data["org_id"]},
    )
    assert login.status_code == 200

    # An account that already has a password is attached without a link.
    reused = (
        await console.post(
            "/platform/orgs",
            headers=console.ops,
            json={"name": "Second", "admin_email": "a@example.test"},
        )
    ).json()["data"]
    assert reused["user_created"] is False and reused["setup_url"] is None

    orgs = (await console.get("/platform/orgs", headers=console.ops)).json()["items"]
    assert set(orgs[0]) == {
        "id",
        "name",
        "active",
        "created_at",
        "member_count",
        "admin_emails",
    }
    assert {o["name"]: o["admin_emails"] for o in orgs}["New org"] == ["boss@example.test"]
    audit = (await console.get("/platform/audit", headers=console.ops)).json()["items"]
    assert audit[0]["action"] == "platform.org.create"
    assert token not in json.dumps(audit)


async def test_disable_and_enable_org(console, tenants):
    org = tenants["orgs"][0]
    path = f"/platform/orgs/{org}/active"
    assert (
        await console.post(path, headers=console.ops, json={"active": False})
    ).status_code == 200
    body = {"email": "a@example.test", "password": PASSWORD, "org_id": str(org)}
    assert (await console.post("/auth/login", json=body)).status_code == 403
    assert (await console.post(path, headers=console.ops, json={"active": True})).status_code == 200
    assert (await console.post("/auth/login", json=body)).status_code == 200
    missing = await console.post(
        "/platform/orgs/00000000-0000-0000-0000-000000000009/active",
        headers=console.ops,
        json={"active": False},
    )
    assert missing.status_code == 404


async def test_model_catalog_revisions_and_single_default(console, monkeypatch):
    first = await console.post("/platform/models", headers=console.ops, json=MODEL)
    assert first.status_code == 200, first.text
    view = first.json()["data"]
    assert (
        view["revision"] == 1 and view["default"] is True and view["credential_configured"] is True
    )
    assert "synthetic-platform-key" not in first.text
    second = {**MODEL, "id": "sonnet-economy", "model": "claude-sonnet-5-5", "credential": "spare"}
    assert (
        await console.post("/platform/models", headers=console.ops, json=second)
    ).status_code == 200
    models = {
        m["id"]: m
        for m in (await console.get("/platform/models", headers=console.ops)).json()["items"]
    }
    assert (
        models["opus-standard"]["default"] is False and models["sonnet-economy"]["default"] is True
    )
    assert models["sonnet-economy"]["credential_configured"] is True

    stale = await console.post(
        "/platform/models", headers=console.ops, json={**MODEL, "expected_revision": 5}
    )
    assert stale.status_code == 409
    unchecked = await console.post("/platform/models", headers=console.ops, json=MODEL)
    assert unchecked.status_code == 409
    updated = await console.post(
        "/platform/models",
        headers=console.ops,
        json={**MODEL, "expected_revision": 1},
    )
    assert updated.json()["data"]["revision"] == 2
    invalid = await console.post(
        "/platform/models", headers=console.ops, json={**MODEL, "enabled": False, "default": True}
    )
    assert invalid.status_code == 422


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
@pytest.mark.parametrize(
    "options",
    [
        {"max_tokens": 32000},
        {"max_completion_tokens": 1},
        {"max_output_tokens": 1},
        {"max_new_tokens": 1},
        {"maxTokens": 1},
        {"generation_config": {"maxOutputTokens": 1}},
    ],
)
async def test_catalog_rejects_reserved_output_limits(console, provider, options, tmp_path):
    body = {
        **MODEL,
        "provider": provider,
        "credential": "main_openai" if provider == "openai" else "main",
        "reasoning": [{"name": "unsafe", "request_options": options}],
        "default_reasoning": "unsafe",
    }
    rejected = await console.post("/platform/models", headers=console.ops, json=body)
    assert rejected.status_code == 422, rejected.text
    assert (await console.get("/platform/models", headers=console.ops)).json()["items"] == []
    created = await console.post(
        "/platform/models",
        headers=console.ops,
        json={
            **MODEL,
            "provider": provider,
            "credential": "main_openai" if provider == "openai" else "main",
        },
    )
    assert created.status_code == 200, created.text
    rejected_update = await console.post(
        "/platform/models", headers=console.ops, json={**body, "expected_revision": 1}
    )
    assert rejected_update.status_code == 422, rejected_update.text
    [saved] = (await console.get("/platform/models", headers=console.ops)).json()["items"]
    assert saved["revision"] == 1 and saved["reasoning"] == []
    assert console.vendor.requests == []
    (tmp_path / "catalog-limit-rejection.json").write_text(
        json.dumps(
            {
                "provider": provider,
                "options": options,
                "create_status": rejected.status_code,
                "update_status": rejected_update.status_code,
                "revision": saved["revision"],
            },
            indent=2,
        )
    )


async def test_default_platform_model_drives_extraction_and_records_usage(
    console, tenants, pdf_bytes
):
    assert (
        await console.post("/platform/models", headers=console.ops, json=MODEL)
    ).status_code == 200
    console.vendor.responses.append(anthropic_reply(GOOD_ITEMS))
    org = tenants["orgs"][0]
    header = await org_header(console, org, "a@example.test")
    assert (await console.get("/health")).json()["data"]["real_llm_configured"] is True
    _, document = await create_document(console, header, pdf_bytes)
    await run_job(console, console.app, header, document, "parse")
    _, status = await run_job(console, console.app, header, document, "extract")
    assert status["status"] == "succeeded", status
    [request] = console.vendor.requests
    assert request.headers["x-api-key"] == "synthetic-platform-key"
    async with console.app.state.db.transaction(org) as session:
        [record] = (
            await session.scalars(select(UsageRecord).where(UsageRecord.provider == "anthropic"))
        ).all()
    assert (record.input_tokens, record.output_tokens, record.platform_model_id) == (
        1200,
        300,
        "opus-standard",
    )
    summary = (await console.get("/platform/usage", headers=console.ops)).json()
    [row] = [item for item in summary["items"] if item["source"] == "platform"]
    assert row["org_name"] == "Synthetic tenant A"


async def test_missing_platform_credential_fails_extraction_explicitly(
    console, tenants, pdf_bytes, monkeypatch
):
    await console.post("/platform/models", headers=console.ops, json=MODEL)
    listed = (await console.get("/platform/credentials", headers=console.ops)).json()["items"]
    main = next(item for item in listed if item["name"] == "main")
    await console.post(
        f"/platform/credentials/{main['id']}/active",
        headers=console.ops,
        json={"expected_revision": 1, "active": False, "reason": "incident"},
    )
    header = await org_header(console, tenants["orgs"][0], "a@example.test")
    _, document = await create_document(console, header, pdf_bytes)
    await run_job(console, console.app, header, document, "parse")
    _, status = await run_job(console, console.app, header, document, "extract")
    assert status["status"] == "failed"
    assert status["error"]["code"] == "provider_unavailable"
    assert console.vendor.requests == []


async def test_model_test_button_reports_success_and_failure(console, admin_engine):
    await console.post("/platform/models", headers=console.ops, json=MODEL)
    console.vendor.responses.extend(
        [anthropic_reply([]), httpx.Response(401, json={"error": {"type": "authentication_error"}})]
    )
    organization = (
        await console.post(
            "/platform/orgs",
            headers=console.ops,
            json={"name": "Internal model probe", "admin_email": OPERATOR},
        )
    ).json()["data"]["org_id"]
    probe = {"test_org_id": organization}
    passed = (
        await console.post(
            "/v4/platform/models/opus-standard/test", headers=console.ops, json=probe
        )
    ).json()["data"]
    assert passed["passed"] is True
    failed = (
        await console.post(
            "/v4/platform/models/opus-standard/test", headers=console.ops, json=probe
        )
    ).json()["data"]
    assert failed["passed"] is False and failed["error"]["code"] == "provider_unavailable"
    outcomes = [
        e["outcome"]
        for e in (await console.get("/platform/audit", headers=console.ops)).json()["items"]
    ]
    assert outcomes[:2] == ["failed", "success"]
    with Session(admin_engine) as database:
        records = database.scalars(select(UsageRecord).where(UsageRecord.task_id.is_(None))).all()
        assert len(records) == 1 and str(records[0].org_id) == organization
        assert records[0].job_id is not None and records[0].call_id is not None
    assert (
        await console.post("/v4/platform/models/missing/test", headers=console.ops, json=probe)
    ).status_code == 404


async def test_platform_model_dry_run_without_org_is_read_only(console, admin_engine):
    await console.post("/platform/models", headers=console.ops, json=MODEL)
    tables = (
        "jobs",
        "usage_records",
        "vendor_calls",
        "platform_audit_logs",
    )

    def snapshot():
        with Session(admin_engine) as database:
            return {
                table: database.scalar(text(f"SELECT count(*) FROM {table}")) for table in tables
            }

    before = snapshot()
    response = await console.post(
        "/v4/platform/models/opus-standard/test",
        headers=console.ops,
        json={"dry_run": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert response.headers["X-Bid-Contract-Version"] == "5.0"
    preflight = body["data"]["preflight"]
    assert preflight["task_id"] is None
    assert (
        preflight["planned_calls"] == 1
        and preflight["next_call"]["platform_model_id"] == MODEL["id"]
    )
    assert preflight["full_run_guaranteed"] is False
    assert body["cost"] == preflight["estimate"]
    assert snapshot() == before and console.vendor.requests == []


async def test_platform_model_probe_requires_org_membership(console, tenants):
    await console.post("/platform/models", headers=console.ops, json=MODEL)
    missing = await console.post(
        "/v4/platform/models/opus-standard/test", headers=console.ops, json={}
    )
    assert missing.status_code == 422 and missing.json()["data"]["error"]["exit_code"] == 2
    denied = await console.post(
        "/v4/platform/models/opus-standard/test",
        headers=console.ops,
        json={"test_org_id": str(tenants["orgs"][0])},
    )
    assert denied.status_code == 404 and console.vendor.requests == []


@pytest.mark.parametrize(
    "query", ["from=2026-13", "from=bad", "from=2026-10&to=2026-09", "from=2020-01&to=2026-12"]
)
async def test_usage_rejects_invalid_ranges(console, query):
    response = await console.get(f"/platform/usage?{query}", headers=console.ops)
    assert response.status_code == 400
    assert response.json()["data"]["error"]["code"] == "invalid_month"


async def test_audit_lists_operator_sign_in(console):
    entries = (await console.get("/platform/audit?limit=5", headers=console.ops)).json()["items"]
    assert entries[-1]["action"] == "platform.login" and entries[-1]["actor_email"] == OPERATOR


CODE_PATTERN = re.compile(r"^[A-HJ-NP-Z2-9]{4}(-[A-HJ-NP-Z2-9]{4}){3}$")
