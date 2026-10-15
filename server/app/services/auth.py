import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ServiceError
from app.core.security import TokenSigner, access_key_matches, token_digest
from app.models.entities import ApiToken
from app.models.members import Member, MemberGrant, MemberSession
from app.schemas.attachment_contracts import HUMAN_ONLY_SCOPES as ATTACHMENT_HUMAN_SCOPES
from app.schemas.bid_review import HUMAN_ONLY_SCOPES as BID_REVIEW_HUMAN_SCOPES
from app.schemas.bid_review import TOKEN_SCOPES as BID_REVIEW_TOKEN_SCOPES

AGENT_SCOPES = {
    "card:generate",
    "card:read",
    "certificate:file:read",
    "certificate:read",
    "draft:read",
    "draft:run",
    "evidence:source:read",
    "job:read",
    "profile:read",
    "resource:read",
    "task:read",
}

SCOPES = {
    "attachment:read",
    "card:generate",
    "card:read",
    "card:write",
    "certificate:file:read",
    "certificate:file:write",
    "certificate:read",
    "certificate:write",
    "check:read",
    "check:run",
    "confidential:read",
    "draft:read",
    "draft:run",
    "evidence:source:read",
    "evidence:source:write",
    "job:cancel",
    "job:read",
    "memory:candidate:run",
    "memory:read",
    "memory:retrieve",
    "memory:write",
    "profile:read",
    "profile:write",
    "provider:read",
    "req:extract",
    "resource:read",
    "resource:write",
    "sandbox:capture",
    "sandbox:read",
    "sandbox:render",
    "score:read",
    "score:rubric:generate",
    "score:run",
    "screenshot:read",
    "screenshot:write",
    "task:certificate",
    "task:create",
    "task:profile",
    "task:read",
    "task:resource",
    "task:template",
    "template:read",
    "template:write",
    "tender:parse",
    "tender:upload",
}

SCOPES.update(BID_REVIEW_TOKEN_SCOPES)

HUMAN_ONLY_SCOPES = {
    "member:manage",
    "credential:manage",
    "clef:manage",
    "trust-anchor:manage",
    "agent:cancel",
    "agent:read",
    "agent:run",
    "certificate:lifecycle",
    "check:decide",
    "confidential:reveal",
    "confidential:write",
    "evidence:annotate",
    "evidence:confirm",
    "export",
    "memory:approve",
    "memory:eval:read",
    "memory:eval:review",
    "memory:manage",
    "profile:lifecycle",
    "provider:write",
    "req:confirm",
    "req:manual",
    "score:rubric:review",
    "screenshot:ingest",
    "template:file:read",
    "token:create",
}

HUMAN_ONLY_SCOPES.update(ATTACHMENT_HUMAN_SCOPES)
HUMAN_ONLY_SCOPES.update(BID_REVIEW_HUMAN_SCOPES)

HUMAN_SCOPES = {
    "credential:manage",
    "clef:manage",
    "trust-anchor:manage",
    "agent:cancel",
    "agent:read",
    "agent:run",
    "attachment:read",
    "bid-review:read",
    "bid-review:run",
    "card:generate",
    "card:read",
    "card:write",
    "certificate:file:read",
    "certificate:file:write",
    "certificate:lifecycle",
    "certificate:read",
    "certificate:write",
    "check:decide",
    "check:read",
    "check:run",
    "confidential:read",
    "confidential:reveal",
    "confidential:write",
    "draft:read",
    "draft:run",
    "evidence:annotate",
    "evidence:confirm",
    "evidence:source:read",
    "evidence:source:write",
    "export",
    "job:cancel",
    "job:read",
    "memory:approve",
    "memory:candidate:run",
    "memory:eval:read",
    "memory:eval:review",
    "memory:manage",
    "memory:read",
    "memory:retrieve",
    "memory:write",
    "profile:lifecycle",
    "profile:read",
    "profile:write",
    "provider:read",
    "provider:write",
    "req:confirm",
    "req:extract",
    "req:manual",
    "resource:read",
    "resource:write",
    "sandbox:capture",
    "sandbox:read",
    "sandbox:render",
    "score:read",
    "score:rubric:generate",
    "score:rubric:review",
    "score:run",
    "screenshot:ingest",
    "screenshot:read",
    "screenshot:write",
    "task:certificate",
    "task:create",
    "task:profile",
    "task:read",
    "task:resource",
    "task:template",
    "template:file:read",
    "template:read",
    "template:write",
    "tender:parse",
    "tender:upload",
    "token:create",
}

