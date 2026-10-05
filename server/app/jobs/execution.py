"""Attempt ownership, budget admission and durable accounting for any model job."""

import asyncio
import math
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import Database
from app.models.entities import Job, OrgBalance, UsageRecord, VendorCall
from app.providers.base import ProviderFailure
from app.providers.calls import current_accounting, current_drafting_input
from app.schemas.contracts import ProviderUsage
from app.services import billing

MONEY_UNIT = Decimal("0.00000001")


async def job_cost(session: AsyncSession, job_id: UUID) -> dict:
    tokens, pages, usd, count, priced = (
        await session.execute(
            select(
                func.coalesce(func.sum(UsageRecord.tokens), 0),
                func.coalesce(func.sum(UsageRecord.ocr_pages), 0),
                func.coalesce(func.sum(UsageRecord.usd), 0),
                func.count(UsageRecord.id),
                func.count(UsageRecord.usd),
            ).where(UsageRecord.job_id == job_id)
        )
    ).one()
    return {
        "llm_tokens": tokens,
        "ocr_pages": pages,
        "usd": float(usd) if count == priced else None,
    }


class JobExecution:
    def __init__(self, settings: Settings, db: Database, org_id: UUID, job_id: UUID, run_id: UUID):
        self.settings, self.db = settings, db
        self.org_id, self.job_id, self.run_id = org_id, job_id, run_id
        self.stopped: ProviderFailure | None = None
        self.planned_calls = 0
        self.before_admit: Callable[[AsyncSession], Awaitable[None]] | None = None

    def plan(self, first_pass_calls: int) -> None:
        self.planned_calls = max(self.planned_calls, first_pass_calls)

    @property
    def call_ceiling(self) -> int:
        # A fixed ceiling alone would stop large documents part-way through their first pass;
        # scaling it with the planned batches still stops runaway halving and gap filling.
        scaled = math.ceil(self.planned_calls * self.settings.job_vendor_calls_per_batch)
        return max(self.settings.job_max_vendor_calls, scaled)

    def stop(self, code: str, message: str) -> ProviderFailure:
        # A check may publish partial coverage after an admission cap. A later
        # lease/accounting failure must still fence that publication.
        admission_stops = {
            "insufficient_balance",
            "spend_cap_reached",
            "job_charge_limit_exceeded",
            "job_call_limit_exceeded",
        }
        if self.stopped is None or (
            self.stopped.code in admission_stops and code not in admission_stops
        ):
            self.stopped = ProviderFailure(message, code=code)
        return self.stopped

    async def owned_job(self, session: AsyncSession) -> Job:
        job = await session.scalar(select(Job).where(Job.id == self.job_id).with_for_update())
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
        return job

    async def heartbeat(self) -> None:
        async with self.db.transaction(self.org_id) as session:
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

    async def admit(self, reserved_charge: Decimal, platform_billed: bool) -> UUID:
        if self.stopped is not None:
            raise self.stopped
        call_id = uuid4()
        async with self.db.transaction(self.org_id) as session:
            job = await self.owned_job(session)
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
                    ).where(VendorCall.job_id == self.job_id)
                )
            ).one()
            if count >= self.call_ceiling:
                raise self.stop("job_call_limit_exceeded", "Job vendor-call ceiling reached")
            if user_cap is not None and user_cap < self.settings.job_max_charge:
                if spent + reserved_charge > user_cap:
                    raise self.stop(
                        "spend_cap_reached", "The requested charge cap cannot cover another call"
                    )
            elif spent + reserved_charge > self.settings.job_max_charge:
                raise self.stop(
                    "job_charge_limit_exceeded", "Job charge ceiling cannot cover another call"
                )
            if platform_billed:
                # All admissions and settlements lock job first, then this org row.
                # Other jobs cannot admit against the same unreserved balance.
                balance = await session.scalar(select(OrgBalance).with_for_update())
                held = await session.scalar(
                    select(func.coalesce(func.sum(VendorCall.reserved_charge), 0)).where(
                        VendorCall.state != "completed"
                    )
                )
                assert held is not None
                if (
                    balance is None
                    or balance.balance <= 0
                    or balance.balance - held < reserved_charge
                ):
                    raise self.stop(
                        "insufficient_balance", "Available balance cannot cover another model call"
                    )
                if balance.currency != self.settings.billing_currency:
                    raise self.stop(
                        "billing_currency_mismatch",
                        "Balance currency does not match billing currency",
                    )
            # Settlement may have failed while this admission awaited row locks.
            if self.stopped is not None:
                raise self.stopped
            session.add(
                VendorCall(
                    id=call_id,
                    org_id=self.org_id,
                    job_id=self.job_id,
                    run_id=self.run_id,
                    reserved_charge=reserved_charge,
                )
            )
            await session.flush()
            if job.kind == "card_generate":
                from app.memory.retrieval import attach_call

                await attach_call(session, self, call_id, current_drafting_input.get())
        return call_id

    async def _complete_once(self, call_id: UUID, usage: ProviderUsage) -> bool:
        async with self.db.transaction(self.org_id) as session:
            # A late completed call is billable even after cancellation or takeover.
            job = await session.scalar(select(Job).where(Job.id == self.job_id).with_for_update())
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
            if usage.platform_model_id is not None and usage.charge is None:
                raise ProviderFailure("Platform call has no price", code="usage_accounting_failed")
            amount = Decimal(str(usage.charge if usage.charge is not None else 0)).quantize(
                MONEY_UNIT, rounding=ROUND_HALF_UP
            )
            record_id = await session.scalar(
                insert(UsageRecord)
                .values(
                    id=call_id,
                    org_id=self.org_id,
                    task_id=job.task_id,
                    job_id=self.job_id,
                    run_id=self.run_id,
                    call_id=call_id,
                    **usage.model_dump(),
                )
                .on_conflict_do_nothing(index_elements=["org_id", "job_id", "run_id", "call_id"])
                .returning(UsageRecord.id)
            )
            if record_id is not None:
                record = await session.get(UsageRecord, record_id)
                assert record is not None
                await billing.charge_usage(
                    session, self.org_id, record, self.settings.billing_currency
                )
                call.state, call.charge = "completed", amount
                job.result = {**job.result, "cost": await job_cost(session, self.job_id)}
            if job.kind == "card_generate":
                from app.memory.retrieval import settle_call

                await settle_call(
                    session, self.org_id, self.job_id, self.run_id, call_id, "completed"
                )
            exceeded = amount > call.reserved_charge
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
            raise self.stop(
                "call_charge_bound_exceeded",
                "Vendor usage exceeded its reserved bound; usage was retained",
            )

    async def unknown(self, call_id: UUID) -> None:
        try:
            async with self.db.transaction(self.org_id) as session:
                # Match completion's job -> call lock order. Late settlement is
                # allowed even when the original lease or membership has ended.
                await session.scalar(select(Job).where(Job.id == self.job_id).with_for_update())
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
