"""Transactionally ordered task events and opaque visibility-bound checkpoints."""

import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

from cryptography.fernet import InvalidToken
from pydantic import TypeAdapter
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.core.security import TokenSigner
from app.models.team_workflow import TaskEvent, TaskEventHead
from app.schemas.team_workflow import (
    EventReplayView,
    StreamResetRequired,
    TaskEventPayload,
    TaskEventView,
)

PAYLOAD = TypeAdapter(TaskEventPayload)
CURSOR_SECONDS = 300
RETENTION_SECONDS = 7 * 86400
MAX_EVENTS = 50000


def fingerprint(actor, workflow) -> str:
    value = [
        str(actor.user_id),
        str(actor.token_id or ""),
        actor.actor_kind,
        actor.role,
        sorted(actor.scopes),
        workflow.access_epoch,
    ]
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


def issue_cursor(actor, task_id, workflow, settings, *, purpose="events", seq=0, **fields):
    value = TokenSigner.for_tokens(settings).issue(
        {
            "kind": "task_cursor",
            "o": str(actor.org_id),
            "t": str(task_id),
            "v": fingerprint(actor, workflow),
            "p": purpose,
            "s": seq,
            **fields,
        },
        CURSOR_SECONDS,
    )
    if len(value) > 1024:
        raise ServiceError("board_limit_exceeded", "Cursor exceeds bound", 422, 2)
    return value


def open_cursor(value, actor, task_id, workflow, settings, *, purpose="events"):
    if len(value) > 1024:
        raise ServiceError("invalid_event_cursor", "Invalid cursor", 422, 2)
    try:
        data = json.loads(TokenSigner.for_tokens(settings).cipher.decrypt(value.encode()))
    except (InvalidToken, ValueError, KeyError, TypeError):
        raise ServiceError("invalid_event_cursor", "Invalid or expired cursor", 422, 2) from None
    if (data.get("o"), data.get("t")) != (str(actor.org_id), str(task_id)):
        raise not_found()
    if data.get("kind") != "task_cursor" or data.get("p") != purpose:
        raise ServiceError("invalid_event_cursor", "Invalid cursor", 422, 2)
    if type(data.get("exp")) is not int or data["exp"] <= time.time():
        raise ServiceError(
            "event_cursor_expired" if purpose == "events" else "board_changed",
            "Cursor expired; reload snapshot",
            409,
            2,
        )
    if data.get("v") != fingerprint(actor, workflow):
        raise ServiceError("board_changed", "Task visibility changed; reload snapshot", 409, 2)
    if type(data.get("s")) is not int or data["s"] < 0:
        raise ServiceError("invalid_event_cursor", "Invalid cursor", 422, 2)
    return data


async def head(session, org_id, task_id):
    row = await session.scalar(
        select(TaskEventHead).where(
            TaskEventHead.org_id == org_id, TaskEventHead.task_id == task_id
        )
    )
    if row is None:
        raise not_found()
    return row


async def current_cursor(session, actor, task_id, settings, workflow=None):
    if workflow is None:
        from app.services.task_workflow import access

        _, workflow, _ = await access(session, actor, task_id)
    row = await head(session, actor.org_id, task_id)
    return issue_cursor(actor, task_id, workflow, settings, seq=row.last_seq)


async def append(
    session: AsyncSession, org_id: UUID, task_id: UUID, payload, source_id: UUID | None = None
):
    """Head row lock lasts until caller commit; state and event roll back together."""
    value = PAYLOAD.validate_python(payload).model_dump(mode="json")
    if len(json.dumps(value, separators=(",", ":")).encode()) > 1800:
        if value["type"] == "board_changed":
            from app.schemas.team_workflow import BoardChanged

            value = BoardChanged(
                invalidate_all=True, extraction_job_id=value.get("extraction_job_id")
            ).model_dump(mode="json")
        else:
            raise ServiceError("board_limit_exceeded", "Event metadata exceeds bound", 422, 2)
    # Use the same function as trigger producers, including bounded prefix retention.
    await session.execute(
        text(
            "SELECT public.append_task_event(:org, :task, :kind, CAST(:payload AS jsonb), :source)"
        ),
        {
            "org": org_id,
            "task": task_id,
            "kind": value["type"],
            "payload": json.dumps(value),
            "source": source_id,
        },
    )


