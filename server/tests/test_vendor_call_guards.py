"""API + processor regression scenarios; all vendor traffic uses MockTransport.

Failure modes (defined before the tests): admission omitted from halves/gap-fill/retries;
concurrent jobs reuse a balance; retries reset ceilings; missing prices admit free calls;
cancellation or output parsing drops completed usage; ambiguous accounting commits charge
again; stale attempts renew a lease or keep calling; new attempt totals lose old charges;
reservations/usage cross an org boundary; unknown vendor outcomes release funds prematurely.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pymupdf
import pytest
from app.api.main import create_app
from app.jobs.execution import JobExecution
from app.models.entities import BalanceEntry, Job, OrgBalance, Requirement, UsageRecord, VendorCall
from app.providers.base import ProviderFailure
from app.providers.llm import AnthropicExtractor, OpenAICompatibleExtractor
from conftest import FakeQueue
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError, OperationalError
from test_api import create_document, run_job
from test_job_boundaries import session_for
from test_llm_providers import anthropic_reply, settings_for


def pdf_lines(count=8, parameter=False):
    lines = [f"Required synthetic line {i:03d}.".ljust(28, ".") for i in range(count)]
    if parameter:
        lines = ["Memory >= 64 GB."]
    with pymupdf.open() as pdf:
        page = pdf.new_page(height=max(842, count * 16 + 120))
        page.insert_text((40, 60), "\n".join(lines), fontsize=10)
        return pdf.tobytes()


@asynccontextmanager
async def environment(
    tenants, tmp_path, handler, *, balance="10", provider="anthropic", **settings
):
    config = settings_for(tmp_path, provider, llm_concurrency=1, **settings)
    adapter = AnthropicExtractor if provider == "anthropic" else OpenAICompatibleExtractor
    llm = adapter(
        config,
        httpx.MockTransport(handler),
        platform_model_id="guard-test",
        sale_usd_per_mtok=(1, 1),
    )
    app = create_app(config, llm=llm, queue=FakeQueue())
    org = tenants["orgs"][0]
    async with app.router.lifespan_context(app), session_for(app, tenants) as (api, header):
        async with app.state.db.transaction(org) as session:
            session.add(OrgBalance(org_id=org, currency="USD", balance=Decimal(balance)))
            session.add(
                BalanceEntry(
                    org_id=org,
                    kind="adjust",
                    currency="USD",
                    amount=Decimal(balance),
                    balance_after=Decimal(balance),
                    actor="test",
                    reason="Synthetic initial funds",
                )
            )
        yield app, api, header


async def prepare(app, api, header, content=None):
    _, document = await create_document(api, header, content or pdf_lines())
    _, parsed = await run_job(api, app, header, document, "parse")
    assert parsed["status"] == "succeeded"
    response = await api.post(f"/documents/{document}/extract", headers=header, json={})
    assert response.status_code == 200, response.text
    return document, response.json()["data"]["job_id"]


async def status(api, header, job):
    return (await api.get(f"/jobs/{job}", headers=header)).json()["data"]


async def ledger(app, org, job):
    async with app.state.db.transaction(UUID(str(org))) as session:
        calls = list(
            (await session.scalars(select(VendorCall).where(VendorCall.job_id == UUID(job)))).all()
        )
        usages = list(
            (
                await session.scalars(select(UsageRecord).where(UsageRecord.job_id == UUID(job)))
            ).all()
        )
        entries = list(
            (await session.scalars(select(BalanceEntry).where(BalanceEntry.kind == "usage"))).all()
        )
        balance = await session.scalar(select(OrgBalance.balance))
        saved = await session.scalar(
            select(func.count()).select_from(Requirement).where(Requirement.job_id == UUID(job))
        )
        return calls, usages, entries, balance, saved


def evidence(tmp_path, case, calls, usages, balance, job_status):
    # Reproducible, synthetic-only evidence; pytest --basetemp preserves it outside docs/.
    (tmp_path / f"{case}.json").write_text(
        json.dumps(
            {
                "case": case,
                "calls": [
                    {
                        "id": str(c.id),
                        "run_id": str(c.run_id),
                        "state": c.state,
                        "reserved_charge": str(c.reserved_charge),
                    }
                    for c in calls
                ],
                "usage": [
                    {"call_id": str(u.call_id), "tokens": u.tokens, "charge": str(u.charge)}
                    for u in usages
                ],
                "balance": str(balance),
                "job": job_status,
            },
            indent=2,
        )
    )


@pytest.mark.parametrize(
    "ceiling,settings",
    [
        ("job_call_limit_exceeded", {"job_max_vendor_calls": 5, "job_vendor_calls_per_batch": 1}),
        ("job_charge_limit_exceeded", {"job_max_charge": "0.07"}),
    ],
)
async def test_runaway_truncation_stops_and_retry_cannot_reset_budget(
    tenants, tmp_path, ceiling, settings
):
    requests = []

    def vendor(request):
        requests.append(request)
        payload = anthropic_reply([], stop="max_tokens").json()
        payload["usage"]["output_tokens"] = 32000
        return httpx.Response(200, json=payload)

    async with environment(tenants, tmp_path, vendor, **settings) as (app, api, header):
        document, job = await prepare(app, api, header, pdf_lines(128))
        await app.state.processor(header["X-Org-Id"], job)
        result = await status(api, header, job)
        calls, usages, entries, balance, saved = await ledger(app, header["X-Org-Id"], job)
        assert result["status"] == "failed" and result["error"]["code"] == ceiling
        assert 0 < len(requests) <= 5
        if ceiling == "job_call_limit_exceeded":
            assert len(requests) == 5
        assert len(calls) == len(usages) == len(entries) == len(requests)
        assert saved == 0 and all(c.state == "completed" for c in calls)
        assert balance == Decimal("10") - sum(Decimal(u.charge) for u in usages)
        assert result["result"]["cost"]["llm_tokens"] == sum(u.tokens for u in usages)
        before = len(requests)
        retried = await api.post(
            f"/documents/{document}/extract", headers=header, json={"retry": True}
        )
        assert retried.json()["data"]["job_id"] == job
        await app.state.processor(header["X-Org-Id"], job)
        assert len(requests) == before
        assert (await status(api, header, job))["result"]["cost"] == result["result"]["cost"]
        evidence(tmp_path, ceiling, calls, usages, balance, result)


@pytest.mark.parametrize("phase", ["gap_fill", "retry"])
async def test_gap_fill_and_transient_retries_need_new_admission(
    tenants, tmp_path, monkeypatch, phase
):
    monkeypatch.setattr(AnthropicExtractor, "retry_delays", (0, 0))
    requests = []

    def vendor(request):
        requests.append(request)
        if phase == "retry":
            return httpx.Response(
                500,
                json={
                    "error": {"type": "api_error"},
                    "usage": {"input_tokens": 1200, "output_tokens": 300},
                },
            )
        return anthropic_reply([])

    cap = 1 if phase == "gap_fill" else 2
    async with environment(
        tenants, tmp_path, vendor, job_max_vendor_calls=cap, job_vendor_calls_per_batch=1
    ) as (
        app,
        api,
        header,
    ):
        _, job = await prepare(app, api, header, pdf_lines(parameter=True))
        await app.state.processor(header["X-Org-Id"], job)
        result = await status(api, header, job)
        calls, usages, entries, balance, saved = await ledger(app, header["X-Org-Id"], job)
        assert result["error"]["code"] == "job_call_limit_exceeded"
        assert len(requests) == len(calls) == len(usages) == len(entries) == cap
        assert balance == Decimal("10") - Decimal("0.0015") * cap
        assert saved == 0
        evidence(tmp_path, phase, calls, usages, balance, result)


async def test_concurrent_jobs_share_reservations_without_overdraft(tenants, tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    requests = []

    async def vendor(request):
        requests.append(request)
        entered.set()
        await release.wait()
        return anthropic_reply([])

    async with environment(tenants, tmp_path, vendor, balance="0.06") as (app, api, header):
        _, first = await prepare(app, api, header)
        _, second = await prepare(app, api, header)
        running = asyncio.create_task(app.state.processor(header["X-Org-Id"], first))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await app.state.processor(header["X-Org-Id"], second)
            assert (await status(api, header, second))["error"]["code"] == "insufficient_balance"
            assert len(requests) == 1
            calls, usages, _, balance, _ = await ledger(app, header["X-Org-Id"], first)
            assert usages == [] and sum(c.reserved_charge for c in calls) <= balance
        finally:
            release.set()
            await running
        result = await status(api, header, first)
        calls, usages, entries, balance, _ = await ledger(app, header["X-Org-Id"], first)
        assert result["status"] == "succeeded"
        assert len(usages) == len(entries) == 1
        assert balance == Decimal("0.0585")
        evidence(tmp_path, "concurrent", calls, usages, balance, result)


@pytest.mark.parametrize("cancel_worker", [False, True])
async def test_cancel_mid_extraction_keeps_every_completed_call_once(
    tenants, tmp_path, cancel_worker
):
    entered, release = asyncio.Event(), asyncio.Event()
    requests = []

    async def vendor(request):
        requests.append(request)
        if len(requests) == 2:
            entered.set()
            await release.wait()
        return anthropic_reply([], stop="max_tokens")

    async with environment(tenants, tmp_path, vendor) as (app, api, header):
        _, job = await prepare(app, api, header)
        running = asyncio.create_task(app.state.processor(header["X-Org-Id"], job))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            # The first call must be committed while extraction is still running.
            _, usages, entries, balance, _ = await ledger(app, header["X-Org-Id"], job)
            assert len(usages) == len(entries) == 1 and balance == Decimal("9.9985")
            if cancel_worker:
                running.cancel()
                await asyncio.sleep(0)
                running.cancel()
            else:
                assert (await api.post(f"/jobs/{job}/cancel", headers=header)).status_code == 200
        finally:
            release.set()
            if cancel_worker:
                with pytest.raises(asyncio.CancelledError):
                    await running
            else:
                await running
        result = await status(api, header, job)
        calls, usages, entries, balance, saved = await ledger(app, header["X-Org-Id"], job)
        # A stopped worker leaves its lease to expire; API cancellation is terminal.
        assert result["status"] == ("running" if cancel_worker else "cancelled")
        assert len(requests) == len(usages) == len(entries) == 2
        assert len({u.call_id for u in usages}) == 2
        assert balance == Decimal("9.997") and saved == 0
        assert result["result"]["cost"]["llm_tokens"] == 3000
        evidence(tmp_path, "cancel", calls, usages, balance, result)


@pytest.mark.parametrize("fault", ["before_commit", "after_commit"])
async def test_ambiguous_accounting_commit_retries_without_double_charge(
    tenants, tmp_path, monkeypatch, fault
):
    original = JobExecution._complete_once
    writes = []

    async def uncertain(self, call_id, usage):
        writes.append(call_id)
        exceeded = await original(self, call_id, usage)
        if fault == "after_commit" and len(writes) == 1:
            raise OperationalError(
                None,
                None,
                ConnectionError("synthetic commit acknowledgement lost"),
                connection_invalidated=True,
            )
        return exceeded

    monkeypatch.setattr(JobExecution, "_complete_once", uncertain)
    if fault == "before_commit":
        from app.services import billing

        charge = billing.charge_usage
        failed = False

        async def interrupted_charge(*args):
            nonlocal failed
            await charge(*args)
            if not failed:
                failed = True
                raise OperationalError(
                    None,
                    None,
                    ConnectionError("synthetic transaction interrupted"),
                    connection_invalidated=True,
                )

        monkeypatch.setattr(billing, "charge_usage", interrupted_charge)
    async with environment(tenants, tmp_path, lambda request: anthropic_reply([])) as (
        app,
        api,
        header,
    ):
        _, job = await prepare(app, api, header)
        await app.state.processor(header["X-Org-Id"], job)
        result = await status(api, header, job)
        calls, usages, entries, balance, _ = await ledger(app, header["X-Org-Id"], job)
        assert result["status"] == "succeeded"
        assert len(writes) == 2 and writes[0] == writes[1]
        assert len(calls) == len(usages) == len(entries) == 1
        assert balance == Decimal("9.9985")
        assert result["result"]["cost"]["llm_tokens"] == 1500
        evidence(tmp_path, "accounting_retry", calls, usages, balance, result)


async def test_lease_is_renewed_during_a_long_vendor_call(tenants, tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    requests = []

    async def vendor(request):
        requests.append(request)
        entered.set()
        await release.wait()
        return anthropic_reply([])

    async with environment(
        tenants, tmp_path, vendor, job_lease_seconds=3, job_heartbeat_seconds=0.2
    ) as (app, api, header):
        document, job = await prepare(app, api, header)
        running = asyncio.create_task(app.state.processor(header["X-Org-Id"], job))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            async with app.state.db.transaction(tenants["orgs"][0]) as session:
                original = await session.get(Job, UUID(job))
            await asyncio.sleep(3.3)
            retried = await api.post(
                f"/documents/{document}/extract", headers=header, json={"retry": True}
            )
            assert retried.json()["data"]["status"] == "running"
            await app.state.processor(header["X-Org-Id"], job)
            async with app.state.db.transaction(tenants["orgs"][0]) as session:
                current = await session.get(Job, UUID(job))
            assert current.run_id == original.run_id and current.lease_until > original.lease_until
            assert current.attempts == 1 and len(requests) == 1
        finally:
            release.set()
            await running
        assert (await status(api, header, job))["status"] == "succeeded"


async def test_retry_takeover_fences_old_attempt_but_keeps_late_usage(tenants, tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    requests = []

    async def vendor(request):
        requests.append(request)
        if len(requests) == 1:
            entered.set()
            await release.wait()
            return anthropic_reply([], stop="max_tokens")
        return anthropic_reply(
            [
                {
                    "category": "technical",
                    "starred": False,
                    "text": "Required synthetic line 000.",
                    "ref": "1",
                    "quote": "Required synthetic line 000.",
                    "condition": None,
                }
            ]
        )

    async with environment(tenants, tmp_path, vendor) as (app, api, header):
        document, job = await prepare(app, api, header)
        old = asyncio.create_task(app.state.processor(header["X-Org-Id"], job))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            async with app.state.db.transaction(tenants["orgs"][0]) as session:
                previous = await session.get(Job, UUID(job))
                previous.lease_until = datetime.now(UTC) - timedelta(seconds=1)
                old_run = previous.run_id
            retried = await api.post(
                f"/documents/{document}/extract", headers=header, json={"retry": True}
            )
            assert retried.json()["data"]["status"] == "queued"
            await app.state.processor(header["X-Org-Id"], job)
            assert (await status(api, header, job))["status"] == "succeeded"
            old_execution = JobExecution(
                app.state.processor.settings, app.state.db, tenants["orgs"][0], UUID(job), old_run
            )
            with pytest.raises(ProviderFailure, match="superseded"):
                await old_execution.heartbeat()
        finally:
            release.set()
            await old
        result = await status(api, header, job)
        calls, usages, entries, balance, saved = await ledger(app, header["X-Org-Id"], job)
        assert result["status"] == "succeeded" and len(requests) == 2
        assert len(usages) == len(entries) == 2 and len({u.run_id for u in usages}) == 2
        assert balance == Decimal("9.997") and saved == 1
        assert result["result"]["cost"]["llm_tokens"] == 3000
        evidence(tmp_path, "takeover", calls, usages, balance, result)


async def test_unknown_outcome_keeps_its_reservation_across_retry(tenants, tmp_path):
    requests = []

    def vendor(request):
        requests.append(request)
        raise httpx.ReadTimeout("synthetic unknown vendor outcome")

    async with environment(tenants, tmp_path, vendor, balance="0.06") as (app, api, header):
        _, job = await prepare(app, api, header)
        with pytest.raises(ProviderFailure):
            await app.state.processor(header["X-Org-Id"], job)
        await app.state.processor(header["X-Org-Id"], job)
        result = await status(api, header, job)
        calls, usages, entries, balance, saved = await ledger(app, header["X-Org-Id"], job)
        assert result["error"]["code"] == "insufficient_balance"
        assert len(requests) == len(calls) == 1 and calls[0].state == "unknown"
        assert usages == entries == [] and balance == Decimal("0.06") and saved == 0
        evidence(tmp_path, "unknown_outcome", calls, usages, balance, result)


async def test_new_call_ledger_and_usage_remain_tenant_isolated(tenants, tmp_path):
    async with environment(tenants, tmp_path, lambda request: anthropic_reply([])) as (
        app,
        api,
        header,
    ):
        _, job = await prepare(app, api, header)
        await app.state.processor(header["X-Org-Id"], job)
        calls, usages, _, _, _ = await ledger(app, header["X-Org-Id"], job)
        [call] = calls
        async with app.state.db.transaction(tenants["orgs"][1]) as session:
            assert list((await session.scalars(select(VendorCall))).all()) == []
            assert list((await session.scalars(select(UsageRecord))).all()) == []
            assert (
                await session.scalar(
                    update(VendorCall)
                    .where(VendorCall.id == call.id)
                    .values(state="unknown")
                    .returning(VendorCall.id)
                )
                is None
            )
        with pytest.raises(DBAPIError):
            async with app.state.db.transaction(tenants["orgs"][1]) as session:
                session.add(
                    VendorCall(
                        id=uuid4(),
                        org_id=tenants["orgs"][0],
                        job_id=UUID(job),
                        run_id=call.run_id,
                        reserved_charge=0,
                    )
                )
        with pytest.raises(DBAPIError):
            async with app.state.db.transaction(tenants["orgs"][1]) as session:
                session.add(
                    VendorCall(
                        id=uuid4(),
                        org_id=tenants["orgs"][1],
                        job_id=UUID(job),
                        run_id=call.run_id,
                        reserved_charge=0,
                    )
                )
        assert usages[0].call_id == call.id


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
async def test_refusal_is_accounted_before_output_validation(tenants, tmp_path, provider):
    def vendor(request):
        if provider == "anthropic":
            return anthropic_reply([], stop="refusal")
        return httpx.Response(
            200,
            json={
                "model": "synthetic-model",
                "usage": {"prompt_tokens": 1200, "completion_tokens": 300},
                "choices": [{"message": {"refusal": "declined"}, "finish_reason": "stop"}],
            },
        )

    async with environment(tenants, tmp_path, vendor, provider=provider) as (app, api, header):
        _, job = await prepare(app, api, header)
        await app.state.processor(header["X-Org-Id"], job)
        result = await status(api, header, job)
        _, usages, entries, balance, _ = await ledger(app, header["X-Org-Id"], job)
        assert result["error"]["code"] == "provider_refused"
        assert len(usages) == len(entries) == 1 and balance == Decimal("9.9985")


@pytest.mark.parametrize(
    "case,code",
    [
        ("price", "billing_price_unavailable"),
        ("usage", "invalid_provider_usage"),
        ("bound", "call_charge_bound_exceeded"),
    ],
)
async def test_unknown_price_usage_and_vendor_bound_violation_fail_explicitly(
    tenants, tmp_path, case, code
):
    requests = []

    def vendor(request):
        requests.append(request)
        payload = anthropic_reply([], stop="max_tokens").json()
        if case == "usage":
            del payload["usage"]
        if case == "bound":
            payload["usage"]["output_tokens"] = 1_000_000
        return httpx.Response(200, json=payload)

    async with environment(tenants, tmp_path, vendor) as (app, api, header):
        if case == "price":
            app.state.processor.llm.sale = None
        _, job = await prepare(app, api, header)
        await app.state.processor(header["X-Org-Id"], job)
        result = await status(api, header, job)
        calls, usages, entries, balance, saved = await ledger(app, header["X-Org-Id"], job)
        assert result["error"]["code"] == code and saved == 0
        assert len(requests) == (0 if case == "price" else 1)
        if case == "bound":
            assert len(usages) == len(entries) == 1
            assert usages[0].tokens == 1_001_200 and balance == Decimal("8.9988")
            assert calls[0].state == "completed"
        else:
            assert usages == entries == [] and balance == Decimal("10")
        if case == "usage":
            assert calls[0].state == "unknown" and calls[0].reserved_charge > 0
        evidence(tmp_path, case, calls, usages, balance, result)


async def test_call_ceiling_grows_with_the_planned_first_pass(tenants, tmp_path):
    # Three pages of about 1,000 characters each become three first-pass batches.
    with pymupdf.open() as pdf:
        for number in range(3):
            page = pdf.new_page()
            page.insert_textbox(pdf[-1].rect + (40, 40, -40, -40), f"Page {number} line. " * 55)
        content = pdf.tobytes()
    requests = []

    def vendor(request):
        requests.append(request)
        return anthropic_reply([])

    # The fixed ceiling alone (2) would stop the first pass; one call per planned batch fits.
    async with environment(
        tenants,
        tmp_path,
        vendor,
        job_max_vendor_calls=2,
        job_vendor_calls_per_batch=1,
        llm_batch_chars=1000,
    ) as (app, api, header):
        _, job = await prepare(app, api, header, content)
        await app.state.processor(header["X-Org-Id"], job)
        result = await status(api, header, job)
    assert result["status"] == "succeeded", result
    assert len(requests) == 3
