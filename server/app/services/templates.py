"""Versioned metadata and immutable task selections; no source retrieval."""

import hashlib
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.models.entities import TaskTemplate, Template, TemplateRevision
from app.providers.storage import Storage
from app.schemas.template_contracts import (
    TaskTemplateSelection,
    TaskTemplateSnapshot,
    TemplateCreate,
    TemplateData,
    TemplateFile,
    TemplateUpdate,
)
from app.schemas.template_contracts import (
    TemplateRevision as RevisionContract,
)
from app.services import versioned
from app.services.auth import Identity
from app.services.versioned import VersionedKind


def revision_data(row: TemplateRevision) -> dict:
    return RevisionContract.model_validate(row).model_dump(mode="json")


def snapshot_data(row: TaskTemplate, revision: TemplateRevision) -> dict:
    return TaskTemplateSnapshot(
        id=row.id,
        org_id=row.org_id,
        task_id=row.task_id,
        template_revision_id=revision.id,
        revision=revision.revision,
        lot=row.lot or None,
        data=TemplateData.model_validate(revision.data),
        file=TemplateFile.model_validate(revision.file),
    ).model_dump(mode="json")


TEMPLATES = VersionedKind(
    root=Template,
    revision=TemplateRevision,
    selection=TaskTemplate,
    key="template_id",
    revision_key="template_revision_id",
    label="Template",
    read_scope="template:read",
    write_scope="template:write",
    select_scope="task:template",
    selection_list_scopes=("template:read", "task:read"),
    audit="resource.template",
    select_audit="task.template",
    snapshot_view=snapshot_data,
    conflict_exit_code=4,
)


def stored_file(actor: Identity, descriptor: TemplateFile, content: bytes, storage: Storage):
    """Revision columns and the hook that stores the file under the revision's key."""
    columns = {"id": uuid4(), "file": descriptor.model_dump(mode="json"), "storage_key": ""}

    async def attach(revision: TemplateRevision, template: Template):
        revision.storage_key = (
            f"org/{actor.org_id}/template/{template.id}/{revision.id}/{descriptor.sha256}.docx"
        )
        await storage.put(actor.org_id, revision.storage_key, content)

    return columns, attach


async def create_template(
    session: AsyncSession,
    actor: Identity,
    body: TemplateCreate,
    descriptor: TemplateFile,
    content: bytes,
    storage: Storage,
) -> dict:
    columns, attach = stored_file(actor, descriptor, content, storage)
    revision = await versioned.create(
        session,
        actor,
        TEMPLATES,
        body.data.model_dump(mode="json"),
        columns=columns,
        attach=attach,
    )
    return revision_data(revision)


async def list_templates(
    session: AsyncSession,
    actor: Identity,
    *,
    history: bool = False,
    template_id: UUID | None = None,
) -> tuple[dict, list[dict]]:
    data, rows = await versioned.list_revisions(
        session, actor, TEMPLATES, history=history, root_id=template_id
    )
    return data, [revision_data(row) for row in rows]


async def update_template(
    session: AsyncSession,
    actor: Identity,
    template_id: UUID,
    body: TemplateUpdate,
    descriptor: TemplateFile,
    content: bytes,
    storage: Storage,
) -> dict:
    columns, attach = stored_file(actor, descriptor, content, storage)
    revision = await versioned.update(
        session,
        actor,
        TEMPLATES,
        template_id,
        body.expected_revision,
        body.data.model_dump(mode="json"),
        columns=columns,
        attach=attach,
    )
    return revision_data(revision)


async def select_template(
    session: AsyncSession, actor: Identity, task_id: UUID, body: TaskTemplateSelection
) -> dict:
    return await versioned.select_revision(
        session, actor, TEMPLATES, task_id, body.template_id, body.revision, body.lot
    )


async def list_selections(
    session: AsyncSession, actor: Identity, task_id: UUID, *, history: bool = False
) -> tuple[dict, list[dict]]:
    data, rows = await versioned.list_selections(
        session, actor, TEMPLATES, task_id, history=history
    )
    return data, [snapshot_data(row, revision) for row, revision in rows]


async def require_revision(session: AsyncSession, actor: Identity, revision_id: UUID):
    actor.require("template:read")
    revision = await session.get(TemplateRevision, revision_id)
    if revision is None:
        raise not_found()
    return revision


async def read_revision(
    session: AsyncSession, actor: Identity, revision_id: UUID, storage: Storage
):
    revision = await require_revision(session, actor, revision_id)
    content = await storage.read(actor.org_id, revision.storage_key)
    descriptor = TemplateFile.model_validate(revision.file)
    if (
        len(content) != descriptor.size_bytes
        or hashlib.sha256(content).hexdigest() != descriptor.sha256
    ):
        raise ServiceError("template_integrity", "Template file failed integrity checks", 500, 4)
    return content, descriptor
