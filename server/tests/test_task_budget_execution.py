"""API/worker budget scenarios with synthetic HTTP replies only.

Failure modes: sibling jobs spend the same task reservation; a failed atomic job
publishes rows; cancelled/expired/retried attempts release unknown holds; late
settlement double charges; revocation permits more calls; dry-run changes ledgers.
Run with --basetemp=data/work/budget-validation to retain reproducible artifacts.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
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


async def set_limit(api, header, task, limit):
    current = await api.get(f"/v4/tasks/{task}/budget", headers=header)
    assert current.status_code == 200, current.text
    response = await api.put(
        f"/v4/tasks/{task}/budget",
        headers=header,
        json={
            "limit": limit,
            "currency": "USD",
            "expected_revision": current.json()["data"]["budget"]["revision"],
            "reason": "Synthetic budget scenario",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["budget"]


async def extract(api, header, document, **body):
    response = await api.post(f"/documents/{document}/extract", headers=header, json=body)
    assert response.status_code == 200, response.text
    return response.json()["data"]["job_id"]


async def test_zero_budget_blocks_dispatch_and_keeps_atomic_output_empty(tenants, tmp_path):
    sent = []

    def vendor(request):
        sent.append(request)
        return anthropic_reply([])

    async with environment(tenants, tmp_path, vendor) as (app, api, header):
        task, document = await create_document(api, header, pdf_lines())
        await set_limit(api, header, task, "0")
        await run_job(api, app, header, document, "parse")
        job_id = await extract(api, header, document)
        await app.state.processor(header["X-Org-Id"], job_id)
        response = (await api.get(f"/v4/jobs/{job_id}", headers=header)).json()
        calls, usages, entries, balance, saved = await ledger(app, header["X-Org-Id"], job_id)
        assert not sent and not calls and not usages and not entries and not saved
        assert balance == Decimal("10")
        assert response["ok"] is False
        assert response["data"]["error"]["exit_code"] == 4
        budget = response["data"]["result"]["budget"]
        assert budget["stop_reason"] == "task_budget_exceeded"
        assert budget["intervention"]["auto_retry"] is False
        assert Decimal(budget["intervention"]["minimum_new_limit"]) > 0
        token = (
            await api.post(
                "/tokens",
                headers=header,
                json={
                    "name": "Job status without task access",
                    "scopes": ["job:read"],
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                },
            )
        ).json()["data"]["token"]
        limited = {**header, "Authorization": "Bearer " + token}
        assert (await api.get(f"/v4/jobs/{job_id}", headers=limited)).status_code == 403
        (tmp_path / "zero-budget.json").write_text(json.dumps(response, indent=2))


async def test_two_jobs_on_one_task_cannot_reuse_reserved_budget(tenants, tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    sent = []

    async def vendor(request):
        sent.append(request)
        entered.set()
        await release.wait()
        return anthropic_reply([])

    async with environment(tenants, tmp_path, vendor) as (app, api, header):
        task, first_doc = await create_document(api, header, pdf_lines())
        upload = await api.post(
            f"/tasks/{task}/documents",
            headers=header,
            files={"file": ("second.pdf", pdf_lines(9))},
        )
        second_doc = upload.json()["data"]["id"]
        for document in (first_doc, second_doc):
            await run_job(api, app, header, document, "parse")
        await set_limit(api, header, task, "0.06")
        first, second = [await extract(api, header, doc) for doc in (first_doc, second_doc)]
        running = asyncio.create_task(app.state.processor(header["X-Org-Id"], first))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await app.state.processor(header["X-Org-Id"], second)
            result = (await api.get(f"/v4/jobs/{second}", headers=header)).json()
            assert result["data"]["error"]["code"] == "task_budget_exceeded"
            assert len(sent) == 1
            view = (await api.get(f"/v4/tasks/{task}/budget", headers=header)).json()["data"][
                "budget"
            ]
            assert Decimal(view["reserved"]) > 0 and view["unresolved_calls"] == 1
        finally:
            release.set()
            await running
        view = (await api.get(f"/v4/tasks/{task}/budget", headers=header)).json()["data"]["budget"]
        assert Decimal(view["reserved"]) == 0 and Decimal(view["spent"]) == Decimal("0.0015")
        (tmp_path / "concurrent-task-budget.json").write_text(json.dumps(view, indent=2))


@pytest.mark.parametrize("transition", ["cancel", "expire", "retry"])
async def test_unknown_hold_survives_attempt_transitions(tenants, tmp_path, transition):
    sent = []

    def vendor(request):
        sent.append(request)
        raise httpx.ReadTimeout("Synthetic unknown outcome")

    async with environment(tenants, tmp_path, vendor) as (app, api, header):
        task, document = await create_document(api, header, pdf_lines())
        await set_limit(api, header, task, "0.06")
        await run_job(api, app, header, document, "parse")
        job_id = await extract(api, header, document)
        try:
            await app.state.processor(header["X-Org-Id"], job_id)
        except ProviderFailure:
            pass
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            before = await session.scalar(
                select(func.sum(VendorCall.reserved_task_amount)).where(
                    VendorCall.job_id == UUID(job_id)
                )
            )
            job = await session.get(Job, UUID(job_id))
            assert job is not None
            if transition == "expire":
                job.status = "running"
                job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
            elif transition == "retry":
                job.status = "failed"
        if transition == "cancel":
            await api.post(f"/jobs/{job_id}/cancel", headers=header)
        if transition in {"cancel", "retry"}:
            await extract(api, header, document, retry=True)
        await app.state.processor(header["X-Org-Id"], job_id)
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            after = await session.scalar(
                select(func.sum(VendorCall.reserved_task_amount)).where(
                    VendorCall.job_id == UUID(job_id)
                )
            )
            assert after == before and before > 0
            assert (
                await session.scalar(
                    select(func.count(UsageRecord.id)).where(UsageRecord.job_id == UUID(job_id))
                )
                == 0
            )
        assert len(sent) == 1


async def test_revoked_submitter_cannot_dispatch_from_saved_grants(tenants, tmp_path):
    sent = []

    def vendor(request):
        sent.append(request)
        return anthropic_reply([])

    async with environment(tenants, tmp_path, vendor) as (app, api, header):
        _, document = await create_document(api, header, pdf_lines())
        await run_job(api, app, header, document, "parse")
        job_id = await extract(api, header, document)
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            await session.execute(update(Membership).values(active=False))
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
        await set_limit(api, header, task, "1")
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
        calls, usages, entries, balance, saved = await ledger(app, header["X-Org-Id"], job_id)
        assert len(calls) == len(usages) == len(entries) == 1 and saved == 0
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
        assert len(again[1]) == len(again[2]) == 1 and again[3] == balance
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            job = await session.get(Job, UUID(job_id))
            assert job is not None and job.status == "queued"


async def test_missing_historical_search_cost_stays_unknown_without_unknown_task_liability(
    api, application, headers, tenants, pdf_bytes
):
    from uuid import uuid4

    from app.services.auth import ROLE_SCOPES, Identity, set_actor_context
    from sqlalchemy.exc import DBAPIError

    task, document = await create_document(api, headers[0], pdf_bytes)
    org, user = tenants["orgs"][0], tenants["users"][0]
    async with application.state.db.transaction(org) as session:
        await set_actor_context(session, Identity(user, org, ROLE_SCOPES["admin"], "admin"))
        job = Job(
            org_id=org,
            task_id=UUID(task),
            document_id=UUID(document),
            kind="product_simulation",
            cache_key=uuid4().hex,
            status="succeeded",
            run_id=uuid4(),
            result={},
            vendor_cost_history_complete=False,
        )
        session.add(job)
        await session.flush()
        job_id = job.id
    result = (await api.get(f"/v4/jobs/{job_id}", headers=headers[0])).json()
    assert result["cost"]["usd"] is None
    assert result["cost"]["unpriced_calls"] == 0
    assert Decimal(result["cost"]["task_amount"]) == 0
    assert "historical_vendor_cost_unavailable" in result["warnings"]
    budget = (await api.get(f"/v4/tasks/{task}/budget", headers=headers[0])).json()["data"][
        "budget"
    ]
    assert budget["history_complete"] is True and budget["unpriced_calls"] == 0
    with pytest.raises(DBAPIError):
        async with application.state.db.transaction(org) as session:
            await session.execute(
                update(Job).where(Job.id == job_id).values(vendor_cost_history_complete=True)
            )