HUMAN_SCOPES.update(ATTACHMENT_HUMAN_SCOPES)
HUMAN_SCOPES.update(BID_REVIEW_HUMAN_SCOPES)

# Gateway scopes held by administrators only. Grants and project visibility
# refine the member scopes further in a later gateway slice.
ADMIN_ONLY_SCOPES = {
    "member:manage",
    "credential:manage",
    "clef:manage",
    "trust-anchor:manage",
    "confidential:write",
    "provider:write",
}
# A must_change_password session may call only these routes.
PASSWORD_CHANGE_ROUTES = {"auth_session_show", "auth_session_delete", "account_password_change"}
SEEN_INTERVAL = timedelta(minutes=1)


def member_scopes(role: str) -> set[str]:
    if role == "admin":
        return HUMAN_SCOPES | {"member:manage"}
    return HUMAN_SCOPES - ADMIN_ONLY_SCOPES


@dataclass
class Identity:
    scopes: set[str]
    token_id: UUID | None = None
    actor_kind: str = "session"
    principal_id: UUID | None = None
    session_id: UUID | None = None
    step_id: UUID | None = None
    invocation_id: UUID | None = None
    session_expires_at: datetime | None = None
    job_id: UUID | None = None
    run_id: UUID | None = None
    # Gateway only; absent in a personal installation.
    member_id: UUID | None = None
    member_role: str | None = None
    member_session_id: UUID | None = None
    must_change_password: bool = False
    grants: list[str] = field(default_factory=list)

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
        and (actor.actor_kind, actor.token_id, actor.principal_id, actor.session_id)
        == (previous.actor_kind, previous.token_id, previous.principal_id, previous.session_id)
    ):
        actor.invocation_id = previous.invocation_id
    job = session.info.get("execution_job")
    if actor.actor_kind == "worker" and job is not None:
        actor.principal_id, actor.session_id = job.agent_principal_id, job.agent_session_id
        if job.kind != "agent":
            actor.step_id, actor.invocation_id = job.agent_step_id, job.invocation_id
        elif actor.invocation_id is None:
            actor.invocation_id = job.invocation_id
        actor.job_id, actor.run_id = job.id, job.run_id
        actor.member_id = job.actor_member_id
    session.info["actor"] = actor
    await session.execute(
        text(
            "SELECT set_config('app.actor_kind', :kind, true), "
            "set_config('app.actor_token_id', :token, true), "
            "set_config('app.actor_scopes', :scopes, true), "
            "set_config('app.agent_principal_id', :principal, true), "
            "set_config('app.agent_session_id', :agent_session, true), "
            "set_config('app.agent_step_id', :step, true), "
            "set_config('app.execution_job_id', :execution_job, true), "
            "set_config('app.execution_run_id', :execution_run, true), "
            "set_config('app.invocation_id', :invocation, true), "
            "set_config('app.command', :command, true), "
            "set_config('app.actor_member_id', :member, true)"
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
            "token": str(actor.token_id) if actor.token_id else "",
            "member": str(actor.member_id) if actor.member_id else "",
        },
    )


def access_key_refused() -> ServiceError:
    return ServiceError(
        "access_key_not_accepted", "This gateway accepts member sign-in only", 401, 4
    )


def require_current_password(identity: Identity, route: str) -> None:
    if identity.must_change_password and route not in PASSWORD_CHANGE_ROUTES:
        raise ServiceError("password_change_required", "Change the initial password first", 403, 4)


def invalid_session() -> ServiceError:
    return ServiceError("invalid_session", "Invalid or expired credentials", 401, 4)


async def active_member(session: AsyncSession, member_id: UUID | None) -> Member | None:
    if member_id is None:
        return None
    member = await session.get(Member, member_id, populate_existing=True)
    return member if member is not None and member.status == "active" else None


async def member_grants(session: AsyncSession, member_id: UUID) -> list[str]:
    rows = await session.scalars(
        select(MemberGrant.grant).where(MemberGrant.member_id == member_id)
    )
    return sorted(rows)


