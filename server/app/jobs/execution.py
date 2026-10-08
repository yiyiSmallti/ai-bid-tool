"""Attempt ownership, budget admission and durable accounting for any model job."""

import asyncio
import math
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import ServiceError
from app.models.entities import (
    ApiToken,
    AuditLog,
    Job,
    OrgBalance,
    Task,
    UsageRecord,
    User,
    VendorCall,
)
from app.providers.base import ProviderFailure
from app.providers.calls import current_accounting, current_drafting_input
from app.schemas.budget_contracts import (
    BudgetCallQuote,
    BudgetCost,
    BudgetIntervention,
    BudgetJobResult,
)
from app.schemas.contracts import Cost, ProviderUsage
from app.services import billing, budgets
from app.services.auth import ROLE_SCOPES, SCOPES, Identity, membership

MONEY_UNIT = Decimal("0.00000001")
TASK_STOPS = {
    "task_budget_exceeded",
    "task_budget_unpriced",
    "task_budget_currency_review_required",
}
ADMISSION_STOPS = TASK_STOPS | {
    "insufficient_balance",
    "spend_cap_reached",
    "job_charge_limit_exceeded",
    "job_call_limit_exceeded",
}
JOB_SCOPES = {
    "annotation_render": "evidence:annotate",
    "annotation_release": "card:read",
    "parse": "tender:parse",
    "extract": "req:extract",
    "card_generate": "card:generate",
    "check": "check:run",
    "score": "score:run",
    "score_rubric": "score:rubric:generate",
    "provider_test": "provider:write",
    "screenshot_search": "screenshot:write",
    "screenshot_render": "screenshot:write",
    "screenshot_analyze": "screenshot:write",
    "prototype_generate": "screenshot:write",
    "product_simulation": "task:resource",
    "memory_candidate": "memory:candidate:run",
    "sandbox": "sandbox:render",
    "bid_review_prepare": "bid-review:prepare",
    "bid_review": "bid-review:run",
}


async def locked_job(session: AsyncSession, job_id: UUID) -> Job | None:
    """Resolve the immutable parent before locking Task -> Job on every worker path."""
    task_id = await session.scalar(select(Job.task_id).where(Job.id == job_id))
    if task_id is not None:
        await session.scalar(select(Task).where(Task.id == task_id).with_for_update())
        from app.models.team_workflow import TaskWorkflow

        await session.scalar(
            select(TaskWorkflow).where(TaskWorkflow.task_id == task_id).with_for_update()
        )
    job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is not None and job.task_id != task_id:
        raise ProviderFailure("Job task binding changed", code="job_attempt_stopped")
    if job is not None:
        session.info["execution_job"] = job
    return job


