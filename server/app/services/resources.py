"""Versioned metadata and immutable task selections; no source retrieval."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Product, ProductRevision, TaskResource
from app.schemas.resource_contracts import (
    ProductCreate,
    ProductData,
    ProductUpdate,
    TaskProductSelection,
    TaskProductSnapshot,
)
from app.schemas.resource_contracts import (
    ProductRevision as RevisionContract,
)
from app.services import versioned
from app.services.auth import Identity
from app.services.versioned import VersionedKind


def revision_data(row: ProductRevision) -> dict:
    return RevisionContract.model_validate(row).model_dump(mode="json")


def snapshot_data(row: TaskResource, revision: ProductRevision) -> dict:
    return TaskProductSnapshot(
        id=row.id,
        org_id=row.org_id,
        task_id=row.task_id,
        product_revision_id=revision.id,
        revision=revision.revision,
        lot=row.lot or None,
        data=ProductData.model_validate(revision.data),
    ).model_dump(mode="json")


PRODUCTS = VersionedKind(
    root=Product,
    revision=ProductRevision,
    selection=TaskResource,
    key="product_id",
    revision_key="product_revision_id",
    label="Product",
    read_scope="resource:read",
    write_scope="resource:write",
    select_scope="task:resource",
    selection_list_scopes=("task:read",),
    audit="resource.product",
    select_audit="task.resource",
    snapshot_view=snapshot_data,
)


async def create_product(session: AsyncSession, actor: Identity, body: ProductCreate) -> dict:
    revision = await versioned.create(session, actor, PRODUCTS, body.data.model_dump(mode="json"))
    return revision_data(revision)


async def list_products(
    session: AsyncSession, actor: Identity, *, history: bool = False, product_id: UUID | None = None
) -> tuple[dict, list[dict]]:
    data, rows = await versioned.list_revisions(
        session, actor, PRODUCTS, history=history, root_id=product_id
    )
    return data, [revision_data(row) for row in rows]


async def update_product(
    session: AsyncSession, actor: Identity, product_id: UUID, body: ProductUpdate
) -> dict:
    revision = await versioned.update(
        session,
        actor,
        PRODUCTS,
        product_id,
        body.expected_revision,
        body.data.model_dump(mode="json"),
    )
    return revision_data(revision)


async def select_product(
    session: AsyncSession, actor: Identity, task_id: UUID, body: TaskProductSelection
) -> dict:
    return await versioned.select_revision(
        session, actor, PRODUCTS, task_id, body.product_id, body.revision, body.lot
    )


async def list_selections(
    session: AsyncSession, actor: Identity, task_id: UUID, *, history: bool = False
) -> tuple[dict, list[dict]]:
    data, rows = await versioned.list_selections(session, actor, PRODUCTS, task_id, history=history)
    return data, [snapshot_data(row, revision) for row, revision in rows]
