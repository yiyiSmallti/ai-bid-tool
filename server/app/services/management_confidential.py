"""Bounded confidential projections and owner-specific compare-and-swap writes.

Read SQL projects safe columns only, never ciphertext, and never decrypts a value.
Field metadata has a concurrency counter rather than an immutable revision archive.
"""

import json
import time
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from cryptography.fernet import InvalidToken
from sqlalchemy import func, select, text, true, tuple_, union_all
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.errors import not_found
from app.core.security import Secrets, TokenSigner
from app.models.confidential import ConfidentialField as Field
from app.models.confidential import ConfidentialFieldSearchToken as SearchToken
from app.models.confidential import ConfidentialValue as Value
from app.models.entities import Task
from app.models.team_workflow import TaskMember, TaskWorkflow
from app.schemas.confidential_contracts import (
    ConfidentialFieldView,
    ConfidentialValueSet,
    ConfidentialValueView,
)
from app.schemas.contracts import Contract, Cost, Result
from app.schemas.management_pages import (
    DETAIL_BYTE_LIMIT,
    PAGE_BYTE_LIMIT,
    ConfidentialHistoryQuery,
    ConfidentialQuery,
    ConfidentialValueRevisionSet,
    Page,
    PageData,
)
from app.services import confidential
from app.services.auth import Identity
from app.services.management_templates import (
    authority,
    fail,
    invalid_cursor,
    read_budget,
    result_bytes,
    search_tokens,
    settings_for,
    too_large,
)
from app.services.task_workflow import access, live_actor

COMMANDS = {
    "fields": "confidential field browse",
    "values": "confidential browse",
    "history": "confidential history-page",
    "set_value": "confidential set-checked",
}
CURSOR_SECONDS = 15 * 60
PREFIX_BATCH_SIZE = 128


async def reader(session: AsyncSession, actor: Identity) -> Identity:
    authenticated = session.info.pop("management_authenticated_actor", None)
    live = actor if authenticated is actor else await live_actor(session, actor)
    if live.session_expires_at is not None and live.session_expires_at <= datetime.now(UTC):
        fail("invalid_session", "Invalid or expired credentials", 401, 4)
    return live


async def read_owner(session: AsyncSession, actor: Identity, task_id: UUID | None) -> dict:
    """Read-only task visibility, matching task_workflow.access's read ceiling.

    The caller has refreshed org/identity authority. One joined read keeps task
    pages bounded; writes still use access() and its task-first row locks.
    """
    if task_id is None:
        actor.require("confidential:read")
        return {}
    row = (
        await session.execute(
            select(TaskWorkflow, TaskMember)
            .select_from(Task)
            .join(
                TaskWorkflow,
                (TaskWorkflow.org_id == Task.org_id) & (TaskWorkflow.task_id == Task.id),
            )
            .outerjoin(
                TaskMember,
                (TaskMember.org_id == Task.org_id)
                & (TaskMember.task_id == Task.id)
                & (TaskMember.user_id == actor.user_id)
                & TaskMember.active.is_(True),
            )
            .where(Task.org_id == actor.org_id, Task.id == task_id)
            .execution_options(populate_existing=True)
        )
    ).first()
    recovery = actor.actor_kind == "session" and actor.token_id is None and actor.role == "admin"
    if row is None or (row[1] is None and not recovery):
        raise not_found()
    actor.require("task:read")
    actor.require("confidential:read")
    workflow, member = row
    return {
        "task_id": str(task_id),
        "workflow_revision": workflow.revision,
        "workflow_state": workflow.state,
        "member_revision": member.revision if member else None,
        "member_role": member.role if member else None,
        "review_domains": sorted(member.review_domains) if member else [],
    }


def filters(body: ConfidentialQuery, owner: dict) -> dict:
    return {
        "tokens": search_tokens(body.q),
        "has_query": body.q is not None,
        "field_id": str(body.field_id) if body.field_id else None,
        "archived": body.archived,
        **owner,
    }


