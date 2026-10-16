"""Shared memory authorization and signed pagination.

Library memory is public. On a gateway, private memory belongs to one member
and is visible to that member only, administrators included; anyone else gets
`not_found`. Changing public memory there needs the `memory:edit` grant,
while members change their own private memory without it.
"""

import hashlib
import json
import unicodedata
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, and_, or_

from app.core.errors import ServiceError, not_found
from app.core.security import TokenSigner
from app.models.memory import Memory
from app.schemas.memory_contracts import MemoryTarget
from app.services.auth import Identity


def scope_unavailable() -> ServiceError:
    return ServiceError("memory_scope_unavailable", "Requested memory scope is not enabled", 403, 4)


def target_owner(actor: Identity, target: MemoryTarget) -> UUID | None:
    """The owner a target names: none for library, the acting member for private."""
    if target.scope == "library":
        return None
    if target.scope == "private" and actor.member_id is not None:
        return actor.member_id
    raise scope_unavailable()


def readable(actor: Identity) -> ColumnElement[bool]:
    """Library memory plus the acting member's own private memory."""
    if actor.member_id is None:
        return Memory.scope == "library"
    return or_(
        Memory.scope == "library",
        and_(Memory.scope == "private", Memory.owner_member_id == actor.member_id),
    )


def visible(actor: Identity, memory: Memory) -> None:
    if memory.scope == "library":
        return
    if memory.scope != "private" or memory.owner_member_id != actor.member_id:
        raise not_found()


async def may_change(session, actor: Identity, scope: str) -> bool:
    if scope != "library" or actor.member_id is None or actor.member_role == "admin":
        return True
    from app.services.auth import member_grants

    return "memory:edit" in await member_grants(session, actor.member_id)


async def changeable(session, actor: Identity, scope: str) -> None:
    if not await may_change(session, actor, scope):
        raise ServiceError("forbidden", "Changing public memory needs memory:edit", 403, 4)


def human(actor: Identity, scope: str) -> None:
    actor.require(scope)
    if actor.actor_kind != "session" or actor.token_id is not None:
        raise ServiceError("forbidden", "Human session required", 403, 4)


def writable(actor: Identity, token_id: UUID | None, status: str) -> None:
    actor.require("memory:write")
    if actor.actor_kind == "session" and actor.token_id is None:
        return
    if status != "candidate" or actor.token_id is None or actor.token_id != token_id:
        raise not_found()


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def cursor_binding(actor: Identity, filters: dict) -> str:
    return digest(
        {
            "actor_kind": actor.actor_kind,
            "token": str(actor.token_id),
            "principal": str(actor.principal_id),
            "session": str(actor.session_id),
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
        return (datetime.fromisoformat(data["created_at"]), UUID(data["id"]))
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
    """Recheck live scopes and preserve token, agent and worker attribution."""
    from app.services.auth import set_actor_context
    from app.services.task_access import live_actor

    live = await live_actor(session, actor)
    live.require(scope)
    await set_actor_context(session, live)
    return live
