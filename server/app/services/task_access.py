"""Task existence, project visibility, scoped authority and actor attribution.

On a gateway a project is visible to administrators and to its members only;
everyone else gets `not_found`, the same answer as for a missing task. Worker
steps skip the check: a job admitted before a member left the project finishes.
"""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import ColumnElement, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.models.entities import ApiToken, Task
from app.models.members import TaskMember
from app.models.task_events import TaskEventHead
from app.services.auth import HUMAN_ONLY_SCOPES, HUMAN_SCOPES, SCOPES, Identity, set_actor_context


async def live_actor(session: AsyncSession, actor: Identity) -> Identity:
    scopes = set(actor.scopes)
    if actor.session_expires_at is not None and actor.session_expires_at <= datetime.now(UTC):
        raise ServiceError("invalid_session", "Invalid or expired credentials", 401, 4)
    if actor.token_id is not None:
        token = await session.get(ApiToken, actor.token_id, populate_existing=True)
        if token is None or token.revoked or token.expires_at <= datetime.now(UTC):
            raise ServiceError("forbidden", "Permission denied", 403, 4)
        scopes &= set(token.scopes) & SCOPES
    if actor.actor_kind in {"agent", "worker"} and actor.principal_id is not None:
        from app.models.agent import AgentPrincipal
        from app.services.auth import agent_identity

        principal = await session.get(AgentPrincipal, actor.principal_id, populate_existing=True)
        if principal is None:
            raise not_found()
        delegated = await agent_identity(session, principal)
        scopes &= delegated.scopes
    if actor.actor_kind == "session" and actor.token_id is None:
        scopes &= HUMAN_SCOPES
    else:
        scopes -= HUMAN_ONLY_SCOPES
    return replace(actor, scopes=scopes)


def visibility(actor: Identity) -> ColumnElement[bool] | None:
    """The predicate on `Task.id` that limits what `actor` sees, or None for everything."""
    if actor.member_id is None or actor.member_role == "admin" or actor.actor_kind == "worker":
        return None
    return exists().where(TaskMember.task_id == Task.id, TaskMember.member_id == actor.member_id)


async def visible_task(
    session: AsyncSession, actor: Identity, task_id: UUID, *, lock: bool = False
) -> Task:
    query = select(Task).where(Task.id == task_id)
    condition = visibility(actor)
    if condition is not None:
        query = query.where(condition)
    if lock:
        query = query.with_for_update(of=Task)
    task = await session.scalar(query.execution_options(populate_existing=True))
    if task is None:
        raise not_found()
    return task


async def access(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    scope: str = "task:read",
    *,
    write: bool = False,
    lock: bool | None = None,
    bind_context: bool = True,
) -> Task:
    # Preparation may defer the write lock, but publication must repeat the gate.
    task = await visible_task(session, actor, task_id, lock=write if lock is None else lock)
    live = await live_actor(session, actor)
    live.require("task:read")
    live.require(scope)
    actor.scopes = live.scopes
    # Authority-only checks must preserve the installed worker execution fence.
    if bind_context:
        await set_actor_context(session, live)
    return task


async def seed(session: AsyncSession, actor: Identity, task: Task) -> None:
    live = await live_actor(session, actor)
    live.require("task:create")
    await set_actor_context(session, live)
    await session.execute(
        insert(TaskEventHead)
        .values(task_id=task.id)
        .on_conflict_do_nothing(index_elements=["task_id"])
    )
    if live.member_id is not None:
        # On a gateway the creator owns the project; the deferred trigger
        # rejects a project without an owner at commit.
        session.add(TaskMember(task_id=task.id, member_id=live.member_id, role="owner"))
        await session.flush()
