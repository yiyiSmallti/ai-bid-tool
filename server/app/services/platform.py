"""Platform operator identity, login with TOTP, audit and one-time password setup."""

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import ServiceError, not_found
from app.core.security import Secrets, hash_password, verify_password
from app.core.totp import matching_counter
from app.models.entities import PlatformAuditLog, PlatformCard, PlatformModel, User
from app.providers.base import ProviderFailure
from app.providers.llm import HTTPExtractor, credential_value, platform_llm
from app.schemas.platform_contracts import (
    PlatformBalanceAdjust,
    PlatformCardCreate,
    PlatformModelSet,
)

LOGIN_ACTION = "platform.login"
MAX_FAILURES = 5
FAILURE_WINDOW = timedelta(minutes=15)
SETUP_SECONDS = 24 * 3600
UNUSABLE_PASSWORD = "!setup"
DUMMY_HASH = "pbkdf2$600000$MDAwMDAwMDAwMDAwMDAwMA==$MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


@dataclass
class PlatformIdentity:
    email: str


def audit(
    session: AsyncSession,
    actor: str,
    action: str,
    outcome: str,
    object_id: str | None = None,
    details: dict | None = None,
) -> None:
    session.add(
        PlatformAuditLog(
            id=uuid4(),
            actor_email=actor[:254],
            action=action,
            object_id=object_id,
            outcome=outcome,
            details=details or {},
        )
    )


async def record_failure(db: Database, email: str, reason: str) -> None:
    # Failures are committed separately because the request transaction rolls back.
    async with db.transaction() as session:
        audit(session, email, LOGIN_ACTION, "denied", details={"reason": reason})


async def login(
    db: Database, settings: Settings, crypto: Secrets, email: str, password: str, code: str
) -> dict:
    email = email.strip().lower()
    since = datetime.now(UTC) - FAILURE_WINDOW
    async with db.transaction() as session:
        failures = await session.scalar(
            select(func.count())
            .select_from(PlatformAuditLog)
            .where(
                PlatformAuditLog.actor_email == email[:254],
                PlatformAuditLog.action == LOGIN_ACTION,
                PlatformAuditLog.outcome != "success",
                PlatformAuditLog.created_at >= since,
            )
        )
        if (failures or 0) >= MAX_FAILURES:
            raise ServiceError(
                "too_many_attempts", "Too many failed sign-ins; try again later", 429, 3
            )
        user = await session.scalar(select(User).where(User.email == email, User.active.is_(True)))
        last_counter = await session.scalar(
            select(func.max(PlatformAuditLog.details["totp_counter"].as_integer())).where(
                PlatformAuditLog.actor_email == email,
                PlatformAuditLog.action == LOGIN_ACTION,
                PlatformAuditLog.outcome == "success",
            )
        )
    secret = settings.platform_totp().get(email)
    password_ok = await asyncio.to_thread(
        verify_password, password, user.password_hash if user else DUMMY_HASH
    )
    counter = matching_counter(secret, code) if secret else None
    # One generic answer for every failure, so the response reveals nothing about which check failed.
    if email not in settings.platform_admins() or user is None or not password_ok:
        await record_failure(db, email, "credentials")
        raise ServiceError("invalid_login", "Invalid credentials", 401, 4)
    if counter is None or (last_counter is not None and counter <= last_counter):
        await record_failure(db, email, "totp")
        raise ServiceError("invalid_login", "Invalid credentials", 401, 4)
    async with db.transaction() as session:
        audit(session, email, LOGIN_ACTION, "success", details={"totp_counter": counter})
    return {
        "session": crypto.issue(
            {"kind": "platform", "email": email}, settings.platform_session_seconds
        ),
        "email": email,
        "expires_in": settings.platform_session_seconds,
    }


def identify(settings: Settings, crypto: Secrets, bearer: str) -> PlatformIdentity:
    payload = crypto.open(bearer)
    email = payload.get("email")
    # Removing an operator from the deployment config revokes their sessions at once.
    if payload.get("kind") != "platform" or email not in settings.platform_admins():
        raise ServiceError("invalid_session", "Invalid credentials", 401, 4)
    return PlatformIdentity(email=email)


def fingerprint(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode()).hexdigest()[:32]


def setup_token(crypto: Secrets, user_id: UUID, password_hash: str) -> str:
    # Bound to the current hash, so the link stops working once a password is set.
    return crypto.issue(
        {"kind": "password-setup", "user_id": str(user_id), "fp": fingerprint(password_hash)},
        SETUP_SECONDS,
    )


