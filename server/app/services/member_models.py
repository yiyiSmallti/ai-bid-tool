"""A gateway member's default model, monthly company-model quota and usage.

Admission enforces the quota in `JobExecution.member_quota`; usage here reads
the same ledger. Months are calendar months in UTC.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.jobs.execution import company_tokens, month_start
from app.models.entities import UsageRecord
from app.models.members import Member, MemberQuota, MemberSetting
from app.models.provider_configs import ProviderConfig, ProviderSlot
from app.providers.configured import current_config, effective_default
from app.schemas.members import (
    DefaultModelSet,
    DefaultModelView,
    MemberQuotaSet,
    MemberQuotaView,
    MemberUsageView,
    ModelUsage,
)
from app.services.auth import Identity
from app.services.members import managed, require_member_session


async def set_quota(
    session: AsyncSession, identity: Identity, member_id: UUID, body: MemberQuotaSet
) -> MemberQuotaView:
    await managed(session, identity, member_id)
    row = await session.scalar(
        insert(MemberQuota)
        .values(member_id=member_id, monthly_company_tokens=body.monthly_company_tokens)
        .on_conflict_do_update(
            index_elements=["member_id"],
            set_={"monthly_company_tokens": body.monthly_company_tokens, "updated_at": func.now()},
        )
        .returning(MemberQuota)
    )
    assert row is not None
    return MemberQuotaView(
        member_id=member_id,
        monthly_company_tokens=row.monthly_company_tokens,
        updated_at=row.updated_at,
    )


def month_range(month: str | None) -> tuple[datetime, datetime]:
    if month is None:
        start = month_start()
    else:
        try:
            start = datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC)
        except ValueError:
            raise ServiceError("invalid_input", "month must be YYYY-MM", 400, 2) from None
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, end


async def usage(
    session: AsyncSession, identity: Identity, member_id: UUID, month: str | None
) -> tuple[MemberUsageView, list[ModelUsage]]:
    """A member reads their own usage; `member:manage` reads anyone's, without
    naming their personal models."""
    own = identity.member_id == member_id
    if not own:
        identity.require("member:manage")
    if await session.get(Member, member_id) is None:
        raise not_found()
    start, end = month_range(month)
    owner = ProviderSlot.owner_member_id
    personal = owner.is_not(None)
    shown = (
        (lambda column: column) if own else (lambda column: case((personal, None), else_=column))
    )
    columns = (
        case((personal, "personal"), else_="company").label("owner"),
        shown(ProviderConfig.slot).label("slot"),
        shown(UsageRecord.provider).label("provider"),
        shown(UsageRecord.model).label("model"),
    )
    rows = (
        await session.execute(
            select(
                *columns,
                func.count(UsageRecord.id).label("calls"),
                func.coalesce(func.sum(UsageRecord.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(UsageRecord.output_tokens), 0).label("output_tokens"),
            )
            .select_from(UsageRecord)
            .outerjoin(ProviderConfig, ProviderConfig.id == UsageRecord.provider_config_id)
            .outerjoin(ProviderSlot, ProviderSlot.slot == ProviderConfig.slot)
            .where(
                UsageRecord.member_id == member_id,
                UsageRecord.created_at >= start,
                UsageRecord.created_at < end,
                UsageRecord.capability.in_(["llm", "vision"]),
            )
            .group_by(*columns)
            .order_by(columns[0], columns[3].nulls_last(), columns[1].nulls_last())
        )
    ).all()
    items = [
        ModelUsage(
            owner=row.owner,
            slot=row.slot,
            provider=row.provider,
            model=row.model,
            calls=row.calls,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            tokens=row.input_tokens + row.output_tokens,
        )
        for row in rows
    ]
    cap = await session.scalar(
        select(MemberQuota.monthly_company_tokens).where(MemberQuota.member_id == member_id)
    )
    current = start == month_start()
    return (
        MemberUsageView(
            member_id=member_id,
            month=start.strftime("%Y-%m"),
            monthly_company_tokens=cap,
            # This month's figure is the one admission checks, so it holds unsettled calls.
            company_tokens=await company_tokens(session, member_id, start)
            if current
            else sum(item.tokens for item in items if item.owner == "company"),
        ),
        items,
    )


async def set_default_model(
    session: AsyncSession, identity: Identity, body: DefaultModelSet
) -> DefaultModelView:
    member, _ = require_member_session(identity)
    if body.slot is not None and await current_config(session, body.slot) is None:
        raise ServiceError(
            "provider_model_unavailable", "The model is not saved or was removed", 404, 2
        )
    await session.execute(
        insert(MemberSetting)
        .values(member_id=member, default_slot=body.slot)
        .on_conflict_do_update(
            index_elements=["member_id"],
            set_={"default_slot": body.slot, "updated_at": func.now()},
        )
    )
    return DefaultModelView(default_slot=body.slot, effective_slot=await effective_default(session))
