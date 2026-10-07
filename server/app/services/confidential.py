"""Confidential fields: models and cards see `{{secret.key}}`, exports fill the value.

Values are encrypted, append-only and never logged, audited, returned by list views
or sent to a model. Only a human admin or bidder session can set or reveal one.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import NoReturn
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.core.security import Secrets
from app.models.confidential import ConfidentialField, ConfidentialValue
from app.models.entities import Task
from app.schemas.confidential_contracts import (
    ConfidentialFieldCreate,
    ConfidentialFieldUpdate,
    ConfidentialFieldView,
    ConfidentialReveal,
    ConfidentialValueSet,
    ConfidentialValueView,
)
from app.services import redaction
from app.services.auth import Identity
from app.services.task_authorization import task_authorized
from app.services.versioned import audit

TAIL_KINDS = {"identity", "bank_account", "contact"}


def fail(code: str, message: str, status: int = 400, exit_code: int = 2) -> NoReturn:
    raise ServiceError(code, message, status, exit_code)


def human(actor: Identity) -> None:
    if actor.actor_kind != "session" or actor.token_id is not None:
        raise ServiceError("human_required", "Only a signed-in person can do this", 403, 4)


def tail(kind: str, value: str) -> str | None:
    """The last four characters of a long number-like value; nothing for amounts or names."""
    characters = [char for char in value if char.isalnum()]
    if kind not in TAIL_KINDS or len(characters) < 8:
        return None
    return "".join(characters[-4:])


def field_view(row: ConfidentialField) -> dict:
    return ConfidentialFieldView(
        id=row.id,
        key=row.key,
        placeholder=redaction.secret_placeholder(row.key),
        label=row.label,
        kind=row.kind,  # type: ignore[arg-type]
        scope=row.scope,  # type: ignore[arg-type]
        archived=row.archived,
        revision=row.revision,
        created_at=row.created_at,
    ).model_dump(mode="json")


def value_view(
    field: ConfidentialField, row: ConfidentialValue | None, task_id: UUID | None
) -> dict:
    return ConfidentialValueView(
        field_id=field.id,
        key=field.key,
        placeholder=redaction.secret_placeholder(field.key),
        label=field.label,
        kind=field.kind,  # type: ignore[arg-type]
        scope=field.scope,  # type: ignore[arg-type]
        task_id=task_id if field.scope == "task" else None,
        status="filled" if row else "missing",
        value_id=row.id if row else None,
        version=row.version if row else None,
        tail=row.tail if row else None,
        set_by=row.created_by if row else None,
        set_at=row.created_at if row else None,
    ).model_dump(mode="json")


async def require_field(
    session: AsyncSession, field_id: UUID, *, lock: bool = False
) -> ConfidentialField:
    query = (
        select(ConfidentialField)
        .where(ConfidentialField.id == field_id)
        .execution_options(populate_existing=True)
    )
    row = await session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise not_found()
    return row


async def current_value(
    session: AsyncSession, field: ConfidentialField, task_id: UUID | None
) -> ConfidentialValue | None:
    owner = task_id if field.scope == "task" else None
    if field.scope == "task" and owner is None:
        return None
    return await session.scalar(
        select(ConfidentialValue)
        .where(
            ConfidentialValue.field_id == field.id,
            ConfidentialValue.task_id.is_(None)
            if owner is None
            else ConfidentialValue.task_id == owner,
        )
        .order_by(ConfidentialValue.version.desc())
        .limit(1)
    )


async def create_field(
    session: AsyncSession, actor: Identity, body: ConfidentialFieldCreate
) -> dict:
    actor.require("confidential:write")
    human(actor)
    if await session.scalar(select(ConfidentialField.id).where(ConfidentialField.key == body.key)):
        fail("confidential_key_exists", "A confidential field already uses this key", 409)
    row = ConfidentialField(
        id=uuid4(),
        org_id=actor.org_id,
        created_by=actor.user_id,
        key=body.key,
        label=body.label,
        kind=body.kind,
        scope=body.scope,
        archived=False,
        revision=1,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    audit(
        session,
        actor,
        "confidential.field.create",
        row.id,
        {"revision": row.revision, "kind": row.kind, "scope": row.scope},
    )
    return field_view(row)


async def list_fields(
    session: AsyncSession, actor: Identity, *, archived: bool = False
) -> list[dict]:
    actor.require("confidential:read")
    query = select(ConfidentialField).order_by(ConfidentialField.key)
    if not archived:
        query = query.where(ConfidentialField.archived.is_(False))
    return [field_view(row) for row in (await session.scalars(query)).all()]


async def update_field(
    session: AsyncSession, actor: Identity, field_id: UUID, body: ConfidentialFieldUpdate
) -> dict:
    actor.require("confidential:write")
    human(actor)
    row = await require_field(session, field_id, lock=True)
    if row.revision != body.expected_revision:
        fail("revision_conflict", "The field changed; reload and retry", 409)
    if body.label is not None:
        row.label = body.label
    if body.archived is not None:
        row.archived = body.archived
    row.revision += 1
    await session.flush()
    audit(
        session,
        actor,
        "confidential.field.update",
        row.id,
        {
            "revision": row.revision,
            "archived": row.archived,
            "label_changed": body.label is not None,
        },
    )
    return field_view(row)


@task_authorized("confidential:write", task="body.task_id", write=True, optional=True)
async def set_value(
    session: AsyncSession,
    actor: Identity,
    field_id: UUID,
    body: ConfidentialValueSet,
    secrets: Secrets,
) -> dict:
    actor.require("confidential:write")
    human(actor)
    field = await require_field(session, field_id, lock=True)
    if field.archived:
        fail("confidential_field_archived", "The field is archived", 409)
    if field.scope == "task" and body.task_id is None:
        fail("confidential_task_required", "A task-scope field takes a value per task")
    if field.scope == "org" and body.task_id is not None:
        fail("confidential_task_not_allowed", "An org-scope field has one value for all tasks")
    if body.task_id is not None and await session.get(Task, body.task_id) is None:
        raise not_found()
    owner = body.task_id
    # The field row lock serializes versions for every owner of this field.
    version = (
        await session.scalar(
            select(func.max(ConfidentialValue.version)).where(
                ConfidentialValue.field_id == field.id,
                ConfidentialValue.task_id.is_(None)
                if owner is None
                else ConfidentialValue.task_id == owner,
            )
        )
        or 0
    ) + 1
    row = ConfidentialValue(
        id=uuid4(),
        org_id=actor.org_id,
        created_by=actor.user_id,
        field_id=field.id,
        task_id=owner,
        version=version,
        encrypted_value=secrets.encrypt(body.value),
        tail=tail(field.kind, body.value),
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    audit(
        session,
        actor,
        "confidential.value.set",
        field.id,
        {
            "value_id": str(row.id),
            "task_id": str(owner) if owner else None,
            "version": version,
        },
    )
    return value_view(field, row, owner)


@task_authorized("confidential:read", optional=True)
async def list_values(session: AsyncSession, actor: Identity, task_id: UUID | None) -> list[dict]:
    """Every active field's state: org fields always, task fields only for a task."""
    actor.require("confidential:read")
    if task_id is not None:
        actor.require("task:read")
        if await session.get(Task, task_id) is None:
            raise not_found()
    fields = (
        await session.scalars(
            select(ConfidentialField)
            .where(ConfidentialField.archived.is_(False))
            .order_by(ConfidentialField.key)
        )
    ).all()
    return [
        value_view(field, await current_value(session, field, task_id), task_id)
        for field in fields
        if task_id is not None or field.scope == "org"
    ]


