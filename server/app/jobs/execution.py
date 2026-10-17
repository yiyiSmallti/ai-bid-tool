"""Attempt ownership, call admission and durable usage for any model job."""

import asyncio
import math
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Integer, Text, func, or_, select, text, update
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
    Task,
    UsageRecord,
    VendorCall,
)
from app.providers.base import ProviderFailure
from app.providers.calls import current_accounting, current_drafting_input
from app.schemas.contracts import Cost, ProviderUsage
from app.schemas.preflight_contracts import CallPlan
from app.services.auth import HUMAN_SCOPES, SCOPES, Identity, set_actor_context

ADMISSION_STOPS = {"job_call_limit_exceeded"}
QUOTA_LOCK = "member-quota:{}"
JOB_SCOPES = {
    "annotation_render": "evidence:annotate",
    "annotation_release": "card:read",
    "parse": "tender:parse",
    "extract": "req:extract",
    "card_generate": "card:generate",
    "check": "check:run",
    "score": "score:run",
    "score_rubric": "score:rubric:generate",
    "solution_draft": "solution:draft",
    "scoring_review": "solution:draft",
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
    "bid_review_report": "bid-review:report:render",
    "package_export": "package:export",
    "package_import": "package:import",
}


async def locked_job(session: AsyncSession, job_id: UUID) -> Job | None:
    """Resolve the immutable parent before locking Task -> Job on every worker path."""
    task_id = await session.scalar(select(Job.task_id).where(Job.id == job_id))
    if task_id is not None:
        await session.scalar(select(Task).where(Task.id == task_id).with_for_update())
    job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is not None and job.task_id != task_id:
        raise ProviderFailure("Job task binding changed", code="job_attempt_stopped")
    if job is not None:
        session.info["execution_job"] = job
    return job


async def authorized_job(session: AsyncSession, job: Job, *, bind_context=True) -> Identity:
    """Saved grants remain bounded by live token or agent authority."""
    if not job.actor_scopes or job.actor_kind is None:
        raise ProviderFailure(
            "Resubmit this job with an authorized identity", code="job_actor_required"
        )
    if job.kind in {"annotation_render", "annotation_release"}:
        from app.services.annotations import worker_access

        return await worker_access(session, job, bind_context=bind_context)
    if job.kind == "bid_review_report":
        from app.jobs.bid_review_report import worker_access

        return await worker_access(session, job, bind_context=bind_context)
    if job.kind == "bid_review_prepare":
        from app.services.bid_preparation import worker_access

        return await worker_access(session, job, bind_context=bind_context)
    scopes = set(job.actor_scopes) & HUMAN_SCOPES
    if job.actor_token_id is not None:
        token = await session.get(ApiToken, job.actor_token_id)
        if token is None or token.revoked or token.expires_at <= datetime.now(UTC):
            raise ProviderFailure("Submission token is no longer active", code="forbidden")
        scopes &= set(token.scopes) & SCOPES
    actor = Identity(scopes=scopes, token_id=job.actor_token_id, actor_kind=job.actor_kind)
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
                SandboxRun.job_id == job.id,
                SandboxRun.task_id == job.task_id,
                SandboxInput.task_id == job.task_id,
            )
        )
        if purpose not in {"prototype_offline", "vendor_capture"}:
            raise ProviderFailure("Pinned sandbox input is missing", code="sandbox_input_changed")
        actor.require("sandbox:render" if purpose == "prototype_offline" else "sandbox:capture")
    else:
        actor.require(JOB_SCOPES.get(job.kind, "task:read"))
    if job.task_id is not None and await session.get(Task, job.task_id) is None:
        raise ProviderFailure("Task is unavailable", code="not_found")
    if bind_context:
        await set_actor_context(
            session, replace(actor, actor_kind="worker", job_id=job.id, run_id=job.run_id)
        )
    return actor