def hidden_job_kinds(actor):
    """Cheap necessary-scope filter; survivors still pass full job access gates."""
    scopes = {
        "check": "check:read",
        "score": "score:read",
        "score_rubric": "score:read",
        "draft": "draft:read",
        "card_generate": "card:read",
        "provider_test": "provider:read",
        "screenshot_render": "screenshot:read",
        "screenshot_analyze": "screenshot:read",
        "screenshot_search": "screenshot:read",
        "prototype_generate": "screenshot:read",
        "sandbox": "sandbox:read",
        "memory_candidate": "memory:read",
    }
    denied = {kind for kind, scope in scopes.items() if scope not in actor.scopes}
    if (
        actor.actor_kind != "session"
        or actor.token_id is not None
        or actor.role != "bidder"
        or "export" not in actor.scopes
    ):
        denied |= {"export_render", "export_preview"}
    return denied


async def visible_job(session, actor, job, storage):
    from app.services.jobs import read_access

    key = (job.id, actor.user_id, actor.token_id, actor.role, tuple(sorted(actor.scopes)))
    cache = session.info.setdefault("task_visible_jobs", {})
    if key in cache:
        return cache[key]
    if job.kind in hidden_job_kinds(actor):
        cache[key] = False
        return False
    try:
        await read_access(session, actor, job, storage)
    except ServiceError as error:
        if error.status in (401, 403, 404):
            cache[key] = False
            return False
        raise
    cache[key] = True
    return True


async def replay(session, actor, task_id, settings, storage, *, cursor=None, limit=100):
    from app.models.entities import Job
    from app.services.task_workflow import access

    _, workflow, _ = await access(session, actor, task_id)
    actor.require("card:read")
    actor.require("job:read")
    row = await head(session, actor.org_id, task_id)
    checkpoint = issue_cursor(actor, task_id, workflow, settings, seq=row.last_seq)
    base = dict(
        org_id=actor.org_id,
        task_id=task_id,
        head_cursor=checkpoint,
        next_cursor=None,
        has_more=False,
        events=[],
    )
    if cursor is None:
        return EventReplayView.model_validate(
            {
                **base,
                "reset_required": StreamResetRequired(
                    reason="initial_snapshot_required", head_cursor=checkpoint
                ),
            }
        )

    data = open_cursor(cursor, actor, task_id, workflow, settings)
    seq = data["s"]
    if seq > row.last_seq:
        raise ServiceError("invalid_event_cursor", "Future cursor", 422, 2)
    if seq < row.retained_floor_seq:
        raise ServiceError("event_cursor_expired", "Event window expired; reload snapshot", 409, 2)
    events = list(
        await session.scalars(
            select(TaskEvent)
            .where(
                TaskEvent.org_id == actor.org_id, TaskEvent.task_id == task_id, TaskEvent.seq > seq
            )
            .order_by(TaskEvent.seq)
            .limit(100)
        )
    )
    job_ids = {event.source_id for event in events if event.source_id is not None} | {
        UUID(event.payload["job_id"]) for event in events if event.event_kind == "job_progress"
    }
    jobs = {
        job.id: job
        for job in await session.scalars(
            select(Job).where(
                Job.org_id == actor.org_id, Job.task_id == task_id, Job.id.in_(job_ids)
            )
        )
    }
    visible = set()
    for job in jobs.values():
        if await visible_job(session, actor, job, storage):
            visible.add(job.id)
    output = []
    scanned = seq
    more = False
    for event in events:
        if event.source_id is not None and event.source_id not in visible:
            scanned = event.seq
            continue
        if event.event_kind == "job_progress" and UUID(event.payload["job_id"]) not in visible:
            scanned = event.seq
            continue
        if event.created_at < datetime.now(UTC) - timedelta(seconds=RETENTION_SECONDS):
            raise ServiceError(
                "event_cursor_expired", "Event window expired; reload snapshot", 409, 2
            )
        if len(output) == limit:
            more = True
            break
        scanned = event.seq
        output.append(
            TaskEventView(
                event_id=event.id,
                org_id=actor.org_id,
                task_id=task_id,
                cursor=issue_cursor(actor, task_id, workflow, settings, seq=event.seq),
                payload=event.payload,
                created_at=event.created_at,
            )
        )
    base.update(
        events=output,
        next_cursor=issue_cursor(actor, task_id, workflow, settings, seq=scanned),
        has_more=more,
    )
    return EventReplayView.model_validate(base)
