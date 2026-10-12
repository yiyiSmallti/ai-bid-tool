"""Synthetic API/worker call admission and attempt fencing regression scenarios.

Unknown outcomes survive cancellation, expiry and retry, and every retry is
admitted separately; revoked submitters
cannot dispatch; late usage is idempotent and cannot mutate a newer attempt.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from app.jobs.execution import JobExecution
from app.models.entities import Job, Membership, UsageRecord, VendorCall
from app.providers.base import ProviderFailure
from sqlalchemy import func, select, update
from test_api import create_document, run_job
from test_llm_providers import anthropic_reply
from test_vendor_call_guards import environment, ledger, pdf_lines


async def extract(api, header, document, **body):
    response = await api.post(f"/documents/{document}/extract", headers=header, json=body)
    assert response.status_code == 200, response.text
    return response.json()["data"]["job_id"]


@pytest.mark.parametrize("transition", ["cancel", "expire", "retry"])
async def test_unknown_admission_survives_attempt_transitions(tenants, tmp_path, transition):
    sent = []

    def vendor(request):
        sent.append(request)
        raise httpx.ReadTimeout("Synthetic unknown outcome")

    async with environment(tenants, tmp_path, vendor) as (app, api, header):
        task, document = await create_document(api, header, pdf_lines())
        await run_job(api, app, header, document, "parse")
        job_id = await extract(api, header, document)
        with pytest.raises(ProviderFailure):
            await app.state.processor(header["X-Org-Id"], job_id)
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            before = (
                await session.execute(
                    select(VendorCall.id, VendorCall.state).where(VendorCall.job_id == UUID(job_id))
                )
            ).all()
            job = await session.get(Job, UUID(job_id))
            assert job is not None
            if transition == "expire":
                job.status = "running"
                job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
            elif transition == "retry":
                job.status = "failed"
        assert before and {state for _, state in before} == {"unknown"}
        if transition == "cancel":
            await api.post(f"/jobs/{job_id}/cancel", headers=header)
        if transition in {"cancel", "retry"}:
            await extract(api, header, document, retry=True)
        # Inference may retry an unknown response; the retry is admitted separately.
        with pytest.raises(ProviderFailure):
            await app.state.processor(header["X-Org-Id"], job_id)
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            after = dict(
                (
                    await session.execute(
                        select(VendorCall.id, VendorCall.state).where(
                            VendorCall.job_id == UUID(job_id)
                        )
                    )
                ).all()
            )
            assert all(after[call] == "unknown" for call, _ in before)
            assert len(after) == len(sent) > len(before)
            assert (
                await session.scalar(
                    select(func.count(UsageRecord.id)).where(UsageRecord.job_id == UUID(job_id))
                )
                == 0
            )


async def test_revoked_submitter_cannot_dispatch_from_saved_grants(tenants, tmp_path, admin_engine):
    sent = []

    def vendor(request):
        sent.append(request)
        return anthropic_reply([])

    async with environment(tenants, tmp_path, vendor) as (app, api, header):
        _, document = await create_document(api, header, pdf_lines())
        await run_job(api, app, header, document, "parse")
        job_id = await extract(api, header, document)
        # Revoke outside the runtime role: bid_app may only change members through the
        # admin CAS path, which is not what this test exercises.
        with admin_engine.begin() as connection:
            connection.execute(
                update(Membership)
                .where(Membership.org_id == tenants["orgs"][0])
                .values(active=False)
            )
        await app.state.processor(header["X-Org-Id"], job_id)
        assert not sent


async def test_duplicate_late_settlement_preserves_new_attempt_state(tenants, tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()

    async def vendor(request):
        entered.set()
        await release.wait()
        return anthropic_reply([])

    async with environment(tenants, tmp_path, vendor) as (app, api, header):
        task, document = await create_document(api, header, pdf_lines())
        await run_job(api, app, header, document, "parse")
        job_id = await extract(api, header, document)
        running = asyncio.create_task(app.state.processor(header["X-Org-Id"], job_id))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await api.post(f"/jobs/{job_id}/cancel", headers=header)
            await extract(api, header, document, retry=True)
        finally:
            release.set()
            await running
        calls, usages, saved = await ledger(app, header["X-Org-Id"], job_id)
        assert len(calls) == len(usages) == 1 and saved == 0
        from app.schemas.contracts import ProviderUsage

        usage = ProviderUsage.model_validate(usages[0])
        execution = JobExecution(
            app.state.processor.settings,
            app.state.db,
            tenants["orgs"][0],
            UUID(job_id),
            calls[0].run_id,
        )
        await execution.complete(calls[0].id, usage)
        again = await ledger(app, header["X-Org-Id"], job_id)
        assert len(again[1]) == 1 and again[2] == saved
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            job = await session.get(Job, UUID(job_id))
            assert job is not None and job.status == "queued"
