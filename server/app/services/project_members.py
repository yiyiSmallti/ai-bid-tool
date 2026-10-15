"""Gateway project members, the owner hand-over and the personal-export flag.

The owner or an administrator manages members, holding `task:create`, the
scope that makes a project owner; only an administrator changes sharing. Agents never manage either. A member removed from a project loses it
at once through `visible_task`, and the member's agents on it are cancelled by
their next wake (`agents.member_removed`).
"""

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.models.entities import Task
from app.models.members import Member, TaskMember
from app.schemas.members import (
    ProjectMemberAdd,
    ProjectMemberView,
    ProjectOwnerChange,
    ProjectSharingUpdate,
)
from app.services.auth import Identity
from app.services.members import member_required
from app.services.task_access import access


def human_member(identity: Identity) -> UUID:
    if identity.member_id is None or identity.actor_kind not in {"session", "token"}:
        raise member_required()
    return identity.member_id


async def members(session: AsyncSession, task_id: UUID) -> list[ProjectMemberView]:
    rows = await session.execute(
        select(TaskMember, Member)
        .join(Member, Member.id == TaskMember.member_id)
        .where(TaskMember.task_id == task_id)
        .order_by(TaskMember.role.desc(), TaskMember.added_at, Member.login)
    )
    return [
        ProjectMemberView(
            member_id=member.id,
            login=member.login,
            display_name=member.display_name,
            role=row.role,  # type: ignore[arg-type]
            status=member.status,  # type: ignore[arg-type]
            added_at=row.added_at,
        )
        for row, member in rows.tuples()
    ]


async def managed(session: AsyncSession, identity: Identity, task_id: UUID) -> Task:
    """Lock a project the caller owns or administers."""
    caller = human_member(identity)
    task = await access(session, identity, task_id, "task:create", write=True)
    if identity.member_role != "admin":
        own = await session.get(TaskMember, (task_id, caller), populate_existing=True)
        if own is None or own.role != "owner":
            raise ServiceError(
                "project_owner_required", "Only the owner or an administrator can", 403, 4
            )
    return task


async def owner_checked(session: AsyncSession) -> None:
    """Run the deferred owner trigger now so its error has a code."""
    try:
        await session.flush()
        await session.execute(text("SET CONSTRAINTS task_owner_required IMMEDIATE"))
        await session.execute(text("SET CONSTRAINTS task_owner_required DEFERRED"))
    except DBAPIError as error:
        if "task_owner_required" in str(error.orig):
            raise ServiceError(
                "task_owner_required", "A project needs exactly one owner", 409, 2
            ) from None
        raise


async def list_members(
    session: AsyncSession, identity: Identity, task_id: UUID
) -> list[ProjectMemberView]:
    human_member(identity)
    await access(session, identity, task_id)
    return await members(session, task_id)


async def add_members(
    session: AsyncSession, identity: Identity, task_id: UUID, body: ProjectMemberAdd
) -> list[ProjectMemberView]:
    await managed(session, identity, task_id)
    wanted = set(body.member_ids)
    found = (await session.scalars(select(Member).where(Member.id.in_(wanted)))).all()
    if len(found) != len(wanted):
        raise not_found()
    if any(member.status != "active" for member in found):
        raise ServiceError("member_disabled", "A disabled member cannot join a project", 409, 2)
    present = set(
        await session.scalars(
            select(TaskMember.member_id).where(
                TaskMember.task_id == task_id, TaskMember.member_id.in_(wanted)
            )
        )
    )
    session.add_all(
        TaskMember(task_id=task_id, member_id=member_id, role="member")
        for member_id in sorted(wanted - present)
    )
    await session.flush()
    return await members(session, task_id)


async def remove_member(
    session: AsyncSession, identity: Identity, task_id: UUID, member_id: UUID
) -> None:
    await managed(session, identity, task_id)
    row = await session.get(TaskMember, (task_id, member_id), populate_existing=True)
    if row is None:
        raise not_found()
    if row.role == "owner":
        raise ServiceError("owner_not_removable", "Hand the project to another owner first", 409, 2)
    await session.delete(row)
    await session.flush()


async def change_owner(
    session: AsyncSession, identity: Identity, task_id: UUID, body: ProjectOwnerChange
) -> list[ProjectMemberView]:
    await managed(session, identity, task_id)
    new = await session.get(TaskMember, (task_id, body.member_id), populate_existing=True)
    if new is None:
        raise ServiceError(
            "project_member_required", "The new owner must be a project member", 409, 2
        )
    member = await session.get(Member, body.member_id, populate_existing=True)
    if member is None or member.status != "active":
        raise ServiceError("member_disabled", "A disabled member cannot own a project", 409, 2)
    if new.role != "owner":
        old = await session.scalar(
            select(TaskMember).where(TaskMember.task_id == task_id, TaskMember.role == "owner")
        )
        # Demote first: the single-owner index holds at every statement.
        if old is not None:
            old.role = "member"
            await session.flush()
        new.role = "owner"
    await owner_checked(session)
    return await members(session, task_id)


async def update_sharing(
    session: AsyncSession, identity: Identity, task_id: UUID, body: ProjectSharingUpdate
) -> Task:
    human_member(identity)
    if identity.member_role != "admin":
        raise ServiceError("forbidden", "Permission denied", 403, 4)
    task = await access(session, identity, task_id, "task:create", write=True)
    task.export_to_personal_allowed = body.export_to_personal_allowed
    await session.flush()
    return task
