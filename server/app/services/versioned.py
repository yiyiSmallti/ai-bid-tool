"""Shared lifecycle of versioned org resources and their immutable task selections.

Products, features, certificates, organization profiles and templates each keep a
root row with a current revision pointer, append-only revisions, and per-task
snapshots that pin one revision per lot. A `VersionedKind` names the tables, columns,
scopes and audit actions; the resource modules add their own validation and views.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.models.entities import AuditLog, Feature, FeatureRevision, Product, Task, Template
from app.services.auth import Identity
from app.services.task_authorization import task_authorized


@dataclass(frozen=True)
class VersionedKind:
    root: Any
    revision: Any
    selection: Any
    # Column naming the root on revisions and selections, e.g. "product_id".
    key: str
    # Column naming the pinned revision on selections, e.g. "product_revision_id".
    revision_key: str
    label: str
    read_scope: str
    write_scope: str
    select_scope: str
    # Scopes for listing a task's selections, checked in this order.
    selection_list_scopes: tuple[str, ...]
    # Audit action stems: "<audit>.create" / "<audit>.update" and "<select_audit>.select".
    audit: str
    select_audit: str
    snapshot_view: Callable[[Any, Any], dict]
    conflict_exit_code: int = 2


def audit(session: AsyncSession, actor: Identity, action: str, object_id: UUID, details: dict):
    session.add(
        AuditLog(
            org_id=actor.org_id,
            actor_user_id=actor.user_id,
            actor_token_id=actor.token_id,
            action=action,
            object_id=object_id,
            details=details,
            actor_kind=actor.actor_kind,
            initiated_by="builtin_agent"
            if actor.principal_id
            else "external_agent"
            if actor.token_id
            else None,
            on_behalf_of_user_id=actor.user_id if actor.principal_id or actor.token_id else None,
            agent_principal_id=actor.principal_id,
            agent_session_id=actor.session_id,
            agent_step_id=actor.step_id,
            invocation_id=actor.invocation_id,
            job_id=actor.job_id,
            run_id=actor.run_id,
            command=details.get("command") or session.info.get("command"),
        )
    )


def strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in strings(child)]
    if isinstance(value, list):
        return [text for child in value for text in strings(child)]
    return []


async def check_placeholders(session: AsyncSession, data: dict) -> None:
    """Declarations may name confidential fields instead of carrying their values."""
    from app.services.confidential import check_references

    await check_references(session, strings(data))


Check = Callable[[], Awaitable[None]]
Attach = Callable[[Any, Any], Awaitable[None]]


async def create(
    session: AsyncSession,
    actor: Identity,
    kind: VersionedKind,
    data: dict,
    *,
    columns: dict | None = None,
    check: Check | None = None,
    attach: Attach | None = None,
):
    """Create a root with revision 1. `check` runs before any write; `attach` may
    complete the revision (for example its stored file) before it is inserted."""
    actor.require(kind.write_scope)
    await check_placeholders(session, data)
    if check is not None:
        await check()
    root = kind.root(org_id=actor.org_id, created_by=actor.user_id, current_revision=1)
    session.add(root)
    await session.flush()
    revision = kind.revision(
        org_id=actor.org_id, **{kind.key: root.id}, revision=1, data=data, **(columns or {})
    )
    if attach is not None:
        await attach(revision, root)
    session.add(revision)
    await session.flush()
    audit(
        session,
        actor,
        f"{kind.audit}.create",
        root.id,
        {"new_revision_id": str(revision.id), "revision": 1},
    )
    return revision


async def list_revisions(
    session: AsyncSession,
    actor: Identity,
    kind: VersionedKind,
    *,
    history: bool,
    root_id: UUID | None,
) -> tuple[dict, Sequence[Any]]:
    actor.require(kind.read_scope)
    roots = select(kind.root).order_by(kind.root.created_at, kind.root.id)
    if root_id is not None:
        roots = roots.where(kind.root.id == root_id)
    records = (await session.scalars(roots)).all()
    if root_id is not None and not records:
        raise not_found()
    current = {str(row.id): row.current_revision for row in records}
    key = getattr(kind.revision, kind.key)
    query = select(kind.revision).join(
        kind.root, and_(kind.root.org_id == kind.revision.org_id, kind.root.id == key)
    )
    if not history:
        query = query.where(kind.revision.revision == kind.root.current_revision)
    if root_id is not None:
        query = query.where(key == root_id)
    rows = (
        await session.scalars(query.order_by(kind.revision.created_at, kind.revision.revision))
    ).all()
    return {"history": history, "current_revisions": current}, rows


async def lock_feature_roots(
    session: AsyncSession,
    org_id: UUID,
    feature_id: UUID,
    *,
    product_ids: Sequence[UUID] = (),
    write: bool = False,
    expected_revision: int | None = None,
) -> Feature:
    """Lock feature and associated parents after tasks, with a head race fence.

    Taking an additional parent lock after discovering a changed head would
    violate UUID ordering. Report a conflict instead so a fresh request can
    resolve the new association before taking any library locks.
    """
    observed = (
        await session.execute(
            select(Feature.current_revision, FeatureRevision.product_id)
            .join(
                FeatureRevision,
                (FeatureRevision.org_id == Feature.org_id)
                & (FeatureRevision.feature_id == Feature.id)
                & (FeatureRevision.revision == Feature.current_revision),
            )
            .where(Feature.org_id == org_id, Feature.id == feature_id)
        )
    ).one_or_none()
    if observed is None:
        raise not_found()
    roots = {(feature_id, "feature"), (observed.product_id, "product")}
    roots.update((identifier, "product") for identifier in product_ids)
    feature = None
    for identifier, root_kind in sorted(roots):
        model = Feature if root_kind == "feature" else Product
        query = select(model).where(model.org_id == org_id, model.id == identifier)
        query = (
            query.with_for_update(key_share=True)
            if write and model is Feature
            else query.with_for_update(read=True)
        )
        row = await session.scalar(query.execution_options(populate_existing=True))
        if row is None:
            raise not_found()
        if isinstance(row, Feature):
            feature = row
    if feature is None:
        raise not_found()
    if feature.current_revision != observed.current_revision or (
        expected_revision is not None and feature.current_revision != expected_revision
    ):
        raise ServiceError(
            "revision_conflict",
            "Feature revision changed; read the current revision before updating",
            409,
            2,
        )
    return feature


async def update(
    session: AsyncSession,
    actor: Identity,
    kind: VersionedKind,
    root_id: UUID,
    expected_revision: int,
    data: dict,
    *,
    columns: dict | None = None,
    check: Check | None = None,
    attach: Attach | None = None,
):
    """Append the next revision when the caller saw the current one."""
    actor.require(kind.write_scope)
    # Shared library validity changes lock the affected tasks before library rows.
    # These IDs remain internal: org library authority grants no task visibility.
    affected = list(
        await session.scalars(
            select(kind.selection.task_id)
            .where(
                kind.selection.org_id == actor.org_id,
                getattr(kind.selection, kind.key) == root_id,
            )
            .distinct()
            .order_by(kind.selection.task_id)
            .limit(101)
        )
    )
    if len(affected) > 100:
        raise ServiceError("affected_task_limit", "Shared update affects too many tasks", 409, 2)
    if affected:
        await session.execute(
            select(Task.id)
            .where(Task.org_id == actor.org_id, Task.id.in_(affected))
            .order_by(Task.id)
            .with_for_update()
        )
    if kind.root is Feature:
        root = await lock_feature_roots(
            session,
            actor.org_id,
            root_id,
            product_ids=[columns["product_id"]] if columns else [],
            write=True,
            expected_revision=expected_revision,
        )
    else:
        root = await session.scalar(
            select(kind.root).where(kind.root.id == root_id).with_for_update()
        )
    if root is None:
        raise not_found()
    if root.current_revision != expected_revision:
        raise ServiceError(
            "revision_conflict",
            f"{kind.label} revision changed; read the current revision before updating",
            409,
            kind.conflict_exit_code,
        )
    await check_placeholders(session, data)
    if check is not None:
        await check()
    key = getattr(kind.revision, kind.key)
    old = await session.scalar(
        select(kind.revision).where(key == root_id, kind.revision.revision == root.current_revision)
    )
    if old is None:
        raise not_found()
    revision = kind.revision(
        org_id=actor.org_id,
        **{kind.key: root.id},
        revision=root.current_revision + 1,
        data=data,
        **(columns or {}),
    )
    if attach is not None:
        await attach(revision, root)
    session.add(revision)
    await session.flush()
    root.current_revision = revision.revision
    audit(
        session,
        actor,
        f"{kind.audit}.update",
        root.id,
        {
            "old_revision_id": str(old.id),
            "new_revision_id": str(revision.id),
            "revision": revision.revision,
        },
    )
    return revision


@task_authorized("task:read", write=True)
async def select_revision(
    session: AsyncSession,
    actor: Identity,
    kind: VersionedKind,
    task_id: UUID,
    root_id: UUID,
    revision_number: int | None,
    lot: str | None,
) -> dict:
    """Pin one revision for a task lot, replacing the active selection explicitly."""
    actor.require(kind.select_scope)
    # Serialize only selections on this task; a concurrent duplicate has one result.
    task = await session.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None:
        raise not_found()
    if kind.root is Feature:
        selected_parent = None
        if revision_number is not None:
            selected_parent = await session.scalar(
                select(FeatureRevision.product_id).where(
                    FeatureRevision.org_id == actor.org_id,
                    FeatureRevision.feature_id == root_id,
                    FeatureRevision.revision == revision_number,
                )
            )
            if selected_parent is None:
                raise not_found()
        root = await lock_feature_roots(
            session,
            actor.org_id,
            root_id,
            product_ids=[selected_parent] if selected_parent is not None else [],
        )
    else:
        root = await session.scalar(
            select(kind.root)
            .where(kind.root.id == root_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
    if root is None:
        raise not_found()
    revision = await session.scalar(
        select(kind.revision).where(
            getattr(kind.revision, kind.key) == root.id,
            kind.revision.revision == (revision_number or root.current_revision),
        )
    )
    if revision is None:
        raise not_found()
    lot = (lot or "").strip()
    selection = kind.selection
    previous = await session.scalar(
        select(selection).where(
            selection.task_id == task_id,
            getattr(selection, kind.key) == root.id,
            selection.lot == lot,
            selection.active.is_(True),
        )
    )
    if previous is not None and getattr(previous, kind.revision_key) == revision.id:
        return {
            **kind.snapshot_view(previous, revision),
            "duplicate": True,
            "replaced_snapshot_id": None,
        }
    # An inactive library resource preserves an exact active pin replay, but cannot create
    # any new pin (including an old revision or a different normalized lot). Keep
    # this check before retiring the previous selection; both share the root lock
    # with lifecycle transitions after the task/workflow authorization locks.
    if kind.root in (Product, Feature, Template) and root.lifecycle_state != "active":
        raise ServiceError(
            "resource_inactive",
            f"{kind.label} is inactive; existing selections are preserved",
            409,
            2,
        )
    if kind.root is Feature:
        current_parent = (
            select(FeatureRevision.product_id)
            .where(
                FeatureRevision.org_id == actor.org_id,
                FeatureRevision.feature_id == root.id,
                FeatureRevision.revision == root.current_revision,
            )
            .scalar_subquery()
        )
        inactive_parent = await session.scalar(
            select(Product.id)
            .where(
                Product.org_id == actor.org_id,
                (Product.id == revision.product_id) | (Product.id == current_parent),
                Product.lifecycle_state != "active",
            )
            .limit(1)
        )
        if inactive_parent is not None:
            raise ServiceError(
                "resource_inactive",
                "Parent product is inactive; existing selections are preserved",
                409,
                2,
            )
    if previous is not None:
        previous.active = False
        await session.flush()
    snapshot = selection(
        org_id=actor.org_id,
        task_id=task_id,
        **{kind.key: root.id, kind.revision_key: revision.id},
        lot=lot,
        active=True,
    )
    session.add(snapshot)
    await session.flush()
    audit(
        session,
        actor,
        f"{kind.select_audit}.select",
        snapshot.id,
        {
            "task_id": str(task_id),
            kind.key: str(root.id),
            "old_snapshot_id": str(previous.id) if previous else None,
            "old_revision_id": str(getattr(previous, kind.revision_key)) if previous else None,
            "new_revision_id": str(revision.id),
        },
    )
    return {
        **kind.snapshot_view(snapshot, revision),
        "duplicate": False,
        "replaced_snapshot_id": str(previous.id) if previous else None,
    }


@task_authorized("task:read")
async def list_selections(
    session: AsyncSession, actor: Identity, kind: VersionedKind, task_id: UUID, *, history: bool
) -> tuple[dict, list[tuple[Any, Any]]]:
    """A task's selections in creation order: active ones, or all with `history`."""
    for scope in kind.selection_list_scopes:
        actor.require(scope)
    if await session.get(Task, task_id) is None:
        raise not_found()
    selection = kind.selection
    rows = (
        await session.execute(
            select(selection, kind.revision)
            .join(
                kind.revision,
                and_(
                    selection.org_id == kind.revision.org_id,
                    getattr(selection, kind.revision_key) == kind.revision.id,
                ),
            )
            .where(selection.task_id == task_id)
            .order_by(selection.created_at, selection.id)
        )
    ).all()
    active_ids = [str(row.id) for row, _ in rows if row.active]
    visible = [(row, revision) for row, revision in rows if history or row.active]
    return {"history": history, "active_snapshot_ids": active_ids}, visible
