"""Live, bounded product projections and atomic human lifecycle transitions."""

import json
import re
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from cryptography.fernet import InvalidToken
from pydantic import ValidationError
from sqlalchemy import String, cast, func, select, text, tuple_
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ServiceError, not_found
from app.core.security import TokenSigner
from app.models.entities import AuditLog, Product, ProductRevision, SimulatedResource
from app.models.management import ResourceLifecycleEvent as Event
from app.schemas.contracts import Contract, Cost, Result
from app.schemas.management_pages import (
    DETAIL_BYTE_LIMIT,
    PAGE_BYTE_LIMIT,
    ActionHint,
    LifecycleView,
    Page,
    PageData,
    PageQuery,
    ProductDetail,
    ResourceDetailData,
    ResourceDetailQuery,
    ResourceHistoryRow,
    ResourceLifecycleData,
    ResourceLifecycleEvent,
    ResourceLifecycleSet,
    ResourceQuery,
    ResourceRef,
    ResourceRow,
)
from app.schemas.resource_contracts import ProductRevision as RevisionView
from app.services.auth import Identity, set_actor_context
from app.services.task_workflow import live_actor
from app.services.versioned import audit

WARNINGS = ["Product metadata is a declaration; it is not evidence of parameter compliance"]
CURSOR_SECONDS = 15 * 60
INPUT_BYTE_LIMIT = 16 * 1024
COMMANDS = {
    "browse": "resource product browse",
    "detail": "resource product show",
    "history": "resource product history",
    "lifecycle": "resource product lifecycle set",
    "lifecycle_history": "resource product lifecycle history",
}


def settings_for(session: AsyncSession) -> Settings:
    configured = session.info.get("management_settings")
    return configured if configured is not None else Settings.load()


def fail(code: str, message: str, status=400, exit_code=2):
    raise ServiceError(code, message, status, exit_code)


def invalid_cursor():
    fail("management_cursor_invalid", "Invalid management cursor")


def too_large():
    fail("management_result_too_large", "Management response exceeds its byte limit", 413)


@asynccontextmanager
async def read_budget(session: AsyncSession, body: Contract):
    if len(body.model_dump_json().encode()) > INPUT_BYTE_LIMIT:
        fail("invalid_input", "Management query exceeds its byte limit", 413)
    try:
        await session.execute(text("SET LOCAL statement_timeout = '2s'"))
        yield
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) == "57014":
            fail("management_query_timeout", "Management query timed out", 503, 3)
        raise


async def reader(session: AsyncSession, actor: Identity) -> Identity:
    authenticated = session.info.pop("management_authenticated_actor", None)
    live = actor if authenticated is actor else await live_actor(session, actor)
    if live.session_expires_at is not None and live.session_expires_at <= datetime.now(UTC):
        fail("invalid_session", "Invalid or expired credentials", 401, 4)
    live.require("resource:read")
    return live


def authority(actor: Identity) -> str:
    identity = [
        str(actor.org_id),
        str(actor.user_id),
        str(actor.token_id or ""),
        actor.actor_kind,
        str(actor.principal_id or ""),
        actor.role,
        sorted(actor.scopes),
    ]
    return sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


def search_tokens(value: str | None) -> list[str]:
    # Only literal word prefixes enter PostgreSQL; punctuation is never query syntax.
    return sorted(set(re.findall(r"[^\W_]+", value.casefold(), re.UNICODE))) if value else []


def filters(body: ResourceQuery) -> dict:
    if body.product_id is not None or body.implementation_status is not None:
        fail("invalid_input", "Feature-only filters are unavailable for products", 422)
    return {"tokens": search_tokens(body.q), "state": body.state}


