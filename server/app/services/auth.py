import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ServiceError
from app.core.security import TokenSigner, access_key_matches, token_digest
from app.models.entities import ApiToken
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
            "token": str(actor.token_id) if actor.token_id else "",
        },
    )


async def authenticate(
    session: AsyncSession,
    bearer: str,
    crypto: TokenSigner,
    settings: Settings,
) -> Identity:
    if access_key_matches(bearer, settings.access_key.get_secret_value()):
        return Identity(scopes=set(HUMAN_SCOPES))
    if bearer.startswith("bid_"):
        token = await session.scalar(
            select(ApiToken).where(
                ApiToken.digest == token_digest(bearer), ApiToken.revoked.is_(False)
            )
        )
        if token is None or token.expires_at <= datetime.now(UTC):
            raise ServiceError("invalid_token", "Invalid or expired credentials", 401, 4)
        return Identity(scopes=set(token.scopes) & SCOPES, token_id=token.id)
    try:
        payload = crypto.open(bearer)
    except ServiceError as error:
        if isinstance(error.__cause__, ValueError) and str(error.__cause__) == "expired":
            raise
        raise ServiceError("invalid_access_key", "Invalid access key", 401, 4) from None
    if payload.get("kind") != "session":
        raise ServiceError("invalid_access_key", "Invalid access key", 401, 4)
    return Identity(
        scopes=set(HUMAN_SCOPES),
        session_expires_at=datetime.fromtimestamp(payload["exp"], UTC),
    )


async def agent_identity(session: AsyncSession, principal) -> Identity:
    """Revalidate the live grant without enlarging the original delegation."""
    if principal.revoked_at is not None or principal.authority_expires_at <= datetime.now(UTC):
        raise ServiceError("agent_authority_expired", "Agent authority has expired", 403, 4)
    scopes = set(principal.initial_grants) & set(principal.scopes) & AGENT_SCOPES
    return Identity(scopes=scopes, actor_kind="agent", principal_id=principal.id)
