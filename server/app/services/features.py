"""Versioned metadata and immutable task selections; no source retrieval."""

from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.models.entities import Feature, FeatureRevision, Product, Task, TaskFeature
from app.schemas.feature_contracts import (
    FeatureCreate,
    FeatureData,
    FeatureUpdate,
    TaskFeatureSelection,
    TaskFeatureSnapshot,
)
from app.schemas.feature_contracts import (
    FeatureRevision as RevisionContract,
)
from app.services.auth import Identity
from app.services.resources import audit


def revision_data(row: FeatureRevision) -> dict:
    return RevisionContract.model_validate(row).model_dump(mode="json")


def snapshot_data(row: TaskFeature, revision: FeatureRevision) -> dict:
    return TaskFeatureSnapshot(
        id=row.id,
        org_id=row.org_id,
        task_id=row.task_id,
        feature_revision_id=revision.id,
        revision=revision.revision,
        lot=row.lot or None,
        data=FeatureData.model_validate(revision.data),
    ).model_dump(mode="json")


async def create_feature(session: AsyncSession, actor: Identity, body: FeatureCreate) -> dict:
    actor.require("resource:write")
    if await session.get(Product, body.data.product_id) is None:
        raise not_found()
    feature = Feature(org_id=actor.org_id, created_by=actor.user_id, current_revision=1)
    session.add(feature)
    await session.flush()
    revision = FeatureRevision(
        org_id=actor.org_id,
        feature_id=feature.id,
        product_id=body.data.product_id,
        revision=1,
        data=body.data.model_dump(mode="json"),
    )
    session.add(revision)
    await session.flush()
    audit(
        session,
        actor,
        "resource.feature.create",
        feature.id,
        {"new_revision_id": str(revision.id), "revision": 1},
    )
    return revision_data(revision)


async def list_features(
    session: AsyncSession, actor: Identity, *, history: bool = False, feature_id: UUID | None = None
) -> tuple[dict, list[dict]]:
    actor.require("resource:read")
    features = select(Feature).order_by(Feature.created_at, Feature.id)
    if feature_id is not None:
        features = features.where(Feature.id == feature_id)
    records = (await session.scalars(features)).all()
    if feature_id is not None and not records:
        raise not_found()
    current = {str(row.id): row.current_revision for row in records}
    query = select(FeatureRevision).join(
        Feature,
        and_(Feature.org_id == FeatureRevision.org_id, Feature.id == FeatureRevision.feature_id),
    )
    if not history:
        query = query.where(FeatureRevision.revision == Feature.current_revision)
    if feature_id is not None:
        query = query.where(FeatureRevision.feature_id == feature_id)
    rows = (
        await session.scalars(query.order_by(FeatureRevision.created_at, FeatureRevision.revision))
    ).all()
    return {"history": history, "current_revisions": current}, [revision_data(row) for row in rows]


async def update_feature(
    session: AsyncSession, actor: Identity, feature_id: UUID, body: FeatureUpdate
) -> dict:
    actor.require("resource:write")
    feature = await session.scalar(
        select(Feature).where(Feature.id == feature_id).with_for_update()
    )
    if feature is None:
        raise not_found()
    if feature.current_revision != body.expected_revision:
        raise ServiceError(
            "revision_conflict",
            "Feature revision changed; read the current revision before updating",
            409,
            2,
        )
    if await session.get(Product, body.data.product_id) is None:
        raise not_found()
    old = await session.scalar(
        select(FeatureRevision).where(
            FeatureRevision.feature_id == feature_id,
            FeatureRevision.revision == feature.current_revision,
        )
    )
    if old is None:
        raise not_found()
    revision = FeatureRevision(
        org_id=actor.org_id,
        feature_id=feature.id,
        product_id=body.data.product_id,
        revision=feature.current_revision + 1,
        data=body.data.model_dump(mode="json"),
    )
    session.add(revision)
    await session.flush()
    feature.current_revision = revision.revision
    audit(
        session,
        actor,
        "resource.feature.update",
        feature.id,
        {
            "old_revision_id": str(old.id),
            "new_revision_id": str(revision.id),
            "revision": revision.revision,
        },
    )
    return revision_data(revision)


async def select_feature(
    session: AsyncSession, actor: Identity, task_id: UUID, body: TaskFeatureSelection
) -> dict:
    actor.require("task:resource")
    # Serialize only selections on this task; a concurrent duplicate has one result.
    task = await session.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None:
        raise not_found()
    feature = await session.scalar(
        select(Feature).where(Feature.id == body.feature_id).with_for_update(read=True)
    )
    if feature is None:
        raise not_found()
    revision = await session.scalar(
        select(FeatureRevision).where(
            FeatureRevision.feature_id == feature.id,
            FeatureRevision.revision == (body.revision or feature.current_revision),
        )
    )
    if revision is None:
        raise not_found()
    lot = (body.lot or "").strip()
    previous = await session.scalar(
        select(TaskFeature).where(
            TaskFeature.task_id == task_id,
            TaskFeature.feature_id == feature.id,
            TaskFeature.lot == lot,
            TaskFeature.active.is_(True),
        )
    )
    if previous is not None and previous.feature_revision_id == revision.id:
        return {
            **snapshot_data(previous, revision),
            "duplicate": True,
            "replaced_snapshot_id": None,
        }
    if previous is not None:
        previous.active = False
        await session.flush()
    snapshot = TaskFeature(
        org_id=actor.org_id,
        task_id=task_id,
        feature_id=feature.id,
        feature_revision_id=revision.id,
        lot=lot,
        active=True,
    )
    session.add(snapshot)
    await session.flush()
    audit(
        session,
        actor,
        "task.feature.select",
        snapshot.id,
        {
            "task_id": str(task_id),
            "feature_id": str(feature.id),
            "old_snapshot_id": str(previous.id) if previous else None,
            "old_revision_id": str(previous.feature_revision_id) if previous else None,
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
    actor.require("task:read")
    if await session.get(Task, task_id) is None:
        raise not_found()
    rows = (
        await session.execute(
            select(TaskFeature, FeatureRevision)
            .join(
                FeatureRevision,
                and_(
                    TaskFeature.org_id == FeatureRevision.org_id,
                    TaskFeature.feature_revision_id == FeatureRevision.id,
                ),
            )
            .where(TaskFeature.task_id == task_id)
            .order_by(TaskFeature.created_at, TaskFeature.id)
        )
    ).all()
    active_ids = [str(row.id) for row, _ in rows if row.active]
    items = [snapshot_data(row, revision) for row, revision in rows if history or row.active]
    return {"history": history, "active_snapshot_ids": active_ids}, items
