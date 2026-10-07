"""Bounded org-memory reads; mutations remain in the original memory services."""

from datetime import UTC, datetime
from typing import cast as typed_cast
from uuid import UUID

from sqlalchemy import String, cast, func, literal_column, or_, select, text, true, tuple_
from sqlalchemy.orm import aliased

from app.core.errors import not_found
from app.memory import crud
from app.memory.access import human_admin, normalize
from app.models.entities import AuditLog, Job
from app.models.memory import Memory, MemoryFeedbackEvent, MemoryRevision
from app.models.team_workflow import TaskMember, TaskWorkflow
from app.schemas.contracts import Cost, Result
from app.schemas.management_pages import (
    DETAIL_BYTE_LIMIT,
    PAGE_BYTE_LIMIT,
    ActionCode,
    ActionHint,
    MemoryDetailData,
    MemoryEffectiveStatus,
    MemoryFeedbackRow,
    MemoryQuery,
    Page,
    PageData,
    PageQuery,
    ResourceDetailQuery,
)
from app.services import management_products as bounded
from app.services.task_workflow import live_actor

WARNINGS = ["Memory is guidance, never evidence or human confirmation"]


async def reader(session, actor, *, scope: str | None = "memory:read"):
    authenticated = session.info.pop("management_authenticated_actor", None)
    actor = actor if authenticated is actor else await live_actor(session, actor)
    if actor.session_expires_at is not None and actor.session_expires_at <= datetime.now(UTC):
        bounded.fail("invalid_session", "Invalid or expired credentials", 401, 4)
    if scope is not None:
        actor.require(scope)
    return actor


def encoded(command, data, items, currency, duration_ms=0):
    return Result(
        ok=True,
        command=command,
        data=data.model_dump(mode="json"),
        items=[item.model_dump(mode="json") for item in items],
        warnings=WARNINGS,
        cost=Cost(billing_currency=currency),
        duration_ms=duration_ms,
    )


def page_result(command, page, currency, duration_ms=0):
    output = encoded(command, page.data, page.items, currency, duration_ms)
    if bounded.result_bytes(output) > PAGE_BYTE_LIMIT:
        bounded.too_large()
    return output


def detail_result(command, value, currency, duration_ms=0):
    output = encoded(command, value, [], currency, duration_ms)
    if bounded.result_bytes(output) > DETAIL_BYTE_LIMIT:
        bounded.too_large()
    return output


def binding(collection, filters=None):
    # The shared encrypted cursor codec binds this namespace as well as authority,
    # parent and filters; resource-library cursors cannot be reused here.
    return {"collection": collection, **(filters or {})}


def build_page(session, actor, command, purpose, parent, filters, items, anchors, more, as_of):
    for count in range(len(items), 0, -1):
        continuation = (
            bounded.issue_cursor(session, actor, purpose, parent, filters, anchors[count - 1])
            if more or count < len(items)
            else None
        )
        data = PageData(
            org_id=actor.org_id,
            as_of=as_of,
            returned=count,
            next_cursor=continuation,
            has_more=continuation is not None,
        )
        if (
            bounded.result_bytes(
                encoded(
                    command,
                    data,
                    items[:count],
                    bounded.settings_for(session).billing_currency,
                    9223372036854775807,
                )
            )
            <= PAGE_BYTE_LIMIT
        ):
            return Page(data=data, items=items[:count])
    if items:
        bounded.too_large()
    return Page(
        data=PageData(org_id=actor.org_id, as_of=as_of, returned=0, has_more=False), items=[]
    )


def filters(body):
    return binding(
        "memory",
        {
            "tokens": bounded.search_tokens(normalize(body.q)) if body.q else [],
            "kind": body.kind,
            "status": body.status,
            "tags": body.tags,
            "expiry": body.expiry,
            "include_deleted": body.include_deleted,
        },
    )


