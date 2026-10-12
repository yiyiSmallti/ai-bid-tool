"""Configured platform operator login, model configuration and internal audit."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import ServiceError, not_found
from app.core.password_attempts import PasswordAttempts, invalid_login
from app.core.security import TokenSigner
from app.core.totp import matching_counter
from app.models.entities import PlatformAuditLog, PlatformModel, User
from app.providers.llm import HTTPExtractor, platform_llm
from app.schemas.platform_contracts import (
    PlatformModelSet,
)
from app.schemas.platform_credentials import CatalogResolveTarget, PlatformOperator
from app.services.platform_credentials import PlatformCredentialResolver

LOGIN_ACTION = "platform.login"


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
        if email not in settings.platform_admins():
            raise invalid_login()
        secret = settings.platform_totp().get(email)
        counter = matching_counter(secret, code) if secret else None
        if counter is None:
            raise invalid_login()
        # PasswordAttempts holds the account lock until the successful audit row
        # commits. Re-read and consume under that same lock, across API workers.
        last_counter = await session.scalar(
            select(func.max(PlatformAuditLog.details["totp_counter"].as_integer())).where(
                PlatformAuditLog.actor_email == email,
                PlatformAuditLog.action == LOGIN_ACTION,
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


def plain(value):
    """JSON-ready values for aggregate rows (Decimal, dates, UUIDs)."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


MODEL_FIELDS = (
    "id", "capability", "provider", "model", "base_url", "credential",
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
    rows = await session.scalars(
        select(PlatformModel)
        .where(PlatformModel.capability == "llm_extract")
        .order_by(PlatformModel.id)
    )
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
    from app.schemas.platform_contracts import PlatformModelTest
    from app.schemas.provider_contracts import ProviderTest
    from app.services import preflight as preflight_service
    from app.services import provider_configs
    from app.services.auth import ROLE_SCOPES, Identity, membership, set_actor_context
    from app.services.drafts import digest

    body = body or PlatformModelTest.model_validate({})
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
            data = await preflight_service.attach(
                session,
                {
                    "dry_run": True,
                    "model_id": model_id,
                    "test_org_id": str(body.test_org_id) if body.test_org_id else None,
                },
                command="platform model test",
                task_id=None,
                input_hash=digest({"model": model_identity(llm), "levels": names}),
                settings=settings,
                quote_sources=quotes,
                planned_calls=len(names),
            )
            return data
        assert identity is not None
        if processor is None:
            raise ServiceError(
                "provider_unavailable", "Accounted test processor is unavailable", 503, 4
            )
        await set_actor_context(session, identity)
        job = await provider_configs.submit_test(
            session, identity, ProviderTest(), llm, quick, probe_levels=names
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
