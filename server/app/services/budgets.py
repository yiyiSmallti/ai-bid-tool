"""Tenant budget views and human-only revision changes; no provider dispatch."""

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import func, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ServiceError, not_found
from app.models.entities import (
    AuditLog,
    OrgBalance,
    OrgBalanceNotice,
    Task,
    TaskBudgetRevision,
    UsageRecord,
    VendorCall,
)
from app.schemas.budget_contracts import (
    BudgetHistoryData,
    LowBalanceNoticesData,
    LowBalanceNoticeView,
    LowBalancePolicyData,
    LowBalancePolicySet,
    LowBalancePolicyView,
    TaskBudgetData,
    TaskBudgetRevisionView,
    TaskBudgetSet,
    TaskBudgetView,
)
from app.services.auth import Identity, membership, set_actor_context
from app.services.task_authorization import task_authorized

ZERO = Decimal(0)


def currency_of(settings_or_currency: Settings | str) -> str:
    return (
        settings_or_currency
        if isinstance(settings_or_currency, str)
        else settings_or_currency.billing_currency
    )


def require_human(identity: Identity, scope: str, roles: set[str]) -> None:
    identity.require(scope)
    if (
        identity.actor_kind != "session"
        or identity.token_id is not None
        or identity.role not in roles
    ):
        raise ServiceError(
            "forbidden", "Only an authorized human session may change budgets", 403, 4
        )


async def _human(session: AsyncSession, identity: Identity, scope: str, roles: set[str]) -> None:
    require_human(identity, scope, roles)
    member = await membership(session, identity.user_id, identity.org_id)
    if member.role not in roles:
        raise ServiceError("forbidden", "Current membership cannot change this budget", 403, 4)
    await set_actor_context(session, identity)


async def _task(session: AsyncSession, task_id: UUID, *, lock: bool = False) -> Task:
    query = select(Task).where(Task.id == task_id)
    if lock:
        query = query.with_for_update()
    task = await session.scalar(query)
    if task is None:
        raise not_found()
    return task


async def view(
    session: AsyncSession, task: Task, settings_or_currency: Settings | str = "USD"
) -> TaskBudgetView:
    """Read settled liability once; unresolved calls retain their admission exposure."""
    spent, unknown_usage, wrong_currency = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(UsageRecord.task_amount).filter(
                        UsageRecord.billing_currency == task.budget_currency
                    ),
                    0,
                ),
                func.count().filter(UsageRecord.task_amount.is_(None)),
                func.count().filter(
                    UsageRecord.task_amount > 0,
                    UsageRecord.billing_currency != task.budget_currency,
                ),
            ).where(UsageRecord.org_id == task.org_id, UsageRecord.task_id == task.id)
        )
    ).one()
    reserved, unknown_holds, unresolved, wrong_hold_currency = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(VendorCall.reserved_task_amount).filter(
                        VendorCall.currency == task.budget_currency
                    ),
                    0,
                ),
                func.count().filter(VendorCall.reserved_task_amount.is_(None)),
                func.count(),
                func.count().filter(
                    VendorCall.reserved_task_amount > 0, VendorCall.currency != task.budget_currency
                ),
            ).where(
                VendorCall.org_id == task.org_id,
                VendorCall.task_id == task.id,
                VendorCall.state.in_(("pending", "unknown")),
            )
        )
    ).one()
    unpriced = int(unknown_usage + unknown_holds)
    complete = not (unknown_usage or wrong_currency or wrong_hold_currency)
    state = cast(Literal["active", "currency_review_required"], task.budget_state)
    if task.budget_currency != currency_of(settings_or_currency):
        state = "currency_review_required"
    spent, reserved = Decimal(spent), Decimal(reserved)
    available = (
        Decimal(task.budget_limit) - spent - reserved
        if task.budget_limit is not None and state == "active" and complete and not unpriced
        else None
    )
    return TaskBudgetView(
        org_id=task.org_id,
        task_id=task.id,
        revision=task.budget_revision,
        limit=task.budget_limit,
        currency=task.budget_currency,
        state=state,
        spent=spent,
        reserved=reserved,
        available=available,
        unpriced_calls=unpriced,
        unresolved_calls=unresolved,
        history_complete=complete,
        as_of=datetime.now(UTC),
    )