async def authorized_job(session: AsyncSession, job: Job, *, bind_context=True) -> Identity:
    """Saved grants are an upper bound, never a substitute for live membership."""
    if job.actor_user_id is None or not job.actor_scopes or job.actor_kind is None:
        raise ProviderFailure(
            "Resubmit this job with an authorized identity", code="job_actor_required"
        )
    if job.kind in {"annotation_render", "annotation_release"}:
        from app.services.annotations import worker_access

        return await worker_access(session, job, bind_context=bind_context)
    if job.kind == "bid_review_prepare":
        from app.services.bid_preparation import worker_access

        return await worker_access(session, job, bind_context=bind_context)
    member = await membership(session, job.actor_user_id, job.org_id)
    user = await session.get(User, job.actor_user_id)
    if user is None or not user.active:
        raise ProviderFailure("Submitter is no longer active", code="forbidden")
    scopes = set(job.actor_scopes) & ROLE_SCOPES[member.role]
    if job.actor_token_id is not None:
        token = await session.get(ApiToken, job.actor_token_id)
        if (
            token is None
            or token.user_id != user.id
            or token.revoked
            or token.expires_at <= datetime.now(UTC)
        ):
            raise ProviderFailure("Submission token is no longer active", code="forbidden")
        scopes &= set(token.scopes) & SCOPES
    actor = Identity(user.id, job.org_id, scopes, member.role, job.actor_token_id, job.actor_kind)
    if job.agent_principal_id is not None:
        from app.models.agent import AgentPrincipal
        from app.services.auth import agent_identity

        principal = await session.get(AgentPrincipal, job.agent_principal_id)
        if principal is None:
            raise ProviderFailure("Agent authority is unavailable", code="agent_authority_expired")
        actor = await agent_identity(session, principal)
        actor.scopes &= scopes
        actor.session_id, actor.step_id = job.agent_session_id, job.agent_step_id
        actor.invocation_id = job.invocation_id
        actor.job_id, actor.run_id = job.id, job.run_id
        actor.actor_kind = "worker"
    if job.kind == "sandbox":
        from app.models.sandbox import SandboxInput, SandboxRun

        purpose = await session.scalar(
            select(SandboxInput.purpose)
            .join(SandboxRun, SandboxRun.input_id == SandboxInput.id)
            .where(
                SandboxRun.org_id == job.org_id,
                SandboxRun.job_id == job.id,
                SandboxRun.task_id == job.task_id,
                SandboxInput.org_id == job.org_id,
                SandboxInput.task_id == job.task_id,
            )
        )
        if purpose not in {"prototype_offline", "vendor_capture"}:
            raise ProviderFailure("Pinned sandbox input is missing", code="sandbox_input_changed")
        actor.require("sandbox:render" if purpose == "prototype_offline" else "sandbox:capture")
    else:
        actor.require(JOB_SCOPES.get(job.kind, "task:read"))
    if job.task_id is not None:
        from app.services.task_workflow import access as task_access

        # The actual initiator is retained; workers receive no human admin recovery.
        worker = replace(actor, actor_kind="worker", job_id=job.id, run_id=job.run_id)
        await task_access(session, worker, job.task_id, write=True, bind_context=bind_context)
    return actor


async def job_cost(session: AsyncSession, job_id: UUID, currency: str | None = None) -> dict:
    vendor_history_complete = await session.scalar(
        select(Job.vendor_cost_history_complete).where(Job.id == job_id)
    )
    tokens, pages, usd, count, priced, charge, task_amount, task_priced = (
        await session.execute(
            select(
                func.coalesce(func.sum(UsageRecord.tokens), 0),
                func.coalesce(func.sum(UsageRecord.ocr_pages), 0),
                func.coalesce(func.sum(UsageRecord.usd), 0),
                func.count(UsageRecord.id),
                func.count(UsageRecord.usd),
                func.coalesce(func.sum(UsageRecord.charge), 0),
                func.coalesce(func.sum(UsageRecord.task_amount), 0),
                func.count(UsageRecord.task_amount),
            ).where(UsageRecord.job_id == job_id)
        )
    ).one()
    unresolved, unpriced_holds = (
        await session.execute(
            select(
                func.count(VendorCall.id),
                func.count().filter(VendorCall.reserved_task_amount.is_(None)),
            ).where(VendorCall.job_id == job_id, VendorCall.state.in_(["pending", "unknown"]))
        )
    ).one()
    if currency is None:
        currency = await session.scalar(
            select(UsageRecord.billing_currency)
            .where(UsageRecord.job_id == job_id)
            .order_by(UsageRecord.created_at)
            .limit(1)
        )
        if currency is None:
            currency = await session.scalar(
                select(Task.budget_currency)
                .join(Job, Job.task_id == Task.id)
                .where(Job.id == job_id)
            )
    wrong_currency = await session.scalar(
        select(func.count(UsageRecord.id)).where(
            UsageRecord.job_id == job_id,
            UsageRecord.billing_currency != (currency or "USD"),
            (UsageRecord.task_amount > 0) | (UsageRecord.charge > 0),
        )
    )
    return Cost(
        llm_tokens=tokens,
        ocr_pages=pages,
        usd=float(usd) if count == priced and vendor_history_complete else None,
        basis="actual",
        charge=charge if not wrong_currency else None,
        billing_currency=currency or "USD",
        task_amount=task_amount
        if count == task_priced and not unpriced_holds and not wrong_currency
        else None,
        unpriced_calls=count - task_priced + unpriced_holds,
        unresolved_calls=unresolved or 0,
    ).model_dump(mode="json")


