"""Append-only organization memory with explicit human approval."""

import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select

from app.core.errors import ServiceError, not_found
from app.memory.access import (
    access,
    digest,
    human_admin,
    normalize,
    org_target,
    page_cursor,
    read_cursor,
    writable,
)
from app.memory.safety import reject_sensitive
from app.models.entities import AuditLog, Task
from app.models.memory import Memory, MemoryRevision, MemoryScopeEpoch
from app.models.response_cards import ResponseCard, ResponseCardRevision
from app.schemas.contracts import Result
from app.schemas.memory_contracts import (
    MemoryContent,
    MemoryData,
    MemoryPageData,
    MemoryRevisionView,
    MemorySourceView,
    MemoryTarget,
    MemoryView,
)


def fail(code, message, http=400):
    raise ServiceError(code, message, http, 2)


def expiry(value):
    if value is not None and value <= datetime.now(UTC):
        fail("invalid_expiry", "Memory expiry must be in the future")


def target(memory):
    return MemoryTarget(scope=memory.scope, user_id=memory.user_id, task_id=memory.task_id)


def revision_view(memory, revision, source=None):
    return MemoryRevisionView(
        id=revision.id,
        org_id=revision.org_id,
        memory_id=memory.id,
        revision=revision.revision,
        target=target(memory),
        content=MemoryContent.model_validate(revision.content),
        status=revision.status,
        source=source or MemorySourceView.model_validate(revision.source),
        content_sha256=revision.content_sha256,
        created_at=revision.created_at,
        created_by=revision.created_by,
        actor_kind=revision.actor_kind,
        confirmed_by=revision.confirmed_by,
        confirmed_at=revision.confirmed_at,
        decision=revision.decision,
        decision_reason_sha256=revision.decision_reason_sha256,
        expires_at=revision.expires_at,
    )


def memory_view(memory, revision, source=None, *, as_of=None):
    state = "deleted" if memory.deleted_at else revision.status
    if (
        state == "active"
        and revision.expires_at is not None
        and revision.expires_at <= (as_of or datetime.now(UTC))
    ):
        state = "expired"
    return MemoryView(
        id=memory.id,
        org_id=memory.org_id,
        current=revision_view(memory, revision, source),
        effective_status=state,
        deleted_at=memory.deleted_at,
    )


async def visible_sources(session, actor, revisions):
    """Org memory remains shared while inaccessible task provenance stays private."""
    from app.models.team_workflow import TaskMember, TaskWorkflow

    task_ids = {UUID(row.source["task_id"]) for row in revisions if row.source.get("task_id")}
    visible = set()
    if task_ids and "task:read" in actor.scopes:
        query = select(TaskWorkflow.task_id).where(
            TaskWorkflow.org_id == actor.org_id, TaskWorkflow.task_id.in_(task_ids)
        )
        if not (actor.actor_kind == "session" and actor.token_id is None and actor.role == "admin"):
            query = query.join(
                TaskMember,
                (TaskMember.org_id == TaskWorkflow.org_id)
                & (TaskMember.task_id == TaskWorkflow.task_id),
            ).where(TaskMember.user_id == actor.user_id, TaskMember.active.is_(True))
        visible = set(await session.scalars(query))
    return {
        row.id: MemorySourceView(origin=row.source["origin"], provenance_redacted=True)
        if row.source.get("task_id") and UUID(row.source["task_id"]) not in visible
        else MemorySourceView.model_validate(row.source)
        for row in revisions
    }


async def result(session, actor, command, memory, revision):
    sources = await visible_sources(session, actor, [revision])
    return Result(
        ok=True,
        command=command,
        data=MemoryData(memory=memory_view(memory, revision, sources[revision.id])).model_dump(
            mode="json"
        ),
    )


def audit(session, actor, action, memory, revision, before=None):
    session.add(
        AuditLog(
            org_id=actor.org_id,
            actor_user_id=actor.user_id,
            actor_token_id=actor.token_id,
            action=f"memory.{action}",
            object_id=memory.id,
            details={
                "revision_id": str(revision.id),
                "revision": revision.revision,
                "status": revision.status,
                "before_revision": before,
                "content_sha256": revision.content_sha256,
                "reason_sha256": revision.decision_reason_sha256,
                "actor_kind": actor.actor_kind,
            },
        )
    )