async def setup_password(db: Database, crypto: Secrets, token: str, password: str) -> None:
    invalid = ServiceError("invalid_setup_link", "Setup link is invalid or expired", 400, 4)
    try:
        payload = crypto.open(token)
    except ServiceError:
        raise invalid from None
    if payload.get("kind") != "password-setup":
        raise invalid
    if len(password) < 12:
        raise ServiceError("weak_password", "Password must have at least 12 characters", 400, 2)
    async with db.transaction() as session:
        user = await session.scalar(
            select(User).where(User.id == UUID(payload["user_id"])).with_for_update()
        )
        if user is None or not user.active or fingerprint(user.password_hash) != payload.get("fp"):
            raise invalid
        user.password_hash = await asyncio.to_thread(hash_password, password)


def plain(value):
    """JSON-ready values for aggregate rows (Decimal, dates, UUIDs)."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


async def list_orgs(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(text("SELECT * FROM platform_org_summaries()"))).mappings()
    return [{key: plain(value) for key, value in row.items()} for row in rows]


async def create_org(
    session: AsyncSession, crypto: Secrets, actor: PlatformIdentity, name: str, admin_email: str
) -> dict:
    created = (
        await session.execute(
            text("SELECT * FROM platform_create_org(:name, :email, :hash)"),
            {"name": name, "email": admin_email, "hash": UNUSABLE_PASSWORD},
        )
    ).one()
    user = await session.get(User, created.admin_user_id)
    assert user is not None
    # A link is issued whenever the admin has never set a password, including reused accounts.
    link = None
    if user.password_hash == UNUSABLE_PASSWORD:
        link = "/app/setup-password#token=" + setup_token(crypto, user.id, user.password_hash)
    audit(
        session,
        actor.email,
        "platform.org.create",
        "success",
        str(created.new_org_id),
        {
            "admin_email": admin_email,
            "user_created": created.user_created,
            "setup_link": link is not None,
        },
    )
    return {
        "org_id": str(created.new_org_id),
        "admin_user_id": str(created.admin_user_id),
        "admin_email": admin_email,
        "user_created": created.user_created,
        "setup_url": link,
        "setup_expires_in": SETUP_SECONDS if link else None,
    }


async def set_org_active(
    session: AsyncSession, actor: PlatformIdentity, org_id: UUID, active: bool
) -> dict:
    changed = await session.scalar(
        text("SELECT platform_set_org_active(:org, :active)"), {"org": org_id, "active": active}
    )
    if not changed:
        raise not_found()
    audit(session, actor.email, "platform.org.active", "success", str(org_id), {"active": active})
    return {"org_id": str(org_id), "active": active}


MODEL_FIELDS = (
    "id", "capability", "provider", "model", "base_url", "credential",
    "vendor_input_usd_per_mtok", "vendor_output_usd_per_mtok",
    "sale_input_per_mtok", "sale_output_per_mtok",
    "reasoning", "default_reasoning",
    "enabled", "revision", "updated_by", "updated_at",
)  # fmt: skip


def model_view(row: PlatformModel) -> dict:
    view = {field: plain(getattr(row, field)) for field in MODEL_FIELDS}
    view["default"] = row.is_default
    # Only whether the deployment holds the key; the key itself never leaves the environment.
    view["credential_configured"] = credential_value(row.credential) is not None
    return view


async def list_models(session: AsyncSession) -> list[dict]:
    rows = await session.scalars(select(PlatformModel).order_by(PlatformModel.id))
    return [model_view(row) for row in rows]


async def set_model(session: AsyncSession, actor: PlatformIdentity, body: PlatformModelSet) -> dict:
    row = await session.scalar(
        select(PlatformModel).where(PlatformModel.id == body.id).with_for_update()
    )
    values = body.model_dump(exclude={"id", "default", "expected_revision"})
    if row is None:
        if body.expected_revision is not None:
            raise not_found()
        row = PlatformModel(id=body.id, revision=1, updated_by=actor.email, **values)
        session.add(row)
    else:
        if body.expected_revision != row.revision:
            raise ServiceError(
                "revision_conflict",
                "Model changed; read the current revision before updating",
                409,
                2,
            )
        for key, value in values.items():
            setattr(row, key, value)
        row.revision += 1
        row.updated_by = actor.email
        row.updated_at = datetime.now(UTC)
    if body.default:
        # Only one default per capability; the partial unique index enforces it too.
        await session.execute(
            update(PlatformModel)
            .where(PlatformModel.capability == body.capability, PlatformModel.id != body.id)
            .values(is_default=False)
        )
    row.is_default = body.default
    await session.flush()
    audit(
        session,
        actor.email,
        "platform.model.set",
        "success",
        body.id,
        {
            "revision": row.revision,
            "default": body.default,
            "enabled": body.enabled,
            "reasoning": [level.name for level in body.reasoning],
            "default_reasoning": body.default_reasoning,
        },
    )
    return model_view(row)


TEST_PAGE = "合成测试页面：投标人须具备有效的营业执照。"


TEST_SECONDS = 60


async def test_model(
    db: Database, settings: Settings, actor: PlatformIdentity, model_id: str, transport=None
) -> dict:
    """Call the model once per official reasoning level (once if it has none)."""
    async with db.transaction() as session:
        row = await session.get(PlatformModel, model_id)
    if row is None:
        raise not_found()
    # The vendor calls run outside any transaction and with a short deadline.
    quick = settings.model_copy(update={"llm_timeout_seconds": TEST_SECONDS})
    llm = platform_llm(quick, row, transport)
    chunk = {
        "id": uuid4(),
        "document_id": uuid4(),
        "page": 1,
        "text": TEST_PAGE,
        "citation_verified": True,
    }
    names = list(getattr(llm, "reasoning_levels", None) or {}) or [None]

    async def attempt(name: str | None) -> dict:
        level = llm.at_reasoning(name) if name and isinstance(llm, HTTPExtractor) else llm
        try:
            output = await level.extract([chunk], {})
        except ProviderFailure as exc:
            return {
                "reasoning": name,
                "passed": False,
                "error": {"code": exc.code, "message": str(exc)},
                "usage": exc.usage[-1].model_dump() if exc.usage else None,
            }
        return {
            "reasoning": name,
            "passed": True,
            "items": len(output.extraction.items),
            "usage": output.usage.model_dump(),
        }

    levels = await asyncio.gather(*(attempt(name) for name in names))
    passed = all(level["passed"] for level in levels)
    failed = next((level for level in levels if not level["passed"]), None)
    async with db.transaction() as session:
        audit(
            session,
            actor.email,
            "platform.model.test",
            "success" if passed else "failed",
            model_id,
            {
                "levels": [
                    {
                        "reasoning": level["reasoning"],
                        "passed": level["passed"],
                        "code": (level.get("error") or {}).get("code"),
                        "usage": level["usage"],
                    }
                    for level in levels
                ]
            },
        )
    # Top-level fields describe the default level, or the first failure.
    shown = failed or next(
        (
            level
            for level in levels
            if level["reasoning"] == getattr(llm, "default_reasoning", None)
        ),
        levels[0],
    )
    view = {"model_id": model_id, "passed": passed, "usage": shown["usage"]}
    if failed:
        view["error"] = failed["error"]
    else:
        view["items"] = shown["items"]
    if names != [None]:
        view["levels"] = levels
    return view


def parse_month(value: str) -> date:
    try:
        year, month = (int(part) for part in value.split("-"))
        return date(year, month, 1)
    except ValueError:
        raise ServiceError("invalid_month", "Months use the YYYY-MM format", 400, 2) from None


async def usage(
    session: AsyncSession, start: str | None, end: str | None
) -> tuple[dict, list[dict]]:
    today = datetime.now(UTC).date()
    first = parse_month(start) if start else today.replace(day=1)
    last = parse_month(end) if end else first
    if last < first or (last.year - first.year) * 12 + last.month - first.month > 36:
        raise ServiceError("invalid_month", "Choose a range of at most 36 months", 400, 2)
    names = {row["id"]: row["name"] for row in await list_orgs(session)}
    rows = (
        await session.execute(
            text("SELECT * FROM platform_usage_summary(:a, :b)"), {"a": first, "b": last}
        )
    ).mappings()
    items = [
        {
            **{key: plain(value) for key, value in row.items()},
            "org_name": names.get(str(row["org_id"])),
        }
        for row in rows
    ]
    totals = {
        "calls": sum(item["calls"] for item in items),
        "tokens": sum(item["tokens"] for item in items),
        "vendor_usd": round(sum(item["vendor_usd"] for item in items), 8),
        "charge": round(sum(item["charge"] for item in items), 8),
    }
    return {"from": first.isoformat()[:7], "to": last.isoformat()[:7], "totals": totals}, items


async def audit_entries(session: AsyncSession, limit: int) -> list[dict]:
    rows = await session.scalars(
        select(PlatformAuditLog).order_by(PlatformAuditLog.created_at.desc()).limit(limit)
    )
    return [
        {
            "id": str(row.id),
            "created_at": row.created_at.isoformat(),
            "actor_email": row.actor_email,
            "action": row.action,
            "object_id": row.object_id,
            "outcome": row.outcome,
            "details": row.details,
        }
        for row in rows
    ]


async def user_orgs(db: Database, email: str, password: str) -> list[dict]:
    """Orgs a signed-in user can enter; same password check and timing as login."""
    async with db.transaction() as session:
        user = await session.scalar(
            select(User).where(User.email == email.strip().lower(), User.active.is_(True))
        )
    valid = await asyncio.to_thread(
        verify_password, password, user.password_hash if user else DUMMY_HASH
    )
    if user is None or not valid:
        raise ServiceError("invalid_login", "Invalid credentials", 401, 4)
    async with db.transaction() as session:
        rows = await session.execute(text("SELECT * FROM user_org_memberships(:u)"), {"u": user.id})
        return [
            {
                "org_id": str(row.org_id),
                "name": row.name,
                "role": row.role,
                "active": row.org_active,
            }
            for row in rows
        ]


def card_view(row: PlatformCard) -> dict:
    return {
        "id": str(row.id),
        "last4": row.last4,
        "face_value": float(row.face_value),
        "currency": row.currency,
        "batch_id": str(row.batch_id),
        "note": row.note,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "status": row.status,
        "expired": bool(row.expires_at and row.expires_at <= datetime.now(UTC)),
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "redeemed_org_id": str(row.redeemed_org_id) if row.redeemed_org_id else None,
        "redeemed_at": row.redeemed_at.isoformat() if row.redeemed_at else None,
    }


async def create_cards(
    session: AsyncSession, actor: PlatformIdentity, body: PlatformCardCreate, currency: str
) -> dict:
    from app.services.billing import code_hash, generate_code

    batch = uuid4()
    cards = []
    for _ in range(body.count):
        code = generate_code()
        row = PlatformCard(
            id=uuid4(),
            code_hash=code_hash(code),
            last4=code[-4:],
            face_value=body.face_value,
            currency=currency,
            batch_id=batch,
            note=body.note,
            expires_at=body.expires_at,
            created_by=actor.email,
        )
        session.add(row)
        cards.append({"id": str(row.id), "code": code, "last4": row.last4})
    await session.flush()
    audit(
        session,
        actor.email,
        "platform.card.create",
        "success",
        str(batch),
        {"count": body.count, "face_value": body.face_value, "currency": currency},
    )
    # The only time codes leave the server; the database keeps hashes.
    return {
        "batch_id": str(batch),
        "count": body.count,
        "face_value": body.face_value,
        "currency": currency,
        "expires_at": body.expires_at.isoformat() if body.expires_at else None,
        "note": body.note,
        "cards": cards,
    }


async def list_cards(
    session: AsyncSession, batch_id: UUID | None, status: str | None, limit: int
) -> list[dict]:
    query = select(PlatformCard).order_by(PlatformCard.created_at.desc(), PlatformCard.id)
    if batch_id is not None:
        query = query.where(PlatformCard.batch_id == batch_id)
    if status is not None:
        query = query.where(PlatformCard.status == status)
    return [card_view(row) for row in await session.scalars(query.limit(limit))]


async def void_card(session: AsyncSession, actor: PlatformIdentity, card_id: UUID) -> dict:
    row = await session.scalar(
        select(PlatformCard).where(PlatformCard.id == card_id).with_for_update()
    )
    if row is None:
        raise not_found()
    if row.status != "active":
        raise ServiceError("card_not_active", "Only an unused card can be voided", 409, 2)
    row.status = "void"
    await session.flush()
    audit(session, actor.email, "platform.card.void", "success", str(card_id), {})
    return card_view(row)


async def adjust_balance(
    session: AsyncSession,
    actor: PlatformIdentity,
    org_id: UUID,
    body: PlatformBalanceAdjust,
    currency: str,
) -> dict:
    try:
        async with session.begin_nested():
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM platform_adjust_balance(:org, :mode, CAST(:amount AS numeric), :reason, :actor, :currency)"
                    ),
                    {
                        "org": org_id,
                        "mode": body.mode,
                        "amount": body.amount,
                        "reason": body.reason,
                        "actor": actor.email,
                        "currency": currency,
                    },
                )
            ).first()
    except DBAPIError:
        raise ServiceError(
            "currency_mismatch", "The org balance uses another currency", 409, 4
        ) from None
    if row is None:
        raise not_found()
    data = {
        "org_id": str(org_id),
        "mode": body.mode,
        "delta": float(row.delta),
        "balance": float(row.balance),
        "currency": currency,
    }
    audit(
        session,
        actor.email,
        "platform.org.balance",
        "success",
        str(org_id),
        {**data, "reason": body.reason, "amount": body.amount},
    )
    return data
