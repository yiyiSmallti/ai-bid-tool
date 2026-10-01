"""Prepaid balance end to end: redemption, permissions, deduction and the funds gate."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from app.api.main import create_app
from app.core.config import Settings
from app.models.entities import Membership, PlatformModel
from conftest import PASSWORD, FakeQueue
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_api import create_document, run_job
from test_billing_db import add_card
from test_llm_providers import GOOD_ITEMS, Vendor, anthropic_reply

CODE = "ABCD-EFGH-JKMN-PQRS"


@pytest.fixture
async def billing_app(tenants, tmp_path, monkeypatch):
    monkeypatch.setenv("BID_PLATFORM_CREDENTIAL_MAIN", "synthetic-platform-key")
    vendor = Vendor()
    app = create_app(
        Settings(data_dir=tmp_path), queue=FakeQueue(), llm_transport=vendor.transport()
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api,
    ):
        api.app, api.vendor = app, vendor  # pyright: ignore[reportAttributeAccessIssue]
        yield api


async def org_header(api, org, email="a@example.test"):
    body = {"email": email, "password": PASSWORD, "org_id": str(org)}
    session = (await api.post("/auth/login", json=body)).json()["data"]["session"]
    return {"Authorization": f"Bearer {session}", "X-Org-Id": str(org)}


async def adjust(api, org, mode, amount):
    async with api.app.state.db.transaction() as session:
        await session.execute(
            text(
                "SELECT * FROM platform_adjust_balance(:o, :m, CAST(:a AS numeric), 'test', 'ops', 'USD')"
            ),
            {"o": org, "m": mode, "a": amount},
        )


async def test_redeem_credits_balance_once_and_lists_ledger(billing_app, admin_engine, tenants):
    add_card(admin_engine, CODE, face=50)
    header = await org_header(billing_app, tenants["orgs"][0])
    responses = await asyncio.gather(
        *(
            billing_app.post(
                "/billing/redeem", headers=header, json={"code": "abcd efgh jkmn pqrs"}
            )
            for _ in range(5)
        )
    )
    assert sorted(r.status_code for r in responses) == [200, 400, 400, 400, 400]
    overview = (await billing_app.get("/billing", headers=header)).json()
    assert overview["data"] == {"currency": "USD", "balance": 50.0}
    assert [(e["kind"], e["amount"]) for e in overview["items"]] == [("redeem", 50.0)]
    async with billing_app.app.state.db.transaction() as session:
        stored = await session.scalar(
            text("SELECT count(*) FROM platform_cards WHERE code_hash LIKE :c"), {"c": "%ABCD%"}
        )
        audit = await session.scalar(text("SELECT details::text FROM platform_audit_logs"))
    assert stored == 0 and CODE not in audit


async def test_invalid_cards_are_uniform_and_lock_after_repeated_failures(billing_app, tenants):
    header = await org_header(billing_app, tenants["orgs"][0])
    for code in ["short", "IIII-OOOO-0000-1111", "ZZZZ-ZZZZ-ZZZZ-ZZZZ"]:
        response = await billing_app.post("/billing/redeem", headers=header, json={"code": code})
        assert response.status_code == 400
        assert response.json()["data"]["error"]["code"] == "invalid_card"
    for _ in range(7):
        await billing_app.post(
            "/billing/redeem", headers=header, json={"code": "ZZZZ-ZZZZ-ZZZZ-ZZZZ"}
        )
    locked = await billing_app.post("/billing/redeem", headers=header, json={"code": CODE})
    assert locked.status_code == 429
    other = await org_header(billing_app, tenants["orgs"][1], "b@example.test")
    assert (
        await billing_app.post("/billing/redeem", headers=other, json={"code": "short"})
    ).status_code == 400


async def test_tokens_and_non_admins_cannot_redeem(billing_app, admin_engine, tenants):
    org = tenants["orgs"][0]
    header = await org_header(billing_app, org)
    denied = await billing_app.post(
        "/tokens",
        headers=header,
        json={"name": "T", "scopes": ["billing:redeem"], "expires_at": "2030-01-01T00:00:00+00:00"},
    )
    assert denied.status_code == 403
    token = (
        await billing_app.post(
            "/tokens",
            headers=header,
            json={
                "name": "T",
                "scopes": ["billing:read"],
                "expires_at": "2030-01-01T00:00:00+00:00",
            },
        )
    ).json()["data"]["token"]
    token_header = {"Authorization": f"Bearer {token}", "X-Org-Id": str(org)}
    assert (await billing_app.get("/billing", headers=token_header)).status_code == 200
    redeem = await billing_app.post("/billing/redeem", headers=token_header, json={"code": CODE})
    assert redeem.status_code == 403

    with Session(admin_engine) as session, session.begin():
        session.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": str(org)})
        session.add(Membership(org_id=org, user_id=tenants["users"][1], role="bidder"))
    bidder = await org_header(billing_app, org, "b@example.test")
    assert (await billing_app.get("/billing", headers=bidder)).status_code == 403
    assert (
        await billing_app.post("/billing/redeem", headers=bidder, json={"code": CODE})
    ).status_code == 403


def set_default_model(admin_engine):
    with Session(admin_engine) as session, session.begin():
        session.add(
            PlatformModel(
                id="opus-standard",
                capability="llm_extract",
                provider="anthropic",
                model="claude-opus-5-5",
                credential="main",
                vendor_input_usd_per_mtok=4,
                vendor_output_usd_per_mtok=20,
                sale_input_per_mtok=6,
                sale_output_per_mtok=30,
                is_default=True,
                updated_by="ops@example.test",
            )
        )


async def test_usage_deducts_and_empty_balance_blocks_new_jobs(
    billing_app, admin_engine, tenants, pdf_bytes
):
    set_default_model(admin_engine)
    org = tenants["orgs"][0]
    header = await org_header(billing_app, org)
    _, document = await create_document(billing_app, header, pdf_bytes)
    await run_job(billing_app, billing_app.app, header, document, "parse")

    refused = await billing_app.post(f"/documents/{document}/extract", headers=header, json={})
    assert refused.status_code == 402
    assert refused.json()["data"]["error"] == {
        "code": "insufficient_balance",
        "message": "Balance is used up; redeem a recharge card first",
        "exit_code": 4,
    }
    assert billing_app.vendor.requests == []

    # A tiny positive balance admits one job, which may finish below zero.
    await adjust(billing_app, org, "set", 0.001)
    billing_app.vendor.responses.append(anthropic_reply(GOOD_ITEMS))
    _, status = await run_job(billing_app, billing_app.app, header, document, "extract")
    assert status["status"] == "succeeded", status
    overview = (await billing_app.get("/billing", headers=header)).json()
    assert overview["data"]["balance"] == pytest.approx(0.001 - 0.0162)
    assert [e["kind"] for e in overview["items"]] == ["usage", "adjust"]
    assert sum(e["amount"] for e in overview["items"]) == pytest.approx(overview["data"]["balance"])
    retry = await billing_app.post(
        f"/documents/{document}/extract", headers=header, json={"retry": True}
    )
    assert retry.status_code == 402


async def test_job_queued_before_balance_ran_out_fails_without_vendor_call(
    billing_app, admin_engine, tenants, pdf_bytes
):
    set_default_model(admin_engine)
    org = tenants["orgs"][0]
    header = await org_header(billing_app, org)
    _, document = await create_document(billing_app, header, pdf_bytes)
    await run_job(billing_app, billing_app.app, header, document, "parse")
    await adjust(billing_app, org, "add", 1)
    job = (
        await billing_app.post(f"/documents/{document}/extract", headers=header, json={})
    ).json()["data"]["job_id"]
    await adjust(billing_app, org, "set", 0)
    await billing_app.app.state.processor(str(org), job)
    status = (await billing_app.get(f"/jobs/{job}", headers=header)).json()["data"]
    assert status["status"] == "failed" and status["error"]["code"] == "insufficient_balance"
    assert billing_app.vendor.requests == []


async def test_auth_orgs_lists_memberships_after_password_check(billing_app, tenants):
    found = await billing_app.post(
        "/auth/orgs", json={"email": "A@example.test", "password": PASSWORD}
    )
    assert found.status_code == 200
    assert found.json()["items"] == [
        {
            "org_id": str(tenants["orgs"][0]),
            "name": "Synthetic tenant A",
            "role": "admin",
            "active": True,
        }
    ]
    wrong = await billing_app.post(
        "/auth/orgs", json={"email": "a@example.test", "password": "wrong"}
    )
    assert wrong.status_code == 401
    unknown = await billing_app.post(
        "/auth/orgs", json={"email": "nobody@example.test", "password": PASSWORD}
    )
    assert unknown.status_code == 401
    assert json.dumps(unknown.json()) == json.dumps(wrong.json()).replace(
        str(wrong.json()["duration_ms"]), str(unknown.json()["duration_ms"]), 1
    )


async def test_startup_refuses_balances_in_another_currency(tenants, admin_engine, tmp_path):
    org = tenants["orgs"][0]
    with Session(admin_engine) as session, session.begin():
        session.execute(
            text("INSERT INTO org_balances (org_id, currency, balance) VALUES (:o, 'CNY', 10)"),
            {"o": org},
        )
    app = create_app(Settings(data_dir=tmp_path), queue=FakeQueue())
    with pytest.raises(RuntimeError, match="BID_BILLING_CURRENCY=USD"):
        async with app.router.lifespan_context(app):
            pass
    other = create_app(Settings(data_dir=tmp_path, billing_currency="cny"), queue=FakeQueue())
    async with other.router.lifespan_context(other):
        pass
    assert uuid4()