async def bind_member(session: AsyncSession, job: Job) -> None:
    """Attribute this transaction's calls and usage to the member the job runs for."""
    await session.execute(
        text("SELECT set_config('app.actor_member_id', :member, true)"),
        {"member": str(job.actor_member_id) if job.actor_member_id else ""},
    )


def month_start() -> datetime:
    return datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def personal_configs():
    """Revisions of personal models, whose calls no company quota counts."""
    from app.models.provider_configs import ProviderConfig, ProviderSlot

    return (
        select(ProviderConfig.id)
        .join(ProviderSlot, ProviderSlot.slot == ProviderConfig.slot)
        .where(ProviderSlot.owner_member_id.is_not(None))
    )


async def company_tokens(session: AsyncSession, member: UUID, start: datetime) -> int:
    """A member's company-model tokens since `start`: used, plus the upper bound
    of every admitted call without a receipt yet."""
    used = await session.scalar(
        select(
            func.coalesce(func.sum(UsageRecord.input_tokens + UsageRecord.output_tokens), 0)
        ).where(
            UsageRecord.member_id == member,
            UsageRecord.created_at >= start,
            or_(
                UsageRecord.provider_config_id.is_(None),
                UsageRecord.provider_config_id.not_in(personal_configs()),
            ),
        )
    )
    plan = VendorCall.provider_identity
    config = plan["provider_config_id"].astext
    reserved = await session.scalar(
        select(
            func.coalesce(
                func.sum(
                    func.coalesce(plan["input_tokens_upper_bound"].astext.cast(Integer), 0)
                    + func.coalesce(plan["output_tokens_upper_bound"].astext.cast(Integer), 0)
                ),
                0,
            )
        ).where(
            VendorCall.member_id == member,
            VendorCall.created_at >= start,
            VendorCall.state.in_(["pending", "unknown"]),
            or_(
                config.is_(None),
                config.not_in(select(personal_configs().subquery().c.id.cast(Text))),
            ),
        )
    )
    return (used or 0) + (reserved or 0)


async def job_cost(session: AsyncSession, job_id: UUID) -> dict:
    tokens, pages, searches, browser_units, calls = (
        await session.execute(
            select(
                func.coalesce(func.sum(UsageRecord.tokens), 0),
                func.coalesce(func.sum(UsageRecord.ocr_pages), 0),
                func.coalesce(func.sum(UsageRecord.search_requests), 0),
                func.coalesce(func.sum(UsageRecord.browser_units), 0),
                func.count(UsageRecord.id),
            ).where(UsageRecord.job_id == job_id)
        )
    ).one()
    unresolved = await session.scalar(
        select(func.count(VendorCall.id)).where(
            VendorCall.job_id == job_id, VendorCall.state.in_(["pending", "unknown"])
        )
    )
    return Cost(
        calls=calls,
        llm_tokens=tokens,
        ocr_pages=pages,
        search_requests=searches,
        browser_units=browser_units,
        unresolved_calls=unresolved or 0,
    ).model_dump(mode="json")