def browse_statement(actor, body, anchor=None, as_of=None):
    as_of = as_of or datetime.now(UTC)
    statement = (
        select(Memory, MemoryRevision)
        .join(
            MemoryRevision,
            (MemoryRevision.org_id == Memory.org_id)
            & (MemoryRevision.memory_id == Memory.id)
            & (MemoryRevision.id == Memory.current_revision_id)
            & (MemoryRevision.revision == Memory.revision),
        )
        .where(Memory.org_id == actor.org_id, Memory.scope == "org")
    )
    if not body.include_deleted:
        statement = statement.where(Memory.deleted_at.is_(None))
    if body.kind:
        statement = statement.where(Memory.search_kind == body.kind)
    if body.status:
        statement = statement.where(Memory.search_status == body.status)
    if body.expiry == "expired":
        statement = statement.where(Memory.search_expires_at <= as_of)
    elif body.expiry == "unexpired":
        statement = statement.where(
            or_(Memory.search_expires_at.is_(None), Memory.search_expires_at > as_of)
        )
    if body.tags:
        statement = statement.where(Memory.search_tags.contains(body.tags))
    if body.q is not None:
        tokens = filters(body)["tokens"]
        statement = (
            statement.where(
                Memory.search_vector.op("@@")(
                    func.to_tsquery("simple", " & ".join(token + ":*" for token in tokens))
                )
            )
            if tokens
            else statement.where(text("false"))
        )
    if anchor:
        statement = statement.where(tuple_(Memory.created_at, Memory.id) < tuple_(*anchor))
    return statement.order_by(Memory.created_at.desc(), Memory.id.desc()).limit(body.limit + 1)


def check_revision(actor, root, revision):
    if (
        root.org_id != actor.org_id
        or revision.org_id != actor.org_id
        or revision.memory_id != root.id
        or revision.revision > root.revision
        or root.scope != "org"
    ):
        bounded.fail("management_integrity_error", "Memory revision identity is invalid", 409, 4)


def memory_view(root, revision, source, as_of):
    return crud.memory_view(root, revision, source, as_of=as_of)


async def query(session, actor, body: MemoryQuery, settings):
    session.info["management_settings"] = settings
    async with bounded.read_budget(session, body):
        actor = await reader(session, actor)
        if body.include_deleted:
            human_admin(actor, "memory:manage")
        normalized = filters(body)
        anchor = bounded.open_cursor(session, actor, body, "browse", None, normalized)
        as_of = datetime.now(UTC)
        rows = (await session.execute(browse_statement(actor, body, anchor, as_of))).all()
        sources = await crud.visible_sources(session, actor, [r for _, r in rows[: body.limit]])
        items, anchors = [], []
        for root, revision in rows[: body.limit]:
            check_revision(actor, root, revision)
            items.append(memory_view(root, revision, sources[revision.id], as_of))
            anchors.append([root.created_at.isoformat(), str(root.id)])
        return build_page(
            session,
            actor,
            "memory browse",
            "browse",
            None,
            normalized,
            items,
            anchors,
            len(rows) > body.limit,
            as_of,
        )


async def authors(session, actor, revisions):
    ids = [row.id for row in revisions]
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(
                MemoryRevision.id,
                func.count(AuditLog.id),
                func.min(cast(AuditLog.actor_user_id, String)),
            )
            .join(
                AuditLog,
                (AuditLog.org_id == MemoryRevision.org_id)
                & (AuditLog.object_id == MemoryRevision.memory_id)
                & (AuditLog.memory_revision_id_text == cast(MemoryRevision.id, String))
                & (AuditLog.details["revision"] == func.to_jsonb(MemoryRevision.revision)),
            )
            .where(
                MemoryRevision.org_id == actor.org_id,
                MemoryRevision.id.in_(ids),
                AuditLog.org_id == actor.org_id,
                AuditLog.memory_revision_id_text.in_([str(value) for value in ids]),
                text(
                    "audit_logs.action IN ('memory.create','memory.update','memory.approve','memory.reject','memory.disable','memory.delete','memory.candidate.publish')"
                ),
            )
            .group_by(MemoryRevision.id)
        )
    ).all()
    result: dict[UUID, UUID | None] = {value: None for value in ids}
    for revision_id, count, user_id in rows:
        if count == 1 and user_id:
            result[revision_id] = UUID(user_id)
    return result


