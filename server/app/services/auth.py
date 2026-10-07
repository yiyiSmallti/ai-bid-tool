import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.core.password_attempts import PasswordAttempts, invalid_login
from app.core.security import TokenSigner, token_digest
from app.models.entities import ApiToken, Membership, Org, User

AGENT_SCOPES = {
    "task:read",
    "job:read",
    "card:read",
    "card:generate",
    "draft:read",
    "draft:run",
    "resource:read",
    "certificate:read",
    "certificate:file:read",
    "profile:read",
    "evidence:source:read",
}

SCOPES = {
    "provider:read",
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
        "provider:write",
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
        "export",
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

SCOPES.update({"screenshot:read", "screenshot:write"})
# confidential:write and confidential:reveal are deliberately absent from SCOPES:
# tokens and agents can name a confidential field but never set or read its value.
SCOPES.update({"confidential:read", "check:read", "check:run"})
# Rubric review is a session-only capability, including admin classification.
SCOPES.update({"score:read", "score:run", "score:rubric:generate"})

SCOPES.update({"memory:read", "memory:write", "memory:retrieve", "memory:candidate:run"})

for _role, _scopes in ROLE_SCOPES.items():
    _scopes.add("template:file:read")
    _scopes.add("agent:read")
    if _role != "viewer":
        _scopes.update({"agent:run", "agent:cancel"})
    _scopes.add("card:comment")
    if _role in {"admin", "bidder"}:
        _scopes.update({"task:members:write", "task:archive", "card:assign", "task:review-policy"})
        _scopes.update({"certificate:lifecycle", "profile:lifecycle"})
    if _role in {"bidder", "technical"}:
        _scopes.add("card:cosign")
    _scopes.update({"memory:read", "memory:retrieve"})
    if _role != "viewer":
        _scopes.update({"memory:write", "memory:candidate:run"})
    if _role == "admin":
        _scopes.update(
            {"memory:approve", "memory:manage", "memory:eval:read", "memory:eval:review"}
        )
    _scopes.update({"provider:read", "screenshot:read"})
    if _role != "viewer":
        _scopes.update({"screenshot:write", "screenshot:ingest", "evidence:annotate"})
    _scopes.update({"card:read", "draft:read", "sandbox:read", "confidential:read", "check:read"})
    _scopes.add("score:read")
    if _role != "viewer":
        _scopes.add("score:rubric:review")
    if _role == "admin":
        _scopes.add("billing:alert:write")
    if _role in {"admin", "bidder"}:
        _scopes.add("task:budget:write")
        _scopes.update({"confidential:write", "confidential:reveal"})
    if _role in {"bidder", "technical"}:
        _scopes.add("check:decide")
    if _role != "viewer":
        # Advisory jobs, like check:run; admins issue the tokens that may carry them.
        _scopes.update({"score:run", "score:rubric:generate"})
    if _role != "viewer":
        _scopes.update(
            {
                "card:write",
                "card:generate",
                "draft:run",
                "check:run",
                "evidence:confirm",
                "sandbox:render",
                "sandbox:capture",
            }
        )


for _role in ("admin", "bidder", "technical"):
    ROLE_SCOPES[_role].update({"req:confirm", "req:manual"})


HUMAN_ONLY_SCOPES = {
    "template:file:read",
    "evidence:annotate",
    "certificate:lifecycle",
    "profile:lifecycle",
    "req:confirm",
    "req:manual",
    "agent:read",
    "agent:run",
    "agent:cancel",
    "task:members:write",
    "task:archive",
    "card:assign",
    "card:comment",
    "task:review-policy",
    "card:cosign",
    "evidence:confirm",
    "export",
    "confidential:write",
    "confidential:reveal",
    "provider:write",
    "token:create",
    "screenshot:ingest",
    "check:decide",
    "score:rubric:review",
    "billing:redeem",
    "task:budget:write",
    "billing:alert:write",
    "memory:approve",
    "memory:manage",
    "memory:eval:read",
    "memory:eval:review",
}


@dataclass
class Identity:
    user_id: UUID
    org_id: UUID
    scopes: set[str]
    role: str
    token_id: UUID | None = None
    actor_kind: str = "session"
    principal_id: UUID | None = None
    session_id: UUID | None = None
    step_id: UUID | None = None
    invocation_id: UUID | None = None
    session_expires_at: datetime | None = None
    job_id: UUID | None = None
    run_id: UUID | None = None

    def __post_init__(self):
        if self.token_id is not None and self.actor_kind == "session":
            self.actor_kind = "token"

    def require(self, scope: str) -> None:
        if scope in HUMAN_ONLY_SCOPES and (
            self.actor_kind != "session" or self.token_id is not None
        ):
            raise ServiceError("forbidden", "A human session is required", 403, 4)
        if scope not in self.scopes:
            raise ServiceError("forbidden", "Permission denied", 403, 4)


async def set_actor_context(session: AsyncSession, actor: Identity) -> None:
    """Only authenticated server code supplies transaction-local decision identity."""
    previous = session.info.get("actor")
    if (
        actor.invocation_id is None
        and previous is not None
        and (actor.user_id, actor.org_id, actor.token_id)
        == (previous.user_id, previous.org_id, previous.token_id)
    ):
        actor.invocation_id = previous.invocation_id
    job = session.info.get("execution_job")
    if (
        actor.actor_kind == "worker"
        and job is not None
        and (job.org_id == actor.org_id and job.actor_user_id == actor.user_id)
    ):
        actor.principal_id, actor.session_id = job.agent_principal_id, job.agent_session_id
        if job.kind != "agent":
            actor.step_id, actor.invocation_id = job.agent_step_id, job.invocation_id
        elif actor.invocation_id is None:
            actor.invocation_id = job.invocation_id
        actor.job_id, actor.run_id = job.id, job.run_id
    session.info["actor"] = actor
    await session.execute(
        text(
            "SELECT set_config('app.actor_kind', :kind, true), "
            "set_config('app.actor_user_id', :user, true), "
            "set_config('app.actor_token_id', :token, true), "
            "set_config('app.actor_scopes', :scopes, true), "
            "set_config('app.agent_principal_id', :principal, true), "
            "set_config('app.agent_session_id', :agent_session, true), "
            "set_config('app.agent_step_id', :step, true), "
            "set_config('app.execution_job_id', :execution_job, true), "
            "set_config('app.execution_run_id', :execution_run, true), "
            "set_config('app.invocation_id', :invocation, true), "
            "set_config('app.command', :command, true)"
        ),
        {
            "scopes": json.dumps(sorted(actor.scopes)),
            "principal": str(actor.principal_id) if actor.principal_id else "",
            "agent_session": str(actor.session_id) if actor.session_id else "",
            "step": str(actor.step_id) if actor.step_id else "",
            "execution_job": str(actor.job_id) if actor.job_id else "",
            "execution_run": str(actor.run_id) if actor.run_id else "",
            "invocation": str(actor.invocation_id) if actor.invocation_id else "",
            "command": session.info.get("command", ""),
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
        try:
            await membership(session, user.id, org_id)
        except ServiceError as error:
            # A non-member must look like a wrong password, or the reply confirms the password.
            if error.code == "not_found":
                raise invalid_login() from None
            raise
    return user


async def authenticate(
    session: AsyncSession,
    bearer: str,
    org_id: UUID,
    crypto: TokenSigner,
    *,
    joined_membership=False,
) -> Identity:
    session_expires_at = None
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
        session_expires_at = datetime.fromtimestamp(payload["exp"], UTC)
        token_scopes = None
        token_id = None
    if joined_membership:
        # The product read projection has a strict round-trip budget. Outer joins
        # preserve the established user -> membership -> org error precedence.
        row = (
            await session.execute(
                select(User, Membership, Org)
                .outerjoin(
                    Membership,
                    (Membership.user_id == User.id)
                    & (Membership.org_id == org_id)
                    & Membership.active.is_(True),
                )
                .outerjoin(Org, Org.id == Membership.org_id)
                .where(User.id == user_id)
                .execution_options(populate_existing=True)
            )
        ).first()
        if row is None or not row[0].active:
            raise ServiceError("invalid_session", "Invalid credentials", 401, 4)
        _, member, org = row
        if member is None:
            raise not_found()
        if org is None or not org.active:
            raise ServiceError("org_inactive", "Organization is disabled", 403, 4)
    else:
        user = await session.get(User, user_id)
        if user is None or not user.active:
            raise ServiceError("invalid_session", "Invalid credentials", 401, 4)
        member = await membership(session, user_id, org_id)
    role_scopes = ROLE_SCOPES[member.role]
    scopes = role_scopes if token_scopes is None else token_scopes & role_scopes & SCOPES
    return Identity(
        user_id, org_id, set(scopes), member.role, token_id, session_expires_at=session_expires_at
    )


async def agent_identity(session: AsyncSession, principal) -> Identity:
    """Revalidate every admission; new role grants never enlarge existing delegation."""
    if principal.revoked_at is not None or principal.authority_expires_at <= datetime.now(UTC):
        raise ServiceError("agent_authority_expired", "Agent authority has expired", 403, 4)
    user = await session.get(User, principal.user_id)
    if user is None or not user.active:
        raise ServiceError("invalid_session", "Invalid credentials", 401, 4)
    member = await membership(session, principal.user_id, principal.org_id)
    if member.id != principal.membership_id:
        raise not_found()
    scopes = (
        set(principal.initial_grants)
        & set(principal.scopes)
        & ROLE_SCOPES[member.role]
        & AGENT_SCOPES
    )
    return Identity(
        principal.user_id,
        principal.org_id,
        scopes,
        member.role,
        actor_kind="agent",
        principal_id=principal.id,
    )
