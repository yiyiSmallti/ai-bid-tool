"""A01 safety ceilings over the existing call ledger, never a second task budget."""

import math
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ServiceError
from app.models.agent import AgentJobLink, AgentPrincipal, AgentSession
from app.models.entities import Job, Task, UsageRecord, VendorCall
from app.providers.base import ProviderFailure
from app.schemas.agent_contracts import AgentCostView, AgentLimits
from app.schemas.budget_contracts import BudgetCallQuote
from app.schemas.contracts import Cost
from app.services.auth import Identity, agent_identity
from app.services.redaction import RULE_VERSION

TERMINAL = {"completed", "partial", "failed", "cancelled"}
HARD_STOPS = {
    "agent_step_limit",
    "agent_call_limit",
    "agent_time_limit",
    "agent_lifetime_limit",
    "agent_vendor_cost_limit",
    "agent_platform_cost_limit",
}


def active_used(row: AgentSession, now: datetime) -> int:
    elapsed = max(0, math.ceil((now - row.active_since).total_seconds())) if row.active_since else 0
    return row.active_seconds_used + elapsed


def remaining_seconds(row: AgentSession, now: datetime) -> float:
    limits = AgentLimits.model_validate(row.limits)
    elapsed = max(0.0, (now - row.active_since).total_seconds()) if row.active_since else 0.0
    return max(
        0.0,
        min(
            limits.max_active_seconds - row.active_seconds_used - elapsed,
            (row.expires_at - now).total_seconds(),
        ),
    )


def own_jobs(row: AgentSession):
    return select(AgentJobLink.job_id).where(
        AgentJobLink.org_id == row.org_id,
        AgentJobLink.session_id == row.id,
        AgentJobLink.owned.is_(True),
    )


async def ledger(session: AsyncSession, row: AgentSession):
    jobs = own_jobs(row)
    usages = list(
        (await session.scalars(select(UsageRecord).where(UsageRecord.job_id.in_(jobs)))).all()
    )
    calls = list(
        (
            await session.scalars(
                select(VendorCall).where(
                    VendorCall.job_id.in_(jobs), VendorCall.state != "not_sent"
                )
            )
        ).all()
    )
    return usages, calls


async def cost_view(session: AsyncSession, row: AgentSession) -> AgentCostView:
    """Each UsageRecord belongs to exactly one owned job; cached results add zero."""
    usages, calls = await ledger(session, row)
    pending = [call for call in calls if call.state in {"pending", "unknown"}]
    vendor_known = all(usage.usd is not None for usage in usages)
    holds = [BudgetCallQuote.model_validate(call.quote).vendor_usd_upper_bound for call in pending]
    charge = sum((Decimal(str(usage.charge or 0)) for usage in usages), Decimal(0))
    currency = AgentLimits.model_validate(row.limits).billing_currency
    unpriced = sum(usage.usd is None for usage in usages) + sum(value is None for value in holds)
    task_known = all(usage.task_amount is not None for usage in usages)
    vendor = sum((Decimal(str(usage.usd or 0)) for usage in usages), Decimal(0))
    return AgentCostView(
        cost=Cost(
            llm_tokens=sum(usage.tokens for usage in usages),
            ocr_pages=sum(usage.ocr_pages for usage in usages),
            usd=float(vendor) if vendor_known else None,
            basis="actual",
            charge=charge,
            billing_currency=currency,
            task_amount=sum((usage.task_amount or Decimal(0) for usage in usages), Decimal(0))
            if task_known
            else None,
            unpriced_calls=unpriced,
            unresolved_calls=len(pending),
        ),
        platform_charge=charge,
        reserved_platform_charge=sum((call.reserved_charge for call in pending), Decimal(0)),
        reserved_vendor_usd=sum((value or Decimal(0) for value in holds), Decimal(0))
        if all(value is not None for value in holds)
        else None,
        billing_currency=currency,
        unpriced_calls=unpriced,
        unresolved_calls=len(pending),
    )


async def locked_session(session: AsyncSession, session_id) -> AgentSession:
    """The task lock serializes session calls before the existing balance lock."""
    task_id = await session.scalar(
        select(AgentSession.task_id).where(AgentSession.id == session_id)
    )
    if task_id is None:
        raise ProviderFailure("Agent session is unavailable", code="agent_session_missing")
    await session.scalar(select(Task).where(Task.id == task_id).with_for_update())
    row = await session.scalar(
        select(AgentSession).where(AgentSession.id == session_id).with_for_update()
    )
    if row is None:
        raise ProviderFailure("Agent session is unavailable", code="agent_session_missing")
    return row