@task_authorized("confidential:read", optional=True)
async def history(
    session: AsyncSession, actor: Identity, field_id: UUID, task_id: UUID | None
) -> list[dict]:
    actor.require("confidential:read")
    field = await require_field(session, field_id)
    rows = (
        await session.scalars(
            select(ConfidentialValue)
            .where(
                ConfidentialValue.field_id == field.id,
                ConfidentialValue.task_id.is_(None)
                if task_id is None
                else ConfidentialValue.task_id == task_id,
            )
            .order_by(ConfidentialValue.version.desc())
        )
    ).all()
    return [value_view(field, row, task_id) for row in rows]


@task_authorized("confidential:reveal", parent=("value_id", "confidential_values"), optional=True)
async def reveal(session: AsyncSession, actor: Identity, value_id: UUID, secrets: Secrets) -> dict:
    actor.require("confidential:reveal")
    human(actor)
    row = await session.get(ConfidentialValue, value_id)
    if row is None:
        raise not_found()
    field = await require_field(session, row.field_id)
    audit(
        session,
        actor,
        "confidential.value.reveal",
        field.id,
        {"value_id": str(row.id), "task_id": str(row.task_id) if row.task_id else None},
    )
    return ConfidentialReveal(
        field_id=field.id,
        value_id=row.id,
        key=field.key,
        value=secrets.decrypt(row.encrypted_value),
    ).model_dump(mode="json")


async def value_text(session: AsyncSession, value_id: UUID, secrets: Secrets) -> str:
    """A value for an export render only; the caller must never return or log it."""
    row = await session.get(ConfidentialValue, value_id)
    if row is None:
        raise not_found()
    return secrets.decrypt(row.encrypted_value)


@dataclass(frozen=True)
class Entry:
    field: ConfidentialField
    value: ConfidentialValue | None


async def task_entries(session: AsyncSession, task_id: UUID) -> dict[str, Entry]:
    """Current value rows for every active field as seen from one task, by key."""
    fields = (
        await session.scalars(
            select(ConfidentialField).where(ConfidentialField.archived.is_(False))
        )
    ).all()
    return {
        field.key: Entry(field, await current_value(session, field, task_id)) for field in fields
    }


def library(entries: dict[str, Entry], secrets: Secrets) -> list[redaction.LibraryValue]:
    """Outbound substitution patterns. Decrypted values never leave this list."""
    found = []
    for key, entry in sorted(entries.items()):
        if entry.value is not None:
            item = redaction.library_value(
                key, entry.field.kind, secrets.decrypt(entry.value.encrypted_value)
            )
            if item is not None:
                found.append(item)
    return found


def prompt_fields(entries: dict[str, Entry]) -> list[dict]:
    return [
        {
            "placeholder": redaction.secret_placeholder(key),
            "label": entry.field.label,
            "kind": entry.field.kind,
        }
        for key, entry in sorted(entries.items())
    ]


async def check_references(session: AsyncSession, texts: Iterable[str | None]) -> None:
    """Refuse a card text that names a field this org does not have or has archived."""
    keys = {key for text in texts for key in redaction.secret_keys(text)}
    if not keys:
        return
    active = set(
        (
            await session.scalars(
                select(ConfidentialField.key).where(
                    ConfidentialField.key.in_(keys), ConfidentialField.archived.is_(False)
                )
            )
        ).all()
    )
    if keys - active:
        fail(
            "unknown_confidential_field",
            "The response names a confidential field that does not exist or is archived",
            422,
        )
