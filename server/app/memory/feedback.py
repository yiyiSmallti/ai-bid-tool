"""Immutable, sanitized human feedback and organization-only evaluation samples."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from cryptography.fernet import InvalidToken
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ServiceError, not_found
from app.core.security import Secrets
from app.models.entities import Job, Task
from app.models.memory import MemoryEvalSample, MemoryFeedbackEvent
from app.schemas.contracts import Result
from app.schemas.memory_contracts import MemoryEvalSampleView, MemoryFeedbackView
from app.services.response_cards import access
from app.services.versioned import audit

GENERATOR_VERSION = "feedback-copy-v1"
SANITIZER_VERSION = "feedback-sanitize-v1"
EDIT_FIELDS = ("response_text", "deviation", "deviation_note")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def summarize_edit(before: dict, after: dict) -> str:
    changes = [
        f"{key}: {before.get(key) or ''} → {after.get(key) or ''}"
        for key in EDIT_FIELDS
        if before.get(key) != after.get(key)
    ]
    if not changes:
        return ""
    summary = "人工修改，尚未说明是否可泛化。\n" + "\n".join(changes)
    return summary if len(summary) <= 1900 else ""


def seal(settings: Settings, org_id: UUID, object_id: UUID, summary: str) -> str:
    return Secrets.for_data(settings).encrypt(
        json.dumps(
            {"org_id": str(org_id), "object_id": str(object_id), "summary": summary},
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def unseal(settings: Settings, org_id: UUID, object_id: UUID, ciphertext: str) -> str:
    try:
        value = json.loads(Secrets.for_data(settings).decrypt(ciphertext))
        if value["org_id"] != str(org_id) or value["object_id"] != str(object_id):
            raise ValueError("binding mismatch")
        summary = value["summary"]
        if not isinstance(summary, str) or not 1 <= len(summary) <= 2000:
            raise ValueError("invalid summary")
        return summary
    except (InvalidToken, KeyError, ValueError, TypeError) as exc:
        raise ServiceError(
            "memory_feedback_integrity", "Feedback integrity check failed", 500, 4
        ) from exc


def required_settings(session: AsyncSession) -> Settings:
    settings = session.info.get("memory_settings")
    if not isinstance(settings, Settings):
        raise ServiceError("memory_settings_missing", "Memory settings are not configured", 500, 4)
    return settings


def take_pending_jobs(session: AsyncSession) -> list[Job]:
    return session.info.pop("memory_candidate_jobs", [])


async def record_feedback(session, actor, card, before, after, kind, reason=None):
    """Called after append_revision, still under the original card/task locks."""
    if actor.actor_kind != "session" or actor.token_id is not None or before.model_job_id is None:
        return None
    if kind == "card_edited" and not any(
        getattr(before, key) != getattr(after, key) for key in EDIT_FIELDS
    ):
        return None
    settings = required_settings(session)
    from app.memory.candidates import reusable_text
    from app.memory.safety import SANITIZER_VERSION as safety_version
    from app.memory.safety import sanitize
    from app.services.response_cards import linked_evidence

    evidence = [
        *await linked_evidence(session, before.id),
        *await linked_evidence(session, after.id),
    ]
    quotes = set()
    for row in evidence:
        if row.quote:
            quotes.add(row.quote)
            sanitized_quote = await sanitize(session, actor, row.quote, settings)
            # An all-secret quote has no material text left to copy; matching its
            # generic mask would hide unrelated sensitive-value classifications.
            if reusable_text(sanitized_quote):
                quotes.add(sanitized_quote)

    async def clean(text):
        for quote in sorted(quotes, key=len, reverse=True):
            text = text.replace(quote, "[EVIDENCE_OMITTED]")
        text = await sanitize(session, actor, text, settings)
        for quote in sorted(quotes, key=len, reverse=True):
            text = text.replace(quote, "[EVIDENCE_OMITTED]")
        return text

    if kind == "card_edited":
        old, new = {}, {}
        changed = [key for key in EDIT_FIELDS if getattr(before, key) != getattr(after, key)]
        for key in changed:
            old[key] = await clean(str(getattr(before, key) or ""))
            new[key] = await clean(str(getattr(after, key) or ""))
        if not any(reusable_text(value) for value in [*old.values(), *new.values()]):
            summary = "[REDACTED_CONFIDENTIAL]"
        else:
            summary = summarize_edit(old, new)
    elif kind == "card_rejected":
        summary = await clean(reason or "")
    else:
        summary = "人工确认模型派生响应；该动作不代表事实正确性或通用规则批准。"
    if not summary.strip() or len(summary) > 1900:
        summary = "[NO_REUSABLE_FEEDBACK]"
    extraction = await session.get(Job, card.extraction_job_id)
    if extraction is None or extraction.task_id != card.task_id or extraction.document_id is None:
        raise not_found()
    event = MemoryFeedbackEvent(
        id=uuid4(),
        org_id=actor.org_id,
        task_id=card.task_id,
        document_id=extraction.document_id,
        model_job_id=before.model_job_id,
        card_id=card.id,
        before_revision_id=before.id,
        after_revision_id=after.id,
        actor_user_id=actor.user_id,
        actor_kind="session",
        kind=kind,
        review_domain=before.review_domain,
        sanitized_sha256=digest(summary),
        sanitizer_version=SANITIZER_VERSION + ":" + safety_version,
    )
    event.encrypted_summary = seal(settings, actor.org_id, event.id, summary)
    session.add(event)
    await session.flush()
    sample = MemoryEvalSample(
        id=uuid4(),
        org_id=actor.org_id,
        task_id=card.task_id,
        feedback_event_id=event.id,
        card_id=card.id,
        before_revision_id=before.id,
        after_revision_id=after.id,
        label=kind,
        actor_user_id=actor.user_id,
        generator_version=GENERATOR_VERSION,
        sanitized_sha256=event.sanitized_sha256,
        review_state="unreviewed",
        review_revision=1,
    )
    sample.encrypted_summary = seal(settings, actor.org_id, sample.id, summary)
    session.add(sample)
    await session.flush()
    job = None
    if kind != "card_confirmed":
        from app.memory.candidates import create_job

        job, _ = await create_job(session, actor, card.task_id, [event], retry=False)
        session.info.setdefault("memory_candidate_jobs", []).append(job)
    audit(
        session,
        actor,
        "memory.feedback.record",
        event.id,
        {
            "card_id": str(card.id),
            "before_revision_id": str(before.id),
            "after_revision_id": str(after.id),
            "sample_id": str(sample.id),
            "job_id": str(job.id) if job else None,
            "kind": kind,
            "sanitized_sha256": event.sanitized_sha256,
            "sanitizer_version": event.sanitizer_version,
        },
    )
    return event


async def read_access(session, actor, task_id, *, evaluation=False, review=False):
    scope = "memory:eval:review" if review else "memory:eval:read" if evaluation else "memory:read"
    from app.services.task_workflow import access as task_access

    await task_access(session, actor, task_id, scope=scope, write=review, lock=review)
    actor = await access(session, actor, scope)
    actor.require("card:read")
    if evaluation and (actor.role != "admin" or actor.actor_kind != "session" or actor.token_id):
        raise ServiceError("forbidden", "Administrator human session required", 403, 4)
    if await session.get(Task, task_id) is None:
        raise not_found()
    return actor


async def page(session, actor, task_id, model, view, command, cursor, limit):
    if not 1 <= limit <= 100:
        raise ServiceError("invalid_input", "Page limit must be between 1 and 100", 400, 2)
    from app.memory.access import page_cursor, read_cursor
    from app.schemas.memory_contracts import MemoryPageData

    settings = required_settings(session)
    filters = {"task_id": str(task_id), "collection": model.__tablename__}
    statement = select(model).where(model.task_id == task_id).order_by(model.created_at, model.id)
    anchor = read_cursor(settings, actor, filters, cursor)
    if anchor:
        statement = statement.where(tuple_(model.created_at, model.id) > anchor)
    rows = list(await session.scalars(statement.limit(limit + 1)))
    items = [
        view.model_validate(row, from_attributes=True).model_dump(mode="json")
        for row in rows[:limit]
    ]
    next_cursor = (
        page_cursor(settings, actor, filters, rows[limit - 1].created_at, rows[limit - 1].id)
        if len(rows) > limit
        else None
    )
    return Result(
        ok=True,
        command=command,
        data=MemoryPageData(returned=len(items), next_cursor=next_cursor).model_dump(),
        items=items,
    )


async def list_feedback(session, actor, task_id, cursor=None, limit=50):
    actor = await read_access(session, actor, task_id)
    return await page(
        session,
        actor,
        task_id,
        MemoryFeedbackEvent,
        MemoryFeedbackView,
        "memory feedback list",
        cursor,
        limit,
    )


async def list_samples(session, actor, task_id, cursor=None, limit=50):
    actor = await read_access(session, actor, task_id, evaluation=True)
    return await page(
        session,
        actor,
        task_id,
        MemoryEvalSample,
        MemoryEvalSampleView,
        "memory samples list",
        cursor,
        limit,
    )


async def show_sample(session, actor, sample_id, settings):
    sample = await session.get(MemoryEvalSample, sample_id)
    if sample is None:
        raise not_found()
    await read_access(session, actor, sample.task_id, evaluation=True)
    summary = unseal(settings, actor.org_id, sample.id, sample.encrypted_summary)
    if digest(summary) != sample.sanitized_sha256:
        raise ServiceError("memory_feedback_integrity", "Feedback integrity check failed", 500, 4)
    return Result(
        ok=True,
        command="memory samples show",
        data={
            "sample": MemoryEvalSampleView.model_validate(sample, from_attributes=True).model_dump(
                mode="json"
            ),
            "sanitized_summary": summary,
        },
    )


async def review_sample(session, actor, sample_id, body, settings):
    sample = await session.scalar(
        select(MemoryEvalSample).where(MemoryEvalSample.id == sample_id).with_for_update()
    )
    if sample is None:
        raise not_found()
    actor = await read_access(session, actor, sample.task_id, evaluation=True, review=True)
    if sample.review_revision != body.expected_revision:
        raise ServiceError(
            "revision_conflict", "Read the current sample revision before writing", 409, 2
        )
    from app.memory.safety import reject_sensitive

    await reject_sensitive(session, actor, [body.reason], settings)
    sample.review_state = "accepted" if body.action == "accept" else "excluded"
    sample.review_revision += 1
    sample.reviewed_by, sample.reviewed_at = actor.user_id, datetime.now(UTC)
    sample.review_reason_sha256 = digest(body.reason)
    audit(
        session,
        actor,
        "memory.eval.review",
        sample.id,
        {
            "review_revision": sample.review_revision,
            "review_state": sample.review_state,
            "reason_sha256": sample.review_reason_sha256,
            "feedback_event_id": str(sample.feedback_event_id),
        },
    )
    await session.flush()
    return Result(
        ok=True,
        command="memory samples review",
        data={
            "sample": MemoryEvalSampleView.model_validate(sample, from_attributes=True).model_dump(
                mode="json"
            )
        },
    )