def cursor_binding(actor, purpose, parent, normalized):
    return {
        "kind": "management_confidential_cursor",
        "org": str(actor.org_id),
        "authority": authority(actor),
        "purpose": purpose,
        "parent": str(parent or ""),
        "filters": sha256(
            json.dumps(
                normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest(),
        "order": "version_desc" if purpose == "history" else "key_asc",
    }


def open_cursor(session, actor, body, purpose, parent, normalized):
    if body.cursor is None:
        return None
    try:
        value = json.loads(
            TokenSigner.for_tokens(settings_for(session)).cipher.decrypt(body.cursor.encode())
        )
        expected = cursor_binding(actor, purpose, parent, normalized)
        if not isinstance(value, dict) or any(
            value.get(key) != item for key, item in expected.items()
        ):
            invalid_cursor()
        if type(value.get("exp")) is not int:
            invalid_cursor()
        if value["exp"] <= time.time():
            fail("management_cursor_expired", "Management cursor expired; restart the page", 409)
        anchor = value["anchor"]
        if not isinstance(anchor, list) or len(anchor) != 2:
            invalid_cursor()
        if purpose == "history":
            if type(anchor[0]) is not int or anchor[0] < 1:
                invalid_cursor()
        elif not isinstance(anchor[0], str) or not 2 <= len(anchor[0]) <= 48:
            invalid_cursor()
        return anchor[0], UUID(anchor[1])
    except (InvalidToken, ValueError, TypeError, KeyError):
        invalid_cursor()


def issue_cursor(session, actor, purpose, parent, normalized, anchor):
    result = TokenSigner.for_tokens(settings_for(session)).issue(
        {**cursor_binding(actor, purpose, parent, normalized), "anchor": anchor}, CURSOR_SECONDS
    )
    if len(result) > 2048:
        invalid_cursor()
    return result


def encoded_result(
    command: str, data: Contract, items: list[Contract], currency: str, duration_ms=0
):
    return Result(
        ok=True,
        command=command,
        data=data.model_dump(mode="json"),
        items=[item.model_dump(mode="json") for item in items],
        warnings=[],
        cost=Cost(billing_currency=currency),
        duration_ms=duration_ms,
    )


def page_result(command: str, page: Page, currency: str, duration_ms: int) -> Result:
    result = encoded_result(command, page.data, page.items, currency, duration_ms)
    if result_bytes(result) > PAGE_BYTE_LIMIT:
        too_large()
    return result


def detail_result(command: str, data: Contract, currency: str, duration_ms: int) -> Result:
    result = encoded_result(command, data, [], currency, duration_ms)
    if result_bytes(result) > DETAIL_BYTE_LIMIT:
        too_large()
    return result


def build_page(session, actor, purpose, parent, normalized, items, anchors, more, as_of):
    retained = len(items)
    while retained:
        has_more = more or retained < len(items)
        continuation = (
            issue_cursor(session, actor, purpose, parent, normalized, anchors[retained - 1])
            if has_more
            else None
        )
        data = PageData(
            org_id=actor.org_id,
            as_of=as_of,
            returned=retained,
            next_cursor=continuation,
            has_more=has_more,
        )
        result = encoded_result(
            COMMANDS[purpose],
            data,
            items[:retained],
            settings_for(session).billing_currency,
            9223372036854775807,
        )
        if result_bytes(result) <= PAGE_BYTE_LIMIT:
            return Page(data=data, items=items[:retained])
        retained -= 1
    if items:
        too_large()
    return Page(
        data=PageData(org_id=actor.org_id, as_of=as_of, returned=0, has_more=False), items=[]
    )


def prefix_bounds(lexeme, prefix: str):
    """Literal UTF-8/C range; increment a Unicode scalar, never emit a surrogate."""
    for index in range(len(prefix) - 1, -1, -1):
        point = ord(prefix[index])
        if point < 0x10FFFF:
            successor = 0xE000 if point == 0xD7FF else point + 1
            upper = prefix[:index] + chr(successor)
            return lexeme >= prefix, lexeme < upper
    return (lexeme >= prefix,)


def prefix_candidates(actor: Identity, tokens: list[str]):
    def batch(rows):
        # Identical ordering keeps both arrays aligned, including equal tokens
        # owned by different fields. An empty chunk terminates the recursion.
        order = (rows.c.token, rows.c.field_id)
        return select(
            func.array_agg(aggregate_order_by(rows.c.token, *order)).label("tokens"),
            func.array_agg(aggregate_order_by(rows.c.field_id, *order)).label("field_ids"),
        ).having(func.count() > 0)

    seed = aliased(SearchToken, name="prefix_seed")
    first_rows = (
        select(seed.token, seed.field_id)
        .where(seed.org_id == actor.org_id, *prefix_bounds(seed.token, tokens[0]))
        .order_by(seed.token, seed.field_id)
        .limit(PREFIX_BATCH_SIZE)
        .subquery("first_tokens")
    )
    batches = batch(first_rows).cte("prefix_batches", recursive=True)
    seek = aliased(SearchToken, name="prefix_seek")
    next_rows = (
        select(seek.token, seek.field_id)
        .where(
            seek.org_id == actor.org_id,
            *prefix_bounds(seek.token, tokens[0]),
            tuple_(seek.token, seek.field_id)
            > tuple_(
                batches.c.tokens[func.cardinality(batches.c.tokens)],
                batches.c.field_ids[func.cardinality(batches.c.field_ids)],
            ),
        )
        .order_by(seek.token, seek.field_id)
        .limit(PREFIX_BATCH_SIZE)
        .correlate(batches)
        .subquery("next_tokens")
    )
    next_batch = batch(next_rows).lateral("next_batch")
    batches = batches.union_all(
        select(next_batch.c.tokens, next_batch.c.field_ids)
        .select_from(batches)
        .join(next_batch, true())
    )
    # A materialization fence alone does not constrain its inner scan. Traverse
    # the prefix B-tree in bounded, ordered windows instead of an unordered
    # DISTINCT over the tenant. Continue to exhaustion: this is not a search cap.
    candidates = (
        select(func.unnest(batches.c.field_ids).label("field_id"))
        .distinct()
        .cte("prefix_fields")
        .prefix_with("MATERIALIZED")
    )
    statement = select(candidates.c.field_id)
    for index, token in enumerate(tokens[1:]):
        other = aliased(SearchToken, name=f"prefix_{index}")
        statement = statement.where(
            select(other.field_id)
            .where(
                other.org_id == actor.org_id,
                other.field_id == candidates.c.field_id,
                *prefix_bounds(other.token, token),
            )
            .correlate(candidates)
            # Preserve an indexed owner lookup for each candidate instead of
            # flattening EXISTS into a hash join over a broad remaining token.
            .limit(1)
            .offset(0)
            .exists()
        )
    # Multiple matching lexemes of one field cause only one owner probe and one
    # hydration. Page LIMIT remains after all owner and field filters.
    return statement.cte("matching_fields").prefix_with("MATERIALIZED")


def fields_statement(actor: Identity, body: ConfidentialQuery, anchor=None, *, values=False):
    statement = select(Field).where(Field.org_id == actor.org_id)
    if body.field_id is not None:
        statement = statement.where(Field.id == body.field_id)
    if not body.archived:
        statement = statement.where(Field.archived.is_(False))
    if values and body.task_id is None:
        statement = statement.where(Field.scope == "org")
    if anchor:
        statement = statement.where(tuple_(Field.key, Field.id) > tuple_(*anchor))
    projected = Field
    if body.q is not None:
        tokens = search_tokens(body.q)
        if tokens:
            candidates = prefix_candidates(actor, tokens)
            # @@ is not leakproof: under FORCE RLS it cannot drive the GIN
            # scan. Use leakproof scalar token ranges to choose candidates,
            # then keep @@ only as the original semantic check on point reads.
            # OFFSET 0 prevents flattening this parameterized lookup back into
            # an all-fields join/scan. All filters still precede the page LIMIT.
            point = (
                statement.where(
                    Field.id == candidates.c.field_id,
                    Field.search_vector.op("@@")(
                        func.to_tsquery("simple", " & ".join(token + ":*" for token in tokens))
                    ),
                )
                .correlate(candidates)
                .offset(0)
                .lateral("matching_field")
            )
            projected = aliased(Field, point)
            statement = select(projected).select_from(candidates).join(point, true())
        else:
            statement = statement.where(text("false"))
    return (
        statement.order_by(projected.key, projected.id)
        .limit(body.limit + 1)
        .execution_options(populate_existing=True)
    )


def safe_columns():
    return [
        Value.id.label("value_id"),
        Value.org_id.label("value_org_id"),
        Value.field_id.label("value_field_id"),
        Value.task_id.label("value_task_id"),
        Value.version,
        Value.tail,
        Value.created_by.label("set_by"),
        Value.created_at.label("set_at"),
    ]


def values_statement(actor: Identity, body: ConfidentialQuery, anchor=None):
    # LIMIT on fields precedes the lateral lookup. Each arm matches its partial
    # owner index, so task context never scans values belonging to other tasks.
    page_fields = aliased(Field, fields_statement(actor, body, anchor, values=True).subquery())
    branches = []
    for scope, task_id in (("org", None), ("task", body.task_id)):
        if scope == "task" and task_id is None:
            continue
        branches.append(
            select(*safe_columns())
            .where(
                Value.org_id == actor.org_id,
                Value.field_id == page_fields.id,
                page_fields.scope == scope,
                Value.task_id.is_(None) if task_id is None else Value.task_id == task_id,
            )
            .order_by(Value.version.desc(), Value.id.desc())
            .limit(1)
            .correlate(page_fields)
        )
    latest = union_all(*branches).lateral("current_value")
    return (
        select(page_fields, *latest.c)
        .outerjoin(latest, true())
        .order_by(page_fields.key, page_fields.id)
        .execution_options(populate_existing=True)
    )


def check_field(actor: Identity, field: Field):
    if field.org_id != actor.org_id:
        fail("management_integrity_error", "Confidential field identity is invalid", 409, 4)


def value_view(actor, field, record, task_id):
    check_field(actor, field)
    owner = task_id if field.scope == "task" else None
    value_id = record["value_id"]
    if value_id is not None and (
        record["value_org_id"] != actor.org_id
        or record["value_field_id"] != field.id
        or record["value_task_id"] != owner
    ):
        fail("management_integrity_error", "Confidential value identity is invalid", 409, 4)
    return ConfidentialValueView(
        field_id=field.id,
        key=field.key,
        placeholder=confidential.redaction.secret_placeholder(field.key),
        label=field.label,
        kind=field.kind,
        scope=field.scope,
        task_id=owner,
        status="filled" if value_id else "missing",
        value_id=value_id,
        version=record["version"],
        tail=record["tail"],
        set_by=record["set_by"],
        set_at=record["set_at"],
    )


async def fields(
    session: AsyncSession, actor: Identity, body: ConfidentialQuery
) -> Page[ConfidentialFieldView]:
    async with read_budget(session, body):
        actor = await reader(session, actor)
        owner = await read_owner(session, actor, body.task_id)
        normalized = filters(body, owner)
        anchor = open_cursor(session, actor, body, "fields", None, normalized)
        rows = (await session.scalars(fields_statement(actor, body, anchor))).all()
        items = []
        for field in rows[: body.limit]:
            check_field(actor, field)
            items.append(ConfidentialFieldView.model_validate(confidential.field_view(field)))
        return build_page(
            session,
            actor,
            "fields",
            None,
            normalized,
            items,
            [[field.key, str(field.id)] for field in rows[: body.limit]],
            len(rows) > body.limit,
            datetime.now(UTC),
        )


async def values(
    session: AsyncSession, actor: Identity, body: ConfidentialQuery
) -> Page[ConfidentialValueView]:
    async with read_budget(session, body):
        actor = await reader(session, actor)
        owner = await read_owner(session, actor, body.task_id)
        normalized = filters(body, owner)
        anchor = open_cursor(session, actor, body, "values", None, normalized)
        rows = (await session.execute(values_statement(actor, body, anchor))).all()
        items = [
            value_view(actor, row[0], row._mapping, body.task_id) for row in rows[: body.limit]
        ]
        return build_page(
            session,
            actor,
            "values",
            None,
            normalized,
            items,
            [[row[0].key, str(row[0].id)] for row in rows[: body.limit]],
            len(rows) > body.limit,
            datetime.now(UTC),
        )


def history_statement(actor: Identity, field_id: UUID, body: ConfidentialHistoryQuery, anchor=None):
    relation = (
        (Value.org_id == actor.org_id)
        & (Value.field_id == field_id)
        & (Value.task_id.is_(None) if body.task_id is None else Value.task_id == body.task_id)
    )
    if anchor:
        relation &= tuple_(Value.version, Value.id) < tuple_(*anchor)
    # Bound revisions before joining metadata even for a very long history.
    revisions = (
        select(*safe_columns())
        .where(relation)
        .order_by(Value.version.desc(), Value.id.desc())
        .limit(body.limit + 1)
        .subquery()
    )
    return (
        select(Field, *revisions.c)
        .outerjoin(revisions, true())
        .where(Field.org_id == actor.org_id, Field.id == field_id)
        .order_by(revisions.c.version.desc(), revisions.c.value_id.desc())
        .execution_options(populate_existing=True)
    )


def check_owner(field: Field, task_id: UUID | None):
    if field.scope == "task" and task_id is None:
        fail("confidential_task_required", "A task-scope field takes a value per task")
    if field.scope == "org" and task_id is not None:
        fail("confidential_task_not_allowed", "An org-scope field has one value for all tasks")


async def history(
    session: AsyncSession, actor: Identity, field_id: UUID, body: ConfidentialHistoryQuery
) -> Page[ConfidentialValueView]:
    async with read_budget(session, body):
        actor = await reader(session, actor)
        owner = await read_owner(session, actor, body.task_id)
        anchor = open_cursor(session, actor, body, "history", field_id, owner)
        rows = (await session.execute(history_statement(actor, field_id, body, anchor))).all()
        if not rows:
            raise not_found()
        field = rows[0][0]
        check_field(actor, field)
        check_owner(field, body.task_id)
        records = [row for row in rows if row._mapping["value_id"] is not None]
        items = [
            value_view(actor, field, row._mapping, body.task_id) for row in records[: body.limit]
        ]
        return build_page(
            session,
            actor,
            "history",
            field_id,
            owner,
            items,
            [
                [row._mapping["version"], str(row._mapping["value_id"])]
                for row in records[: body.limit]
            ],
            len(records) > body.limit,
            datetime.now(UTC),
        )


async def set_value(
    session: AsyncSession,
    actor: Identity,
    field_id: UUID,
    body: ConfidentialValueRevisionSet,
    secrets: Secrets,
) -> ConfidentialValueView:
    actor = await reader(session, actor)
    if body.task_id is not None:
        # Preserve task/workflow -> field lock order used by the legacy setter.
        await access(session, actor, body.task_id, scope="confidential:write", write=True)
    actor.require("confidential:write")
    confidential.human(actor)
    field = await confidential.require_field(session, field_id, lock=True)
    check_field(actor, field)
    check_owner(field, body.task_id)
    if field.archived:
        fail("confidential_field_archived", "The field is archived", 409)
    if field.revision != body.expected_field_revision:
        fail("revision_conflict", "The field changed; reload and retry", 409)
    current = await session.scalar(
        select(Value.id)
        .where(
            Value.org_id == actor.org_id,
            Value.field_id == field_id,
            Value.task_id.is_(None) if body.task_id is None else Value.task_id == body.task_id,
        )
        .order_by(Value.version.desc())
        .limit(1)
    )
    if current != body.expected_value_id:
        fail("confidential_value_conflict", "The value changed; reload and enter it again", 409)
    # Generic serialization deliberately omits the secret. Explicitly construct
    # only the legacy input fields, once, while the owner/field locks are held.
    saved = await confidential.set_value(
        session,
        actor,
        field_id,
        ConfidentialValueSet(value=body.value, task_id=body.task_id),
        secrets,
    )
    return ConfidentialValueView.model_validate(saved)
