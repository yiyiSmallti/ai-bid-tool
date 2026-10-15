"""Gateway member accounts, sign-in sessions and passwords.

Initial and reset passwords are returned once and stored only as PBKDF2
hashes. Every password change, reset or disable revokes sessions in the same
transaction, and `authenticate` reloads the session row on every request, so
the change takes effect on the next request.
"""

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import ServiceError, not_found
from app.core.password_attempts import PasswordAttempts
from app.core.security import TokenSigner
from app.models.members import Member, MemberGrant, MemberSession
from app.schemas.members import (
    MemberCreate,
    MemberPassword,
    MemberSessionView,
    MemberSignIn,
    MemberUpdate,
    MemberView,
    PasswordChange,
    SessionView,
)
from app.services.auth import Identity, member_grants


def member_required() -> ServiceError:
    return ServiceError("member_session_required", "A gateway member session is required", 403, 4)


def require_member_session(identity: Identity) -> tuple[UUID, UUID]:
    if identity.member_id is None or identity.member_session_id is None:
        raise member_required()
    return identity.member_id, identity.member_session_id


async def view(session: AsyncSession, member: Member) -> MemberView:
    return MemberView.model_validate(
        {
            "id": member.id,
            "login": member.login,
            "display_name": member.display_name,
            "role": member.role,
            "grants": await member_grants(session, member.id),
            "status": member.status,
        }
    )


def check_length(password: str, settings: Settings) -> None:
    if len(password) < settings.password_min_length:
        raise ServiceError(
            "password_too_short",
            f"A password needs at least {settings.password_min_length} characters",
            422,
            2,
        )


def initial_password(settings: Settings) -> str:
    # token_urlsafe(n) yields at least n characters.
    return secrets.token_urlsafe(max(18, settings.password_min_length))


async def revoke_sessions(
    session: AsyncSession, member_id: UUID, *, keep: UUID | None = None
) -> int:
    statement = (
        update(MemberSession)
        .where(MemberSession.member_id == member_id, MemberSession.revoked_at.is_(None))
        .values(revoked_at=func.now())
        .execution_options(synchronize_session=False)
    )
    if keep is not None:
        statement = statement.where(MemberSession.id != keep)
    return (await session.execute(statement)).rowcount  # type: ignore[attr-defined]


async def sign_in(
    db: Database,
    attempts: PasswordAttempts,
    settings: Settings,
    crypto: TokenSigner,
    body: MemberSignIn,
    source: str | None,
) -> SessionView:
    member = await attempts.sign_in(body.login, body.password.get_secret_value(), source)
    seconds = settings.session_hours * 3600
    async with db.transaction() as session:
        row = MemberSession(
            id=uuid4(),
            member_id=member.id,
            expires_at=datetime.now(UTC) + timedelta(seconds=seconds),
            client_label=body.client_label,
        )
        session.add(row)
        await session.flush()
        member_view = await view(session, member)
    token = crypto.issue({"kind": "session", "sid": str(row.id), "mid": str(member.id)}, seconds)
    return SessionView(
        session=token,
        expires_in=seconds,
        member=member_view,
        must_change_password=member.must_change_password,
    )


