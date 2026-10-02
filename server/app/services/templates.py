"""Versioned metadata and immutable task selections; no source retrieval."""

import hashlib
from uuid import UUID, uuid4

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.models.entities import Task, TaskTemplate, Template, TemplateRevision
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
from app.services.auth import Identity
from app.services.resources import audit


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


async def create_template(
    session: AsyncSession,
    actor: Identity,
    body: TemplateCreate,
    descriptor: TemplateFile,
    content: bytes,
    storage: Storage,
) -> dict:
    actor.require("template:write")
    template = Template(org_id=actor.org_id, created_by=actor.user_id, current_revision=1)
    session.add(template)
    await session.flush()
    revision = TemplateRevision(
        org_id=actor.org_id,
        template_id=template.id,
        revision=1,
        id=uuid4(),
        data=body.data.model_dump(mode="json"),
        file=descriptor.model_dump(mode="json"),
        storage_key="",
    )
    revision.storage_key = (
        f"org/{actor.org_id}/template/{template.id}/{revision.id}/{descriptor.sha256}.docx"
    )
    await storage.put(actor.org_id, revision.storage_key, content)
    session.add(revision)
    await session.flush()
    audit(
        session,
        actor,
        "resource.template.create",
        template.id,
        {"new_revision_id": str(revision.id), "revision": 1},
    )
    return revision_data(revision)


async def list_templates(
    session: AsyncSession,
    actor: Identity,
    *,
    history: bool = False,
    template_id: UUID | None = None,
) -> tuple[dict, list[dict]]:
    actor.require("template:read")
    templates = select(Template).order_by(Template.created_at, Template.id)
    if template_id is not None:
        templates = templates.where(Template.id == template_id)
    records = (await session.scalars(templates)).all()
    if template_id is not None and not records:
        raise not_found()
    current = {str(row.id): row.current_revision for row in records}
    query = select(TemplateRevision).join(
        Template,
        and_(
            Template.org_id == TemplateRevision.org_id,
            Template.id == TemplateRevision.template_id,
        ),
    )
    if not history:
        query = query.where(TemplateRevision.revision == Template.current_revision)
    if template_id is not None:
        query = query.where(TemplateRevision.template_id == template_id)
    rows = (
        await session.scalars(
            query.order_by(TemplateRevision.created_at, TemplateRevision.revision)
        )
    ).all()
    return {"history": history, "current_revisions": current}, [revision_data(row) for row in rows]


async def update_template(
    session: AsyncSession,
    actor: Identity,
    template_id: UUID,
    body: TemplateUpdate,
    descriptor: TemplateFile,
    content: bytes,
    storage: Storage,
) -> dict:
    actor.require("template:write")
    template = await session.scalar(
        select(Template).where(Template.id == template_id).with_for_update()
    )
    if template is None:
        raise not_found()
    if template.current_revision != body.expected_revision:
        raise ServiceError(
            "revision_conflict",
            "Template revision changed; read the current revision before updating",
            409,
            4,
        )
    old = await session.scalar(
        select(TemplateRevision).where(
            TemplateRevision.template_id == template_id,
            TemplateRevision.revision == template.current_revision,
        )
    )
    if old is None:
        raise not_found()
    revision = TemplateRevision(
        org_id=actor.org_id,
        template_id=template.id,
        revision=template.current_revision + 1,
        id=uuid4(),
        data=body.data.model_dump(mode="json"),
        file=descriptor.model_dump(mode="json"),
        storage_key="",
    )
    revision.storage_key = (
        f"org/{actor.org_id}/template/{template.id}/{revision.id}/{descriptor.sha256}.docx"
    )
    await storage.put(actor.org_id, revision.storage_key, content)
    session.add(revision)
    await session.flush()
    template.current_revision = revision.revision
    audit(
        session,
        actor,
        "resource.template.update",
        template.id,
        {
            "old_revision_id": str(old.id),
            "new_revision_id": str(revision.id),
            "revision": revision.revision,
        },
    )
    return revision_data(revision)


async def select_template(
    session: AsyncSession, actor: Identity, task_id: UUID, body: TaskTemplateSelection
) -> dict:
    actor.require("task:template")
    # Serialize only selections on this task; a concurrent duplicate has one result.
    task = await session.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None:
        raise not_found()
    template = await session.scalar(
        select(Template).where(Template.id == body.template_id).with_for_update(read=True)
    )
    if template is None:
        raise not_found()
    revision = await session.scalar(
        select(TemplateRevision).where(
            TemplateRevision.template_id == template.id,
            TemplateRevision.revision == (body.revision or template.current_revision),
        )
    )
    if revision is None:
        raise not_found()
    lot = (body.lot or "").strip()
    previous = await session.scalar(
        select(TaskTemplate).where(
            TaskTemplate.task_id == task_id,
            TaskTemplate.template_id == template.id,
            TaskTemplate.lot == lot,
            TaskTemplate.active.is_(True),
        )
    )
    if previous is not None and previous.template_revision_id == revision.id:
        return {
            **snapshot_data(previous, revision),
            "duplicate": True,
            "replaced_snapshot_id": None,
        }
    if previous is not None:
        previous.active = False
        await session.flush()
    snapshot = TaskTemplate(
        org_id=actor.org_id,
        task_id=task_id,
        template_id=template.id,
        template_revision_id=revision.id,
        lot=lot,
        active=True,
    )
    session.add(snapshot)
    await session.flush()
    audit(
        session,
        actor,
        "task.template.select",
        snapshot.id,
        {
            "task_id": str(task_id),
            "template_id": str(template.id),
            "old_snapshot_id": str(previous.id) if previous else None,
            "old_revision_id": str(previous.template_revision_id) if previous else None,
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
    actor.require("template:read")
    actor.require("task:read")
    if await session.get(Task, task_id) is None:
        raise not_found()
    rows = (
        await session.execute(
            select(TaskTemplate, TemplateRevision)
            .join(
                TemplateRevision,
                and_(
                    TaskTemplate.org_id == TemplateRevision.org_id,
                    TaskTemplate.template_revision_id == TemplateRevision.id,
                ),
            )
            .where(TaskTemplate.task_id == task_id)
            .order_by(TaskTemplate.created_at, TaskTemplate.id)
        )
    ).all()
    active_ids = [str(row.id) for row, _ in rows if row.active]
    items = [snapshot_data(row, revision) for row, revision in rows if history or row.active]
    return {"history": history, "active_snapshot_ids": active_ids}, items


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