async def load(session, actor, identifier, *, lock=False, deleted=False):
    actor = await access(session, actor, "memory:read")
    query = select(Memory).where(Memory.org_id == actor.org_id, Memory.id == identifier)
    if lock:
        query = query.with_for_update()
    memory = await session.scalar(query)
    if memory is None or (memory.deleted_at is not None and not deleted):
        raise not_found()
    org_target(target(memory))
    revision = await session.scalar(
        select(MemoryRevision).where(
            MemoryRevision.org_id == actor.org_id, MemoryRevision.id == memory.current_revision_id
        )
    )
    if revision is None:
        raise not_found()
    return memory, revision


async def check_source(session, actor, source):
    if source is None:
        return
    from app.services.task_workflow import access as task_access

    await task_access(session, actor, source.task_id)
    if (
        await session.scalar(
            select(Task.id).where(Task.id == source.task_id, Task.org_id == actor.org_id)
        )
        is None
    ):
        raise not_found()
    if source.card_id is not None:
        actor.require("card:read")
        card = await session.scalar(
            select(ResponseCard).where(
                ResponseCard.id == source.card_id,
                ResponseCard.org_id == actor.org_id,
                ResponseCard.task_id == source.task_id,
            )
        )
        revision = await session.scalar(
            select(ResponseCardRevision.id).where(
                ResponseCardRevision.id == source.card_revision_id,
                ResponseCardRevision.org_id == actor.org_id,
                ResponseCardRevision.card_id == source.card_id,
            )
        )
        if card is None or revision is None:
            raise not_found()


def new_revision(
    memory,
    actor,
    content,
    source,
    *,
    status="candidate",
    expires_at=None,
    decision=None,
    reason=None,
):
    approved = decision == "approve"
    return MemoryRevision(
        id=uuid4(),
        org_id=actor.org_id,
        memory_id=memory.id,
        revision=memory.revision,
        normalized_text=normalize(content.text),
        normalized_tags=[normalize(tag) for tag in content.tags],
        content=content.model_dump(mode="json"),
        source=source.model_dump(mode="json"),
        status=status,
        content_sha256=digest(content.model_dump(mode="json")),
        created_by=actor.user_id,
        actor_kind=actor.actor_kind,
        actor_token_id=actor.token_id,
        confirmed_by=actor.user_id if approved else None,
        confirmed_at=datetime.now(UTC) if approved else None,
        decision=decision,
        decision_reason_sha256=hashlib.sha256(reason.encode()).hexdigest() if reason else None,
        expires_at=expires_at,
        task_id=source.task_id,
        card_id=source.card_id,
        card_revision_id=source.card_revision_id,
        feedback_event_id=source.feedback_event_id,
        proposal_job_id=source.proposal_job_id,
        proposal_run_id=source.proposal_run_id,
    )


async def create_memory(session, actor, body, settings):
    actor = await access(session, actor, "memory:write")
    org_target(body.target)
    expiry(body.expires_at)
    await reject_sensitive(
        session, actor, [body.content.text, body.content.conflict_key, *body.content.tags], settings
    )
    await check_source(session, actor, body.source)
    source = MemorySourceView(origin="human", **(body.source.model_dump() if body.source else {}))
    memory = Memory(
        id=uuid4(), org_id=actor.org_id, scope="org", revision=1, current_revision_id=uuid4()
    )
    revision = new_revision(memory, actor, body.content, source, expires_at=body.expires_at)
    memory.current_revision_id = revision.id
    session.add(memory)
    await session.flush()
    session.add(revision)
    audit(session, actor, "create", memory, revision)
    await session.flush()
    return await result(session, actor, "memory add", memory, revision)


async def show_memory(session, actor, identifier):
    return await result(session, actor, "memory show", *(await load(session, actor, identifier)))


