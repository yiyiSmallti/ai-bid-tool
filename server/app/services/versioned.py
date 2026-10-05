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
from app.models.entities import AuditLog, Task
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
    root = await session.scalar(select(kind.root).where(kind.root.id == root_id).with_for_update())
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
    root = await session.scalar(
        select(kind.root).where(kind.root.id == root_id).with_for_update(read=True)
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