async def detail(session, actor, identifier, body: ResourceDetailQuery, settings):
    session.info["management_settings"] = settings
    async with bounded.read_budget(session, body):
        actor = await reader(session, actor)
        selected = body.revision if body.revision is not None else Memory.revision
        row = (
            await session.execute(
                select(Memory, MemoryRevision)
                .join(
                    MemoryRevision,
                    (MemoryRevision.org_id == Memory.org_id)
                    & (MemoryRevision.memory_id == Memory.id)
                    & (MemoryRevision.revision == selected),
                )
                .where(
                    Memory.org_id == actor.org_id,
                    Memory.id == identifier,
                    Memory.deleted_at.is_(None),
                    Memory.scope == "org",
                )
            )
        ).first()
        if row is None:
            raise not_found()
        root, revision = row
        check_revision(actor, root, revision)
        sources = await crud.visible_sources(session, actor, [revision])
        by = await authors(session, actor, [revision])
        human = actor.actor_kind == "session" and actor.token_id is None and actor.role == "admin"
        current = revision.revision == root.revision
        editable = current and "memory:write" in actor.scopes
        if editable and not human:
            editable = (
                revision.status == "candidate"
                and revision.created_by == actor.user_id
                and revision.actor_token_id == actor.token_id
            )
            if editable:
                editable = (
                    await session.scalar(
                        select(MemoryRevision.id)
                        .where(
                            MemoryRevision.org_id == actor.org_id,
                            MemoryRevision.memory_id == root.id,
                            MemoryRevision.status == "active",
                        )
                        .limit(1)
                    )
                    is None
                )
        actions = []
        decisions: list[tuple[ActionCode, bool]] = [
            ("revise", editable),
            (
                "approve",
                current
                and human
                and "memory:approve" in actor.scopes
                and revision.status == "candidate",
            ),
            (
                "reject",
                current
                and human
                and "memory:approve" in actor.scopes
                and revision.status == "candidate",
            ),
            (
                "disable",
                current
                and human
                and "memory:manage" in actor.scopes
                and revision.status != "disabled",
            ),
        ]
        for action, allowed in decisions:
            actions.append(
                ActionHint(
                    action=action, allowed=allowed, reason=None if allowed else "role_required"
                )
            )
        as_of = datetime.now(UTC)
        current_status = root.search_status
        if (
            current_status == "active"
            and root.search_expires_at
            and root.search_expires_at <= as_of
        ):
            current_status = "expired"
        value = MemoryDetailData(
            memory=memory_view(root, revision, sources[revision.id], as_of),
            current_revision=root.revision,
            current_effective_status=typed_cast(MemoryEffectiveStatus, current_status),
            as_of=as_of,
            revised_at=revision.created_at,
            revised_by=by[revision.id],
            actions=actions,
        )
        detail_result("memory show", value, settings.billing_currency, 9223372036854775807)
        return value


async def history(session, actor, identifier, body: PageQuery, settings):
    session.info["management_settings"] = settings
    async with bounded.read_budget(session, body):
        actor = await reader(session, actor)
        normalized = binding("memory_history")
        anchor = bounded.open_cursor(session, actor, body, "history", identifier, normalized)
        relation = (MemoryRevision.org_id == Memory.org_id) & (
            MemoryRevision.memory_id == Memory.id
        )
        if anchor:
            relation &= tuple_(MemoryRevision.revision, MemoryRevision.id) < tuple_(*anchor)
        rows = (
            await session.execute(
                select(Memory, MemoryRevision)
                .outerjoin(MemoryRevision, relation)
                .where(
                    Memory.org_id == actor.org_id, Memory.id == identifier, Memory.scope == "org"
                )
                .order_by(MemoryRevision.revision.desc(), MemoryRevision.id.desc())
                .limit(body.limit + 1)
            )
        ).all()
        if not rows:
            raise not_found()
        root = rows[0][0]
        if root.deleted_at:
            human_admin(actor, "memory:manage")
        revisions = [r for _, r in rows if r is not None]
        sources = await crud.visible_sources(session, actor, revisions[: body.limit])
        items, anchors = [], []
        for revision in revisions[: body.limit]:
            check_revision(actor, root, revision)
            items.append(crud.revision_view(root, revision, sources[revision.id]))
            anchors.append([revision.revision, str(revision.id)])
        return build_page(
            session,
            actor,
            "memory history",
            "history",
            identifier,
            normalized,
            items,
            anchors,
            len(revisions) > body.limit,
            datetime.now(UTC),
        )


