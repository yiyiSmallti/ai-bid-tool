import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.core.security import Secrets, token_digest, verify_password
from app.models.entities import ApiToken, Membership, User

SCOPES = {
    "evidence:source:read",
    "evidence:source:write",
    "task:read",
    "task:create",
    "tender:upload",
    "tender:parse",
    "req:extract",
    "job:read",
    "job:cancel",
    "resource:read",
    "resource:write",
    "task:resource",
    "certificate:read",
    "certificate:write",
    "task:certificate",
    "profile:read",
    "profile:write",
    "task:profile",
    "certificate:file:read",
    "template:read",
    "certificate:file:write",
    "template:write",
    "task:template",
}
ROLE_SCOPES = {
    "admin": {
        "evidence:source:read",
        "evidence:source:write",
        "task:read",
        "task:create",
        "tender:upload",
        "tender:parse",
        "req:extract",
        "job:read",
        "job:cancel",
        "resource:read",
        "resource:write",
        "task:resource",
        "certificate:read",
        "certificate:write",
        "task:certificate",
        "token:create",
        "certificate:file:write",
        "template:write",
        "profile:read",
        "profile:write",
        "task:profile",
        "certificate:file:read",
        "template:read",
        "task:template",
    },
    "bidder": {
        "evidence:source:read",
        "evidence:source:write",
        "task:read",
        "task:create",
        "tender:upload",
        "tender:parse",
        "req:extract",
        "job:read",
        "job:cancel",
        "resource:read",
        "task:resource",
        "certificate:read",
        "certificate:write",
        "certificate:file:write",
        "task:certificate",
        "profile:read",
        "profile:write",
        "task:profile",
        "certificate:file:read",
        "template:read",
        "task:template",
    },
    "technical": {
        "evidence:source:read",
        "evidence:source:write",
        "task:read",
        "tender:upload",
        "tender:parse",
        "req:extract",
        "job:read",
        "job:cancel",
        "resource:read",
        "resource:write",
        "task:resource",
        "certificate:read",
        "task:certificate",
        "profile:read",
        "task:profile",
        "certificate:file:read",
        "template:read",
        "task:template",
    },
    "viewer": {
        "evidence:source:read",
        "task:read",
        "job:read",
        "resource:read",
        "certificate:read",
        "profile:read",
        "certificate:file:read",
        "template:read",
    },
}


@dataclass
class Identity:
    user_id: UUID
    org_id: UUID
    scopes: set[str]
    role: str
    token_id: UUID | None = None

    def require(self, scope: str) -> None:
        if scope not in self.scopes:
            raise ServiceError("forbidden", "Permission denied", 403, 4)


async def membership(session: AsyncSession, user_id: UUID, org_id: UUID) -> Membership:
    member = await session.scalar(
        select(Membership).where(
            Membership.user_id == user_id, Membership.org_id == org_id, Membership.active.is_(True)
        )
    )
    if member is None:
        raise not_found()
    return member


async def login(session: AsyncSession, email: str, password: str, org_id: UUID) -> User:
    user = await session.scalar(
        select(User).where(User.email == email.lower().strip(), User.active.is_(True))
    )
    if user is None:
        # The same expensive password check runs for unknown users as known users.
        await asyncio.to_thread(
            verify_password,
            password,
            "pbkdf2$600000$MDAwMDAwMDAwMDAwMDAwMA==$MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
        )
        raise ServiceError("invalid_login", "Invalid credentials", 401, 4)
    # PBKDF2 is CPU-bound; running it inline would stall every request on the event loop.
    if not await asyncio.to_thread(verify_password, password, user.password_hash):
        raise ServiceError("invalid_login", "Invalid credentials", 401, 4)
    await membership(session, user.id, org_id)
    return user


async def authenticate(
    session: AsyncSession, bearer: str, org_id: UUID, crypto: Secrets
) -> Identity:
    if bearer.startswith("bid_"):
        token = await session.scalar(
            select(ApiToken).where(
                ApiToken.digest == token_digest(bearer), ApiToken.revoked.is_(False)
            )
        )
        if token is None or token.expires_at <= datetime.now(UTC):
            raise ServiceError("invalid_token", "Invalid or expired credentials", 401, 4)
        user_id = token.user_id
        token_scopes = set(token.scopes)
        token_id = token.id
    else:
        payload = crypto.open(bearer)
        if payload.get("kind") != "session":
            raise ServiceError("invalid_session", "Invalid credentials", 401, 4)
        user_id = UUID(payload["user_id"])
        token_scopes = None
        token_id = None
    user = await session.get(User, user_id)
    if user is None or not user.active:
        raise ServiceError("invalid_session", "Invalid credentials", 401, 4)
    member = await membership(session, user_id, org_id)
    role_scopes = ROLE_SCOPES[member.role]
    scopes = role_scopes if token_scopes is None else token_scopes & role_scopes & SCOPES
    return Identity(user_id, org_id, scopes, member.role, token_id)
