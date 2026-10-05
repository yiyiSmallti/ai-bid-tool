import asyncio
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
import pytest
from app.api.main import create_app
from app.core.config import Settings
from app.models.entities import Job, Requirement, UsageRecord
from app.providers.base import ProviderFailure
from conftest import PASSWORD, FakeQueue
from fakes import FakeLLM
from sqlalchemy import func, select
from test_api import create_document, run_job


class InvalidCitation(FakeLLM):
    async def _extract(self, chunks, schema):
        result = await super()._extract(chunks, schema)
        for item in result.extraction.items:
            item.source.quote = "Fabricated citation"
        return result


@asynccontextmanager
async def session_for(app, tenants):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        logged = await client.post(
            "/auth/login",
            json={
                "email": "a@example.test",
                "password": PASSWORD,
                "org_id": str(tenants["orgs"][0]),
            },
        )
        header = {
            "Authorization": "Bearer " + logged.json()["data"]["session"],
            "X-Org-Id": str(tenants["orgs"][0]),
        }
        yield client, header


async def test_invalid_ai_citation_saves_no_requirement_but_records_usage(
    tenants, tmp_path, pdf_bytes
):
    app = create_app(Settings(data_dir=tmp_path), llm=InvalidCitation(), queue=FakeQueue())
    async with app.router.lifespan_context(app):
        async with session_for(app, tenants) as (api, header):
            _, document = await create_document(api, header, pdf_bytes)
            await run_job(api, app, header, document, "parse")
            _, status = await run_job(api, app, header, document, "extract")
            assert status["status"] == "failed" and status["error"]["code"] == "invalid_citation"
            async with app.state.db.transaction(tenants["orgs"][0]) as session:
                assert await session.scalar(select(func.count()).select_from(Requirement)) == 0
                assert await session.scalar(select(func.count()).select_from(UsageRecord)) == 1


async def test_cancellation_during_provider_call_cannot_commit_results(
    tenants, tmp_path, pdf_bytes
):
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowProvider(FakeLLM):
        async def _extract(self, chunks, schema):
            entered.set()
            await release.wait()
            return await super()._extract(chunks, schema)

    app = create_app(Settings(data_dir=tmp_path), llm=SlowProvider(), queue=FakeQueue())
    async with app.router.lifespan_context(app):
        async with session_for(app, tenants) as (api, header):
            _, document = await create_document(api, header, pdf_bytes)
            await run_job(api, app, header, document, "parse")
            queued = await api.post(
                f"/documents/{document}/extract", headers=header, json={"dry_run": False}
            )
            job = queued.json()["data"]["job_id"]
            running = asyncio.create_task(app.state.processor(header["X-Org-Id"], job))
            await entered.wait()
            assert (await api.post(f"/jobs/{job}/cancel", headers=header)).status_code == 200
            release.set()
            await running
            async with app.state.db.transaction(tenants["orgs"][0]) as session:
                assert await session.scalar(select(func.count()).select_from(Requirement)) == 0
                assert (await session.get(Job, UUID(job))).status == "cancelled"
                assert await session.scalar(select(func.count()).select_from(UsageRecord)) == 1


async def test_worker_context_cannot_process_another_tenant_job(
    api, application, headers, pdf_bytes
):
    _, document = await create_document(api, headers[1], pdf_bytes)
    queued = await api.post(
        f"/documents/{document}/parse", headers=headers[1], json={"dry_run": False}
    )
    job = queued.json()["data"]["job_id"]
    await application.state.processor(headers[0]["X-Org-Id"], job)
    status = (await api.get(f"/jobs/{job}", headers=headers[1])).json()["data"]
    assert status["status"] == "queued"
    assert (await api.get(f"/documents/{document}/chunks", headers=headers[1])).json()[
        "items"
    ] == []


async def test_retryable_failures_are_bounded(tenants, tmp_path, pdf_bytes):
    class FailingProvider(FakeLLM):
        async def _extract(self, chunks, schema):
            raise ProviderFailure("Synthetic timeout", retryable=True)

    app = create_app(Settings(data_dir=tmp_path), llm=FailingProvider(), queue=FakeQueue())
    async with app.router.lifespan_context(app):
        async with session_for(app, tenants) as (api, header):
            _, document = await create_document(api, header, pdf_bytes)
            await run_job(api, app, header, document, "parse")
            queued = await api.post(
                f"/documents/{document}/extract", headers=header, json={"dry_run": False}
            )
            job = queued.json()["data"]["job_id"]
            for _ in range(2):
                with pytest.raises(ProviderFailure):
                    await app.state.processor(header["X-Org-Id"], job)
            await app.state.processor(header["X-Org-Id"], job)
            status = (await api.get(f"/jobs/{job}", headers=header)).json()["data"]
            assert status["status"] == "failed" and status["attempts"] == 3
            assert status["error"]["exit_code"] == 3


async def test_cancel_retry_invalidates_old_attempt_results(tenants, tmp_path, pdf_bytes):
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowOnce(FakeLLM):
        async def _extract(self, chunks, schema):
            entered.set()
            await release.wait()
            return await super()._extract(chunks, schema)

    app = create_app(Settings(data_dir=tmp_path), llm=SlowOnce(), queue=FakeQueue())
    async with app.router.lifespan_context(app), session_for(app, tenants) as (api, header):
        _, document = await create_document(api, header, pdf_bytes)
        await run_job(api, app, header, document, "parse")
        queued = await api.post(
            f"/documents/{document}/extract", headers=header, json={"dry_run": False}
        )
        job = queued.json()["data"]["job_id"]
        old = asyncio.create_task(app.state.processor(header["X-Org-Id"], job))
        await entered.wait()
        await api.post(f"/jobs/{job}/cancel", headers=header)
        retried = await api.post(
            f"/documents/{document}/extract", headers=header, json={"retry": True}
        )
        assert retried.status_code == 200 and retried.json()["data"]["status"] == "queued"
        release.set()
        await old
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            assert await session.scalar(select(func.count()).select_from(Requirement)) == 0
            assert (await session.get(Job, UUID(job))).status == "queued"
        await app.state.processor(header["X-Org-Id"], job)
        assert (await api.get(f"/jobs/{job}", headers=header)).json()["data"][
            "status"
        ] == "succeeded"