class JobExecution:
    def __init__(self, settings: Settings, db: Database, org_id: UUID, job_id: UUID, run_id: UUID):
        self.settings, self.db = settings, db
        self.org_id, self.job_id, self.run_id = org_id, job_id, run_id
        self.stopped: ProviderFailure | None = None
        self.planned_calls = 0
        self.absolute_call_ceiling: int | None = None
        self.before_admit: Callable[[AsyncSession], Awaitable[None]] | None = None
        self.intervention: BudgetIntervention | None = None
        self.call_deadline: datetime | None = None

    def plan(self, first_pass_calls: int) -> None:
        self.planned_calls = max(self.planned_calls, first_pass_calls)

    @property
    def call_ceiling(self) -> int:
        # A fixed ceiling alone would stop large documents part-way through their first pass;
        # scaling it with the planned batches still stops runaway halving and gap filling.
        scaled = math.ceil(self.planned_calls * self.settings.job_vendor_calls_per_batch)
        value = max(self.settings.job_max_vendor_calls, scaled)
        return (
            min(value, self.absolute_call_ceiling)
            if self.absolute_call_ceiling is not None
            else value
        )

    def stop(self, code: str, message: str) -> ProviderFailure:
        # A check may publish partial coverage after an admission cap. A later
        # lease/accounting failure must still fence that publication.
        if self.stopped is None or (
            self.stopped.code in ADMISSION_STOPS and code not in ADMISSION_STOPS
        ):
            self.stopped = ProviderFailure(message, code=code)
        return self.stopped

    async def owned_job(self, session: AsyncSession) -> Job:
        if self.stopped is not None and self.stopped.code not in ADMISSION_STOPS:
            raise self.stopped
        job = await locked_job(session, self.job_id)
        now = await session.scalar(select(func.clock_timestamp()))
        assert now is not None
        if (
            job is None
            or job.status != "running"
            or job.run_id != self.run_id
            or job.lease_until is None
            or job.lease_until <= now
        ):
            raise self.stop(
                "job_attempt_stopped", "Job attempt was cancelled, superseded or expired"
            )
        if job.agent_session_id is not None and job.kind != "agent":
            from app.services.agent_limits import guard_job

            await guard_job(session, job, self.settings)
        # A controller's current step has a more specific invocation than its Job.
        # Recheck authority without replacing the caller's already installed fence.
        await authorized_job(session, job, bind_context=job.kind != "agent")
        return job

    async def heartbeat(self) -> None:
        async with self.db.transaction(self.org_id) as session:
            try:
                await self.owned_job(session)
            except ServiceError as error:
                raise self.stop(error.code, "Submission authorization is no longer valid") from None
            renewed = await session.scalar(
                update(Job)
                .where(
                    Job.id == self.job_id,
                    Job.run_id == self.run_id,
                    Job.status == "running",
                    Job.lease_until > func.clock_timestamp(),
                )
                .values(
                    lease_until=func.clock_timestamp()
                    + timedelta(seconds=self.settings.job_lease_seconds)
                )
                .returning(Job.id)
            )
        if renewed is None:
            raise self.stop(
                "job_attempt_stopped", "Job attempt was cancelled, superseded or expired"
            )

    async def _heartbeats(self) -> None:
        while True:
            await asyncio.sleep(self.settings.job_heartbeat_seconds)
            try:
                await self.heartbeat()
            except ProviderFailure:
                return  # owned_job will also reject the next call.
            except SQLAlchemyError:
                self.stop(
                    "job_heartbeat_failed", "Could not renew the job lease; no more calls allowed"
                )
                return

    @asynccontextmanager
    async def activate(self):
        token = current_accounting.set(self)
        heartbeat = asyncio.create_task(self._heartbeats())
        try:
            yield self
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
            current_accounting.reset(token)
            await self.finalize_result()

    async def admission_failure(self, session, job, quote, code, message):
        task = await session.get(Task, job.task_id) if job.task_id else None
        view = await budgets.view(session, task, self.settings) if task else None
        required = quote.reserved_task_amount
        known = view is not None and view.history_complete and not view.unpriced_calls
        action = (
            "recharge_org"
            if code == "insufficient_balance"
            else "review_currency"
            if code == "task_budget_currency_review_required"
            else "configure_price"
            if code == "task_budget_unpriced"
            else "raise_task_budget"
            if code == "task_budget_exceeded"
            else "review_job_limit"
        )
        self.intervention = BudgetIntervention(
            code=code,
            org_id=self.org_id,
            task_id=job.task_id,
            job_id=self.job_id,
            budget_revision=view.revision if view else None,
            currency=self.settings.billing_currency,
            available=view.available if view and action != "recharge_org" else None,
            required_next_call=required if action != "recharge_org" else quote.reserved_charge,
            minimum_new_limit=view.spent + view.reserved + required
            if known and view is not None and required is not None
            else None,
            action=action,
            authorized_roles=["admin", "bidder"]
            if action in {"raise_task_budget", "review_currency"}
            else ["admin"],
        )
        raise self.stop(code, message)

    async def audit_stop(self, action: str, code: str) -> None:
        # The denied admission transaction is already rolled back. Task/Job locks
        # also serialize duplicate error reports from concurrent batches.
        async with self.db.transaction(self.org_id) as session:
            job = await locked_job(session, self.job_id)
            if job is None or job.actor_user_id is None:
                return
            revision = self.intervention.budget_revision if self.intervention else None
            details = {
                "job_id": str(self.job_id),
                "run_id": str(self.run_id),
                "task_id": str(job.task_id) if job.task_id else None,
                "budget_revision": revision,
                "reason_code": code,
                "actor_kind": "worker",
                "submitter_kind": job.actor_kind,
            }
            existing = await session.scalar(
                select(AuditLog.id)
                .where(
                    AuditLog.action == action,
                    AuditLog.object_id == self.job_id,
                    AuditLog.details == details,
                )
                .limit(1)
            )
            if existing is None:
                from app.services.auth import set_actor_context
                from app.services.versioned import audit

                actor = Identity(
                    job.actor_user_id,
                    job.org_id,
                    set(job.actor_scopes),
                    "viewer",
                    job.actor_token_id,
                    "worker",
                    principal_id=job.agent_principal_id,
                    session_id=job.agent_session_id,
                    step_id=job.agent_step_id,
                    invocation_id=job.invocation_id,
                    job_id=job.id,
                    run_id=self.run_id,
                )
                await set_actor_context(session, actor)
                audit(session, actor, action, self.job_id, details)

    async def admit(
        self, quote: BudgetCallQuote | Decimal, platform_billed: bool | None = None
    ) -> UUID:
        if self.stopped is not None:
            raise self.stopped
        if not isinstance(quote, BudgetCallQuote):
            raise self.stop(
                "billing_price_unavailable", "Admission requires a fixed provider quote"
            )
        try:
            return await self._admit_once(quote)
        except ProviderFailure as error:
            if error.code in ADMISSION_STOPS:
                await self.audit_stop("task.budget.admission_denied", error.code)
            raise

    async def _admit_once(self, quote: BudgetCallQuote) -> UUID:
        call_id = uuid4()
        async with self.db.transaction(self.org_id) as session:
            job = await self.owned_job(session)
            try:
                await authorized_job(session, job)
            except ServiceError as error:
                raise self.stop(error.code, "Submission authorization is no longer valid") from None
            if quote.currency != self.settings.billing_currency:
                raise self.stop(
                    "billing_currency_mismatch", "Call currency differs from deployment currency"
                )
            if job.agent_session_id is not None:
                from app.services.agent_limits import guard_job

                guarded = await guard_job(session, job, self.settings, quote)
                assert guarded is not None
                _, self.call_deadline = guarded
            task = await session.get(Task, job.task_id) if job.task_id else None
            if task is not None:
                view = await budgets.view(session, task, self.settings)
                amount = quote.reserved_task_amount
                # Explicit zero liability remains available for local work even when
                # old pricing or currency needs human review.
                if amount != 0:
                    if view.state != "active" or view.currency != quote.currency:
                        await self.admission_failure(
                            session,
                            job,
                            quote,
                            "task_budget_currency_review_required",
                            "Review the task currency before more paid work",
                        )
                    if view.limit is not None:
                        if amount is None or not view.history_complete or view.unpriced_calls:
                            await self.admission_failure(
                                session,
                                job,
                                quote,
                                "task_budget_unpriced",
                                "The task has unknown cost exposure",
                            )
                        assert amount is not None
                        if view.spent + view.reserved + amount > view.limit:
                            await self.admission_failure(
                                session,
                                job,
                                quote,
                                "task_budget_exceeded",
                                "Task budget cannot cover the next call",
                            )
            submitted_cap = (job.result or {}).get("submission", {}).get("max_charge")
            user_cap = Decimal(submitted_cap) if submitted_cap is not None else None
            if self.before_admit is not None:
                await self.before_admit(session)
            count, spent = (
                await session.execute(
                    select(
                        func.count(VendorCall.id),
                        func.coalesce(
                            func.sum(
                                case(
                                    (VendorCall.state == "completed", VendorCall.charge),
                                    else_=VendorCall.reserved_charge,
                                )
                            ),
                            0,
                        ),
                    ).where(VendorCall.job_id == self.job_id, VendorCall.state != "not_sent")
                )
            ).one()
            if count >= self.call_ceiling:
                await self.admission_failure(
                    session,
                    job,
                    quote,
                    "job_call_limit_exceeded",
                    "Job vendor-call ceiling reached",
                )
            reserved_charge = quote.reserved_charge
            if user_cap is not None and user_cap < self.settings.job_max_charge:
                if spent + reserved_charge > user_cap:
                    await self.admission_failure(
                        session,
                        job,
                        quote,
                        "spend_cap_reached",
                        "The requested charge cap cannot cover another call",
                    )
            elif spent + reserved_charge > self.settings.job_max_charge:
                await self.admission_failure(
                    session,
                    job,
                    quote,
                    "job_charge_limit_exceeded",
                    "Job charge ceiling cannot cover another call",
                )
            if quote.payer == "org_platform":
                balance = await session.scalar(select(OrgBalance).with_for_update())
                held = await session.scalar(
                    select(func.coalesce(func.sum(VendorCall.reserved_charge), 0)).where(
                        VendorCall.state.in_(["pending", "unknown"])
                    )
                )
                assert held is not None
                if balance is None or balance.balance - held < reserved_charge:
                    await self.admission_failure(
                        session,
                        job,
                        quote,
                        "insufficient_balance",
                        "Available balance cannot cover another call",
                    )
                assert balance is not None
                if balance.currency != quote.currency:
                    raise self.stop(
                        "billing_currency_mismatch", "Balance currency does not match call currency"
                    )
            if self.stopped is not None:
                raise self.stopped
            session.add(
                VendorCall(
                    id=call_id,
                    org_id=self.org_id,
                    job_id=self.job_id,
                    run_id=self.run_id,
                    task_id=job.task_id,
                    budget_revision=task.budget_revision if task else None,
                    capability=quote.capability,
                    payer=quote.payer,
                    currency=quote.currency,
                    price_revision=quote.price_revision,
                    request_sha256=quote.request_sha256,
                    reserved_charge=reserved_charge,
                    reserved_task_amount=quote.reserved_task_amount,
                    quote=quote.model_dump(mode="json"),
                )
            )
            await session.flush()
            if job.kind == "card_generate" and job.agent_session_id is None:
                from app.memory.retrieval import attach_call

                await attach_call(session, self, call_id, current_drafting_input.get())
        return call_id

    async def _complete_once(self, call_id: UUID, usage: ProviderUsage) -> bool:
        async with self.db.transaction(self.org_id) as session:
            # A late completed call is billable even after cancellation or takeover.
            job = await locked_job(session, self.job_id)
            if job is None:
                raise ProviderFailure("Accounting job is missing", code="usage_accounting_failed")
            call = await session.scalar(
                select(VendorCall).where(
                    VendorCall.id == call_id,
                    VendorCall.job_id == self.job_id,
                    VendorCall.run_id == self.run_id,
                )
            )
            if call is None:
                raise ProviderFailure("Call admission is missing", code="usage_accounting_failed")
            if call.state == "not_sent":
                raise ProviderFailure("Unsent calls cannot settle", code="usage_accounting_failed")
            quote = BudgetCallQuote.model_validate(call.quote)
            actual_identity = (
                usage.capability,
                usage.payer,
                usage.billing_currency,
                usage.price_revision,
                usage.provider,
                usage.version,
                usage.provider_config_id,
                usage.platform_model_id,
                usage.image_count,
                usage.image_price_revision,
            )
            admitted_identity = (
                quote.capability,
                quote.payer,
                quote.currency,
                quote.price_revision,
                quote.provider,
                quote.version,
                quote.provider_config_id,
                quote.platform_model_id,
                quote.image_count,
                quote.image_price_revision,
            )
            if actual_identity != admitted_identity:
                raise ProviderFailure(
                    "Usage does not match admitted pricing identity", code="usage_accounting_failed"
                )
            if usage.platform_model_id is not None and usage.charge is None:
                raise ProviderFailure("Platform call has no price", code="usage_accounting_failed")
            task_amount = (
                usage.task_amount.quantize(MONEY_UNIT, rounding=ROUND_HALF_UP)
                if usage.task_amount is not None
                else None
            )
            # The provider retains exact liability as Decimal. charge/usd retain
            # legacy float wire types and must not round-trip the ledger through float.
            amount = task_amount if quote.payer == "org_platform" else Decimal(0)
            if (
                (quote.payer == "org_platform" and amount is None)
                or (quote.payer in {"local_free", "platform_absorbed"} and task_amount != 0)
                or (quote.payer != "org_platform" and usage.charge not in (None, 0))
                or (
                    quote.payer == "org_direct"
                    and task_amount is not None
                    and quote.currency != "USD"
                )
            ):
                raise ProviderFailure(
                    "Usage liability does not match its payer", code="usage_accounting_failed"
                )
            assert amount is not None
            stored_usage = {**usage.model_dump(), "charge": amount, "task_amount": task_amount}
            if quote.payer == "org_direct" and task_amount is not None:
                stored_usage["usd"] = task_amount
            record_id = await session.scalar(
                insert(UsageRecord)
                .values(
                    id=call_id,
                    org_id=self.org_id,
                    task_id=job.task_id,
                    job_id=self.job_id,
                    run_id=self.run_id,
                    call_id=call_id,
                    **stored_usage,
                )
                .on_conflict_do_nothing(index_elements=["org_id", "job_id", "run_id", "call_id"])
                .returning(UsageRecord.id)
            )
            if record_id is not None:
                record = await session.get(UsageRecord, record_id)
                assert record is not None
                # Release the reservation before the balance mutation so low-balance
                # triggers observe either the hold or the final charge, never both.
                call.state, call.charge = "completed", amount
                await session.flush()
                await billing.charge_usage(session, self.org_id, record, quote.currency)
                await session.flush()
                job.result = {
                    **job.result,
                    "cost": await job_cost(session, self.job_id, quote.currency),
                }
                if job.result.get("budget"):
                    await self.refresh_budget_totals(session, job)
            if job.kind == "card_generate":
                from app.memory.retrieval import settle_call

                await settle_call(
                    session, self.org_id, self.job_id, self.run_id, call_id, "completed"
                )
            exceeded = amount > call.reserved_charge or (
                task_amount is not None
                and call.reserved_task_amount is not None
                and task_amount > call.reserved_task_amount
            )
            if job.agent_session_id is not None and quote.vendor_usd_upper_bound is not None:
                exceeded = (
                    exceeded
                    or usage.usd is None
                    or (Decimal(str(usage.usd)) > quote.vendor_usd_upper_bound)
                )
        return exceeded

    async def complete(self, call_id: UUID, usage: ProviderUsage) -> None:
        for attempt in range(3):
            try:
                exceeded = await self._complete_once(call_id, usage)
                break
            except DBAPIError as exc:
                code = getattr(exc.orig, "sqlstate", None)
                transient = exc.connection_invalidated or (
                    code and (code.startswith("08") or code in {"40001", "40P01"})
                )
                if not transient or attempt == 2:
                    raise self.stop(
                        "usage_accounting_failed",
                        "Could not persist vendor usage; no more calls allowed",
                    ) from None
                await asyncio.sleep(0.05 * (attempt + 1))
            except (SQLAlchemyError, ProviderFailure):
                # Pool timeouts and domain failures are not transient DBAPI errors.
                # Fence admission before the caller tries to persist unknown state.
                raise self.stop(
                    "usage_accounting_failed",
                    "Could not persist vendor usage; no more calls allowed",
                ) from None
        else:
            raise AssertionError("unreachable")
        if exceeded:
            await self.audit_stop("task.budget.bound_exceeded", "call_charge_bound_exceeded")
            raise self.stop(
                "call_charge_bound_exceeded",
                "Vendor usage exceeded its reserved bound; usage was retained",
            )

    async def unknown(self, call_id: UUID) -> None:
        try:
            async with self.db.transaction(self.org_id) as session:
                # Match completion's Task -> Job -> call lock order. Late settlement is
                # allowed even when the original lease or membership has ended.
                await locked_job(session, self.job_id)
                await session.execute(
                    update(VendorCall)
                    .where(
                        VendorCall.id == call_id,
                        VendorCall.job_id == self.job_id,
                        VendorCall.run_id == self.run_id,
                        VendorCall.state == "pending",
                    )
                    .values(state="unknown")
                )
                from app.memory.retrieval import settle_call

                await settle_call(
                    session, self.org_id, self.job_id, self.run_id, call_id, "unknown"
                )
        except SQLAlchemyError:
            # A failed marker leaves the durable pending reservation in place.
            # Never replace an accounting stop with a raw pool/driver exception.
            raise self.stop(
                "usage_accounting_failed",
                "Could not persist unknown vendor outcome; no more calls allowed",
            ) from None

    async def not_sent(self, call_id: UUID) -> None:
        """Release this attempt's pending admission after credential preparation failed.

        The call boundary invokes this only before starting the HTTP operation, so no
        usage exists and the reservation cannot represent an unknown vendor charge.
        """
        try:
            async with self.db.transaction(self.org_id) as session:
                await locked_job(session, self.job_id)
                await session.execute(
                    update(VendorCall)
                    .where(
                        VendorCall.id == call_id,
                        VendorCall.job_id == self.job_id,
                        VendorCall.run_id == self.run_id,
                        VendorCall.state == "pending",
                    )
                    .values(
                        state="not_sent",
                        reserved_charge=Decimal(0),
                        reserved_task_amount=Decimal(0),
                        charge=None,
                    )
                )
        except SQLAlchemyError:
            raise self.stop(
                "usage_accounting_failed", "Could not release unsent call admission"
            ) from None

    async def finalize_result(self) -> None:
        """Attach a cumulative budget result without publishing an obsolete attempt."""
        async with self.db.transaction(self.org_id) as session:
            job = await locked_job(session, self.job_id)
            if (
                job is None
                or job.run_id != self.run_id
                or job.status not in {"succeeded", "failed", "cancelled"}
            ):
                return
            cost = await job_cost(session, self.job_id, self.settings.billing_currency)
            task = await session.get(Task, job.task_id) if job.task_id else None
            view = await budgets.view(session, task, self.settings) if task else None
            result = job.result or {}
            stop = result.get("stop_reason") or (job.error or {}).get("code")
            completion = (
                "failed" if job.status != "succeeded" else (result.get("completion") or "complete")
            )
            unresolved = list(
                (
                    await session.scalars(
                        select(VendorCall.id)
                        .where(
                            VendorCall.job_id == self.job_id,
                            VendorCall.state.in_(["pending", "unknown"]),
                        )
                        .order_by(VendorCall.created_at, VendorCall.id)
                    )
                ).all()
            )
            usage_ids = list(
                (
                    await session.scalars(
                        select(UsageRecord.id)
                        .where(UsageRecord.job_id == self.job_id)
                        .order_by(UsageRecord.created_at, UsageRecord.id)
                    )
                ).all()
            )
            published = list(result.get("created_revision_ids", result.get("published_ids", [])))
            if job.status == "succeeded":
                published.extend(
                    result[key]
                    for key in (
                        "report_id",
                        "rubric_id",
                        "analysis_run_id",
                        "draft_id",
                        "review_id",
                    )
                    if result.get(key)
                )
                published.extend(
                    result[key]["id"]
                    for key in ("prototype_generation", "vendor_search")
                    if isinstance(result.get(key), dict) and result[key].get("id")
                )
            remaining = list(result.get("remaining_ids", []))
            if job.kind == "bid_review":
                remaining.extend(result.get("coverage", {}).get("unassessed_page_ids", []))
            if job.kind == "card_generate":
                from app.services.card_generation import PROTECTED

                remaining.extend(
                    key
                    for key, reason in result.get("skipped", {}).items()
                    if reason not in PROTECTED
                )
                remaining.extend(result.get("needs_material", []))
                remaining.extend(result.get("rejected_references", {}))
            published, remaining = list(dict.fromkeys(published)), list(dict.fromkeys(remaining))
            continuation = (
                "none"
                if completion == "complete"
                else (
                    "reconcile_first"
                    if unresolved
                    else "submit_remaining"
                    if completion == "partial"
                    else "retry_job"
                )
            )
            budget = BudgetJobResult(
                job_id=self.job_id,
                run_id=self.run_id,
                task_id=job.task_id,
                completion=completion,
                stop_reason=stop,
                intervention=self.intervention,
                cost=BudgetCost.model_validate(cost),
                task_budget=view,
                published_ids=published,
                remaining_ids=remaining,
                usage_record_ids=usage_ids,
                unresolved_call_ids=unresolved,
                continuation=continuation,
            )
            warnings = list(result.get("warnings", []))
            if not job.vendor_cost_history_complete:
                warnings.append("historical_vendor_cost_unavailable")
            low = await session.scalar(select(OrgBalance.low_balance_active))
            if low and "low_balance" not in warnings:
                warnings.append("low_balance")
            job.result = {
                **result,
                "cost": cost,
                "budget": budget.model_dump(mode="json"),
                "warnings": warnings,
            }

    async def refresh_budget_totals(self, session: AsyncSession, job: Job) -> None:
        """Late settlement refreshes accounting only, preserving successor decisions."""
        previous = dict(job.result["budget"])
        task = await session.get(Task, job.task_id) if job.task_id else None
        view = await budgets.view(session, task, self.settings) if task else None
        unresolved = list(
            (
                await session.scalars(
                    select(VendorCall.id)
                    .where(
                        VendorCall.job_id == job.id, VendorCall.state.in_(["pending", "unknown"])
                    )
                    .order_by(VendorCall.created_at, VendorCall.id)
                )
            ).all()
        )
        usage_ids = list(
            (
                await session.scalars(
                    select(UsageRecord.id)
                    .where(UsageRecord.job_id == job.id)
                    .order_by(UsageRecord.created_at, UsageRecord.id)
                )
            ).all()
        )
        previous.update(
            cost=job.result["cost"],
            task_budget=view.model_dump(mode="json") if view else None,
            usage_record_ids=[str(identifier) for identifier in usage_ids],
            unresolved_call_ids=[str(identifier) for identifier in unresolved],
        )
        if previous.get("continuation") == "reconcile_first" and not unresolved:
            previous["continuation"] = (
                "none"
                if previous["completion"] == "complete"
                else "submit_remaining"
                if previous["completion"] == "partial"
                else "retry_job"
            )
        job.result = {**job.result, "budget": previous}