class JobExecution:
    def __init__(self, settings: Settings, db: Database, job_id: UUID, run_id: UUID):
        self.settings, self.db = settings, db
        self.job_id, self.run_id = job_id, run_id
        self.stopped: ProviderFailure | None = None
        self.planned_calls = 0
        self.absolute_call_ceiling: int | None = None
        self.before_admit: Callable[[AsyncSession], Awaitable[None]] | None = None
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
        async with self.db.transaction() as session:
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

    async def audit_stop(self, action: str, code: str) -> None:
        # The denied admission transaction is already rolled back. Task/Job locks
        # also serialize duplicate error reports from concurrent batches.
        async with self.db.transaction() as session:
            job = await locked_job(session, self.job_id)
            if job is None:
                return
            details = {
                "job_id": str(self.job_id),
                "run_id": str(self.run_id),
                "task_id": str(job.task_id) if job.task_id else None,
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
                    scopes=set(job.actor_scopes),
                    token_id=job.actor_token_id,
                    actor_kind="worker",
                    principal_id=job.agent_principal_id,
                    session_id=job.agent_session_id,
                    step_id=job.agent_step_id,
                    invocation_id=job.invocation_id,
                    job_id=job.id,
                    run_id=self.run_id,
                )
                await set_actor_context(session, actor)
                audit(session, actor, action, self.job_id, details)

    async def admit(self, quote: CallPlan) -> UUID:
        if self.stopped is not None:
            raise self.stopped
        if not isinstance(quote, CallPlan):
            raise self.stop(
                "provider_plan_required", "Admission requires a fixed provider call plan"
            )
        try:
            return await self._admit_once(quote)
        except ProviderFailure as error:
            if error.code in ADMISSION_STOPS:
                await self.audit_stop("job.call.admission_denied", error.code)
            raise

    async def member_quota(self, session: AsyncSession, job: Job, quote: CallPlan) -> None:
        """Admit a company-model call only while the member's month stays within the cap."""
        from app.models.members import MemberQuota
        from app.models.provider_configs import ProviderConfig, ProviderSlot

        member = job.actor_member_id
        assert member is not None
        cap = await session.scalar(
            select(MemberQuota.monthly_company_tokens).where(MemberQuota.member_id == member)
        )
        if cap is None:
            return
        if quote.provider_config_id is not None and await session.scalar(
            select(ProviderSlot.owner_member_id)
            .join(ProviderConfig, ProviderConfig.slot == ProviderSlot.slot)
            .where(ProviderConfig.id == quote.provider_config_id)
        ):
            return  # Personal models are not capped.
        # Every capped admission for this member serializes here, after its
        # Task -> Job locks, so parallel jobs cannot each see room for the same tokens.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": QUOTA_LOCK.format(member)},
        )
        planned = quote.input_tokens_upper_bound + quote.output_tokens_upper_bound
        if await company_tokens(session, member, month_start()) + planned > cap:
            raise self.stop(
                "member_quota_exceeded", "The member's monthly company-model tokens are used up"
            )

    async def _admit_once(self, quote: CallPlan) -> UUID:
        call_id = uuid4()
        async with self.db.transaction() as session:
            job = await self.owned_job(session)
            await bind_member(session, job)
            if job.agent_session_id is not None:
                from app.services.agent_limits import guard_job

                guarded = await guard_job(session, job, self.settings, quote)
                assert guarded is not None
                _, self.call_deadline = guarded
            if self.before_admit is not None:
                await self.before_admit(session)
            count = await session.scalar(
                select(func.count(VendorCall.id)).where(
                    VendorCall.job_id == self.job_id, VendorCall.state != "not_sent"
                )
            )
            if (count or 0) >= self.call_ceiling:
                raise self.stop("job_call_limit_exceeded", "Job vendor-call ceiling reached")
            if self.stopped is not None:
                raise self.stopped
            unresolved_query = select(VendorCall.id).where(
                VendorCall.job_id == self.job_id,
                VendorCall.request_sha256 == quote.request_sha256,
            )
            if job.agent_session_id is not None or quote.capability == "browser":
                # Stateful execution must reconcile an uncertain dispatch before replay.
                unresolved_query = unresolved_query.where(
                    VendorCall.state.in_(["pending", "unknown"])
                )
            else:
                # Inference can retry an unknown response and concurrently inspect
                # identical inputs. Only a still-running predecessor blocks takeover;
                # every retry retains its own admission and counts toward the ceiling.
                unresolved_query = unresolved_query.where(
                    VendorCall.state == "pending", VendorCall.run_id != self.run_id
                )
            unresolved = await session.scalar(unresolved_query.limit(1))
            if unresolved is not None:
                raise self.stop(
                    "unresolved_call",
                    "Verify the previous identical request before dispatching again",
                )
            if job.actor_member_id is not None:
                await self.member_quota(session, job, quote)
            session.add(
                VendorCall(
                    id=call_id,
                    job_id=self.job_id,
                    run_id=self.run_id,
                    task_id=job.task_id,
                    capability=quote.capability,
                    request_sha256=quote.request_sha256,
                    provider_identity=quote.model_dump(mode="json"),
                )
            )
            await session.flush()
            if job.kind == "card_generate" and job.agent_session_id is None:
                from app.memory.retrieval import attach_call

                await attach_call(session, self, call_id, current_drafting_input.get())
        return call_id

    async def _complete_once(self, call_id: UUID, usage: ProviderUsage) -> None:
        async with self.db.transaction() as session:
            # A late receipt remains durable even after cancellation or takeover.
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
                raise ProviderFailure(
                    "Unsent calls cannot complete", code="usage_accounting_failed"
                )
            await bind_member(session, job)
            quote = CallPlan.model_validate(call.provider_identity)
            fields = (
                "capability",
                "provider",
                "version",
                "provider_config_id",
                "image_count",
            )
            if any(getattr(usage, field) != getattr(quote, field) for field in fields):
                raise ProviderFailure(
                    "Usage does not match admitted provider identity",
                    code="usage_accounting_failed",
                )
            stored_usage = usage.model_dump()
            record_id = await session.scalar(
                insert(UsageRecord)
                .values(
                    id=call_id,
                    task_id=job.task_id,
                    job_id=self.job_id,
                    run_id=self.run_id,
                    call_id=call_id,
                    **stored_usage,
                )
                .on_conflict_do_nothing(index_elements=["job_id", "run_id", "call_id"])
                .returning(UsageRecord.id)
            )
            if record_id is not None:
                record = await session.get(UsageRecord, record_id)
                assert record is not None
                call.state = "completed"
                await session.flush()
                job.result = {**job.result, "cost": await job_cost(session, self.job_id)}
            if job.kind == "card_generate":
                from app.memory.retrieval import settle_call

                await settle_call(session, self.job_id, self.run_id, call_id, "completed")

    async def complete(self, call_id: UUID, usage: ProviderUsage) -> None:
        for attempt in range(3):
            try:
                await self._complete_once(call_id, usage)
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

    async def unknown(self, call_id: UUID) -> None:
        try:
            async with self.db.transaction() as session:
                # Match completion's Task -> Job -> call lock order. Late completion is
                # allowed even when the original lease has ended.
                job = await locked_job(session, self.job_id)
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
                if job is not None and job.kind == "card_generate":
                    from app.memory.retrieval import settle_call

                    await settle_call(session, self.job_id, self.run_id, call_id, "unknown")
        except SQLAlchemyError:
            # A failed marker leaves the durable pending call in place.
            # Never replace an accounting stop with a raw pool/driver exception.
            raise self.stop(
                "usage_accounting_failed",
                "Could not persist unknown vendor outcome; no more calls allowed",
            ) from None

    async def not_sent(self, call_id: UUID) -> None:
        """Release this attempt's pending admission when no request left this process.

        The call boundary invokes this after credential preparation failed or when the
        connection never opened, so no usage exists and the admission cannot represent
        an unknown dispatch.
        """
        try:
            async with self.db.transaction() as session:
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
                    )
                )
        except SQLAlchemyError:
            raise self.stop(
                "usage_accounting_failed", "Could not release unsent call admission"
            ) from None

    async def finalize_result(self) -> None:
        """Refresh cumulative usage without publishing an obsolete attempt."""
        async with self.db.transaction() as session:
            job = await locked_job(session, self.job_id)
            if (
                job is None
                or job.run_id != self.run_id
                or job.status not in {"succeeded", "failed", "cancelled"}
            ):
                return
            job.result = {**(job.result or {}), "cost": await job_cost(session, self.job_id)}
