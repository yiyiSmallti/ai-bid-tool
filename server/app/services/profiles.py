"""Versioned metadata and immutable task selections; no source retrieval."""

from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.models.entities import OrgProfile, OrgProfileRevision, Task, TaskOrgProfile
from app.schemas.profile_contracts import (
    OrgProfileCreate,
    OrgProfileData,
    OrgProfileUpdate,
    TaskOrgProfileSelection,
    TaskOrgProfileSnapshot,
)
from app.schemas.profile_contracts import (
    OrgProfileRevision as RevisionContract,
)
from app.services.auth import Identity
from app.services.resources import audit


def revision_data(row: OrgProfileRevision) -> dict:
    return RevisionContract.model_validate(row).model_dump(mode="json")


def snapshot_data(row: TaskOrgProfile, revision: OrgProfileRevision) -> dict:
    return TaskOrgProfileSnapshot(
        id=row.id,
        org_id=row.org_id,
        task_id=row.task_id,
        profile_revision_id=revision.id,
        revision=revision.revision,
        lot=row.lot or None,
        data=OrgProfileData.model_validate(revision.data),
    ).model_dump(mode="json")


async def create_profile(session: AsyncSession, actor: Identity, body: OrgProfileCreate) -> dict:
    actor.require("profile:write")
    profile = OrgProfile(org_id=actor.org_id, created_by=actor.user_id, current_revision=1)
    session.add(profile)
    await session.flush()
    revision = OrgProfileRevision(
        org_id=actor.org_id,
        profile_id=profile.id,
        revision=1,
        data=body.data.model_dump(mode="json"),
    )
    session.add(revision)
    await session.flush()
    audit(
        session,
        actor,
        "resource.profile.create",
        profile.id,
        {"new_revision_id": str(revision.id), "revision": 1},
    )
    return revision_data(revision)


async def list_profiles(
    session: AsyncSession, actor: Identity, *, history: bool = False, profile_id: UUID | None = None
) -> tuple[dict, list[dict]]:
    actor.require("profile:read")
    org_profiles = select(OrgProfile).order_by(OrgProfile.created_at, OrgProfile.id)
    if profile_id is not None:
        org_profiles = org_profiles.where(OrgProfile.id == profile_id)
    records = (await session.scalars(org_profiles)).all()
    if profile_id is not None and not records:
        raise not_found()
    current = {str(row.id): row.current_revision for row in records}
    query = select(OrgProfileRevision).join(
        OrgProfile,
        and_(
            OrgProfile.org_id == OrgProfileRevision.org_id,
            OrgProfile.id == OrgProfileRevision.profile_id,
        ),
    )
    if not history:
        query = query.where(OrgProfileRevision.revision == OrgProfile.current_revision)
    if profile_id is not None:
        query = query.where(OrgProfileRevision.profile_id == profile_id)
    rows = (
        await session.scalars(
            query.order_by(OrgProfileRevision.created_at, OrgProfileRevision.revision)
        )
    ).all()
    return {"history": history, "current_revisions": current}, [revision_data(row) for row in rows]


async def update_profile(
    session: AsyncSession, actor: Identity, profile_id: UUID, body: OrgProfileUpdate
) -> dict:
    actor.require("profile:write")
    profile = await session.scalar(
        select(OrgProfile).where(OrgProfile.id == profile_id).with_for_update()
    )
    if profile is None:
        raise not_found()
    if profile.current_revision != body.expected_revision:
        raise ServiceError(
            "revision_conflict",
            "OrgProfile revision changed; read the current revision before updating",
            409,
            2,
        )
    old = await session.scalar(
        select(OrgProfileRevision).where(
            OrgProfileRevision.profile_id == profile_id,
            OrgProfileRevision.revision == profile.current_revision,
        )
    )
    if old is None:
        raise not_found()
    revision = OrgProfileRevision(
        org_id=actor.org_id,
        profile_id=profile.id,
        revision=profile.current_revision + 1,
        data=body.data.model_dump(mode="json"),
    )
    session.add(revision)
    await session.flush()
    profile.current_revision = revision.revision
    audit(
        session,
        actor,
        "resource.profile.update",
        profile.id,
        {
            "old_revision_id": str(old.id),
            "new_revision_id": str(revision.id),
            "revision": revision.revision,
        },
    )
    return revision_data(revision)


async def select_profile(
    session: AsyncSession, actor: Identity, task_id: UUID, body: TaskOrgProfileSelection
) -> dict:
    actor.require("task:profile")
    # Serialize only selections on this task; a concurrent duplicate has one result.
    task = await session.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None:
        raise not_found()
    profile = await session.scalar(
        select(OrgProfile).where(OrgProfile.id == body.profile_id).with_for_update(read=True)
    )
    if profile is None:
        raise not_found()
    revision = await session.scalar(
        select(OrgProfileRevision).where(
            OrgProfileRevision.profile_id == profile.id,
            OrgProfileRevision.revision == (body.revision or profile.current_revision),
        )
    )
    if revision is None:
        raise not_found()
    lot = (body.lot or "").strip()
    previous = await session.scalar(
        select(TaskOrgProfile).where(
            TaskOrgProfile.task_id == task_id,
            TaskOrgProfile.profile_id == profile.id,
            TaskOrgProfile.lot == lot,
            TaskOrgProfile.active.is_(True),
        )
    )
    if previous is not None and previous.profile_revision_id == revision.id:
        return {
            **snapshot_data(previous, revision),
            "duplicate": True,
            "replaced_snapshot_id": None,
        }
    if previous is not None:
        previous.active = False
        await session.flush()
    snapshot = TaskOrgProfile(
        org_id=actor.org_id,
        task_id=task_id,
        profile_id=profile.id,
        profile_revision_id=revision.id,
        lot=lot,
        active=True,
    )
    session.add(snapshot)
    await session.flush()
    audit(
        session,
        actor,
        "task.profile.select",
        snapshot.id,
        {
            "task_id": str(task_id),
            "profile_id": str(profile.id),
            "old_snapshot_id": str(previous.id) if previous else None,
            "old_revision_id": str(previous.profile_revision_id) if previous else None,
            "new_revision_id": str(revision.id),
        },
    )
    return {
        **snapshot_data(snapshot, revision),
        "duplicate": False,
        "replaced_snapshot_id": str(previous.id) if previous else None,
    }


async def list_selections(
    session: AsyncSession, actor: Identity, task_id: UUID, *, history: bool = False
) -> tuple[dict, list[dict]]:
    actor.require("profile:read")
    actor.require("task:read")
    if await session.get(Task, task_id) is None:
        raise not_found()
    rows = (
        await session.execute(
            select(TaskOrgProfile, OrgProfileRevision)
            .join(
                OrgProfileRevision,
                and_(
                    TaskOrgProfile.org_id == OrgProfileRevision.org_id,
                    TaskOrgProfile.profile_revision_id == OrgProfileRevision.id,
                ),
            )
            .where(TaskOrgProfile.task_id == task_id)
            .order_by(TaskOrgProfile.created_at, TaskOrgProfile.id)
        )
    ).all()
    active_ids = [str(row.id) for row, _ in rows if row.active]
    items = [snapshot_data(row, revision) for row, revision in rows if history or row.active]
    return {"history": history, "active_snapshot_ids": active_ids}, items