@task_authorized("task:read")
async def show(
    session: AsyncSession, identity: Identity, task_id: UUID, currency: str = "USD"
) -> TaskBudgetData:
    task = await _task(session, task_id)
    identity.require("task:read")
    return TaskBudgetData(budget=await view(session, task, currency))


@task_authorized("task:budget:write", write=True)
async def set_budget(
    session: AsyncSession,
    identity: Identity,
    task_id: UUID,
    body: TaskBudgetSet,
    currency: str = "USD",
) -> TaskBudgetData:
    task = await _task(session, task_id, lock=True)
    await _human(session, identity, "task:budget:write", {"admin", "bidder"})
    if body.currency != currency:
        raise ServiceError(
            "billing_currency_mismatch", "Budget currency must match billing currency", 400, 2
        )
    if task.budget_revision != body.expected_revision:
        raise ServiceError(
            "budget_revision_conflict", "Budget revision changed; read it again", 409, 2
        )
    current = await view(session, task, currency)
    if body.limit is not None:
        mismatched_usage = await session.scalar(
            select(UsageRecord.id)
            .where(
                UsageRecord.task_id == task.id,
                UsageRecord.task_amount > 0,
                UsageRecord.billing_currency != body.currency,
            )
            .limit(1)
        )
        mismatched_hold = await session.scalar(
            select(VendorCall.id)
            .where(
                VendorCall.task_id == task.id,
                VendorCall.state.in_(("pending", "unknown")),
                VendorCall.reserved_task_amount > 0,
                VendorCall.currency != body.currency,
            )
            .limit(1)
        )
        if not current.history_complete or current.unpriced_calls:
            raise ServiceError(
                "task_budget_unpriced", "Resolve unknown historical liability first", 409, 2
            )
        if mismatched_usage is not None or mismatched_hold is not None:
            raise ServiceError(
                "task_budget_unpriced",
                "Historical liability requires currency reconciliation",
                409,
                2,
            )
        if body.limit < current.spent + current.reserved:
            raise ServiceError(
                "budget_below_exposure",
                "Budget cannot be below spent and reserved liability",
                409,
                2,
            )
    reason_hash = hashlib.sha256(body.reason.encode()).hexdigest()
    revision = task.budget_revision + 1
    session.add(
        TaskBudgetRevision(
            org_id=identity.org_id,
            task_id=task.id,
            revision=revision,
            limit=body.limit,
            currency=body.currency,
            state="active",
            actor_user_id=identity.user_id,
            origin="human_update",
            reason_sha256=reason_hash,
        )
    )
    task.budget_limit, task.budget_currency = body.limit, body.currency
    task.budget_revision, task.budget_state = revision, "active"
    # The legacy value is display-only; never convert a non-USD limit to USD.
    task.budget_usd = body.limit if body.currency == "USD" else None
    session.add(
        AuditLog(
            org_id=identity.org_id,
            actor_user_id=identity.user_id,
            action="task.budget.changed",
            object_id=task.id,
            details={"revision": revision, "currency": body.currency, "reason_sha256": reason_hash},
        )
    )
    await session.flush()
    return TaskBudgetData(budget=await view(session, task, currency))


@task_authorized("task:read")
async def history(
    session: AsyncSession,
    identity: Identity,
    task_id: UUID,
    before_revision: int | None = None,
    limit: int = 100,
) -> tuple[BudgetHistoryData, list[TaskBudgetRevisionView]]:
    await _task(session, task_id)
    identity.require("task:read")
    query = select(TaskBudgetRevision).where(TaskBudgetRevision.task_id == task_id)
    if before_revision is not None:
        query = query.where(TaskBudgetRevision.revision < before_revision)
    rows = (
        await session.scalars(query.order_by(TaskBudgetRevision.revision.desc()).limit(limit + 1))
    ).all()
    more = len(rows) > limit
    items = [
        TaskBudgetRevisionView.model_validate(row, from_attributes=True) for row in rows[:limit]
    ]
    return BudgetHistoryData(
        task_id=task_id, next_before_revision=items[-1].revision if more else None
    ), items


