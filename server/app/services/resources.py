"""Versioned metadata and immutable task selections; no source retrieval."""

from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.models.entities import AuditLog, Product, ProductRevision, Task, TaskResource
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
from app.services.auth import Identity


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


def audit(session: AsyncSession, actor: Identity, action: str, object_id: UUID, details: dict):
    session.add(
        AuditLog(
            org_id=actor.org_id,
            actor_user_id=actor.user_id,
            actor_token_id=actor.token_id,
            action=action,
            object_id=object_id,
            details=details,
        )
    )


async def create_product(session: AsyncSession, actor: Identity, body: ProductCreate) -> dict:
    actor.require("resource:write")
    product = Product(org_id=actor.org_id, created_by=actor.user_id, current_revision=1)
    session.add(product)
    await session.flush()
    revision = ProductRevision(
        org_id=actor.org_id,
        product_id=product.id,
        revision=1,
        data=body.data.model_dump(mode="json"),
    )
    session.add(revision)
    await session.flush()
    audit(
        session,
        actor,
        "resource.product.create",
        product.id,
        {"new_revision_id": str(revision.id), "revision": 1},
    )
    return revision_data(revision)


async def list_products(
    session: AsyncSession, actor: Identity, *, history: bool = False, product_id: UUID | None = None
) -> tuple[dict, list[dict]]:
    actor.require("resource:read")
    products = select(Product).order_by(Product.created_at, Product.id)
    if product_id is not None:
        products = products.where(Product.id == product_id)
    records = (await session.scalars(products)).all()
    if product_id is not None and not records:
        raise not_found()
    current = {str(row.id): row.current_revision for row in records}
    query = select(ProductRevision).join(
        Product,
        and_(Product.org_id == ProductRevision.org_id, Product.id == ProductRevision.product_id),
    )
    if not history:
        query = query.where(ProductRevision.revision == Product.current_revision)
    if product_id is not None:
        query = query.where(ProductRevision.product_id == product_id)
    rows = (
        await session.scalars(query.order_by(ProductRevision.created_at, ProductRevision.revision))
    ).all()
    return {"history": history, "current_revisions": current}, [revision_data(row) for row in rows]


async def update_product(
    session: AsyncSession, actor: Identity, product_id: UUID, body: ProductUpdate
) -> dict:
    actor.require("resource:write")
    product = await session.scalar(
        select(Product).where(Product.id == product_id).with_for_update()
    )
    if product is None:
        raise not_found()
    if product.current_revision != body.expected_revision:
        raise ServiceError(
            "revision_conflict",
            "Product revision changed; read the current revision before updating",
            409,
            2,
        )
    old = await session.scalar(
        select(ProductRevision).where(
            ProductRevision.product_id == product_id,
            ProductRevision.revision == product.current_revision,
        )
    )
    if old is None:
        raise not_found()
    revision = ProductRevision(
        org_id=actor.org_id,
        product_id=product.id,
        revision=product.current_revision + 1,
        data=body.data.model_dump(mode="json"),
    )
    session.add(revision)
    await session.flush()
    product.current_revision = revision.revision
    audit(
        session,
        actor,
        "resource.product.update",
        product.id,
        {
            "old_revision_id": str(old.id),
            "new_revision_id": str(revision.id),
            "revision": revision.revision,
        },
    )
    return revision_data(revision)


async def select_product(
    session: AsyncSession, actor: Identity, task_id: UUID, body: TaskProductSelection
) -> dict:
    actor.require("task:resource")
    # Serialize only selections on this task; a concurrent duplicate has one result.
    task = await session.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None:
        raise not_found()
    product = await session.scalar(
        select(Product).where(Product.id == body.product_id).with_for_update(read=True)
    )
    if product is None:
        raise not_found()
    revision = await session.scalar(
        select(ProductRevision).where(
            ProductRevision.product_id == product.id,
            ProductRevision.revision == (body.revision or product.current_revision),
        )
    )
    if revision is None:
        raise not_found()
    lot = (body.lot or "").strip()
    previous = await session.scalar(
        select(TaskResource).where(
            TaskResource.task_id == task_id,
            TaskResource.product_id == product.id,
            TaskResource.lot == lot,
            TaskResource.active.is_(True),
        )
    )
    if previous is not None and previous.product_revision_id == revision.id:
        return {
            **snapshot_data(previous, revision),
            "duplicate": True,
            "replaced_snapshot_id": None,
        }
    if previous is not None:
        previous.active = False
        await session.flush()
    snapshot = TaskResource(
        org_id=actor.org_id,
        task_id=task_id,
        product_id=product.id,
        product_revision_id=revision.id,
        lot=lot,
        active=True,
    )
    session.add(snapshot)
    await session.flush()
    audit(
        session,
        actor,
        "task.resource.select",
        snapshot.id,
        {
            "task_id": str(task_id),
            "product_id": str(product.id),
            "old_snapshot_id": str(previous.id) if previous else None,
            "old_revision_id": str(previous.product_revision_id) if previous else None,
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
            select(TaskResource, ProductRevision)
            .join(
                ProductRevision,
                and_(
                    TaskResource.org_id == ProductRevision.org_id,
                    TaskResource.product_revision_id == ProductRevision.id,
                ),
            )
            .where(TaskResource.task_id == task_id)
            .order_by(TaskResource.created_at, TaskResource.id)
        )
    ).all()
    active_ids = [str(row.id) for row, _ in rows if row.active]
    items = [snapshot_data(row, revision) for row, revision in rows if history or row.active]
    return {"history": history, "active_snapshot_ids": active_ids}, items