async def member_session_identity(
    session: AsyncSession, payload: dict, settings: Settings
) -> Identity:
    """Load the session row and its member on every request, so revocation,
    disabling and password changes take effect on the next one."""
    try:
        session_id, member_id = UUID(payload["sid"]), UUID(payload["mid"])
    except (KeyError, TypeError, ValueError):
        raise invalid_session() from None
    row = await session.get(MemberSession, session_id, populate_existing=True)
    member = await active_member(session, member_id)
    now = datetime.now(UTC)
    if (
        row is None
        or member is None
        or row.member_id != member.id
        or row.revoked_at is not None
        or row.expires_at <= now
        or row.last_seen_at <= now - timedelta(minutes=settings.session_idle_minutes)
    ):
        raise invalid_session()
    if row.last_seen_at <= now - SEEN_INTERVAL:
        # SKIP LOCKED: a concurrent request of the same session already touches it.
        await session.execute(
            update(MemberSession)
            .where(
                MemberSession.id.in_(
                    select(MemberSession.id)
                    .where(MemberSession.id == session_id)
                    .with_for_update(skip_locked=True)
                )
            )
            .values(last_seen_at=now)
            .execution_options(synchronize_session=False)
        )
    return Identity(
        scopes=member_scopes(member.role),
        session_expires_at=row.expires_at,
        member_id=member.id,
        member_role=member.role,
        member_session_id=row.id,
        must_change_password=member.must_change_password,
        grants=await member_grants(session, member.id),
    )


async def authenticate(
    session: AsyncSession,
    bearer: str,
    crypto: TokenSigner,
    settings: Settings,
) -> Identity:
    enterprise = settings.deployment == "enterprise"
    if access_key_matches(bearer, settings.access_key.get_secret_value()):
        if enterprise:
            # The access key serves the gateway host's admin command line only.
            raise access_key_refused()
        return Identity(scopes=set(HUMAN_SCOPES))
    if bearer.startswith("bid_"):
        token = await session.scalar(
            select(ApiToken).where(
                ApiToken.digest == token_digest(bearer), ApiToken.revoked.is_(False)
            )
        )
        if token is None or token.expires_at <= datetime.now(UTC):
            raise ServiceError("invalid_token", "Invalid or expired credentials", 401, 4)
        scopes = set(token.scopes) & SCOPES
        member = await active_member(session, token.member_id)
        if enterprise:
            # A token works only while its member does, within the member's scopes.
            if member is None:
                raise ServiceError("invalid_token", "Invalid or expired credentials", 401, 4)
            scopes &= member_scopes(member.role)
        return Identity(
            scopes=scopes,
            token_id=token.id,
            member_id=member.id if member is not None else None,
            member_role=member.role if member is not None else None,
        )
    try:
        payload = crypto.open(bearer)
    except ServiceError as error:
        if isinstance(error.__cause__, ValueError) and str(error.__cause__) == "expired":
            raise
        if enterprise:
            raise invalid_session() from None
        raise ServiceError("invalid_access_key", "Invalid access key", 401, 4) from None
    if payload.get("kind") != "session":
        raise ServiceError("invalid_access_key", "Invalid access key", 401, 4)
    if enterprise:
        return await member_session_identity(session, payload, settings)
    if "sid" in payload:
        raise invalid_session()
    return Identity(
        scopes=set(HUMAN_SCOPES),
        session_expires_at=datetime.fromtimestamp(payload["exp"], UTC),
    )


async def agent_identity(session: AsyncSession, principal) -> Identity:
    """Revalidate the live grant without enlarging the original delegation."""
    if principal.revoked_at is not None or principal.authority_expires_at <= datetime.now(UTC):
        raise ServiceError("agent_authority_expired", "Agent authority has expired", 403, 4)
    scopes = set(principal.initial_grants) & set(principal.scopes) & AGENT_SCOPES
    member = None
    if principal.member_id is not None:
        # The member who started the agent must still be active, every step.
        member = await active_member(session, principal.member_id)
        if member is None:
            raise ServiceError("agent_authority_expired", "Agent authority has expired", 403, 4)
        scopes &= member_scopes(member.role)
    return Identity(
        scopes=scopes,
        actor_kind="agent",
        principal_id=principal.id,
        member_id=principal.member_id,
        member_role=member.role if member is not None else None,
    )
