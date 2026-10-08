"""Platform operator identity, login with TOTP, audit and one-time password setup."""

import asyncio
import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import ServiceError, not_found
from app.core.password_attempts import PasswordAttempts, invalid_login
from app.core.security import TokenSigner, hash_password
from app.core.totp import matching_counter
from app.models.entities import PlatformAuditLog, PlatformCard, PlatformModel, User
from app.providers.llm import HTTPExtractor, platform_llm
from app.schemas.platform_contracts import (
    PlatformBalanceAdjust,
    PlatformCardCreate,
    PlatformModelSet,
)
from app.schemas.platform_credentials import CatalogResolveTarget, PlatformOperator
from app.services.platform_credentials import PlatformCredentialResolver

LOGIN_ACTION = "platform.login"
SETUP_SECONDS = 24 * 3600
UNUSABLE_PASSWORD = "!setup"


PlatformIdentity = PlatformOperator


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


async def login(
    attempts: PasswordAttempts,
    settings: Settings,
    crypto: TokenSigner,
    email: str,
    password: str,
    code: str,
    source: str | None,
) -> dict:
    email = email.strip().lower()

    async def consume_totp(session: AsyncSession, _user: User) -> dict:
        from app.services.operator_enrollment import factor_secret

        if email not in settings.platform_admins():
            raise invalid_login()
        secret = await factor_secret(settings, session, email)
        counter = matching_counter(secret, code) if secret else None
        if counter is None:
            raise invalid_login()
        # PasswordAttempts holds the account lock until the successful audit row
        # commits. Re-read and consume under that same lock, across API workers.
        last_counter = await session.scalar(
            select(func.max(PlatformAuditLog.details["totp_counter"].as_integer())).where(
                PlatformAuditLog.actor_email == email,
                PlatformAuditLog.action.in_((LOGIN_ACTION, "platform.operator.enroll")),
                PlatformAuditLog.outcome == "success",
            )
        )
        if last_counter is not None and counter <= last_counter:
            raise invalid_login()
        return {"totp_counter": counter}

    await attempts.authenticate(email, password, source, consume_totp)
    return {
        "session": crypto.issue(
            {"kind": "platform", "email": email, "totp": True}, settings.platform_session_seconds
        ),
        "email": email,
        "expires_in": settings.platform_session_seconds,
    }


def identify(settings: Settings, crypto: TokenSigner, bearer: str) -> PlatformIdentity:
    payload = crypto.open(bearer)
    email = payload.get("email")
    # Removing an operator from the deployment config revokes their sessions at once.
    if (
        payload.get("kind") != "platform"
        or payload.get("totp") is not True
        or email not in settings.platform_admins()
    ):
        raise ServiceError("invalid_session", "Invalid credentials", 401, 4)
    return PlatformIdentity(
        email=email, session_expires_at=datetime.fromtimestamp(payload["exp"], UTC)
    )


def fingerprint(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode()).hexdigest()[:32]


def setup_token(crypto: TokenSigner, user_id: UUID, password_hash: str) -> str:
    # Bound to the current hash, so the link stops working once a password is set.
    return crypto.issue(
        {"kind": "password-setup", "user_id": str(user_id), "fp": fingerprint(password_hash)},
        SETUP_SECONDS,
    )


async def setup_password(db: Database, crypto: TokenSigner, token: str, password: str) -> None:
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
    session: AsyncSession, crypto: TokenSigner, actor: PlatformIdentity, name: str, admin_email: str
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


def model_view(row: PlatformModel, configured: bool = False) -> dict:
    view = {field: plain(getattr(row, field)) for field in MODEL_FIELDS}
    view["default"] = row.is_default
    # Active metadata is not proof of decryption or vendor connectivity.
    view["credential_configured"] = configured
    return view