async def available_balance(session: AsyncSession, balance: OrgBalance | None) -> Decimal:
    held = await session.scalar(
        select(func.coalesce(func.sum(VendorCall.reserved_charge), 0)).where(
            VendorCall.state.in_(("pending", "unknown")),
        )
    )
    return Decimal(balance.balance) - Decimal(held or 0) if balance else -Decimal(held or 0)


async def policy_show(
    session: AsyncSession, identity: Identity, currency: str = "USD"
) -> LowBalancePolicyData:
    identity.require("billing:read")
    balance = await session.get(OrgBalance, identity.org_id)
    available = await available_balance(session, balance)
    threshold = balance.low_balance_threshold if balance else ZERO
    return LowBalancePolicyData(
        policy=LowBalancePolicyView(
            org_id=identity.org_id,
            threshold=threshold,
            currency=balance.currency if balance else currency,
            revision=balance.alert_revision if balance else 1,
            available_balance=available,
            low=threshold is not None and available <= threshold,
            cycle=balance.alert_cycle if balance else 0,
        )
    )


async def policy_set(
    session: AsyncSession, identity: Identity, body: LowBalancePolicySet, currency: str = "USD"
) -> LowBalancePolicyData:
    await _human(session, identity, "billing:alert:write", {"admin"})
    if body.currency != currency:
        raise ServiceError(
            "billing_currency_mismatch", "Alert currency must match billing currency", 400, 2
        )
    await session.execute(
        insert(OrgBalance)
        .values(org_id=identity.org_id, currency=currency, balance=0)
        .on_conflict_do_nothing(index_elements=["org_id"])
    )
    balance = await session.scalar(
        select(OrgBalance).where(OrgBalance.org_id == identity.org_id).with_for_update()
    )
    assert balance is not None
    if body.expected_revision != balance.alert_revision:
        raise ServiceError(
            "alert_revision_conflict", "Alert revision changed; read it again", 409, 2
        )
    balance.low_balance_threshold = body.threshold
    balance.alert_revision += 1
    session.add(
        AuditLog(
            org_id=identity.org_id,
            actor_user_id=identity.user_id,
            action="billing.low_balance_policy.changed",
            object_id=identity.org_id,
            details={"revision": balance.alert_revision, "currency": currency},
        )
    )
    await session.flush()
    await session.execute(
        text(
            "SET CONSTRAINTS balance_alert_initial, balance_alert_final, balance_alert_reservation IMMEDIATE"
        )
    )
    await session.execute(
        text(
            "SET CONSTRAINTS balance_alert_initial, balance_alert_final, balance_alert_reservation DEFERRED"
        )
    )
    await session.refresh(balance)
    return await policy_show(session, identity, currency)


async def notices(
    session: AsyncSession, identity: Identity, before: UUID | None = None, limit: int = 100
) -> tuple[LowBalanceNoticesData, list[LowBalanceNoticeView]]:
    identity.require("billing:read")
    query = select(OrgBalanceNotice)
    if before is not None:
        cursor = await session.get(OrgBalanceNotice, before)
        if cursor is None:
            raise not_found()
        query = query.where(
            tuple_(OrgBalanceNotice.created_at, OrgBalanceNotice.id)
            < (cursor.created_at, cursor.id)
        )
    rows = (
        await session.scalars(
            query.order_by(OrgBalanceNotice.created_at.desc(), OrgBalanceNotice.id.desc()).limit(
                limit + 1
            )
        )
    ).all()
    more = len(rows) > limit
    items = [LowBalanceNoticeView.model_validate(row, from_attributes=True) for row in rows[:limit]]
    return LowBalanceNoticesData(next_before=items[-1].id if more else None), items
