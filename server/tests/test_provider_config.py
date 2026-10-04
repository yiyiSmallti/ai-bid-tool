"""API/processor gates for org providers, with MockTransport and two real tenants.

Failure modes recorded before implementation:
* A/B configuration, encrypted key, usage, job or revision can cross tenant scope.
* Tokens/nonadmins can write/test configuration or forge human database context.
* Concurrent updates lose revisions, history mutates, or stale revisions overwrite.
* Keys leak through views, validation, audits, vendor errors or usage model echoes.
* Missing/wrong dedicated encryption key falls back to the storage key or platform.
* Queued extraction/drafting uses a different revision or hits an earlier cache.
* Org-key calls require prepaid funds, charge the platform, or evade call ceilings.
* Selected/default platform calls skip admission, settlement or catalog fencing.
* Quota errors retry or direct org-key users to the wrong payer.
* Balance probes follow redirects, query non-DeepSeek hosts or trust malformed data.
* Provider tests manufacture tender records or fail to record paid malformed replies.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from app.api.main import create_app
from app.core.config import Settings
from app.models.entities import (
    ApiToken,
    AuditLog,
    Document,
    Job,
    OrgBalance,
    PlatformModel,
    Task,
    UsageRecord,
    VendorCall,
)
from app.models.provider_configs import ProviderConfig
from app.services.auth import ROLE_SCOPES, Identity, set_actor_context
from conftest import FakeQueue
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import DBAPIError
from test_api import create_document, run_job
from test_card_generation import DraftVendor, token_header
from test_llm_providers import GOOD_ITEMS, provider_reply
from test_response_cards import create_tender, login, set_role

KEY = "synthetic-org-provider-key-4321"


def org_config(**changes):
    return {
        "capability": "llm_extract",
        "source": "org",
        "provider": "openai",
        "model": "synthetic-model",
        "base_url": "https://api.deepseek.com/v1",
        "json_mode": "json_object",
        "api_key": KEY,
        "input_usd_per_mtok": 1,
        "output_usd_per_mtok": 2,
        "reasoning": [{"name": "low", "request_options": {"reasoning_effort": "low"}}],
        "default_reasoning": "low",
        **changes,
    }


class Vendor:
    def __init__(self):
        self.calls = []
        self.balance_calls = 0
        self.response: httpx.Response | None = None

    async def __call__(self, request):
        if request.method == "GET":
            assert str(request.url) == "https://api.deepseek.com/user/balance"
            self.balance_calls += 1
            return httpx.Response(
                200,
                json={
                    "is_available": True,
                    "balance_infos": [
                        {
                            "currency": "USD",
                            "total_balance": "12.34",
                            "granted_balance": "0",
                            "topped_up_balance": "12.34",
                        }
                    ],
                },
            )
        body = json.loads(request.content)
        self.calls.append((body, request.headers.get("authorization")))
        return self.response or provider_reply("openai", GOOD_ITEMS)


@asynccontextmanager
async def configured(tenants, tmp_path, *, vendor=None, **options):
    vendor = vendor or Vendor()
    settings = Settings(
        data_dir=tmp_path, secrets_key=SecretStr(Fernet.generate_key().decode()), **options
    )
    app = create_app(settings, queue=FakeQueue(), llm_transport=httpx.MockTransport(vendor))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api,
    ):
        headers = [
            await login(api, org, label)
            for org, label in zip(tenants["orgs"], ("a", "b"), strict=True)
        ]
        yield app, api, headers, vendor


async def save(api, header, **changes):
    response = await api.post("/providers", headers=header, json=org_config(**changes))
    assert response.status_code == 200, response.text
    assert KEY not in response.text and "encrypted_key" not in response.text
    return response.json()["data"]


async def catalog(app, org, **changes):
    async with app.state.db.transaction(org) as session:
        session.add(
            PlatformModel(
                id="paid",
                capability="llm_extract",
                provider="openai",
                model="synthetic-model",
                base_url="https://vendor.example/v1",
                credential="provider_test",
                is_default=True,
                enabled=True,
                vendor_input_usd_per_mtok=1,
                vendor_output_usd_per_mtok=2,
                sale_input_per_mtok=3,
                sale_output_per_mtok=4,
                reasoning=[],
                revision=1,
                updated_by="ops@example.test",
                **changes,
            )
        )


async def test_revision_isolation_and_database_gates(tenants, tmp_path):
    async with configured(tenants, tmp_path) as (app, api, headers, vendor):
        a, b = headers
        first = await save(api, a)
        foreign = await save(api, b, api_key="synthetic-other-key-9876")
        for header, expected in ((a, first), (b, foreign)):
            for history in (False, True):
                response = await api.get("/providers", headers=header, params={"history": history})
                assert response.status_code == 200
                assert [row["id"] for row in response.json()["items"]] == [expected["id"]]
                assert KEY not in response.text
        for method, path, body in (
            ("GET", "/providers", None),
            ("POST", "/providers", org_config()),
            ("POST", "/providers/test", {"capability": "llm_extract"}),
        ):
            response = await api.request(
                method, path, headers={**a, "X-Org-Id": b["X-Org-Id"]}, json=body
            )
            assert response.status_code == 404
            assert (await api.request(method, path, json=body)).status_code in {401, 422}
        stale = await api.post("/providers", headers=a, json=org_config())
        assert stale.status_code == 409
        second = await save(api, a, expected_revision=1, api_key=None)
        assert second["revision"] == 2 and second["key_last4"] == "4321"
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            rows = (await session.scalars(select(ProviderConfig))).all()
            assert len(rows) == 2 and all(KEY not in row.encrypted_key for row in rows)
            assert await session.get(ProviderConfig, UUID(foreign["id"])) is None
            audits = (await session.scalars(select(AuditLog.details))).all()
            assert KEY not in json.dumps(audits)
        async with app.state.db.transaction() as session:
            assert (await session.scalars(select(ProviderConfig))).all() == []
        for statement in (
            "UPDATE provider_configs SET revision=revision",
            "DELETE FROM provider_configs",
        ):
            with pytest.raises(DBAPIError):
                async with app.state.db.transaction(tenants["orgs"][0]) as session:
                    await session.execute(text(statement))
        with pytest.raises(DBAPIError) as failure:
            async with app.state.db.transaction(tenants["orgs"][0]) as session:
                session.add(
                    ApiToken(
                        org_id=tenants["orgs"][0],
                        user_id=tenants["users"][0],
                        name="forged",
                        digest=uuid4().hex,
                        scopes=["provider:write"],
                        expires_at=datetime.now(UTC) + timedelta(days=1),
                    )
                )
        assert getattr(failure.value.orig, "sqlstate", None) == "23514"
        token = await api.post(
            "/tokens",
            headers=a,
            json={
                "name": "forbidden",
                "scopes": ["provider:write"],
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
        )
        assert token.status_code in {400, 403}


async def test_org_extraction_pins_revision_and_skips_prepaid(tenants, tmp_path, pdf_bytes):
    async with configured(tenants, tmp_path) as (app, api, headers, vendor):
        first = await save(api, headers[0])
        task, document = await create_document(api, headers[0], pdf_bytes)
        await run_job(api, app, headers[0], document, "parse")
        queued = await api.post(f"/documents/{document}/extract", headers=headers[0], json={})
        old_job = queued.json()["data"]["job_id"]
        second = await save(api, headers[0], expected_revision=1, api_key="synthetic-new-key-8765")
        await app.state.processor(headers[0]["X-Org-Id"], old_job)
        assert vendor.calls[0][1] == f"Bearer {KEY}"
        assert vendor.calls[0][0]["reasoning_effort"] == "low"
        new_job, status = await run_job(api, app, headers[0], document, "extract")
        assert status["status"] == "succeeded" and new_job != old_job
        assert vendor.calls[-1][1] == "Bearer synthetic-new-key-8765"
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            usages = (
                await session.scalars(
                    select(UsageRecord).where(UsageRecord.provider_config_id.is_not(None))
                )
            ).all()
            assert {str(row.provider_config_id) for row in usages} == {first["id"], second["id"]}
            assert all(
                row.charge == 0 and row.usd > 0 and row.platform_model_id is None for row in usages
            )
            assert await session.scalar(select(func.count(VendorCall.id))) == len(vendor.calls)
            assert await session.scalar(select(func.count(OrgBalance.org_id))) == 0
            assert (await session.get(Job, UUID(old_job))).provider_config_id == UUID(first["id"])
        listing = (await api.get("/providers", headers=headers[0])).json()
        assert listing["data"]["month_usage"]["input_tokens"] > 0
        assert listing["items"][0]["balance"]["status"] == "supported"
        assert (await api.get(f"/jobs/{old_job}", headers=headers[1])).status_code == 404
        await asyncio.to_thread(
            Path("data/work/provider-validation").mkdir, parents=True, exist_ok=True
        )
        await asyncio.to_thread(
            Path("data/work/provider-validation/api-flow.json").write_text,
            json.dumps(
                {
                    "old_job": old_job,
                    "new_job": new_job,
                    "config_ids": [first["id"], second["id"]],
                    "vendor_calls": len(vendor.calls),
                    "status": status["status"],
                },
                indent=2,
            )
            + "\n",
        )


@pytest.mark.parametrize("source", ["unconfigured", "default", "selected"])
async def test_resolution_and_platform_admission(source, tenants, tmp_path, pdf_bytes, monkeypatch):
    monkeypatch.setenv("BID_PLATFORM_CREDENTIAL_PROVIDER_TEST", "synthetic-platform-key")
    async with configured(tenants, tmp_path) as (app, api, headers, vendor):
        if source != "unconfigured":
            await catalog(app, tenants["orgs"][0])
        if source == "selected":
            selected = await api.post(
                "/providers",
                headers=headers[0],
                json={
                    "capability": "llm_extract",
                    "source": "platform",
                    "platform_model_id": "paid",
                },
            )
            assert selected.status_code == 200
        _, document = await create_document(api, headers[0], pdf_bytes)
        await run_job(api, app, headers[0], document, "parse")
        response = await api.post(f"/documents/{document}/extract", headers=headers[0], json={})
        if source == "unconfigured":
            job = response.json()["data"]["job_id"]
            await app.state.processor(headers[0]["X-Org-Id"], job)
            status = (await api.get(f"/jobs/{job}", headers=headers[0])).json()["data"]
            assert status["error"]["code"] == "provider_unavailable" and not vendor.calls
            return
        assert response.status_code == 402 and not vendor.calls
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            session.add(OrgBalance(org_id=tenants["orgs"][0], currency="USD", balance=Decimal(10)))
        _, status = await run_job(api, app, headers[0], document, "extract")
        assert status["status"] == "succeeded"
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            usage = await session.scalar(
                select(UsageRecord).where(UsageRecord.platform_model_id == "paid")
            )
            assert usage.charge > 0 and bool(usage.provider_config_id) == (source == "selected")


async def test_org_drafting_uses_fixed_config(tenants, tmp_path):
    async with configured(tenants, tmp_path, vendor=DraftVendor()) as (app, api, headers, vendor):
        first = await save(api, headers[0], base_url="https://vendor.example/v1")
        task, _, extraction, _ = await create_tender(api, app, headers[0], tmp_path)
        response = await api.post(
            f"/tasks/{task}/cards/generations",
            headers=headers[0],
            json={"extraction_job_id": extraction},
        )
        assert response.status_code == 200, response.text
        job = response.json()["data"]["job_id"]
        await save(
            api,
            headers[0],
            expected_revision=1,
            api_key="synthetic-replacement-key",
            base_url="https://new.example/v1",
        )
        await app.state.processor(headers[0]["X-Org-Id"], job)
        status = (await api.get(f"/jobs/{job}", headers=headers[0])).json()["data"]
        assert status["status"] == "succeeded", status
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            usages = (
                await session.scalars(select(UsageRecord).where(UsageRecord.job_id == UUID(job)))
            ).all()
            assert usages and all(
                str(row.provider_config_id) == first["id"] and row.charge == 0 for row in usages
            )


async def test_probe_quota_and_permissions(tenants, tmp_path, admin_engine):
    async with configured(tenants, tmp_path) as (app, api, headers, vendor):
        await save(api, headers[0])
        response = await api.post(
            "/providers/test", headers=headers[0], json={"capability": "llm_extract"}
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["usage"]["charge"] == 0 and data["usage"]["input_tokens"] > 0
        assert data["balance"]["status"] == "supported"
        assert len(vendor.calls) == 1
        vendor.response = httpx.Response(
            402, json={"error": {"type": KEY, "message": KEY + " reset 2030-01-01 12:30"}}
        )
        failed = await api.post(
            "/providers/test", headers=headers[0], json={"capability": "llm_extract"}
        )
        assert failed.json()["data"]["error"]["code"] == "provider_quota_exhausted"
        assert (
            KEY not in failed.text and "vendor" in failed.text and "2030-01-01 12:30" in failed.text
        )
        assert len(vendor.calls) == 2
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "viewer")
        assert (await api.get("/providers", headers=headers[0])).status_code == 200
        for path, body in (
            ("/providers", org_config(expected_revision=1)),
            ("/providers/test", {"capability": "llm_extract"}),
        ):
            assert (await api.post(path, headers=headers[0], json=body)).status_code == 403


async def test_database_requires_human_admin_and_foreign_keys(tenants, tmp_path):
    async with configured(tenants, tmp_path) as (app, api, headers, vendor):
        foreign = await save(api, headers[1])
        for kind in ("token", "worker", "session"):
            with pytest.raises(DBAPIError):
                async with app.state.db.transaction(tenants["orgs"][0]) as session:
                    actor = Identity(
                        tenants["users"][0],
                        tenants["orgs"][0],
                        set(ROLE_SCOPES["admin"]),
                        "admin",
                        actor_kind=kind,
                    )
                    if kind != "session":
                        await set_actor_context(session, actor)
                    session.add(
                        ProviderConfig(
                            id=uuid4(),
                            org_id=tenants["orgs"][0],
                            capability="llm_extract",
                            source="platform",
                            platform_model_id="missing",
                            revision=1,
                            updated_by=tenants["users"][0],
                            data={},
                        )
                    )
        with pytest.raises(DBAPIError) as foreign_key:
            async with app.state.db.transaction(tenants["orgs"][0]) as session:
                await set_actor_context(
                    session,
                    Identity(
                        tenants["users"][0], tenants["orgs"][0], set(ROLE_SCOPES["admin"]), "admin"
                    ),
                )
                session.add(
                    Job(
                        org_id=tenants["orgs"][0],
                        kind="provider_test",
                        cache_key=uuid4().hex,
                        provider_config_id=UUID(foreign["id"]),
                        result={"submission": {"actor_user_id": str(tenants["users"][0])}},
                    )
                )
        assert getattr(foreign_key.value.orig, "sqlstate", None) == "23503"


async def test_org_calls_obey_cumulative_admission_and_account_malformed_replies(
    tenants, tmp_path, pdf_bytes
):
    async with configured(
        tenants, tmp_path, job_max_vendor_calls=1, job_vendor_calls_per_batch=1
    ) as (app, api, headers, vendor):
        await save(
            api,
            headers[0],
            reasoning=[{"name": "low", "batch_chars": 1000}],
            base_url="https://vendor.example/v1",
        )
        vendor.response = provider_reply("openai", [])
        _, document = await create_document(api, headers[0], pdf_bytes)
        await run_job(api, app, headers[0], document, "parse")
        # A malformed response splits this multi-page batch; admission must stop the halves.
        vendor.response = httpx.Response(
            200,
            json={
                "model": "synthetic-model",
                "choices": [{"finish_reason": "stop", "message": {"content": "malformed"}}],
                "usage": {"prompt_tokens": 90, "completion_tokens": 10},
            },
        )
        job, status = await run_job(api, app, headers[0], document, "extract")
        assert status["status"] == "failed" and status["error"]["code"] == "job_call_limit_exceeded"
        assert len(vendor.calls) == 1
        response = await api.post(
            f"/documents/{document}/extract", headers=headers[0], json={"retry": True}
        )
        assert response.json()["data"]["job_id"] == job
        await app.state.processor(headers[0]["X-Org-Id"], job)
        assert len(vendor.calls) == 1
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            usage = (
                await session.scalars(select(UsageRecord).where(UsageRecord.job_id == UUID(job)))
            ).one()
            assert usage.input_tokens == 90 and usage.output_tokens == 10 and usage.charge == 0
            assert (
                await session.scalar(select(VendorCall).where(VendorCall.job_id == UUID(job)))
            ).state == "completed"
        # A fresh single-call probe reports metered usage even though its output is invalid.
        probe = (await api.post("/providers/test", headers=headers[0], json={})).json()
        assert not probe["ok"] and probe["data"]["usage"]["input_tokens"] == 90
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            assert await session.scalar(select(func.count(Task.id))) == 1
            assert await session.scalar(select(func.count(Document.id))) == 1


async def test_concurrent_updates_and_token_reads(tenants, tmp_path):
    async with configured(tenants, tmp_path) as (app, api, headers, vendor):
        await save(api, headers[0])
        replies = await asyncio.gather(
            *(
                api.post(
                    "/providers",
                    headers=headers[0],
                    json=org_config(expected_revision=1, api_key=None),
                )
                for _ in range(2)
            )
        )
        assert sorted(reply.status_code for reply in replies) == [200, 409]
        token = await token_header(api, headers[0], scopes=["provider:read"])
        for history in (False, True):
            response = await api.get("/providers", headers=token, params={"history": history})
            assert response.status_code == 200 and KEY not in response.text
            assert (
                await api.get(
                    "/providers",
                    headers={**token, "X-Org-Id": headers[1]["X-Org-Id"]},
                    params={"history": history},
                )
            ).status_code == 401
        for path, body in (
            ("/providers", org_config(expected_revision=2)),
            ("/providers/test", {}),
        ):
            assert (await api.post(path, headers=token, json=body)).status_code == 403


async def test_catalog_changed_after_submission_is_fenced(
    tenants, tmp_path, pdf_bytes, monkeypatch
):
    monkeypatch.setenv("BID_PLATFORM_CREDENTIAL_PROVIDER_TEST", "synthetic-platform-key")
    async with configured(tenants, tmp_path) as (app, api, headers, vendor):
        await catalog(app, tenants["orgs"][0])
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            session.add(OrgBalance(org_id=tenants["orgs"][0], currency="USD", balance=Decimal(10)))
        _, document = await create_document(api, headers[0], pdf_bytes)
        await run_job(api, app, headers[0], document, "parse")
        response = await api.post(f"/documents/{document}/extract", headers=headers[0], json={})
        job = response.json()["data"]["job_id"]
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            row = await session.get(PlatformModel, "paid")
            row.revision += 1
            row.sale_input_per_mtok = 10
        await app.state.processor(headers[0]["X-Org-Id"], job)
        status = (await api.get(f"/jobs/{job}", headers=headers[0])).json()["data"]
        assert status["error"]["code"] == "provider_model_changed" and not vendor.calls


async def test_failed_decryption_and_key_echo_fail_closed(tenants, tmp_path, pdf_bytes, caplog):
    async with configured(tenants, tmp_path) as (app, api, headers, vendor):
        await save(api, headers[0])
        _, document = await create_document(api, headers[0], pdf_bytes)
        await run_job(api, app, headers[0], document, "parse")
        response = await api.post(f"/documents/{document}/extract", headers=headers[0], json={})
        job = response.json()["data"]["job_id"]
        original_key = app.state.processor.settings.secrets_key
        app.state.processor.settings.secrets_key = None
        await app.state.processor(headers[0]["X-Org-Id"], job)
        status = (await api.get(f"/jobs/{job}", headers=headers[0])).json()["data"]
        assert status["error"]["code"] == "provider_secrets_unavailable" and not vendor.calls
        app.state.processor.settings.secrets_key = original_key
        vendor.response = provider_reply("openai", [{**GOOD_ITEMS[0], "quote": KEY}])
        response = await api.post("/providers/test", headers=headers[0], json={})
        assert response.json()["data"]["error"]["code"] == "invalid_provider_output"
        assert KEY not in response.text and KEY not in caplog.text
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            usage = await session.scalar(select(UsageRecord))
            assert usage is not None and usage.charge == 0
        invalid = await api.post(
            "/providers", headers=headers[0], json=org_config(api_key=KEY + "\n")
        )
        assert invalid.status_code == 422 and KEY not in invalid.text


async def test_new_table_forced_rls_and_cross_scope_inserts(tenants, tmp_path, admin_engine):
    async with configured(tenants, tmp_path) as (app, api, headers, vendor):
        saved = await save(api, headers[0])
        await save(api, headers[1], api_key="synthetic-b-key-9876")
        with admin_engine.connect() as connection:
            flags = connection.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname='provider_configs'"
                )
            ).one()
            assert flags.relrowsecurity and flags.relforcerowsecurity
            row = dict(
                connection.execute(
                    select(ProviderConfig.__table__).where(ProviderConfig.id == UUID(saved["id"]))
                )
                .mappings()
                .one()
            )
        for context in (None, tenants["orgs"][1]):
            with pytest.raises(DBAPIError) as error:
                async with app.state.db.transaction(context) as session:
                    row["id"] = uuid4()
                    await session.execute(insert(ProviderConfig).values(**row))
            assert getattr(error.value.orig, "sqlstate", None) == "42501"
        async with app.state.db.transaction(tenants["orgs"][1]) as session:
            assert await session.scalar(select(func.count(UsageRecord.id))) == 0
        # A probe usage row may not be rebound to another tenant's configuration.
        await api.post("/providers/test", headers=headers[0], json={})
        foreign = (await api.get("/providers", headers=headers[1])).json()["items"][0]["id"]
        with pytest.raises(DBAPIError):
            async with app.state.db.transaction(tenants["orgs"][0]) as session:
                await session.execute(
                    text("UPDATE usage_records SET provider_config_id=:id"), {"id": UUID(foreign)}
                )
