from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.core.password_attempts import PasswordAttempts
from app.core.security import Secrets, token_digest
from app.models.entities import ApiToken, Membership, Org, User

SCOPES = {
    "sandbox:read",
    "sandbox:render",
    "sandbox:capture",
    "card:read",
    "card:write",
    "card:generate",
    "draft:run",
    "draft:read",
    "billing:read",
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
        # billing:redeem is deliberately absent from SCOPES: tokens can never redeem cards.
        "billing:read",
        "billing:redeem",
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

for _role, _scopes in ROLE_SCOPES.items():
    _scopes.update({"card:read", "draft:read", "sandbox:read"})
    if _role != "viewer":
        _scopes.update(
            {
                "card:write",
                "card:generate",
                "draft:run",
                "evidence:confirm",
                "sandbox:render",
                "sandbox:capture",
            }
        )


@dataclass
class Identity:
    user_id: UUID
    org_id: UUID
    scopes: set[str]
    role: str
    token_id: UUID | None = None
    actor_kind: str = "session"

    def __post_init__(self):
        if self.token_id is not None and self.actor_kind == "session":
            self.actor_kind = "token"

    def require(self, scope: str) -> None:
        if scope not in self.scopes:
            raise ServiceError("forbidden", "Permission denied", 403, 4)


async def set_actor_context(session: AsyncSession, actor: Identity) -> None:
    """Only authenticated server code supplies transaction-local decision identity."""
    await session.execute(
        text(
            "SELECT set_config('app.actor_kind', :kind, true), "
            "set_config('app.actor_user_id', :user, true), "
            "set_config('app.actor_token_id', :token, true)"
        ),
        {
            "kind": actor.actor_kind,
            "user": str(actor.user_id),
            "token": str(actor.token_id) if actor.token_id else "",
        },
    )


async def membership(session: AsyncSession, user_id: UUID, org_id: UUID) -> Membership:
    member = await session.scalar(
        select(Membership).where(
            Membership.user_id == user_id, Membership.org_id == org_id, Membership.active.is_(True)
        )
    )
    if member is None:
        raise not_found()
    # Every login, session and token request passes here, so disabling an org is immediate.
    if not await session.scalar(select(Org.active).where(Org.id == org_id)):
        raise ServiceError("org_inactive", "Organization is disabled", 403, 4)
    return member


async def login(
    attempts: PasswordAttempts, email: str, password: str, org_id: UUID, source: str | None
) -> User:
    user = await attempts.authenticate(email, password, source)
    async with attempts.db.transaction(org_id) as session:
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
