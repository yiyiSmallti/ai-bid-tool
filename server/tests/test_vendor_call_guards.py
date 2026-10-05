"""API + processor regression scenarios; all vendor traffic uses MockTransport.

Failure modes (defined before the tests): admission omitted from halves/gap-fill/retries;
concurrent jobs reuse a balance; retries reset ceilings; missing prices admit free calls;
cancellation or output parsing drops completed usage; ambiguous accounting commits charge
again; stale attempts renew a lease or keep calling; new attempt totals lose old charges;
reservations/usage cross an org boundary; unknown vendor outcomes release funds prematurely.
Output-limit aliases must fail before admission, and the largest transmitted limit must
be reserved. Pool timeouts, permanent/exhausted DB errors and missing accounting parents
must stop queued batches even if marking the outcome unknown also fails; sent calls settle.
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
from app.jobs.execution import JobExecution
from app.models.entities import BalanceEntry, Job, OrgBalance, Requirement, UsageRecord, VendorCall
from app.providers.base import ProviderFailure
from app.providers.llm import AnthropicExtractor, OpenAICompatibleExtractor
from conftest import FakeQueue, credential_app
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
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
    config = settings_for(tmp_path, provider, **({"llm_concurrency": 1} | settings))
    adapter = AnthropicExtractor if provider == "anthropic" else OpenAICompatibleExtractor
    llm = adapter(
        config,
        httpx.MockTransport(handler),
        platform_model_id="guard-test",
        sale_usd_per_mtok=(1, 1),
    )
    app = await credential_app(config, llm=llm, queue=FakeQueue())
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


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
@pytest.mark.parametrize(
    "options",
    [
        {"max_completion_tokens": 1},
        {"max_tokens": 64000},
        {"max_output_tokens": 1},
        {"max_new_tokens": 1},
        {"maxOutputTokens": 1},
        {"generation_config": {"max_output_tokens": 1}},
    ],
)
async def test_conflicting_output_limits_never_reach_extraction_vendor(
    tenants, tmp_path, provider, options
):
    requests = []

    def vendor(request):
        requests.append(request)
        raise AssertionError("Conflicting limits must fail before dispatch")

    async with environment(
        tenants, tmp_path, vendor, provider=provider, llm_request_options=json.dumps(options)
    ) as (app, api, header):
        _, job = await prepare(app, api, header)
        await app.state.processor(header["X-Org-Id"], job)
        result = await status(api, header, job)
        calls, usages, entries, balance, saved = await ledger(app, header["X-Org-Id"], job)
        assert result["status"] == "failed"
        assert result["error"]["code"] == "invalid_provider_options"
        assert requests == calls == usages == entries == []
        assert saved == 0 and balance == Decimal("10")
        evidence(tmp_path, "conflicting_limits", calls, usages, balance, result)


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
async def test_reservation_uses_largest_transmitted_output_limit(
    tenants, tmp_path, monkeypatch, provider
):
    # Inject at the post boundary to exercise the accounting defence separately
    # from request-options validation. A vendor may ignore the smaller alias.
    adapter = AnthropicExtractor if provider == "anthropic" else OpenAICompatibleExtractor
    original = adapter.post
    bodies = []

    async def with_two_limits(self, client, url, headers, body, **kwargs):
        return await original(
            self, client, url, headers, {**body, "max_completion_tokens": 1}, **kwargs
        )

    monkeypatch.setattr(adapter, "post", with_two_limits)

    def vendor(request):
        body = json.loads(request.content)
        bodies.append(body)
        if provider == "anthropic":
            payload = anthropic_reply([]).json()
            payload["usage"]["output_tokens"] = body["max_tokens"]
            return httpx.Response(200, json=payload)
        return httpx.Response(
            200,
            json={
                "model": "synthetic-model",
                "usage": {"prompt_tokens": 1200, "completion_tokens": body["max_tokens"]},
                "choices": [{"message": {"content": '{"items":[]}'}, "finish_reason": "stop"}],
            },
        )

    async with environment(tenants, tmp_path, vendor, provider=provider) as (app, api, header):
        _, job = await prepare(app, api, header)
        await app.state.processor(header["X-Org-Id"], job)
        result = await status(api, header, job)
        calls, usages, entries, balance, _ = await ledger(app, header["X-Org-Id"], job)
        assert result["status"] == "succeeded", result
        assert len(calls) == len(usages) == len(entries) == len(bodies) == 1
        assert calls[0].reserved_charge >= Decimal("0.032")
        assert calls[0].reserved_charge >= usages[0].charge
        assert bodies[0]["max_completion_tokens"] == 1 and bodies[0]["max_tokens"] == 32000
        assert balance == Decimal("9.9668")
        evidence(tmp_path, "largest_limit", calls, usages, balance, result)


@pytest.mark.parametrize("unknown_fails", [False, True])
@pytest.mark.parametrize("fault", ["pool_timeout", "permanent", "exhausted", "missing_parent"])
async def test_settlement_failure_stops_waiting_batches_and_drains_sent_calls(
    tenants, tmp_path, monkeypatch, unknown_fails, fault
):
    sent_two, unknown_finished = asyncio.Event(), asyncio.Event()
    bodies, writes, stopped = [], [], []
    original_complete = JobExecution._complete_once
    original_unknown = JobExecution.unknown
    first_call = None
    fail_unknown = False

    async def vendor(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 2:
            sent_two.set()
            await asyncio.wait_for(unknown_finished.wait(), 5)
        return anthropic_reply([])

    async def fail_first_settlement(self, call_id, usage):
        nonlocal first_call, fail_unknown
        writes.append(call_id)
        if first_call is None:
            first_call = call_id
        if call_id == first_call:
            await asyncio.wait_for(sent_two.wait(), 5)
            fail_unknown = unknown_fails
            if fault == "pool_timeout":
                raise PoolTimeoutError("synthetic pool exhausted")
            if fault == "missing_parent":
                raise ProviderFailure("Accounting job is missing", code="usage_accounting_failed")
            raise OperationalError(
                None,
                None,
                ConnectionError("synthetic settlement failure"),
                connection_invalidated=fault == "exhausted",
            )
        return await original_complete(self, call_id, usage)

    async def observe_unknown(self, call_id):
        try:
            return await original_unknown(self, call_id)
        finally:
            stopped.append(self.stopped.code if self.stopped else None)
            unknown_finished.set()

    monkeypatch.setattr(JobExecution, "_complete_once", fail_first_settlement)
    monkeypatch.setattr(JobExecution, "unknown", observe_unknown)
    with pymupdf.open() as pdf:
        for page_number in range(5):
            page = pdf.new_page()
            lines = [f"Synthetic page {page_number}, unique obligation {i:02d}." for i in range(30)]
            assert page.insert_textbox(page.rect + (40, 40, -40, -40), "\n".join(lines)) >= 0
        content = pdf.tobytes()
    async with environment(tenants, tmp_path, vendor, llm_concurrency=2, llm_batch_chars=1000) as (
        app,
        api,
        header,
    ):
        transaction = app.state.db.transaction

        @asynccontextmanager
        async def interrupted_transaction(*args, **kwargs):
            nonlocal fail_unknown
            if fail_unknown:
                fail_unknown = False
                raise PoolTimeoutError("synthetic unknown-state pool exhausted")
            async with transaction(*args, **kwargs) as session:
                yield session

        monkeypatch.setattr(app.state.db, "transaction", interrupted_transaction)
        _, job = await prepare(app, api, header, content)
        await asyncio.wait_for(app.state.processor(header["X-Org-Id"], job), 15)
        result = await status(api, header, job)
        calls, usages, entries, balance, saved = await ledger(app, header["X-Org-Id"], job)
        assert result["status"] == "failed" and result["error"]["code"] == "usage_accounting_failed"
        assert stopped == ["usage_accounting_failed"]
        assert len(bodies) == len(calls) == 2  # three further first-pass batches never sent
        assert writes.count(first_call) == (3 if fault == "exhausted" else 1)
        assert len(usages) == len(entries) == 1 and usages[0].call_id != first_call
        assert {call.state for call in calls} == {
            "completed",
            "pending" if unknown_fails else "unknown",
        }
        assert balance == Decimal("9.9985") and saved == 0
        assert result["result"]["cost"]["llm_tokens"] == 1500
        evidence(tmp_path, "settlement_stop", calls, usages, balance, result)


async def test_proven_unsent_credential_failure_releases_hold_without_delete_or_call_count(
    tenants,
    tmp_path,
):
    """Run the credential preparation failure against bid_app's real UPDATE-only ledger."""
    from app.core.errors import ServiceError
    from app.schemas.platform_credentials import CatalogResolveTarget, ResolvedCredential
    from pydantic import SecretStr

    requests = []

    def vendor(request):
        requests.append(request)
        return anthropic_reply([])

    class Resolver:
        unavailable = True

        async def resolve_for_call(self, target):
            if self.unavailable:
                raise ServiceError("credential_disabled", "Credential is disabled", 409, 4)
            return ResolvedCredential(
                uuid4(),
                1,
                1,
                "anthropic",
                "https://api.anthropic.com",
                SecretStr("synthetic-unsent-credential-key"),
            )

    async with environment(
        tenants, tmp_path, vendor, job_max_vendor_calls=1, job_vendor_calls_per_batch=1
    ) as (app, api, header):
        _, job_id = await prepare(app, api, header, pdf_lines(1))
        run_id = uuid4()
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            job = await session.get(Job, UUID(job_id))
            job.status, job.run_id = "running", run_id
            job.lease_until = datetime.now(UTC) + timedelta(minutes=5)
        execution = JobExecution(
            app.state.processor.settings, app.state.db, tenants["orgs"][0], UUID(job_id), run_id
        )
        resolver = Resolver()
        llm = app.state.processor.llm
        llm.credential_resolver = resolver
        llm.credential_target = CatalogResolveTarget(
            model_id="guard-test", expected_model_revision=1
        )
        async with execution.activate(), httpx.AsyncClient(transport=llm.transport) as client:
            with pytest.raises(ServiceError) as caught:
                await llm.post(
                    client,
                    "https://api.anthropic.com/v1/messages",
                    {},
                    {"max_tokens": 1024},
                    reserved_charge=Decimal("0.02"),
                )
            assert caught.value.code == "provider_unavailable"
            calls, usages, entries, balance, _ = await ledger(app, header["X-Org-Id"], job_id)
            assert len(calls) == 1 and calls[0].state == "not_sent"
            assert calls[0].reserved_charge == 0 and calls[0].charge is None
            assert not requests and not usages and not entries and balance == Decimal("10")
            # Repeating the cleanup is idempotent; a later valid call still fits ceiling=1.
            await execution.not_sent(calls[0].id)
            resolver.unavailable = False
            await llm.post(
                client,
                "https://api.anthropic.com/v1/messages",
                {},
                {"max_tokens": 1024},
                reserved_charge=Decimal("0.02"),
            )
        calls, usages, entries, balance, _ = await ledger(app, header["X-Org-Id"], job_id)
        assert len(requests) == len(usages) == len(entries) == 1
        assert sorted(call.state for call in calls) == ["completed", "not_sent"]
        assert balance == Decimal("10") - Decimal(usages[0].charge)
        evidence(
            tmp_path,
            "unsent-credential-release",
            calls,
            usages,
            balance,
            await status(api, header, job_id),
        )