async def sign_out(session: AsyncSession, identity: Identity) -> bool:
    if identity.member_session_id is None:
        # A personal session token is stateless; it simply expires.
        return False
    result = await session.execute(
        update(MemberSession)
        .where(MemberSession.id == identity.member_session_id, MemberSession.revoked_at.is_(None))
        .values(revoked_at=func.now())
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1  # type: ignore[attr-defined]


async def change_password(
    session: AsyncSession,
    attempts: PasswordAttempts,
    settings: Settings,
    identity: Identity,
    body: PasswordChange,
) -> int:
    member_id, session_id = require_member_session(identity)
    member = await session.get(Member, member_id, with_for_update=True, populate_existing=True)
    if member is None:
        raise member_required()
    if not await attempts.verify(body.current.get_secret_value(), member.password_hash):
        raise ServiceError("invalid_login", "The current password is wrong", 401, 4)
    new = body.new.get_secret_value()
    check_length(new, settings)
    if new == body.current.get_secret_value():
        raise ServiceError("password_unchanged", "Choose a new password", 422, 2)
    member.password_hash = await attempts.hash(new)
    member.must_change_password = False
    member.password_changed_at = datetime.now(UTC)
    return await revoke_sessions(session, member_id, keep=session_id)


async def own_sessions(session: AsyncSession, identity: Identity) -> list[MemberSessionView]:
    member_id, current = require_member_session(identity)
    rows = await session.scalars(
        select(MemberSession)
        .where(
            MemberSession.member_id == member_id,
            MemberSession.revoked_at.is_(None),
            MemberSession.expires_at > func.now(),
        )
        .order_by(MemberSession.created_at.desc())
    )
    return [
        MemberSessionView(
            id=row.id,
            created_at=row.created_at,
            expires_at=row.expires_at,
            last_seen_at=row.last_seen_at,
            client_label=row.client_label,
            current=row.id == current,
        )
        for row in rows
    ]


async def revoke_own_session(session: AsyncSession, identity: Identity, session_id: UUID) -> None:
    member_id, _ = require_member_session(identity)
    result = await session.execute(
        update(MemberSession)
        .where(
            MemberSession.id == session_id,
            MemberSession.member_id == member_id,
            MemberSession.revoked_at.is_(None),
        )
        .values(revoked_at=func.now())
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        raise not_found()


async def list_members(session: AsyncSession, identity: Identity) -> list[MemberView]:
    identity.require("member:manage")
    rows = await session.scalars(select(Member).order_by(func.lower(Member.login)))
    return [await view(session, row) for row in rows]


async def set_grants(session: AsyncSession, member_id: UUID, grants: list[str]) -> None:
    await session.execute(delete(MemberGrant).where(MemberGrant.member_id == member_id))
    session.add_all(MemberGrant(member_id=member_id, grant=grant) for grant in sorted(set(grants)))


async def guarded_flush(session: AsyncSession) -> None:
    """Translate the database rules on members into their error codes."""
    try:
        await session.flush()
    except DBAPIError as error:
        # The last-admin trigger raises check_violation, an IntegrityError too.
        if "member_login_unique" in str(error.orig):
            raise ServiceError(
                "member_login_taken", "That login is already in use", 409, 2
            ) from None
        if "last_admin" in str(error.orig):
            raise ServiceError(
                "last_admin", "At least one active administrator is required", 409, 2
            ) from None
        raise


async def create_member(
    session: AsyncSession,
    attempts: PasswordAttempts,
    settings: Settings,
    body: MemberCreate,
    identity: Identity | None = None,
) -> MemberPassword:
    """Create a member with a one-time password. `identity` is None only for the
    gateway host's bootstrap command."""
    if identity is not None:
        identity.require("member:manage")
    password = initial_password(settings)
    member = Member(
        id=uuid4(),
        login=body.login,
        display_name=body.display_name.strip(),
        role=body.role,
        password_hash=await attempts.hash(password),
        must_change_password=True,
        status="active",
    )
    session.add(member)
    await guarded_flush(session)
    await set_grants(session, member.id, list(body.grants))
    await session.flush()
    return MemberPassword(member=await view(session, member), initial_password=password)


async def managed(session: AsyncSession, identity: Identity, member_id: UUID) -> Member:
    identity.require("member:manage")
    member = await session.get(Member, member_id, with_for_update=True, populate_existing=True)
    if member is None:
        raise not_found()
    return member


async def update_member(
    session: AsyncSession, identity: Identity, member_id: UUID, body: MemberUpdate
) -> MemberView:
    member = await managed(session, identity, member_id)
    if body.display_name is not None:
        member.display_name = body.display_name.strip()
    if body.role is not None:
        member.role = body.role
    if body.status is not None and body.status != member.status:
        member.status = body.status
        member.disabled_at = datetime.now(UTC) if body.status == "disabled" else None
    await guarded_flush(session)
    if member.status == "disabled":
        await revoke_sessions(session, member.id)
    if body.grants is not None:
        await set_grants(session, member.id, list(body.grants))
        await session.flush()
    return await view(session, member)


async def reset_password(
    session: AsyncSession,
    attempts: PasswordAttempts,
    settings: Settings,
    identity: Identity,
    member_id: UUID,
) -> MemberPassword:
    member = await managed(session, identity, member_id)
    password = initial_password(settings)
    member.password_hash = await attempts.hash(password)
    member.must_change_password = True
    member.password_changed_at = datetime.now(UTC)
    await session.flush()
    await revoke_sessions(session, member.id)
    return MemberPassword(member=await view(session, member), initial_password=password)


async def revoke_member_sessions(session: AsyncSession, identity: Identity, member_id: UUID) -> int:
    member = await managed(session, identity, member_id)
    return await revoke_sessions(session, member.id)