def feedback_statement(actor, task_id, body, anchor=None):
    event_query = select(MemoryFeedbackEvent).where(
        MemoryFeedbackEvent.org_id == actor.org_id, MemoryFeedbackEvent.task_id == task_id
    )
    if anchor:
        event_query = event_query.where(
            tuple_(MemoryFeedbackEvent.created_at, MemoryFeedbackEvent.id) < tuple_(*anchor)
        )

    # Limit the event relation before looking up receipts. Root/job joins never
    # expand the retained page or materialize the task's complete event history.
    events = (
        event_query.order_by(MemoryFeedbackEvent.created_at.desc(), MemoryFeedbackEvent.id.desc())
        .limit(body.limit + 1)
        .subquery()
    )
    event = aliased(MemoryFeedbackEvent, events)
    recorded_ids = Job.result.op("->")(literal_column("'submission'")).op("->")(
        literal_column("'event_ids'")
    )
    job = (
        select(
            Job.id.label("job_id"), Job.status.label("job_status"), recorded_ids.label("event_ids")
        )
        .where(
            Job.org_id == actor.org_id,
            Job.task_id == task_id,
            text("jobs.kind = 'memory_candidate'"),
            recorded_ids.op("?")(cast(event.id, String)),
        )
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
        .lateral()
    )
    return (
        select(event, job.c.job_id, job.c.job_status, job.c.event_ids, Memory.id.label("memory_id"))
        .select_from(event)
        .outerjoin(job, true())
        .outerjoin(
            Memory,
            (Memory.org_id == actor.org_id)
            & (Memory.source_feedback_event_id == event.id)
            & (Memory.generator_version == "feedback-copy-v1")
            & Memory.deleted_at.is_(None),
        )
        .order_by(event.created_at.desc(), event.id.desc())
    )


async def feedback_page(session, actor, task_id, body: PageQuery, settings):
    from app.services.task_workflow import require_read_authority

    session.info["management_settings"] = settings
    async with bounded.read_budget(session, body):
        actor = await reader(session, actor, scope=None)
        authority_row = (
            await session.execute(
                select(TaskWorkflow, TaskMember)
                .outerjoin(
                    TaskMember,
                    (TaskMember.org_id == TaskWorkflow.org_id)
                    & (TaskMember.task_id == TaskWorkflow.task_id)
                    & (TaskMember.user_id == actor.user_id)
                    & TaskMember.active.is_(True),
                )
                .where(TaskWorkflow.org_id == actor.org_id, TaskWorkflow.task_id == task_id)
                .execution_options(populate_existing=True)
            )
        ).first()
        workflow, member = authority_row if authority_row else (None, None)
        require_read_authority(actor, workflow, member, "memory:read")
        assert workflow is not None
        actor.require("card:read")
        normalized = binding("memory_feedback", {"access_epoch": workflow.access_epoch})
        anchor = bounded.open_cursor(session, actor, body, "browse", task_id, normalized)
        rows = (await session.execute(feedback_statement(actor, task_id, body, anchor))).all()
        retained = rows[: body.limit]
        items = []
        for event, job_id, job_status, event_ids, memory_id in retained:
            if event.org_id != actor.org_id or event.task_id != task_id:
                raise not_found()
            item = MemoryFeedbackRow.model_validate(event, from_attributes=True)
            item.candidate_job_id, item.candidate_job_status = job_id, job_status
            item.candidate_event_ids = [UUID(value) for value in (event_ids or [])]
            if len(item.candidate_event_ids) > 100 or (
                job_id and event.id not in item.candidate_event_ids
            ):
                bounded.fail("management_integrity_error", "Invalid candidate job manifest", 409, 4)
            item.candidate_memory_id = memory_id
            items.append(item)
        return build_page(
            session,
            actor,
            "memory feedback list",
            "browse",
            task_id,
            normalized,
            items,
            [[row[0].created_at.isoformat(), str(row[0].id)] for row in retained],
            len(rows) > body.limit,
            datetime.now(UTC),
        )
