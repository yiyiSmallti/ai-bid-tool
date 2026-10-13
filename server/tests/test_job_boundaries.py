import asyncio
import os
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
import pytest
from app.api.main import create_app
from app.core.config import Settings
from app.models.entities import Job, Requirement, UsageRecord
from app.providers.base import ProviderFailure
from conftest import FakeQueue
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
async def session_for(app, database):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        logged = await client.post(
            "/auth/session", json={"access_key": os.environ["BID_ACCESS_KEY"]}
        )
        header = {"Authorization": "Bearer " + logged.json()["data"]["session"]}
        yield client, header


async def test_invalid_ai_citation_saves_no_requirement_but_records_usage(
    database, tmp_path, pdf_bytes
):
    app = create_app(Settings(data_dir=tmp_path), llm=InvalidCitation(), queue=FakeQueue())
    async with app.router.lifespan_context(app):
        async with session_for(app, database) as (api, header):
            _, document = await create_document(api, header, pdf_bytes)
            await run_job(api, app, header, document, "parse")
            _, status = await run_job(api, app, header, document, "extract")
            assert status["status"] == "failed" and status["error"]["code"] == "invalid_citation"
            async with app.state.db.transaction() as session:
                assert await session.scalar(select(func.count()).select_from(Requirement)) == 0
                assert await session.scalar(select(func.count()).select_from(UsageRecord)) == 1


async def test_cancellation_during_provider_call_cannot_commit_results(
    database, tmp_path, pdf_bytes
):
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowProvider(FakeLLM):
        async def _extract(self, chunks, schema):
            entered.set()
            await release.wait()
            return await super()._extract(chunks, schema)

    app = create_app(Settings(data_dir=tmp_path), llm=SlowProvider(), queue=FakeQueue())
    async with app.router.lifespan_context(app):
        async with session_for(app, database) as (api, header):
            _, document = await create_document(api, header, pdf_bytes)
            await run_job(api, app, header, document, "parse")
            queued = await api.post(
                f"/documents/{document}/extract", headers=header, json={"dry_run": False}
            )
            job = queued.json()["data"]["job_id"]
            running = asyncio.create_task(app.state.processor(job))
            await entered.wait()
            assert (await api.post(f"/jobs/{job}/cancel", headers=header)).status_code == 200
            release.set()
            await running
            async with app.state.db.transaction() as session:
                assert await session.scalar(select(func.count()).select_from(Requirement)) == 0
                assert (await session.get(Job, UUID(job))).status == "cancelled"
                assert await session.scalar(select(func.count()).select_from(UsageRecord)) == 1


async def test_retryable_failures_are_bounded(database, tmp_path, pdf_bytes):
    class FailingProvider(FakeLLM):
        async def _extract(self, chunks, schema):
            raise ProviderFailure("Synthetic timeout", retryable=True)

    app = create_app(Settings(data_dir=tmp_path), llm=FailingProvider(), queue=FakeQueue())
    async with app.router.lifespan_context(app):
        async with session_for(app, database) as (api, header):
            _, document = await create_document(api, header, pdf_bytes)
            await run_job(api, app, header, document, "parse")
            queued = await api.post(
                f"/documents/{document}/extract", headers=header, json={"dry_run": False}
            )
            job = queued.json()["data"]["job_id"]
            for _ in range(2):
                with pytest.raises(ProviderFailure):
                    await app.state.processor(job)
            await app.state.processor(job)
            status = (await api.get(f"/jobs/{job}", headers=header)).json()["data"]
            assert status["status"] == "failed" and status["attempts"] == 3
            assert status["error"]["exit_code"] == 3


async def test_cancel_retry_invalidates_old_attempt_results(database, tmp_path, pdf_bytes):
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowOnce(FakeLLM):
        async def _extract(self, chunks, schema):
            entered.set()
            await release.wait()
            return await super()._extract(chunks, schema)

    app = create_app(Settings(data_dir=tmp_path), llm=SlowOnce(), queue=FakeQueue())
    async with app.router.lifespan_context(app), session_for(app, database) as (api, header):
        _, document = await create_document(api, header, pdf_bytes)
        await run_job(api, app, header, document, "parse")
        queued = await api.post(
            f"/documents/{document}/extract", headers=header, json={"dry_run": False}
        )
        job = queued.json()["data"]["job_id"]
        old = asyncio.create_task(app.state.processor(job))
        await entered.wait()
        await api.post(f"/jobs/{job}/cancel", headers=header)
        retried = await api.post(
            f"/documents/{document}/extract", headers=header, json={"retry": True}
        )
        assert retried.status_code == 200 and retried.json()["data"]["status"] == "queued"
        release.set()
        await old
        async with app.state.db.transaction() as session:
            assert await session.scalar(select(func.count()).select_from(Requirement)) == 0
            assert (await session.get(Job, UUID(job))).status == "queued"
        await app.state.processor(job)
        assert (await api.get(f"/jobs/{job}", headers=header)).json()["data"][
            "status"
        ] == "succeeded"