async def append(
    session,
    actor,
    memory,
    previous,
    content,
    *,
    status="candidate",
    expires_at=None,
    decision=None,
    reason=None,
):
    before = memory.revision
    revision = new_revision(
        memory,
        actor,
        content,
        MemorySourceView.model_validate(previous.source),
        status=status,
        expires_at=expires_at,
        decision=decision,
        reason=reason,
    )
    revision.revision = before + 1
    session.add(revision)
    await session.flush()
    memory.revision = before + 1
    memory.current_revision_id = revision.id
    if decision == "delete":
        memory.deleted_at = datetime.now(UTC)
    audit(session, actor, decision or "update", memory, revision, before)
    await session.flush()
    return revision


def expected(memory, value):
    if memory.revision != value:
        fail("memory_revision_conflict", "Memory changed; refresh its exact revision", 409)


async def update_memory(session, actor, identifier, body, settings):
    memory, previous = await load(session, actor, identifier, lock=True)
    expected(memory, body.expected_revision)
    actor = await access(session, actor, "memory:write")
    writable(actor, previous.created_by, previous.actor_token_id, previous.status)
    if actor.role != "admin" or actor.actor_kind != "session":
        was_active = await session.scalar(
            select(MemoryRevision.id)
            .where(
                MemoryRevision.memory_id == memory.id,
                MemoryRevision.org_id == actor.org_id,
                MemoryRevision.status == "active",
            )
            .limit(1)
        )
        if was_active is not None:
            raise not_found()
    expiry(body.expires_at)
    await reject_sensitive(
        session, actor, [body.content.text, body.content.conflict_key, *body.content.tags], settings
    )
    revision = await append(
        session, actor, memory, previous, body.content, expires_at=body.expires_at
    )
    return await result(session, actor, "memory update", memory, revision)


async def decide_memory(session, actor, identifier, body, settings):
    actor = await access(session, actor, "memory:approve")
    human_admin(actor, "memory:approve")
    memory, previous = await load(session, actor, identifier, lock=True)
    expected(memory, body.expected_revision)
    if previous.status != "candidate":
        fail("memory_invalid_transition", "Only candidate memory can be reviewed", 409)
    await reject_sensitive(session, actor, [body.reason], settings)
    if body.action == "approve":
        expiry(previous.expires_at)
        # Serialize approvals at scope level before checking effective conflicts.
        await session.scalar(
            select(MemoryScopeEpoch)
            .where(
                MemoryScopeEpoch.org_id == actor.org_id,
                MemoryScopeEpoch.scope == "org",
                MemoryScopeEpoch.owner_id == actor.org_id,
            )
            .with_for_update()
        )
        rows = (
            await session.execute(
                select(Memory, MemoryRevision)
                .join(MemoryRevision, Memory.current_revision_id == MemoryRevision.id)
                .where(
                    Memory.org_id == actor.org_id,
                    Memory.scope == "org",
                    Memory.deleted_at.is_(None),
                    Memory.id != memory.id,
                    MemoryRevision.status == "active",
                )
            )
        ).all()
        for _, other in rows:
            if other.expires_at is not None and other.expires_at <= datetime.now(UTC):
                continue
            if (
                other.content["kind"] == previous.content["kind"]
                and other.content["conflict_key"] == previous.content["conflict_key"]
            ):
                fail(
                    "memory_conflict_key", "An effective memory already uses this conflict key", 409
                )
    revision = await append(
        session,
        actor,
        memory,
        previous,
        MemoryContent.model_validate(previous.content),
        status="active" if body.action == "approve" else "disabled",
        expires_at=previous.expires_at,
        decision=body.action,
        reason=body.reason,
    )
    return await result(session, actor, f"memory {body.action}", memory, revision)


async def manage(session, actor, identifier, body, settings, deleting=False):
    actor = await access(session, actor, "memory:manage")
    human_admin(actor, "memory:manage")
    memory, previous = await load(session, actor, identifier, lock=True)
    expected(memory, body.expected_revision)
    await reject_sensitive(session, actor, [body.reason], settings)
    if not deleting and previous.status == "disabled":
        fail("memory_invalid_transition", "Disabled memory cannot be disabled again", 409)
    decision = "delete" if deleting else "disable"
    revision = await append(
        session,
        actor,
        memory,
        previous,
        MemoryContent.model_validate(previous.content),
        status="disabled",
        expires_at=previous.expires_at,
        decision=decision,
        reason=body.reason,
    )
    return await result(session, actor, f"memory {decision}", memory, revision)


