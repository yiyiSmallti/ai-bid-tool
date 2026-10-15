"""API/processor gates for local provider revisions.

Failure inventory: tokens write/test; stale writes lose revisions; historical keys
mutate; keys leak through errors/views/audits; queued jobs change identity; calls
evade admission; quota failures retry; probes lose durable usage. MockTransport
supplies synthetic vendor replies against the integration PostgreSQL database.
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
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
    Task,
    UsageRecord,
    VendorCall,
)
from app.models.provider_configs import ProviderConfig
from app.services.auth import Identity, set_actor_context
from conftest import FakeQueue
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from test_api import create_document, run_job
from test_llm_providers import GOOD_ITEMS, provider_reply
from test_response_cards import create_tender

KEY = "synthetic-local-provider-key-4321"


def local_config(**changes):
    return {
        "capability": "llm_extract",
        "provider": "openai",
        "model": "synthetic-model",
        "base_url": "https://api.deepseek.com/v1",
        "json_mode": "json_object",
        "api_key": KEY,
        "reasoning": [{"name": "low", "request_options": {"reasoning_effort": "low"}}],
        "default_reasoning": "low",
        **changes,
    }


async def token_header(api, header, *, scopes):
    response = await api.post(
        "/tokens",
        headers=header,
        json={
            "name": "Synthetic provider reader",
            "scopes": scopes,
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["data"]["token"]}


class Vendor:
    def __init__(self):
        self.calls = []
        self.response: httpx.Response | None = None

    async def __call__(self, request):
        assert request.method == "POST"
        body = json.loads(request.content)
        self.calls.append((body, request.headers.get("authorization")))
        return self.response or provider_reply("openai", GOOD_ITEMS)


@asynccontextmanager
async def configured(database, tmp_path, *, vendor=None, **options):
    vendor = vendor or Vendor()
    settings = Settings(
        data_dir=tmp_path, secrets_key=SecretStr(Fernet.generate_key().decode()), **options
    )
    app = create_app(settings, queue=FakeQueue(), llm_transport=httpx.MockTransport(vendor))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api,
    ):
        headers = {"Authorization": "Bearer " + os.environ["BID_ACCESS_KEY"]}
        yield app, api, headers, vendor


async def save(api, header, **changes):
    response = await api.post("/providers", headers=header, json=local_config(**changes))
    assert response.status_code == 200, response.text
    assert KEY not in response.text and "encrypted_key" not in response.text
    return response.json()["data"]


async def test_revision_isolation_and_database_gates(database, tmp_path):
    async with configured(database, tmp_path) as (app, api, headers, vendor):
        a = headers
        first = await save(api, a)
        for history in (False, True):
            response = await api.get("/providers", headers=a, params={"history": history})
            assert response.status_code == 200
            assert [row["id"] for row in response.json()["items"]] == [first["id"]]
            assert KEY not in response.text
        assert (await api.get("/providers")).status_code == 401
        stale = await api.post("/providers", headers=a, json=local_config())
        assert stale.status_code == 409
        second = await save(api, a, expected_revision=1, api_key=None)
        assert second["revision"] == 2 and second["key_last4"] == "4321"
        async with app.state.db.transaction() as session:
            rows = (await session.scalars(select(ProviderConfig))).all()
            assert len(rows) == 2 and all(KEY not in row.encrypted_key for row in rows)
            audits = (await session.scalars(select(AuditLog.details))).all()
            assert KEY not in json.dumps(audits)
        for statement in (
            "UPDATE provider_configs SET revision=revision",
            "DELETE FROM provider_configs",
        ):
            with pytest.raises(DBAPIError):
                async with app.state.db.transaction() as session:
                    await session.execute(text(statement))
        with pytest.raises(DBAPIError) as failure:
            async with app.state.db.transaction() as session:
                session.add(
                    ApiToken(
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


async def test_extraction_pins_revision_and_records_usage(database, tmp_path, pdf_bytes):
    async with configured(database, tmp_path) as (app, api, headers, vendor):
        first = await save(api, headers)
        task, document = await create_document(api, headers, pdf_bytes)
        await run_job(api, app, headers, document, "parse")
        queued = await api.post(f"/documents/{document}/extract", headers=headers, json={})
        old_job = queued.json()["data"]["job_id"]
        second = await save(api, headers, expected_revision=1, api_key="synthetic-new-key-8765")
        await app.state.processor(old_job)
        assert vendor.calls[0][1] == f"Bearer {KEY}"
        assert vendor.calls[0][0]["reasoning_effort"] == "low"
        new_job, status = await run_job(api, app, headers, document, "extract")
        assert status["status"] == "succeeded" and new_job != old_job
        assert vendor.calls[-1][1] == "Bearer synthetic-new-key-8765"
        async with app.state.db.transaction() as session:
            usages = (
                await session.scalars(
                    select(UsageRecord).where(UsageRecord.provider_config_id.is_not(None))
                )
            ).all()
            assert {str(row.provider_config_id) for row in usages} == {first["id"], second["id"]}
            assert await session.scalar(select(func.count(VendorCall.id))) == len(vendor.calls)
            assert (await session.get(Job, UUID(old_job))).provider_config_id == UUID(first["id"])
        listing = (await api.get("/providers", headers=headers)).json()
        assert listing["data"]["month_usage"]["input_tokens"] > 0
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


async def test_drafting_uses_fixed_config(database, tmp_path):
    from test_card_generation import DraftVendor

    async with configured(database, tmp_path, vendor=DraftVendor()) as (app, api, headers, vendor):
        first = await save(api, headers, base_url="https://vendor.example/v1")
        task, _, extraction, _ = await create_tender(api, app, headers, tmp_path)
        response = await api.post(
            f"/tasks/{task}/cards/generations",
            headers=headers,
            json={"extraction_job_id": extraction},
        )
        assert response.status_code == 200, response.text
        job = response.json()["data"]["job_id"]
        await save(
            api,
            headers,
            expected_revision=1,
            api_key="synthetic-replacement-key",
            base_url="https://new.example/v1",
        )
        await app.state.processor(job)
        status = (await api.get(f"/jobs/{job}", headers=headers)).json()["data"]
        assert status["status"] == "succeeded", status
        async with app.state.db.transaction() as session:
            usages = (
                await session.scalars(select(UsageRecord).where(UsageRecord.job_id == UUID(job)))
            ).all()
            assert usages and all(str(row.provider_config_id) == first["id"] for row in usages)


async def test_probe_quota_and_permissions(database, tmp_path, admin_engine):
    async with configured(database, tmp_path) as (app, api, headers, vendor):
        await save(api, headers)
        response = await api.post(
            "/providers/test", headers=headers, json={"capability": "llm_extract"}
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["usage"]["input_tokens"] > 0
        assert len(vendor.calls) == 1
        vendor.response = httpx.Response(
            402, json={"error": {"type": KEY, "message": KEY + " reset 2030-01-01 12:30"}}
        )
        failed = await api.post(
            "/providers/test", headers=headers, json={"capability": "llm_extract"}
        )
        assert failed.json()["data"]["error"]["code"] == "provider_quota_exhausted"
        assert (
            KEY not in failed.text
            and "vendor" in failed.text
            and ("2030-01-01 12:30" in failed.text)
        )
        assert len(vendor.calls) == 2
        headers = await token_header(api, headers, scopes=["provider:read"])
        assert (await api.get("/providers", headers=headers)).status_code == 200
        for path, body in (
            ("/providers", local_config(expected_revision=1)),
            ("/providers/test", {"capability": "llm_extract"}),
        ):
            assert (await api.post(path, headers=headers, json=body)).status_code == 403


async def test_database_requires_human_and_foreign_keys(database, tmp_path):
    async with configured(database, tmp_path) as (app, api, headers, vendor):
        first = await save(api, headers)
        for kind in ("token", "worker", "session"):
            with pytest.raises(DBAPIError):
                async with app.state.db.transaction() as session:
                    if kind != "session":
                        await set_actor_context(
                            session, Identity(scopes={"provider:write"}, actor_kind=kind)
                        )
                    previous = await session.get(ProviderConfig, UUID(first["id"]))
                    session.add(
                        ProviderConfig(
                            id=uuid4(),
                            capability="llm_extract",
                            revision=2,
                            data=previous.data,
                            encrypted_key=previous.encrypted_key,
                            key_last4=previous.key_last4,
                        )
                    )
        with pytest.raises(DBAPIError) as foreign_key:
            async with app.state.db.transaction() as session:
                await set_actor_context(session, Identity(scopes={"provider:write"}))
                session.add(
                    Job(
                        kind="provider_test",
                        cache_key=uuid4().hex,
                        provider_config_id=uuid4(),
                        actor_kind="session",
                        result={"submission": {}},
                    )
                )
        assert getattr(foreign_key.value.orig, "sqlstate", None) == "23503"


async def test_calls_obey_cumulative_admission_and_account_malformed_replies(
    database, tmp_path, pdf_bytes
):
    async with configured(
        database, tmp_path, job_max_vendor_calls=1, job_vendor_calls_per_batch=1
    ) as (app, api, headers, vendor):
        await save(
            api,
            headers,
            reasoning=[{"name": "low", "batch_chars": 1000}],
            base_url="https://vendor.example/v1",
        )
        vendor.response = provider_reply("openai", [])
        _, document = await create_document(api, headers, pdf_bytes)
        await run_job(api, app, headers, document, "parse")
        vendor.response = httpx.Response(
            200,
            json={
                "model": "synthetic-model",
                "choices": [{"finish_reason": "stop", "message": {"content": "malformed"}}],
                "usage": {"prompt_tokens": 90, "completion_tokens": 10},
            },
        )
        job, status = await run_job(api, app, headers, document, "extract")
        assert status["status"] == "failed" and status["error"]["code"] == "job_call_limit_exceeded"
        assert len(vendor.calls) == 1
        response = await api.post(
            f"/documents/{document}/extract", headers=headers, json={"retry": True}
        )
        assert response.json()["data"]["job_id"] == job
        await app.state.processor(job)
        assert len(vendor.calls) == 1
        async with app.state.db.transaction() as session:
            usage = (
                await session.scalars(select(UsageRecord).where(UsageRecord.job_id == UUID(job)))
            ).one()
            assert usage.input_tokens == 90 and usage.output_tokens == 10
            assert (
                await session.scalar(select(VendorCall).where(VendorCall.job_id == UUID(job)))
            ).state == "completed"
        probe = (await api.post("/providers/test", headers=headers, json={})).json()
        assert not probe["ok"] and probe["data"]["usage"]["input_tokens"] == 90
        async with app.state.db.transaction() as session:
            assert await session.scalar(select(func.count(Task.id))) == 1
            assert await session.scalar(select(func.count(Document.id))) == 1


async def test_concurrent_updates_and_token_reads(database, tmp_path):
    async with configured(database, tmp_path) as (app, api, headers, vendor):
        await save(api, headers)
        replies = await asyncio.gather(
            *(
                api.post(
                    "/providers",
                    headers=headers,
                    json=local_config(expected_revision=1, api_key=None),
                )
                for _ in range(2)
            )
        )
        assert sorted(reply.status_code for reply in replies) == [200, 409]
        token = await token_header(api, headers, scopes=["provider:read"])
        for history in (False, True):
            response = await api.get("/providers", headers=token, params={"history": history})
            assert response.status_code == 200 and KEY not in response.text
        for path, body in (
            ("/providers", local_config(expected_revision=2)),
            ("/providers/test", {}),
        ):
            assert (await api.post(path, headers=token, json=body)).status_code == 403


async def test_failed_decryption_and_key_echo_fail_closed(database, tmp_path, pdf_bytes, caplog):
    async with configured(database, tmp_path) as (app, api, headers, vendor):
        await save(api, headers)
        _, document = await create_document(api, headers, pdf_bytes)
        await run_job(api, app, headers, document, "parse")
        response = await api.post(f"/documents/{document}/extract", headers=headers, json={})
        job = response.json()["data"]["job_id"]
        original_key = app.state.processor.settings.secrets_key
        app.state.processor.settings.secrets_key = None
        await app.state.processor(job)
        status = (await api.get(f"/jobs/{job}", headers=headers)).json()["data"]
        assert status["error"]["code"] == "provider_secrets_unavailable" and (not vendor.calls)
        app.state.processor.settings.secrets_key = original_key
        vendor.response = provider_reply("openai", [{**GOOD_ITEMS[0], "quote": KEY}])
        response = await api.post("/providers/test", headers=headers, json={})
        assert response.json()["data"]["error"]["code"] == "invalid_provider_output"
        assert KEY not in response.text and KEY not in caplog.text
        async with app.state.db.transaction() as session:
            usage = await session.scalar(select(UsageRecord))
            assert usage is not None
        invalid = await api.post(
            "/providers", headers=headers, json=local_config(api_key=KEY + "\n")
        )
        assert invalid.status_code == 422 and KEY not in invalid.text


async def test_several_models_default_choice_and_removal(database, tmp_path, pdf_bytes):
    """Failure inventory: a second model replaces the first; a chosen model is ignored;
    a key leaks to another endpoint; removal strands pinned jobs or leaves no default."""
    async with configured(database, tmp_path) as (app, api, headers, vendor):
        first = await save(api, headers, name="DeepSeek")
        assert first["is_default"]
        # Same endpoint: the saved key is reused without being sent again.
        same = await save(api, headers, new=True, name="DeepSeek 推理", model="r", api_key=None)
        other_key = "synthetic-second-vendor-key-9876"
        other = await save(
            api,
            headers,
            new=True,
            name="智谱",
            model="glm-synthetic",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            api_key=other_key,
        )
        missing = await api.post(
            "/providers",
            headers=headers,
            json=local_config(new=True, base_url="https://api.moonshot.cn/v1", api_key=None),
        )
        assert missing.status_code == 400 and "provider_key_required" in missing.text
        assert {same["key_last4"], other["key_last4"]} == {"4321", "9876"}
        assert len({first["slot"], same["slot"], other["slot"]}) == 3
        assert not same["is_default"] and not other["is_default"]
        listing = (await api.get("/providers", headers=headers)).json()["items"]
        assert [row["slot"] for row in listing][0] == first["slot"] and len(listing) == 3
        settings = (await api.get("/management/providers", headers=headers)).json()["data"]
        assert settings["default_slot"] == first["slot"] and len(settings["models"]) == 3
        assert settings["current"]["name"] == "DeepSeek"

        # Revising one model leaves the others alone.
        stale = await api.post(
            "/providers",
            headers=headers,
            json=local_config(slot=other["slot"], expected_revision=first["revision"]),
        )
        assert stale.status_code == 409
        revised = await save(
            api,
            headers,
            slot=other["slot"],
            expected_revision=other["revision"],
            name="智谱 flash",
            model="glm-synthetic",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            api_key=None,
        )
        assert revised["slot"] == other["slot"] and revised["key_last4"] == "9876"

        task, document = await create_document(api, headers, pdf_bytes)
        await run_job(api, app, headers, document, "parse")
        chosen = await api.post(
            f"/documents/{document}/extract", headers=headers, json={"model": other["slot"]}
        )
        assert chosen.status_code == 200, chosen.text
        chosen_job = chosen.json()["data"]["job_id"]
        default_job = (
            await api.post(f"/documents/{document}/extract", headers=headers, json={})
        ).json()["data"]["job_id"]
        assert chosen_job != default_job

        # Removing a model keeps its queued job runnable and moves the default.
        removed = await api.delete(f"/providers/models/{other['slot']}", headers=headers)
        assert removed.status_code == 200, removed.text
        assert removed.json()["data"]["default"] == first["slot"]
        await app.state.processor(chosen_job)
        assert vendor.calls[-1][1] == f"Bearer {other_key}"
        await app.state.processor(default_job)
        assert vendor.calls[-1][1] == f"Bearer {KEY}"
        gone = await api.post(
            f"/documents/{document}/extract", headers=headers, json={"model": other["slot"]}
        )
        assert gone.status_code == 404 and "provider_model_unavailable" in gone.text

        made_default = await api.post(f"/providers/models/{same['slot']}/default", headers=headers)
        assert made_default.status_code == 200 and made_default.json()["data"]["is_default"]
        await api.delete(f"/providers/models/{same['slot']}", headers=headers)
        settings = (await api.get("/management/providers", headers=headers)).json()["data"]
        assert settings["default_slot"] == first["slot"] and len(settings["models"]) == 1
        async with app.state.db.transaction() as session:
            jobs = {
                str(job.id): str(job.provider_config_id)
                for job in await session.scalars(
                    select(Job).where(Job.id.in_([UUID(chosen_job), UUID(default_job)]))
                )
            }
        assert jobs == {chosen_job: revised["id"], default_job: first["id"]}
        await asyncio.to_thread(
            Path("data/work/provider-validation").mkdir, parents=True, exist_ok=True
        )
        await asyncio.to_thread(
            Path("data/work/provider-validation/multi-model-flow.json").write_text,
            json.dumps(
                {
                    "slots": [first["slot"], same["slot"], other["slot"]],
                    "jobs": jobs,
                    "final_default": settings["default_slot"],
                    "vendor_calls": len(vendor.calls),
                },
                indent=2,
            )
            + "\n",
        )