def cursor_binding(actor: Identity, purpose: str, parent: UUID | None, normalized: dict) -> dict:
    return {
        "kind": "management_product_cursor",
        "org": str(actor.org_id),
        "authority": authority(actor),
        "purpose": purpose,
        "parent": str(parent or ""),
        # Bind normalized filters without letting Unicode JSON expansion make
        # an otherwise valid 200-character query exceed the cursor byte budget.
        "filters": sha256(
            json.dumps(
                normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest(),
        "order": "root_desc" if purpose == "browse" else "revision_desc",
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
            value.get(key) != field for key, field in expected.items()
        ):
            invalid_cursor()
        if type(value.get("exp")) is not int:
            invalid_cursor()
        if value["exp"] <= time.time():
            fail("management_cursor_expired", "Management cursor expired; restart the page", 409)
        anchor = value["anchor"]
        if not isinstance(anchor, list) or len(anchor) != 2:
            invalid_cursor()
        identifier = UUID(anchor[1])
        if purpose == "browse":
            first = datetime.fromisoformat(anchor[0])
            if first.tzinfo is None:
                invalid_cursor()
        else:
            first = anchor[0]
            if type(first) is not int or first < 1:
                invalid_cursor()
        return first, identifier
    except (InvalidToken, ValueError, TypeError, KeyError):
        invalid_cursor()


def issue_cursor(session, actor, purpose, parent, normalized, anchor):
    value = TokenSigner.for_tokens(settings_for(session)).issue(
        {**cursor_binding(actor, purpose, parent, normalized), "anchor": anchor}, CURSOR_SECONDS
    )
    if len(value) > 2048:
        invalid_cursor()
    return value


def ref(root_id: UUID) -> ResourceRef:
    return ResourceRef(kind="products", resource_id=root_id)


def lifecycle_view(root: Product) -> LifecycleView:
    return LifecycleView.model_validate(
        {"state": root.lifecycle_state, "revision": root.lifecycle_revision}
    )


def actions(actor: Identity, root: Product) -> list[ActionHint]:
    write = "resource:write" in actor.scopes
    human = actor.actor_kind == "session" and actor.token_id is None
    return [
        ActionHint(action="revise", allowed=write, reason=None if write else "role_required"),
        ActionHint(
            action="deactivate" if root.lifecycle_state == "active" else "restore",
            allowed=write and human,
            reason=None if write and human else "human_required" if not human else "role_required",
        ),
        # An org library has no task authority; selection requires a separately authorized task.
        ActionHint(
            action="select",
            allowed=False,
            reason="resource_inactive"
            if root.lifecycle_state == "inactive"
            else "task_access_required",
        ),
    ]


def check_revision(actor: Identity, root: Product, revision: ProductRevision):
    if (
        root.org_id != actor.org_id
        or revision.org_id != actor.org_id
        or revision.product_id != root.id
        or revision.revision > root.current_revision
    ):
        fail("management_integrity_error", "Product revision identity is invalid", 409, 4)


async def authors(session: AsyncSession, actor: Identity, revisions) -> dict[UUID, UUID | None]:
    if not revisions:
        return {}
    ids = [row.id for row in revisions]
    rows = (
        await session.execute(
            select(
                ProductRevision.id,
                func.count(AuditLog.id),
                func.min(cast(AuditLog.actor_user_id, String)),
            )
            .join(
                AuditLog,
                (AuditLog.org_id == ProductRevision.org_id)
                & (AuditLog.object_id == ProductRevision.product_id)
                & (AuditLog.resource_revision_id_text == cast(ProductRevision.id, String))
                & (AuditLog.details["revision"] == func.to_jsonb(ProductRevision.revision)),
            )
            .where(
                ProductRevision.org_id == actor.org_id,
                ProductRevision.id.in_(ids),
                # Constrain the audit relation itself to this authorized page.
                # Stored base columns can use the composite index under RLS.
                AuditLog.org_id == actor.org_id,
                AuditLog.resource_revision_id_text.in_([str(identifier) for identifier in ids]),
                # Fixed literals preserve partial-index eligibility for generic
                # prepared plans as well as custom plans.
                text("audit_logs.action IN ('resource.product.create','resource.product.update')"),
            )
            .group_by(ProductRevision.id)
        )
    ).all()
    result: dict[UUID, UUID | None] = {row.id: None for row in revisions}
    for revision_id, count, user_id in rows:
        if count == 1 and user_id is not None:
            result[revision_id] = UUID(user_id)
    return result


def encoded_result(
    command: str, data: Contract, items: list[Contract], currency: str, duration_ms=0
) -> Result:
    return Result(
        ok=True,
        command=command,
        data=data.model_dump(mode="json"),
        items=[item.model_dump(mode="json") for item in items],
        warnings=WARNINGS,
        cost=Cost(billing_currency=currency),
        duration_ms=duration_ms,
    )


def result_bytes(result: Result) -> int:
    # Match the final middleware encoder, including its whitespace and unicode policy.
    return len(json.dumps(result.model_dump(mode="json"), ensure_ascii=False).encode())


def page_result(command: str, page: Page, currency: str, duration_ms: int) -> Result:
    value = encoded_result(command, page.data, page.items, currency, duration_ms)
    if result_bytes(value) > PAGE_BYTE_LIMIT:
        too_large()
    return value


def detail_result(command: str, data: Contract, currency: str, duration_ms: int) -> Result:
    value = encoded_result(command, data, [], currency, duration_ms)
    if result_bytes(value) > DETAIL_BYTE_LIMIT:
        too_large()
    return value


def build_page(session, actor, purpose, parent, normalized, items, anchors, more, as_of):
    currency = settings_for(session).billing_currency
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
        if (
            result_bytes(
                encoded_result(
                    COMMANDS[purpose], data, items[:retained], currency, 9223372036854775807
                )
            )
            <= PAGE_BYTE_LIMIT
        ):
            return Page(data=data, items=items[:retained])
        retained -= 1
    if items:
        too_large()
    return Page(
        data=PageData(org_id=actor.org_id, as_of=as_of, returned=0, has_more=False), items=[]
    )


def browse_statement(actor: Identity, body: ResourceQuery, anchor=None):
    normalized = filters(body)
    statement = (
        select(Product, ProductRevision, SimulatedResource.id)
        .join(
            ProductRevision,
            (ProductRevision.org_id == Product.org_id)
            & (ProductRevision.product_id == Product.id)
            & (ProductRevision.revision == Product.current_revision),
        )
        .outerjoin(
            SimulatedResource,
            (SimulatedResource.org_id == Product.org_id)
            & (SimulatedResource.product_id == Product.id),
        )
        .where(Product.org_id == actor.org_id)
    )
    if body.state != "all":
        statement = statement.where(Product.lifecycle_state == body.state)
    if body.q is not None:
        tokens = normalized["tokens"]
        statement = (
            statement.where(
                Product.search_vector.op("@@")(
                    func.to_tsquery("simple", " & ".join(token + ":*" for token in tokens))
                )
            )
            if tokens
            else statement.where(text("false"))
        )
    if anchor:
        statement = statement.where(tuple_(Product.created_at, Product.id) < tuple_(*anchor))
    return statement.order_by(Product.created_at.desc(), Product.id.desc()).limit(body.limit + 1)


async def query(session: AsyncSession, actor: Identity, body: ResourceQuery) -> Page[ResourceRow]:
    async with read_budget(session, body):
        actor = await reader(session, actor)
        normalized = filters(body)
        anchor = open_cursor(session, actor, body, "browse", None, normalized)
        rows = (await session.execute(browse_statement(actor, body, anchor))).all()
        retained = rows[: body.limit]
        revision_authors = await authors(session, actor, [row[1] for row in retained])
        items, anchors = [], []
        for root, revision, simulated in retained:
            check_revision(actor, root, revision)
            items.append(
                ResourceRow(
                    org_id=actor.org_id,
                    ref=ref(root.id),
                    name=revision.data["name"],
                    revision_id=revision.id,
                    revision=revision.revision,
                    lifecycle=lifecycle_view(root),
                    provenance="simulated" if simulated is not None else "declared",
                    created_at=root.created_at,
                    revised_at=revision.created_at,
                    revised_by=revision_authors[revision.id],
                    actions=actions(actor, root),
                )
            )
            anchors.append([root.created_at.isoformat(), str(root.id)])
        return build_page(
            session,
            actor,
            "browse",
            None,
            normalized,
            items,
            anchors,
            len(rows) > body.limit,
            datetime.now(UTC),
        )


async def root_for(session: AsyncSession, actor: Identity, root_id: UUID, *, lock=False) -> Product:
    statement = (
        select(Product)
        .where(Product.org_id == actor.org_id, Product.id == root_id)
        .execution_options(populate_existing=True)
    )
    root = await session.scalar(statement.with_for_update() if lock else statement)
    if root is None:
        raise not_found()
    return root


async def detail(
    session: AsyncSession, actor: Identity, root_id: UUID, body: ResourceDetailQuery
) -> ResourceDetailData:
    async with read_budget(session, body):
        actor = await reader(session, actor)
        selected_revision = body.revision if body.revision is not None else Product.current_revision
        row = (
            await session.execute(
                select(Product, ProductRevision, SimulatedResource.id)
                .join(
                    ProductRevision,
                    (ProductRevision.org_id == Product.org_id)
                    & (ProductRevision.product_id == Product.id)
                    & (ProductRevision.revision == selected_revision),
                )
                .outerjoin(
                    SimulatedResource,
                    (SimulatedResource.org_id == Product.org_id)
                    & (SimulatedResource.product_id == Product.id),
                )
                .where(Product.org_id == actor.org_id, Product.id == root_id)
                .execution_options(populate_existing=True)
            )
        ).first()
        if row is None:
            raise not_found()
        root, revision, simulated = row
        check_revision(actor, root, revision)
        revision_authors = await authors(session, actor, [revision])
        try:
            value = ResourceDetailData(
                org_id=actor.org_id,
                ref=ref(root.id),
                current_revision=root.current_revision,
                lifecycle=lifecycle_view(root),
                provenance="simulated" if simulated is not None else "declared",
                detail=ProductDetail(revision=RevisionView.model_validate(revision)),
                revised_at=revision.created_at,
                revised_by=revision_authors[revision.id],
                actions=actions(actor, root),
            )
        except ValidationError as error:
            if "byte budget" in str(error):
                too_large()
            fail("management_integrity_error", "Product detail is invalid", 409, 4)
        detail_result(
            COMMANDS["detail"], value, settings_for(session).billing_currency, 9223372036854775807
        )
        return value


async def history(
    session: AsyncSession, actor: Identity, root_id: UUID, body: PageQuery
) -> Page[ResourceHistoryRow]:
    async with read_budget(session, body):
        actor = await reader(session, actor)
        anchor = open_cursor(session, actor, body, "history", root_id, {})
        relation = (ProductRevision.org_id == Product.org_id) & (
            ProductRevision.product_id == Product.id
        )
        if anchor:
            relation &= tuple_(ProductRevision.revision, ProductRevision.id) < tuple_(*anchor)
        # A left join retains an authorized root even beyond the last revision, so
        # empty history pages need no extra root-existence query.
        records = (
            await session.execute(
                select(Product, ProductRevision)
                .outerjoin(ProductRevision, relation)
                .where(Product.org_id == actor.org_id, Product.id == root_id)
                .order_by(ProductRevision.revision.desc(), ProductRevision.id.desc())
                .limit(body.limit + 1)
                .execution_options(populate_existing=True)
            )
        ).all()
        if not records:
            raise not_found()
        root = records[0][0]
        rows = [record[1] for record in records if record[1] is not None]
        revision_authors = await authors(session, actor, rows[: body.limit])
        items, anchors = [], []
        for revision in rows[: body.limit]:
            check_revision(actor, root, revision)
            items.append(
                ResourceHistoryRow(
                    org_id=actor.org_id,
                    ref=ref(root_id),
                    revision_id=revision.id,
                    revision=revision.revision,
                    name=revision.data["name"],
                    created_at=revision.created_at,
                    created_by=revision_authors[revision.id],
                    current=revision.revision == root.current_revision,
                    has_file=False,
                )
            )
            anchors.append([revision.revision, str(revision.id)])
        return build_page(
            session,
            actor,
            "history",
            root_id,
            {},
            items,
            anchors,
            len(rows) > body.limit,
            datetime.now(UTC),
        )


def event_view(row: Event, actor: Identity, root_id: UUID) -> ResourceLifecycleEvent:
    if row.org_id != actor.org_id or row.product_id != root_id or row.feature_id is not None:
        fail("management_integrity_error", "Product lifecycle identity is invalid", 409, 4)
    return ResourceLifecycleEvent.model_validate(
        {
            "id": row.id,
            "org_id": row.org_id,
            "ref": ref(root_id),
            "revision": row.revision,
            "resource_revision": row.resource_revision,
            "before": row.before_state,
            "after": row.after_state,
            "reason_code": row.reason_code,
            "actor_user_id": row.actor_user_id,
            "actor_kind": row.actor_kind,
            "created_at": row.created_at,
        }
    )


async def lifecycle_history(
    session: AsyncSession, actor: Identity, root_id: UUID, body: PageQuery
) -> Page[ResourceLifecycleEvent]:
    async with read_budget(session, body):
        actor = await reader(session, actor)
        await root_for(session, actor, root_id)
        anchor = open_cursor(session, actor, body, "lifecycle_history", root_id, {})
        statement = select(Event).where(Event.org_id == actor.org_id, Event.product_id == root_id)
        if anchor:
            statement = statement.where(tuple_(Event.revision, Event.id) < tuple_(*anchor))
        rows = (
            await session.scalars(
                statement.order_by(Event.revision.desc(), Event.id.desc()).limit(body.limit + 1)
            )
        ).all()
        items = [event_view(row, actor, root_id) for row in rows[: body.limit]]
        anchors = [[row.revision, str(row.id)] for row in rows[: body.limit]]
        return build_page(
            session,
            actor,
            "lifecycle_history",
            root_id,
            {},
            items,
            anchors,
            len(rows) > body.limit,
            datetime.now(UTC),
        )


async def set_state(
    session: AsyncSession, actor: Identity, root_id: UUID, body: ResourceLifecycleSet
) -> ResourceLifecycleData:
    root = await root_for(session, actor, root_id, lock=True)
    actor = await live_actor(session, actor)
    if actor.session_expires_at is not None and actor.session_expires_at <= datetime.now(UTC):
        fail("invalid_session", "Invalid or expired credentials", 401, 4)
    if actor.actor_kind != "session" or actor.token_id is not None:
        fail("forbidden", "A human session is required", 403, 4)
    actor.require("resource:write")
    await set_actor_context(session, actor)
    if root.current_revision != body.expected_revision:
        fail("revision_conflict", "Product revision changed", 409)
    if (
        root.lifecycle_revision != body.expected_lifecycle_revision
        or root.lifecycle_state == body.state
    ):
        fail("lifecycle_conflict", "Product lifecycle changed or transition is not applicable", 409)
    event = Event(
        org_id=actor.org_id,
        product_id=root.id,
        revision=root.lifecycle_revision + 1,
        resource_revision=root.current_revision,
        before_state=root.lifecycle_state,
        after_state=body.state,
        reason_code=body.reason_code,
        actor_user_id=actor.user_id,
    )
    session.add(event)
    await session.flush()
    audit(
        session,
        actor,
        "resource.product.deactivate" if body.state == "inactive" else "resource.product.restore",
        root.id,
        {
            "event_id": str(event.id),
            "old_lifecycle_revision": event.revision - 1,
            "new_lifecycle_revision": event.revision,
            "resource_revision": event.resource_revision,
            "before": event.before_state,
            "after": event.after_state,
            "reason_code": event.reason_code,
        },
    )
    await session.flush()
    await session.refresh(root)
    return ResourceLifecycleData(
        event=event_view(event, actor, root_id), lifecycle=lifecycle_view(root)
    )