async def disable_memory(session, actor, identifier, body, settings):
    return await manage(session, actor, identifier, body, settings)


async def delete_memory(session, actor, identifier, body, settings):
    return await manage(session, actor, identifier, body, settings, True)


async def list_memories(session, actor, body, settings):
    actor = await access(session, actor, "memory:read")
    org_target(body.target)
    if body.include_deleted:
        human_admin(actor, "memory:manage")
    filters = body.model_dump(mode="json", exclude={"cursor", "limit"})
    anchor = read_cursor(settings, actor, filters, body.cursor)
    query = (
        select(Memory, MemoryRevision)
        .join(MemoryRevision, Memory.current_revision_id == MemoryRevision.id)
        .where(Memory.org_id == actor.org_id, Memory.scope == "org")
    )
    if not body.include_deleted:
        query = query.where(Memory.deleted_at.is_(None))
    if body.status:
        query = query.where(MemoryRevision.status == body.status)
    if anchor:
        query = query.where(
            or_(
                Memory.created_at > anchor[0],
                and_(Memory.created_at == anchor[0], Memory.id > anchor[1]),
            )
        )
    rows = (
        await session.execute(query.order_by(Memory.created_at, Memory.id).limit(body.limit + 1))
    ).all()
    sources = await visible_sources(session, actor, [r for _, r in rows[: body.limit]])
    items = [
        memory_view(m, r, sources[r.id]).model_dump(mode="json") for m, r in rows[: body.limit]
    ]
    next_cursor = (
        page_cursor(
            settings, actor, filters, rows[body.limit - 1][0].created_at, rows[body.limit - 1][0].id
        )
        if len(rows) > body.limit
        else None
    )
    return Result(
        ok=True,
        command="memory list",
        data=MemoryPageData(returned=len(items), next_cursor=next_cursor).model_dump(),
        items=items,
    )


async def history(session, actor, identifier, settings, *, cursor=None, limit=50):
    memory, _ = await load(session, actor, identifier, deleted=True)
    if memory.deleted_at is not None:
        human_admin(await access(session, actor, "memory:manage"), "memory:manage")
    filters = {"history": str(identifier)}
    anchor = read_cursor(settings, actor, filters, cursor)
    query = select(MemoryRevision).where(
        MemoryRevision.org_id == actor.org_id, MemoryRevision.memory_id == identifier
    )
    if anchor:
        query = query.where(
            or_(
                MemoryRevision.created_at > anchor[0],
                and_(MemoryRevision.created_at == anchor[0], MemoryRevision.id > anchor[1]),
            )
        )
    rows = list(
        (
            await session.scalars(
                query.order_by(MemoryRevision.created_at, MemoryRevision.id).limit(limit + 1)
            )
        ).all()
    )
    sources = await visible_sources(session, actor, rows[:limit])
    items = [
        revision_view(memory, row, sources[row.id]).model_dump(mode="json") for row in rows[:limit]
    ]
    next_cursor = (
        page_cursor(settings, actor, filters, rows[limit - 1].created_at, rows[limit - 1].id)
        if len(rows) > limit
        else None
    )
    return Result(
        ok=True,
        command="memory history",
        data=MemoryPageData(returned=len(items), next_cursor=next_cursor).model_dump(),
        items=items,
    )


async def create_system_candidate(
    session, actor, event, proposal, generator_version, *, job_id, run_id
):
    source = MemorySourceView(
        origin="system",
        task_id=event.task_id,
        card_id=event.card_id,
        card_revision_id=event.after_revision_id,
        feedback_event_id=event.id,
        proposal_job_id=job_id,
        proposal_run_id=run_id,
    )
    memory = Memory(
        id=uuid4(),
        org_id=actor.org_id,
        scope="org",
        revision=1,
        current_revision_id=uuid4(),
        source_feedback_event_id=event.id,
        generator_version=generator_version,
    )
    revision = new_revision(memory, actor, proposal.content, source)
    memory.current_revision_id = revision.id
    session.add(memory)
    await session.flush()
    session.add(revision)
    audit(session, actor, "candidate.publish", memory, revision)
    await session.flush()
    return memory
