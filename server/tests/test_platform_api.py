"""Platform console routes end to end: access boundaries, provisioning, catalog and billing."""

import json
import re
from urllib.parse import urlsplit

import httpx
import pytest
from app.api.main import create_app
from app.models.entities import UsageRecord
from conftest import PASSWORD, FakeQueue
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
    "vendor_input_usd_per_mtok": 4.0,
    "vendor_output_usd_per_mtok": 20.0,
    "sale_input_per_mtok": 6.0,
    "sale_output_per_mtok": 30.0,
    "default": True,
    "enabled": True,
}
ROUTES = [
    ("GET", "/platform/orgs", None),
    ("POST", "/platform/orgs", {"name": "X", "admin_email": "x@example.test"}),
    ("POST", "/platform/orgs/00000000-0000-0000-0000-000000000001/active", {"active": False}),
    ("GET", "/platform/models", None),
    ("POST", "/platform/models", MODEL),
    ("POST", "/platform/models/opus-standard/test", None),
    ("GET", "/platform/usage", None),
    ("GET", "/platform/audit", None),
    (
        "POST",
        "/platform/orgs/00000000-0000-0000-0000-000000000001/balance",
        {"mode": "add", "amount": 1, "reason": "x"},
    ),
    ("POST", "/platform/cards", {"count": 1, "face_value": 10}),
    ("GET", "/platform/cards", None),
    ("POST", "/platform/cards/00000000-0000-0000-0000-000000000001/void", None),
]


@pytest.fixture
async def console(operator, tmp_path, monkeypatch):
    monkeypatch.setenv("BID_PLATFORM_CREDENTIAL_MAIN", "synthetic-platform-key")
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
        "currency",
        "balance",
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
    assert models["sonnet-economy"]["credential_configured"] is False

    stale = await console.post(
        "/platform/models", headers=console.ops, json={**MODEL, "expected_revision": 5}
    )
    assert stale.status_code == 409
    unchecked = await console.post("/platform/models", headers=console.ops, json=MODEL)
    assert unchecked.status_code == 409
    updated = await console.post(
        "/platform/models",
        headers=console.ops,
        json={**MODEL, "sale_input_per_mtok": 7.0, "expected_revision": 1},
    )
    assert updated.json()["data"]["revision"] == 2
    invalid = await console.post(
        "/platform/models", headers=console.ops, json={**MODEL, "enabled": False, "default": True}
    )
    assert invalid.status_code == 422


async def test_default_platform_model_drives_extraction_and_charges(console, tenants, pdf_bytes):
    assert (
        await console.post("/platform/models", headers=console.ops, json=MODEL)
    ).status_code == 200
    console.vendor.responses.append(anthropic_reply(GOOD_ITEMS))
    org = tenants["orgs"][0]
    async with console.app.state.db.transaction() as session:
        await session.execute(
            text("SELECT * FROM platform_adjust_balance(:o, 'add', 10, 'seed', 'ops', 'USD')"),
            {"o": org},
        )
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
    # 1200 input and 300 output tokens at vendor 4/20 and sale 6/30 per million.
    assert (record.input_tokens, record.output_tokens, record.platform_model_id) == (
        1200,
        300,
        "opus-standard",
    )
    assert float(record.usd) == pytest.approx(0.0108)
    assert float(record.charge) == pytest.approx(0.0162)

    summary = (await console.get("/platform/usage", headers=console.ops)).json()
    [row] = [item for item in summary["items"] if item["billing"] == "platform"]
    assert row["org_name"] == "Synthetic tenant A" and row["charge"] == pytest.approx(0.0162)
    assert summary["data"]["totals"]["charge"] == pytest.approx(0.0162)


async def test_missing_platform_credential_fails_extraction_explicitly(
    console, tenants, pdf_bytes, monkeypatch
):
    monkeypatch.delenv("BID_PLATFORM_CREDENTIAL_MAIN")
    await console.post("/platform/models", headers=console.ops, json=MODEL)
    header = await org_header(console, tenants["orgs"][0], "a@example.test")
    _, document = await create_document(console, header, pdf_bytes)
    await run_job(console, console.app, header, document, "parse")
    _, status = await run_job(console, console.app, header, document, "extract")
    assert status["status"] == "failed"
    assert status["error"]["code"] == "provider_unavailable"
    assert console.vendor.requests == []


async def test_model_test_button_reports_success_and_failure(console):
    await console.post("/platform/models", headers=console.ops, json=MODEL)
    console.vendor.responses.extend(
        [anthropic_reply([]), httpx.Response(401, json={"error": {"type": "authentication_error"}})]
    )
    passed = (
        await console.post("/platform/models/opus-standard/test", headers=console.ops)
    ).json()["data"]
    assert passed["passed"] is True and passed["usage"]["charge"] == pytest.approx(0.0162)
    failed = (
        await console.post("/platform/models/opus-standard/test", headers=console.ops)
    ).json()["data"]
    assert failed["passed"] is False and failed["error"]["code"] == "provider_unavailable"
    outcomes = [
        e["outcome"]
        for e in (await console.get("/platform/audit", headers=console.ops)).json()["items"]
    ]
    assert outcomes[:2] == ["failed", "success"]
    assert (
        await console.post("/platform/models/missing/test", headers=console.ops)
    ).status_code == 404


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