async def list_models(session: AsyncSession, resolver: PlatformCredentialResolver) -> list[dict]:
    rows = await session.scalars(select(PlatformModel).order_by(PlatformModel.id))
    views = []
    for row in rows:
        state = await resolver.readiness(
            CatalogResolveTarget(model_id=row.id, expected_model_revision=row.revision)
        )
        views.append(model_view(row, state.configured))
    return views


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
    db: Database,
    settings: Settings,
    actor: PlatformIdentity,
    model_id: str,
    transport=None,
    *,
    body=None,
    processor=None,
) -> dict:
    """Probe a fixed catalog identity through a tenant's taskless accounted job."""
    from app.models.entities import Job
    from app.providers.configured import model_identity
    from app.schemas.budget_contracts import BudgetPlatformModelTest, BudgetProviderTest
    from app.services import budget_preflight, provider_configs
    from app.services.auth import ROLE_SCOPES, Identity, membership, set_actor_context
    from app.services.drafts import digest

    body = body or BudgetPlatformModelTest.model_validate({})
    async with db.transaction() as session:
        row = await session.get(PlatformModel, model_id)
        user = await session.scalar(
            select(User).where(User.email == actor.email, User.active.is_(True))
        )
    if row is None:
        raise not_found()
    quick = settings.model_copy(update={"llm_timeout_seconds": TEST_SECONDS})
    llm = platform_llm(quick, row, transport)
    names = [level["name"] for level in row.reasoning or []] or [None]
    probe = [
        {
            "id": UUID(int=1),
            "document_id": UUID(int=2),
            "page": 1,
            "text": "Connectivity test only. The delivery package must include a user guide.",
        }
    ]
    quotes = []
    if isinstance(llm, HTTPExtractor):
        for name in names:
            level = llm.at_reasoning(name) if name else llm
            quotes.append(lambda level=level: level.quote(level.extraction_request(probe)))
    async with db.transaction(body.test_org_id) as session:
        identity = None
        if body.test_org_id is not None:
            if user is None:
                raise not_found()
            member = await membership(session, user.id, body.test_org_id)
            identity = Identity(user.id, body.test_org_id, ROLE_SCOPES[member.role], member.role)
            await provider_configs.require_access(session, identity, write=True)
        if body.dry_run:
            data = await budget_preflight.attach(
                session,
                {
                    "dry_run": True,
                    "model_id": model_id,
                    "test_org_id": str(body.test_org_id) if body.test_org_id else None,
                },
                command="platform model test",
                task_id=None,
                input_hash=digest({"model": model_identity(llm), "levels": names}),
                currency=settings.billing_currency,
                settings=settings,
                quote_sources=quotes,
                planned_calls=len(names),
                check_balance=body.test_org_id is not None,
            )
            return data
        assert identity is not None
        if processor is None:
            raise ServiceError(
                "provider_unavailable", "Accounted test processor is unavailable", 503, 4
            )
        await set_actor_context(session, identity)
        job = await provider_configs.submit_test(
            session, identity, BudgetProviderTest(), llm, quick, probe_levels=names
        )
        await session.commit()
    await processor(str(identity.org_id), str(job.id))
    async with db.transaction(identity.org_id) as session:
        saved = await session.get(Job, job.id)
        assert saved is not None
        public = {key: value for key, value in saved.result.items() if key != "submission"}
        view = {
            "model_id": model_id,
            "job_id": str(saved.id),
            "status": saved.status,
            "passed": saved.status == "succeeded",
            **public,
        }
        if saved.error:
            view["error"] = saved.error
    async with db.transaction() as session:
        audit(
            session,
            actor.email,
            "platform.model.test",
            "success" if view["passed"] else "failed",
            model_id,
            {"job_id": str(job.id), "test_org_id": str(identity.org_id)},
        )
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
    rows = list(rows)
    probe_ids = [
        row.details.get("probe_id") for row in rows if row.action == "credential.probe_start"
    ]
    finished = (
        set(
            await session.scalars(
                select(PlatformAuditLog.details["probe_id"].as_string()).where(
                    PlatformAuditLog.action == "credential.probe_finish",
                    PlatformAuditLog.details["probe_id"].as_string().in_(probe_ids),
                )
            )
        )
        if probe_ids
        else set()
    )

    def outcome(row):
        if row.action == "credential.probe_start" and row.outcome == "success":
            if row.details.get("probe_id") not in finished:
                return (
                    "interrupted"
                    if (datetime.now(UTC) - row.created_at).total_seconds() > 30
                    else "in_progress"
                )
        return row.outcome

    return [
        {
            "id": str(row.id),
            "created_at": row.created_at.isoformat(),
            "actor_email": row.actor_email,
            "action": row.action,
            "object_id": row.object_id,
            "outcome": outcome(row),
            "details": row.details,
        }
        for row in rows
    ]


async def user_orgs(
    attempts: PasswordAttempts, email: str, password: str, source: str | None
) -> list[dict]:
    """Orgs a signed-in user can enter; same password check and timing as login."""
    user = await attempts.authenticate(email, password, source)
    async with attempts.db.transaction() as session:
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
