"""Shared first-slice memory authorization and signed pagination."""

import hashlib
import json
import unicodedata
from datetime import datetime
from uuid import UUID

from app.core.errors import ServiceError, not_found
from app.core.security import TokenSigner
from app.schemas.memory_contracts import MemoryTarget
from app.services.auth import Identity


def org_target(target: MemoryTarget) -> None:
    if target.scope != "org":
        raise ServiceError(
            "memory_scope_unavailable", "Requested memory scope is not enabled", 403, 4
        )


def human_admin(actor: Identity, scope: str) -> None:
    actor.require(scope)
    if actor.actor_kind != "session" or actor.token_id is not None or actor.role != "admin":
        raise ServiceError("forbidden", "Human organization administrator required", 403, 4)


def writable(actor: Identity, creator: UUID, token_id: UUID | None, status: str) -> None:
    actor.require("memory:write")
    if actor.actor_kind == "session" and actor.role == "admin":
        return
    if status != "candidate" or actor.user_id != creator or actor.token_id != token_id:
        raise not_found()


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def cursor_binding(actor: Identity, filters: dict) -> str:
    return digest(
        {
            "org": str(actor.org_id),
            "user": str(actor.user_id),
            "token": str(actor.token_id),
            "filters": filters,
        }
    )


def read_cursor(settings, actor: Identity, filters: dict, cursor: str | None):
    if cursor is None:
        return None
    try:
        data = TokenSigner.for_tokens(settings).open(cursor)
        if data.get("kind") != "memory_page" or data.get("binding") != cursor_binding(
            actor, filters
        ):
            raise ValueError
        return datetime.fromisoformat(data["created_at"]), UUID(data["id"])
    except (ServiceError, ValueError, KeyError, TypeError):
        raise ServiceError(
            "invalid_cursor", "Cursor does not match this authorized query", 400, 2
        ) from None


def page_cursor(settings, actor: Identity, filters: dict, created_at: datetime, identifier: UUID):
    return TokenSigner.for_tokens(settings).issue(
        {
            "kind": "memory_page",
            "binding": cursor_binding(actor, filters),
            "created_at": created_at.isoformat(),
            "id": str(identifier),
        },
        3600,
    )


def normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


async def access(session, actor: Identity, scope: str) -> Identity:
    """Recheck live grants without imposing unrelated task capabilities."""
    from datetime import UTC

    from app.models.entities import ApiToken, User
    from app.services.auth import ROLE_SCOPES, SCOPES, membership, set_actor_context

    member = await membership(session, actor.user_id, actor.org_id)
    user = await session.get(User, actor.user_id)
    if user is None or not user.active:
        raise not_found()
    scopes = actor.scopes & ROLE_SCOPES[member.role]
    if actor.token_id is not None:
        token = await session.get(ApiToken, actor.token_id)
        if (
            token is None
            or token.user_id != actor.user_id
            or token.org_id != actor.org_id
            or token.revoked
            or token.expires_at <= datetime.now(UTC)
        ):
            raise ServiceError("forbidden", "Permission denied", 403, 4)
        scopes &= set(token.scopes) & SCOPES
    live = Identity(
        actor.user_id, actor.org_id, scopes, member.role, actor.token_id, actor.actor_kind
    )
    live.require(scope)
    await set_actor_context(session, live)
    return live