async def test_cards_are_shown_once_and_redeem_through_the_org_flow(console, tenants, admin_engine):
    created = await console.post(
        "/platform/cards",
        headers=console.ops,
        json={"count": 3, "face_value": 25, "note": "Synthetic batch"},
    )
    assert created.status_code == 200, created.text
    data = created.json()["data"]
    codes = [card["code"] for card in data["cards"]]
    assert data["currency"] == "USD" and len(set(codes)) == 3
    assert all(CODE_PATTERN.match(code) for code in codes)
    with Session(admin_engine) as session:
        stored = json.dumps(
            [list(map(str, r)) for r in session.execute(text("SELECT * FROM platform_cards")).all()]
        )
        audit = json.dumps(
            [str(r) for r in session.execute(text("SELECT details FROM platform_audit_logs")).all()]
        )
    assert not any(code in stored or code in audit for code in codes)

    listed = (
        await console.get(f"/platform/cards?batch_id={data['batch_id']}", headers=console.ops)
    ).json()
    assert listed["data"]["currency"] == "USD"
    assert {card["last4"] for card in listed["items"]} == {code[-4:] for code in codes}
    assert all("code" not in card and "code_hash" not in card for card in listed["items"])

    header = await org_header(console, tenants["orgs"][0], "a@example.test")
    redeemed = await console.post("/billing/redeem", headers=header, json={"code": codes[0]})
    assert redeemed.status_code == 200 and redeemed.json()["data"]["balance"] == 25.0
    voided = await console.post(
        f"/platform/cards/{data['cards'][1]['id']}/void", headers=console.ops
    )
    assert voided.json()["data"]["status"] == "void"
    refused = await console.post("/billing/redeem", headers=header, json={"code": codes[1]})
    assert refused.json()["data"]["error"]["code"] == "invalid_card"
    again = await console.post(
        f"/platform/cards/{data['cards'][0]['id']}/void", headers=console.ops
    )
    assert again.status_code == 409
    missing = await console.post(
        "/platform/cards/00000000-0000-0000-0000-000000000009/void", headers=console.ops
    )
    assert missing.status_code == 404

    states = {
        c["id"]: c["status"]
        for c in (await console.get("/platform/cards", headers=console.ops)).json()["items"]
    }
    assert sorted(states.values()) == ["active", "redeemed", "void"]
    actions = [
        e["action"]
        for e in (await console.get("/platform/audit", headers=console.ops)).json()["items"]
    ]
    assert {"platform.card.create", "platform.card.void", "billing.redeem"} <= set(actions)


@pytest.mark.parametrize(
    "body",
    [
        {"count": 0, "face_value": 10},
        {"count": 501, "face_value": 10},
        {"count": 1, "face_value": 0},
        {"count": 1, "face_value": 10, "expires_at": "2020-01-01T00:00:00+00:00"},
        {"count": 1, "face_value": 10, "expires_at": "2099-01-01T00:00:00"},
    ],
)
async def test_card_issuance_validates_input(console, body):
    assert (
        await console.post("/platform/cards", headers=console.ops, json=body)
    ).status_code == 422


async def test_balance_adjustments(console, tenants, admin_engine):
    org = tenants["orgs"][0]
    path = f"/platform/orgs/{org}/balance"
    added = await console.post(
        path, headers=console.ops, json={"mode": "add", "amount": 40, "reason": "Opening credit"}
    )
    assert added.json()["data"] == {
        "org_id": str(org),
        "mode": "add",
        "delta": 40.0,
        "balance": 40.0,
        "currency": "USD",
    }
    lowered = await console.post(
        path, headers=console.ops, json={"mode": "set", "amount": 15.5, "reason": "Correction"}
    )
    assert lowered.json()["data"]["delta"] == -24.5
    orgs = {
        o["id"]: o
        for o in (await console.get("/platform/orgs", headers=console.ops)).json()["items"]
    }
    assert (orgs[str(org)]["balance"], orgs[str(org)]["currency"]) == (15.5, "USD")
    header = await org_header(console, org, "a@example.test")
    ledger = (await console.get("/billing", headers=header)).json()["items"]
    assert [(e["kind"], e["amount"], e["reason"]) for e in ledger] == [
        ("adjust", -24.5, "Correction"),
        ("adjust", 40.0, "Opening credit"),
    ]
    for body in (
        {"mode": "add", "amount": 1, "reason": "  "},
        {"mode": "set", "amount": -1, "reason": "x"},
        {"mode": "double", "amount": 1, "reason": "x"},
    ):
        assert (await console.post(path, headers=console.ops, json=body)).status_code == 422
    missing = await console.post(
        "/platform/orgs/00000000-0000-0000-0000-000000000009/balance",
        headers=console.ops,
        json={"mode": "add", "amount": 1, "reason": "x"},
    )
    assert missing.status_code == 404
    other = tenants["orgs"][1]
    with Session(admin_engine) as session, session.begin():
        session.execute(
            text("INSERT INTO org_balances (org_id, currency, balance) VALUES (:o, 'CNY', 1)"),
            {"o": other},
        )
    mismatch = await console.post(
        f"/platform/orgs/{other}/balance",
        headers=console.ops,
        json={"mode": "add", "amount": 1, "reason": "x"},
    )
    assert (
        mismatch.status_code == 409
        and mismatch.json()["data"]["error"]["code"] == "currency_mismatch"
    )