async def enforce(
    session: AsyncSession,
    row: AgentSession,
    settings: Settings,
    quote: BudgetCallQuote | None = None,
) -> Identity:
    """Check authority and session ceilings while Task is locked by the caller."""
    now = await session.scalar(select(func.clock_timestamp()))
    assert now is not None
    limits = AgentLimits.model_validate(row.limits)
    if row.state in TERMINAL:
        raise ProviderFailure("Agent session has stopped", code="agent_session_stopped")
    if now >= row.expires_at:
        raise ProviderFailure("Agent lifetime exhausted", code="agent_lifetime_limit")
    if remaining_seconds(row, now) <= 0:
        raise ProviderFailure("Agent active time exhausted", code="agent_time_limit")
    principal = await session.get(AgentPrincipal, row.principal_id)
    if principal is None:
        raise ProviderFailure("Agent authority unavailable", code="agent_authority_expired")
    try:
        actor = await agent_identity(session, principal)
        if not set(principal.scopes) <= actor.scopes:
            raise ServiceError("agent_authority_changed", "Delegated permissions changed", 403, 4)
    except ServiceError as exc:
        raise ProviderFailure("Agent authority needs human renewal", code=exc.code) from None
    actor.session_id = row.id
    task = await session.get(Task, row.task_id)
    if (
        task is None
        or not task.model_redaction_enabled
        or task.model_redaction_revision != row.model_snapshot.get("redaction_revision")
        or row.model_snapshot.get("redaction_rule_version") != RULE_VERSION
    ):
        raise ProviderFailure("Agent outbound masking changed", code="agent_inputs_changed")
    if limits.billing_currency != settings.billing_currency:
        raise ProviderFailure("Agent billing currency changed", code="billing_currency_mismatch")
    from app.services.confidential import task_entries

    values = sorted(
        str(entry.value.id)
        for entry in (await task_entries(session, row.task_id)).values()
        if entry.value is not None
    )
    if values != row.model_snapshot.get("confidential_value_ids", []):
        raise ProviderFailure("Registered confidential values changed", code="agent_inputs_changed")
    usages, calls = await ledger(session, row)
    if any(call.state == "unknown" for call in calls):
        raise ProviderFailure(
            "Verify unresolved vendor requests first", code="agent_request_uncertain"
        )
    vendor = sum((Decimal(str(usage.usd or 0)) for usage in usages), Decimal(0))
    charge = sum((Decimal(str(usage.charge or 0)) for usage in usages), Decimal(0))
    pending = [call for call in calls if call.state == "pending"]
    pending_quotes = [BudgetCallQuote.model_validate(call.quote) for call in pending]
    if any(usage.usd is None for usage in usages) or any(
        fixed.vendor_usd_upper_bound is None for fixed in pending_quotes
    ):
        raise ProviderFailure("Agent vendor exposure cannot be bounded", code="agent_unpriced")
    vendor += sum(
        (fixed.vendor_usd_upper_bound or Decimal(0) for fixed in pending_quotes), Decimal(0)
    )
    charge += sum((call.reserved_charge for call in pending), Decimal(0))
    if quote is not None:
        model = row.model_snapshot["model"]
        if (
            quote.provider != model["provider"]
            or quote.model != model["model"]
            or quote.version != model["adapter_version"]
            or str(quote.provider_config_id or "") != str(model.get("provider_config_id") or "")
            or quote.platform_model_id != model.get("platform_model_id")
            or quote.price_revision != f"{quote.version}:price:{model.get('model_revision') or 0}"
        ):
            raise ProviderFailure(
                "Pinned agent model or pricing changed", code="agent_model_changed"
            )
        if quote.vendor_usd_upper_bound is None or quote.unknown_reason:
            raise ProviderFailure("Agent calls require a known price bound", code="agent_unpriced")
        if len(calls) >= limits.max_vendor_calls:
            raise ProviderFailure("Agent call ceiling exhausted", code="agent_call_limit")
        vendor += quote.vendor_usd_upper_bound
        charge += quote.reserved_charge
    if vendor > limits.max_vendor_usd:
        raise ProviderFailure("Agent vendor cost ceiling exhausted", code="agent_vendor_cost_limit")
    if charge > limits.max_platform_charge:
        raise ProviderFailure(
            "Agent platform cost ceiling exhausted", code="agent_platform_cost_limit"
        )
    if row.steps_used > limits.max_steps:
        raise ProviderFailure("Agent step ceiling exhausted", code="agent_step_limit")
    return actor


async def guard_job(session: AsyncSession, job: Job, settings: Settings, quote=None):
    """Every descendant's dispatch and publication follows the same session fence."""
    if job.agent_session_id is None:
        return None
    row = await locked_session(session, job.agent_session_id)
    link = await session.scalar(
        select(AgentJobLink).where(
            AgentJobLink.session_id == row.id,
            AgentJobLink.job_id == job.id,
            AgentJobLink.owned.is_(True),
        )
    )
    if link is None or job.agent_principal_id != row.principal_id:
        raise ProviderFailure("Agent job ownership is invalid", code="agent_session_stopped")
    if row.state == "paused":
        raise ProviderFailure("Agent session is paused", code="agent_session_stopped")
    actor = await enforce(session, row, settings, quote)
    actor.step_id, actor.invocation_id = job.agent_step_id, job.invocation_id
    actor.job_id, actor.run_id = job.id, job.run_id
    actor.actor_kind = "worker"
    now = await session.scalar(select(func.clock_timestamp()))
    assert now is not None
    return actor, now + timedelta(seconds=remaining_seconds(row, now))
