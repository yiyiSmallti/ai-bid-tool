"""Versioned metadata and immutable task selections; no source retrieval."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import OrgProfile, OrgProfileRevision, TaskOrgProfile
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
from app.services import versioned
from app.services.auth import Identity
from app.services.versioned import VersionedKind


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


PROFILES = VersionedKind(
    root=OrgProfile,
    revision=OrgProfileRevision,
    selection=TaskOrgProfile,
    key="profile_id",
    revision_key="profile_revision_id",
    label="OrgProfile",
    read_scope="profile:read",
    write_scope="profile:write",
    select_scope="task:profile",
    selection_list_scopes=("profile:read", "task:read"),
    audit="resource.profile",
    select_audit="task.profile",
    snapshot_view=snapshot_data,
)


async def create_profile(session: AsyncSession, actor: Identity, body: OrgProfileCreate) -> dict:
    revision = await versioned.create(session, actor, PROFILES, body.data.model_dump(mode="json"))
    return revision_data(revision)


async def list_profiles(
    session: AsyncSession, actor: Identity, *, history: bool = False, profile_id: UUID | None = None
) -> tuple[dict, list[dict]]:
    data, rows = await versioned.list_revisions(
        session, actor, PROFILES, history=history, root_id=profile_id
    )
    return data, [revision_data(row) for row in rows]


async def update_profile(
    session: AsyncSession, actor: Identity, profile_id: UUID, body: OrgProfileUpdate
) -> dict:
    revision = await versioned.update(
        session,
        actor,
        PROFILES,
        profile_id,
        body.expected_revision,
        body.data.model_dump(mode="json"),
    )
    return revision_data(revision)


async def select_profile(
    session: AsyncSession, actor: Identity, task_id: UUID, body: TaskOrgProfileSelection
) -> dict:
    return await versioned.select_revision(
        session, actor, PROFILES, task_id, body.profile_id, body.revision, body.lot
    )


async def list_selections(
    session: AsyncSession, actor: Identity, task_id: UUID, *, history: bool = False
) -> tuple[dict, list[dict]]:
    data, rows = await versioned.list_selections(session, actor, PROFILES, task_id, history=history)
    return data, [snapshot_data(row, revision) for row, revision in rows]
