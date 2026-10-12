"""Task access and creator authority while the legacy schema remains in use."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import NoReturn
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.models.entities import ApiToken, Membership, Org, Task, User
from app.models.team_workflow import TaskEventHead, TaskMember, TaskWorkflow
from app.services.auth import HUMAN_ONLY_SCOPES, ROLE_SCOPES, SCOPES, Identity, set_actor_context

HUMAN_SCOPES = {
    "req:confirm",
    "req:manual",
    "bid-review:decide",
    "bid-review:classify",
}
DECISIONS = {
    "evidence:confirm",
    "check:decide",
    "score:rubric:review",
    "bid-review:decide",
}


def fail(code, message, status=409, exit_code=2) -> NoReturn:
    raise ServiceError(code, message, status, exit_code)


def domains(role):
    return {"bidder": ["commercial"], "technical": ["technical"]}.get(role, [])


async def live_actor(session, actor):
    row = (
        await session.execute(
            select(Membership, User, Org)
            .join(User, User.id == Membership.user_id)
            .join(Org, Org.id == Membership.org_id)
            .where(Membership.org_id == actor.org_id, Membership.user_id == actor.user_id)
            .execution_options(populate_existing=True)
        )
    ).first()
    if row is None or not row[0].active or not row[1].active or not row[2].active:
        raise not_found()
    member, _, _ = row
    scopes = actor.scopes & ROLE_SCOPES[member.role]
    if actor.token_id is not None:
        token = await session.get(ApiToken, actor.token_id)
        if (
            token is None
            or token.org_id != actor.org_id
            or token.user_id != actor.user_id
            or token.revoked
            or token.expires_at <= datetime.now(UTC)
        ):
            fail("forbidden", "Permission denied", 403, 4)
        scopes &= set(token.scopes) & SCOPES
    if actor.actor_kind in {"agent", "worker"} and actor.principal_id is not None:
        from app.models.agent import AgentPrincipal
        from app.services.auth import agent_identity

        principal = await session.get(AgentPrincipal, actor.principal_id, populate_existing=True)
        if (
            principal is None
            or principal.org_id != actor.org_id
            or principal.user_id != actor.user_id
        ):
            raise not_found()
        delegated = await agent_identity(session, principal)
        scopes &= delegated.scopes
    if actor.actor_kind != "session" or actor.token_id is not None:
        scopes -= HUMAN_ONLY_SCOPES
    # Refresh grants without dropping the immediate actor or immutable agent/job origin.
    return replace(actor, scopes=scopes, role=member.role)


def require_read_authority(
    live: Identity,
    workflow: TaskWorkflow | None,
    member: TaskMember | None,
    scope: str,
    *,
    require_member: bool = False,
) -> bool:
    """Shared task visibility gate for locked writes and joined read projections.

    Callers must first refresh the identity and select this task's active membership.
    """
    recovery = live.actor_kind == "session" and live.token_id is None and live.role == "admin"
    if workflow is None or (member is None and (require_member or not recovery)):
        raise not_found()
    # A visible object is established before scope errors reveal which action was denied.
    live.require("task:read")
    live.require(scope)
    if scope in HUMAN_SCOPES and (live.actor_kind != "session" or live.token_id is not None):
        fail("forbidden", "Human session required", 403, 4)
    return recovery


async def access(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    scope="task:read",
    *,
    write=False,
    domain=None,
    lock: bool | None = None,
    management=False,
    require_member=False,
    bind_context=True,
) -> tuple[Task, TaskWorkflow, TaskMember | None]:
    # Preparation paths may check write authority without holding a task lock;
    # they must repeat this gate with the default write lock before publication.
    should_lock = write if lock is None else lock
    query = select(Task).where(Task.org_id == actor.org_id, Task.id == task_id)
    if should_lock:
        query = query.with_for_update()
    task = await session.scalar(query.execution_options(populate_existing=True))
    if task is None:
        raise not_found()
    live = await live_actor(session, actor)
    workflow_query = select(TaskWorkflow).where(
        TaskWorkflow.org_id == actor.org_id, TaskWorkflow.task_id == task_id
    )
    if should_lock:
        workflow_query = workflow_query.with_for_update()
    workflow = await session.scalar(workflow_query.execution_options(populate_existing=True))
    member = await session.scalar(
        select(TaskMember)
        .where(
            TaskMember.org_id == actor.org_id,
            TaskMember.task_id == task_id,
            TaskMember.user_id == actor.user_id,
            TaskMember.active.is_(True),
        )
        .execution_options(populate_existing=True)
    )
    recovery = require_read_authority(live, workflow, member, scope, require_member=require_member)
    assert workflow is not None  # Established by the shared visibility gate.
    if management:
        if not recovery and (member is None or member.role != "owner"):
            fail("forbidden", "Task owner required", 403, 4)
    elif write:
        if (
            member is None
            or member.role == "observer"
            or (member.role == "reviewer" and scope not in DECISIONS)
        ):
            fail("forbidden", "Task role does not permit this action", 403, 4)
    if domain is not None:
        if (
            member is None
            or domain not in member.review_domains
            or domain not in domains(live.role)
        ):
            fail("forbidden", "Review domain is not granted", 403, 4)
    if write and workflow.state == "archived":
        fail("task_archived", "Task is archived")
    actor.scopes, actor.role = live.scopes, live.role
    # Pure authority checks must not replace an installed worker's execution fence.
    if bind_context:
        await set_actor_context(session, live)
    return task, workflow, member


async def seed(session, actor, task):
    live = await live_actor(session, actor)
    live.require("task:create")
    if (
        task.created_by != actor.user_id
        or task.org_id != actor.org_id
        or live.role not in {"admin", "bidder"}
    ):
        fail("forbidden", "Task owner must be an eligible creator", 403, 4)
    await set_actor_context(session, live)
    now = datetime.now(UTC)
    # Flush owner before workflow to satisfy trigger eligibility; deferred FK guarantees
    # that a transaction cannot commit this intermediate state.
    session.add(
        TaskMember(
            org_id=actor.org_id,
            task_id=task.id,
            user_id=actor.user_id,
            role="owner",
            review_domains=domains(live.role),
            changed_by_user_id=actor.user_id,
            changed_at=now,
        )
    )
    await session.execute(
        insert(TaskEventHead)
        .values(org_id=actor.org_id, task_id=task.id)
        .on_conflict_do_nothing(index_elements=["org_id", "task_id"])
    )
    await session.flush()
    workflow = TaskWorkflow(org_id=actor.org_id, task_id=task.id, owner_user_id=actor.user_id)
    session.add(workflow)
    await session.flush()
    return workflow
