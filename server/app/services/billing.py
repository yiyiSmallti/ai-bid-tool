"""Prepaid balances: funds checks, usage deduction, card codes and redemption."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import Database
from app.core.errors import ServiceError
from app.models.entities import AuditLog, BalanceEntry, OrgBalance, UsageRecord, User, VendorCall
from app.services import platform
from app.services.auth import Identity

# Base32 without I, O, 0 and 1, so codes survive being read aloud or retyped.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 16
REDEEM_ACTION = "billing.redeem"
MAX_REDEEM_FAILURES = 10
REDEEM_WINDOW = timedelta(hours=1)


def generate_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    return "-".join(raw[i : i + 4] for i in range(0, CODE_LENGTH, 4))


def normalize_code(value: str) -> str | None:
    raw = "".join(value.split()).replace("-", "").upper()
    if len(raw) != CODE_LENGTH or any(char not in CODE_ALPHABET for char in raw):
        return None
    return "-".join(raw[i : i + 4] for i in range(0, CODE_LENGTH, 4))


def code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


async def current(session: AsyncSession, currency: str) -> Decimal:
    row = await session.scalar(select(OrgBalance))
    return Decimal(row.balance) if row else Decimal(0)


async def require_funds(session: AsyncSession, currency: str) -> None:
    # Submission is advisory; JobExecution serializes the priced admission itself.
    held = await session.scalar(
        select(func.coalesce(func.sum(VendorCall.reserved_charge), 0)).where(
            VendorCall.state != "completed"
        )
    )
    assert held is not None
    if await current(session, currency) - held <= 0:
        raise ServiceError(
            "insufficient_balance", "Balance is used up; redeem a recharge card first", 402, 4
        )


async def charge_usage(
    session: AsyncSession, org_id: UUID, record: UsageRecord, currency: str
) -> None:
    amount = Decimal(str(record.charge or 0))
    if record.platform_model_id is None or amount <= 0:
        return
    await session.execute(
        insert(OrgBalance)
        .values(org_id=org_id, currency=currency, balance=0)
        .on_conflict_do_nothing(index_elements=["org_id"])
    )
    balance = await session.scalar(select(OrgBalance).with_for_update())
    assert balance is not None
    # JobExecution reserved this call before sending it; settlement retains the full
    # reported amount even if a vendor violates the requested token bound.
    balance.balance = Decimal(balance.balance) - amount
    balance.updated_at = datetime.now(UTC)
    session.add(
        BalanceEntry(
            org_id=org_id,
            kind="usage",
            currency=currency,
            amount=-amount,
            balance_after=balance.balance,
            usage_record_id=record.id,
            actor="system",
        )
    )


async def overview(
    session: AsyncSession, currency: str, limit: int = 100
) -> tuple[dict, list[dict]]:
    entries = await session.scalars(
        select(BalanceEntry).order_by(BalanceEntry.created_at.desc(), BalanceEntry.id).limit(limit)
    )
    items = [
        {
            "id": str(row.id),
            "created_at": row.created_at.isoformat(),
            "kind": row.kind,
            "amount": float(row.amount),
            "balance_after": float(row.balance_after),
            "currency": row.currency,
            "reason": row.reason,
        }
        for row in entries
    ]
    return {"currency": currency, "balance": float(await current(session, currency))}, items


async def record_failure(db: Database, actor: Identity) -> None:
    # Committed separately: the request transaction rolls back on the error.
    async with db.transaction(actor.org_id) as session:
        session.add(
            AuditLog(
                org_id=actor.org_id,
                actor_user_id=actor.user_id,
                actor_token_id=actor.token_id,
                action=REDEEM_ACTION,
                object_id=actor.org_id,
                details={"outcome": "failed"},
            )
        )


async def redeem(db: Database, actor: Identity, raw_code: str, currency: str) -> dict:
    actor.require("billing:redeem")
    since = datetime.now(UTC) - REDEEM_WINDOW
    async with db.transaction(actor.org_id) as session:
        failures = await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.action == REDEEM_ACTION,
                AuditLog.details["outcome"].astext == "failed",
                AuditLog.created_at >= since,
            )
        )
    if (failures or 0) >= MAX_REDEEM_FAILURES:
        raise ServiceError("too_many_attempts", "Too many failed redemptions; try later", 429, 3)
    invalid = ServiceError("invalid_card", "Card is invalid, used, void or expired", 400, 4)
    code = normalize_code(raw_code)
    if code is None:
        await record_failure(db, actor)
        raise invalid
    async with db.transaction(actor.org_id) as session:
        row = (
            await session.execute(
                text("SELECT * FROM redeem_card(:hash, :org, :user, :currency)"),
                {
                    "hash": code_hash(code),
                    "org": actor.org_id,
                    "user": actor.user_id,
                    "currency": currency,
                },
            )
        ).first()
        if row is not None:
            user = await session.get(User, actor.user_id)
            details = {
                "outcome": "success",
                "amount": float(row.amount),
                "balance": float(row.balance),
            }
            session.add(
                AuditLog(
                    org_id=actor.org_id,
                    actor_user_id=actor.user_id,
                    actor_token_id=actor.token_id,
                    action=REDEEM_ACTION,
                    object_id=row.card_id,
                    details=details,
                )
            )
            platform.audit(
                session,
                user.email if user else str(actor.user_id),
                REDEEM_ACTION,
                "success",
                str(row.card_id),
                {"org_id": str(actor.org_id), "amount": float(row.amount)},
            )
    if row is None:
        await record_failure(db, actor)
        raise invalid
    return {
        "card_id": str(row.card_id),
        "amount": float(row.amount),
        "balance": float(row.balance),
        "currency": currency,
    }


async def verify_currency(db: Database, currency: str) -> None:
    """Refuse to start when stored balances use another currency than the configured one."""
    async with db.transaction() as session:
        mismatched = await session.scalar(
            text(
                "SELECT count(*) FROM platform_org_summaries() WHERE currency IS NOT NULL AND currency <> :c"
            ),
            {"c": currency},
        )
    if mismatched:
        raise RuntimeError(
            f"{mismatched} org balances use another currency than BID_BILLING_CURRENCY={currency}"
        )
