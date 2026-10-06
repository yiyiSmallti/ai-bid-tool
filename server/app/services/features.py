"""Versioned metadata and immutable task selections; no source retrieval."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.models.entities import Feature, FeatureRevision, Product, TaskFeature
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
from app.services import versioned
from app.services.auth import Identity
from app.services.versioned import VersionedKind


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


FEATURES = VersionedKind(
    root=Feature,
    revision=FeatureRevision,
    selection=TaskFeature,
    key="feature_id",
    revision_key="feature_revision_id",
    label="Feature",
    read_scope="resource:read",
    write_scope="resource:write",
    select_scope="task:resource",
    selection_list_scopes=("task:read",),
    audit="resource.feature",
    select_audit="task.feature",
    snapshot_view=snapshot_data,
)


def product_exists(session: AsyncSession, product_id: UUID):
    async def check():
        product = await session.scalar(
            select(Product)
            .where(Product.id == product_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        if product is None:
            raise not_found()
        if product.lifecycle_state != "active":
            raise ServiceError(
                "resource_inactive",
                "Parent product is inactive; restore it before association",
                409,
                2,
            )

    return check


async def create_feature(session: AsyncSession, actor: Identity, body: FeatureCreate) -> dict:
    revision = await versioned.create(
        session,
        actor,
        FEATURES,
        body.data.model_dump(mode="json"),
        columns={"product_id": body.data.product_id},
        check=product_exists(session, body.data.product_id),
    )
    return revision_data(revision)


async def list_features(
    session: AsyncSession, actor: Identity, *, history: bool = False, feature_id: UUID | None = None
) -> tuple[dict, list[dict]]:
    data, rows = await versioned.list_revisions(
        session, actor, FEATURES, history=history, root_id=feature_id
    )
    return data, [revision_data(row) for row in rows]


async def update_feature(
    session: AsyncSession, actor: Identity, feature_id: UUID, body: FeatureUpdate
) -> dict:
    revision = await versioned.update(
        session,
        actor,
        FEATURES,
        feature_id,
        body.expected_revision,
        body.data.model_dump(mode="json"),
        columns={"product_id": body.data.product_id},
        check=product_exists(session, body.data.product_id),
    )
    return revision_data(revision)


async def select_feature(
    session: AsyncSession, actor: Identity, task_id: UUID, body: TaskFeatureSelection
) -> dict:
    return await versioned.select_revision(
        session, actor, FEATURES, task_id, body.feature_id, body.revision, body.lot
    )


async def list_selections(
    session: AsyncSession, actor: Identity, task_id: UUID, *, history: bool = False
) -> tuple[dict, list[dict]]:
    data, rows = await versioned.list_selections(session, actor, FEATURES, task_id, history=history)
    return data, [snapshot_data(row, revision) for row, revision in rows]
