"""API and processor regression scenarios with fake vendor transport.

Admission must precede every call; retries preserve ceilings; cancellation and
parse failures retain completed usage; ambiguous writes stay idempotent; stale
attempts cannot publish or dispatch; unknown outcomes block duplicate effects;
calls and usage retain their job relationships. No external vendor is contacted.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pymupdf
import pytest
from app.jobs.execution import JobExecution
from app.models.entities import Job, Requirement, UsageRecord, VendorCall
from app.providers.base import ProviderFailure
from app.providers.llm import AnthropicExtractor, OpenAICompatibleExtractor
from conftest import FakeQueue, credential_app
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
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
async def environment(database, tmp_path, handler, *, provider="anthropic", **settings):
    config = settings_for(tmp_path, provider, **{"llm_concurrency": 1} | settings)
    adapter = AnthropicExtractor if provider == "anthropic" else OpenAICompatibleExtractor
    llm = adapter(config, httpx.MockTransport(handler))
    app = await credential_app(config, llm=llm, queue=FakeQueue())
    async with app.router.lifespan_context(app), session_for(app, database) as (api, header):
        yield (app, api, header)


async def prepare(app, api, header, content=None):
    _, document = await create_document(api, header, content or pdf_lines())
    _, parsed = await run_job(api, app, header, document, "parse")
    assert parsed["status"] == "succeeded"
    response = await api.post(f"/documents/{document}/extract", headers=header, json={})
    assert response.status_code == 200, response.text
    return document, response.json()["data"]["job_id"]


async def status(api, header, job):
    return (await api.get(f"/jobs/{job}", headers=header)).json()["data"]


async def ledger(app, job):
    async with app.state.db.transaction() as session:
        calls = list(
            (await session.scalars(select(VendorCall).where(VendorCall.job_id == UUID(job)))).all()
        )
        usages = list(
            (
                await session.scalars(select(UsageRecord).where(UsageRecord.job_id == UUID(job)))
            ).all()
        )
        saved = await session.scalar(
            select(func.count()).select_from(Requirement).where(Requirement.job_id == UUID(job))
        )
        return (calls, usages, saved)


def evidence(tmp_path, case, calls, usages, job_status):
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
                    }
                    for c in calls
                ],
                "usage": [{"call_id": str(u.call_id), "tokens": u.tokens} for u in usages],
                "job": job_status,
            },
            indent=2,
        )
    )


@pytest.mark.parametrize(
    "ceiling,settings",
    [("job_call_limit_exceeded", {"job_max_vendor_calls": 5, "job_vendor_calls_per_batch": 1})],
)
async def test_runaway_truncation_stops_and_retry_cannot_reset_ceiling(
    database, tmp_path, ceiling, settings
):
    requests = []

    def vendor(request):
        requests.append(request)
        payload = anthropic_reply([], stop="max_tokens").json()
        payload["usage"]["output_tokens"] = 32000
        return httpx.Response(200, json=payload)

    async with environment(database, tmp_path, vendor, **settings) as (app, api, header):
        document, job = await prepare(app, api, header, pdf_lines(128))
        await app.state.processor(job)
        result = await status(api, header, job)
        calls, usages, saved = await ledger(app, job)
        assert result["status"] == "failed" and result["error"]["code"] == ceiling
        assert 0 < len(requests) <= 5
        if ceiling == "job_call_limit_exceeded":
            assert len(requests) == 5
        assert len(calls) == len(usages) == len(requests)
        assert saved == 0 and all(c.state == "completed" for c in calls)
        assert result["result"]["cost"]["llm_tokens"] == sum(u.tokens for u in usages)
        before = len(requests)
        retried = await api.post(
            f"/documents/{document}/extract", headers=header, json={"retry": True}
        )
        assert retried.json()["data"]["job_id"] == job
        await app.state.processor(job)
        assert len(requests) == before
        assert (await status(api, header, job))["result"]["cost"] == result["result"]["cost"]
        evidence(tmp_path, ceiling, calls, usages, result)


@pytest.mark.parametrize("phase", ["gap_fill", "retry"])
async def test_gap_fill_and_transient_retries_need_new_admission(
    database, tmp_path, monkeypatch, phase
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
        database, tmp_path, vendor, job_max_vendor_calls=cap, job_vendor_calls_per_batch=1
    ) as (app, api, header):
        _, job = await prepare(app, api, header, pdf_lines(parameter=True))
        await app.state.processor(job)
        result = await status(api, header, job)
        calls, usages, saved = await ledger(app, job)
        assert result["error"]["code"] == "job_call_limit_exceeded"
        assert len(requests) == len(calls) == len(usages) == cap
        assert saved == 0
        evidence(tmp_path, phase, calls, usages, result)


@pytest.mark.parametrize("cancel_worker", [False, True])
async def test_cancel_mid_extraction_keeps_every_completed_call_once(
    database, tmp_path, cancel_worker
):
    entered, release = (asyncio.Event(), asyncio.Event())
    requests = []

    async def vendor(request):
        requests.append(request)
        if len(requests) == 2:
            entered.set()
            await release.wait()
        return anthropic_reply([], stop="max_tokens")

    async with environment(database, tmp_path, vendor) as (app, api, header):
        _, job = await prepare(app, api, header)
        running = asyncio.create_task(app.state.processor(job))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            _, usages, _ = await ledger(app, job)
            assert len(usages) == 1
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
        calls, usages, saved = await ledger(app, job)
        assert result["status"] == ("running" if cancel_worker else "cancelled")
        assert len(requests) == len(usages) == 2
        assert len({u.call_id for u in usages}) == 2
        assert saved == 0
        assert result["result"]["cost"]["llm_tokens"] == 3000
        evidence(tmp_path, "cancel", calls, usages, result)


@pytest.mark.parametrize("fault", ["before_commit", "after_commit"])
async def test_ambiguous_accounting_commit_retries_without_duplicate_usage(
    database, tmp_path, monkeypatch, fault
):
    original = JobExecution._complete_once
    writes = []

    async def uncertain(self, call_id, usage):
        writes.append(call_id)
        if fault == "before_commit" and len(writes) == 1:
            raise OperationalError(
                None,
                None,
                ConnectionError("synthetic interrupted write"),
                connection_invalidated=True,
            )
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
    async with environment(database, tmp_path, lambda request: anthropic_reply([])) as (
        app,
        api,
        header,
    ):
        _, job = await prepare(app, api, header)
        await app.state.processor(job)
        result = await status(api, header, job)
        calls, usages, _ = await ledger(app, job)
        assert result["status"] == "succeeded"
        assert len(writes) == 2 and writes[0] == writes[1]
        assert len(calls) == len(usages) == 1
        assert result["result"]["cost"]["llm_tokens"] == 1500
        evidence(tmp_path, "accounting_retry", calls, usages, result)


async def test_lease_is_renewed_during_a_long_vendor_call(database, tmp_path):
    entered, release = (asyncio.Event(), asyncio.Event())
    requests = []

    async def vendor(request):
        requests.append(request)
        entered.set()
        await release.wait()
        return anthropic_reply([])

    async with environment(
        database, tmp_path, vendor, job_lease_seconds=3, job_heartbeat_seconds=0.2
    ) as (app, api, header):
        document, job = await prepare(app, api, header)
        running = asyncio.create_task(app.state.processor(job))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            async with app.state.db.transaction() as session:
                original = await session.get(Job, UUID(job))
            await asyncio.sleep(3.3)
            retried = await api.post(
                f"/documents/{document}/extract", headers=header, json={"retry": True}
            )
            assert retried.json()["data"]["status"] == "running"
            await app.state.processor(job)
            async with app.state.db.transaction() as session:
                current = await session.get(Job, UUID(job))
            assert current.run_id == original.run_id and current.lease_until > original.lease_until
            assert current.attempts == 1 and len(requests) == 1
        finally:
            release.set()
            await running
        assert (await status(api, header, job))["status"] == "succeeded"


async def test_retry_takeover_fences_old_attempt_but_keeps_late_usage(database, tmp_path):
    entered, release = (asyncio.Event(), asyncio.Event())
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

    async with environment(database, tmp_path, vendor) as (app, api, header):
        document, job = await prepare(app, api, header)
        old = asyncio.create_task(app.state.processor(job))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            async with app.state.db.transaction() as session:
                previous = await session.get(Job, UUID(job))
                previous.lease_until = datetime.now(UTC) - timedelta(seconds=1)
                old_run = previous.run_id
            retried = await api.post(
                f"/documents/{document}/extract", headers=header, json={"retry": True}
            )
            assert retried.json()["data"]["status"] == "queued"
            await app.state.processor(job)
            assert (await status(api, header, job))["error"]["code"] == "unresolved_call"
            assert len(requests) == 1
            old_execution = JobExecution(
                app.state.processor.settings, app.state.db, UUID(job), old_run
            )
            with pytest.raises(ProviderFailure, match="superseded"):
                await old_execution.heartbeat()
        finally:
            release.set()
            await old
        calls, usages, saved = await ledger(app, job)
        assert len(usages) == 1 and saved == 0
        assert usages[0].run_id == old_run
        retried = await api.post(
            f"/documents/{document}/extract", headers=header, json={"retry": True}
        )
        assert retried.status_code == 200
        await app.state.processor(job)
        result = await status(api, header, job)
        calls, usages, saved = await ledger(app, job)
        assert result["status"] == "succeeded" and len(requests) == 2
        assert len(usages) == 2 and len({u.run_id for u in usages}) == 2
        assert saved == 1
        assert result["result"]["cost"]["llm_tokens"] == 3000
        evidence(tmp_path, "takeover", calls, usages, result)


async def test_unknown_inference_outcomes_retain_call_history_across_bounded_retries(
    database, tmp_path, monkeypatch
):
    monkeypatch.setattr(AnthropicExtractor, "retry_delays", (0, 0))
    requests = []

    def vendor(request):
        requests.append(request)
        raise httpx.ReadTimeout("synthetic unknown vendor outcome")

    async with environment(database, tmp_path, vendor) as (app, api, header):
        _, job = await prepare(app, api, header)
        for attempt in range(1, 3):
            with pytest.raises(ProviderFailure) as failure:
                await app.state.processor(job)
            assert failure.value.retryable
            queued = await status(api, header, job)
            assert queued["status"] == "queued" and queued["attempts"] == attempt
            assert len(requests) == attempt * 3
        await app.state.processor(job)
        result = await status(api, header, job)
        calls, usages, saved = await ledger(app, job)
        assert result["status"] == "failed" and result["attempts"] == 3
        assert result["error"]["exit_code"] == 3
        assert len(requests) == len(calls) == 9
        assert all(call.state == "unknown" for call in calls)
        assert len({call.run_id for call in calls}) == 3
        assert result["result"]["cost"]["unresolved_calls"] == 9
        assert result["result"]["cost"]["calls"] == 0
        assert usages == [] and saved == 0
        await app.state.processor(job)
        assert len(requests) == 9
        evidence(tmp_path, "unknown_outcome", calls, usages, result)


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
async def test_refusal_is_accounted_before_output_validation(database, tmp_path, provider):

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

    async with environment(database, tmp_path, vendor, provider=provider) as (app, api, header):
        _, job = await prepare(app, api, header)
        await app.state.processor(job)
        result = await status(api, header, job)
        _, usages, _ = await ledger(app, job)
        assert result["error"]["code"] == "provider_refused"
        assert len(usages) == 1


@pytest.mark.parametrize("case,code", [("usage", "invalid_provider_usage")])
async def test_missing_usage_fails_explicitly(database, tmp_path, case, code):
    requests = []

    def vendor(request):
        requests.append(request)
        payload = anthropic_reply([], stop="max_tokens").json()
        if case == "usage":
            del payload["usage"]
        return httpx.Response(200, json=payload)

    async with environment(database, tmp_path, vendor) as (app, api, header):
        _, job = await prepare(app, api, header)
        await app.state.processor(job)
        result = await status(api, header, job)
        calls, usages, saved = await ledger(app, job)
        assert result["error"]["code"] == code and saved == 0
        assert len(requests) == 1
        assert usages == []
        assert calls[0].state == "unknown"
        evidence(tmp_path, case, calls, usages, result)


async def test_call_ceiling_grows_with_the_planned_first_pass(database, tmp_path):
    with pymupdf.open() as pdf:
        for number in range(3):
            page = pdf.new_page()
            page.insert_textbox(pdf[-1].rect + (40, 40, -40, -40), f"Page {number} line. " * 55)
        content = pdf.tobytes()
    requests = []

    def vendor(request):
        requests.append(request)
        return anthropic_reply([])

    async with environment(
        database,
        tmp_path,
        vendor,
        job_max_vendor_calls=2,
        job_vendor_calls_per_batch=1,
        llm_batch_chars=1000,
    ) as (app, api, header):
        _, job = await prepare(app, api, header, content)
        await app.state.processor(job)
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
    database, tmp_path, provider, options
):
    requests = []

    def vendor(request):
        requests.append(request)
        raise AssertionError("Conflicting limits must fail before dispatch")

    async with environment(
        database, tmp_path, vendor, provider=provider, llm_request_options=json.dumps(options)
    ) as (app, api, header):
        _, job = await prepare(app, api, header)
        await app.state.processor(job)
        result = await status(api, header, job)
        calls, usages, saved = await ledger(app, job)
        assert result["status"] == "failed"
        assert result["error"]["code"] == "invalid_provider_options"
        assert requests == calls == usages == []
        assert saved == 0
        evidence(tmp_path, "conflicting_limits", calls, usages, result)


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
async def test_plan_retains_largest_transmitted_output_limit(
    database, tmp_path, monkeypatch, provider
):
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

    async with environment(database, tmp_path, vendor, provider=provider) as (app, api, header):
        _, job = await prepare(app, api, header)
        await app.state.processor(job)
        result = await status(api, header, job)
        calls, usages, _ = await ledger(app, job)
        assert result["status"] == "succeeded", result
        assert len(calls) == len(usages) == len(bodies) == 1
        assert calls[0].provider_identity["output_tokens_upper_bound"] == 32000
        assert calls[0].provider_identity["output_tokens_upper_bound"] >= usages[0].output_tokens
        assert bodies[0]["max_completion_tokens"] == 1 and bodies[0]["max_tokens"] == 32000
        evidence(tmp_path, "largest_limit", calls, usages, result)


@pytest.mark.parametrize("unknown_fails", [False, True])
@pytest.mark.parametrize("fault", ["pool_timeout", "permanent", "exhausted", "missing_parent"])
async def test_settlement_failure_stops_waiting_batches_and_drains_sent_calls(
    database, tmp_path, monkeypatch, unknown_fails, fault
):
    sent_two, unknown_finished = (asyncio.Event(), asyncio.Event())
    bodies, writes, stopped = ([], [], [])
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
    async with environment(database, tmp_path, vendor, llm_concurrency=2, llm_batch_chars=1000) as (
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
            async with transaction(**kwargs) as session:
                yield session

        monkeypatch.setattr(app.state.db, "transaction", interrupted_transaction)
        _, job = await prepare(app, api, header, content)
        await asyncio.wait_for(app.state.processor(job), 15)
        result = await status(api, header, job)
        calls, usages, saved = await ledger(app, job)
        assert result["status"] == "failed" and result["error"]["code"] == "usage_accounting_failed"
        assert stopped == ["usage_accounting_failed"]
        assert len(bodies) == len(calls) == 2
        assert writes.count(first_call) == (3 if fault == "exhausted" else 1)
        assert len(usages) == 1 and usages[0].call_id != first_call
        assert {call.state for call in calls} == {
            "completed",
            "pending" if unknown_fails else "unknown",
        }
        assert saved == 0
        assert result["result"]["cost"]["llm_tokens"] == 1500
        evidence(tmp_path, "settlement_stop", calls, usages, result)


async def test_proven_unsent_credential_failure_retains_admission_without_call_count(
    database, tmp_path
):
    """Run the credential preparation failure against bid_app's real UPDATE-only ledger."""
    from app.core.errors import ServiceError
    from app.schemas.platform_credentials import ResolvedCredential, ServiceResolveTarget
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
        database, tmp_path, vendor, job_max_vendor_calls=1, job_vendor_calls_per_batch=1
    ) as (app, api, header):
        _, job_id = await prepare(app, api, header, pdf_lines(1))
        run_id = uuid4()
        async with app.state.db.transaction() as session:
            job = await session.get(Job, UUID(job_id))
            job.status, job.run_id = ("running", run_id)
            job.lease_until = datetime.now(UTC) + timedelta(minutes=5)
        execution = JobExecution(app.state.processor.settings, app.state.db, UUID(job_id), run_id)
        resolver = Resolver()
        llm = app.state.processor.llm
        llm.credential_resolver = resolver
        llm.credential_target = ServiceResolveTarget(
            service="standalone_llm", credential_id=uuid4()
        )
        async with execution.activate(), httpx.AsyncClient(transport=llm.transport) as client:
            with pytest.raises(ServiceError) as caught:
                await llm.post(
                    client, "https://api.anthropic.com/v1/messages", {}, {"max_tokens": 1024}
                )
            assert caught.value.code == "credential_disabled"
            calls, usages, _ = await ledger(app, job_id)
            assert len(calls) == 1 and calls[0].state == "not_sent"
            assert not requests and (not usages)
            await execution.not_sent(calls[0].id)
            resolver.unavailable = False
            await llm.post(
                client, "https://api.anthropic.com/v1/messages", {}, {"max_tokens": 1024}
            )
        calls, usages, _ = await ledger(app, job_id)
        assert len(requests) == len(usages) == 1
        assert sorted(call.state for call in calls) == ["completed", "not_sent"]
        evidence(
            tmp_path, "unsent-credential-release", calls, usages, await status(api, header, job_id)
        )
