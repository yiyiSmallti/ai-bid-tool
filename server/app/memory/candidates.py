"""Local feedback-copy-v1 proposals and durable, recoverable candidate submissions."""

import json
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from procrastinate.exceptions import ConnectorException
from psycopg import OperationalError
from sqlalchemy import select

from app.core.errors import ServiceError, not_found
from app.models.entities import Job
from app.models.memory import MemoryFeedbackEvent
from app.schemas.contracts import Result
from app.schemas.memory_contracts import (
    MemoryCandidateInput,
    MemoryCandidateOutput,
    MemoryCandidateProposal,
    MemoryContent,
    MemoryJobSubmissionData,
)
from app.services import redaction
from app.services.auth import Identity
from app.services.response_cards import access, task_lock

GENERATOR_VERSION = "feedback-copy-v1"


def reusable_text(text: str) -> bool:
    residual = redaction.PLACEHOLDER.sub("", text)
    residual = redaction.SECRET_PLACEHOLDER.sub("", residual)
    residual = residual.replace("[EVIDENCE_OMITTED]", "")
    residual = re.sub(
        rf"(?:{redaction.AMOUNT_LABEL}|身份证(?:号码?|号)?|银行(?:账号|账户|帐号|卡号)|收款账号|账号|帐号"
        r"|联系人|联系人员|联络人|项目联系人|联系电话|手机号码?|手机号|电话|手机"
        r"|identity\s*(?:number|no\.?|id)|bank\s*account(?:\s*(?:number|no\.?))?|IBAN"
        r"|contact(?:\s*(?:person|name))?|telephone|phone|mobile|tel\.?|password|passwd|api[_ -]?key|authorization|token|secret|密码|密钥)\s*[:：=]?",
        "",
        residual,
        flags=re.I,
    )
    return bool(re.search(r"[\w\u4e00-\u9fff]", residual))


class FeedbackCopyProvider:
    name = "local-feedback-copy"
    version = GENERATOR_VERSION

    async def propose(self, request: MemoryCandidateInput) -> MemoryCandidateOutput:
        from app.memory.access import org_target

        org_target(request.target)
        if request.generator_version != self.version:
            raise ServiceError(
                "memory_generator_version", "Candidate generator version is not supported", 409, 4
            )
        text = request.sanitized_summary.strip()
        if text == "[NO_REUSABLE_FEEDBACK]":
            return MemoryCandidateOutput(proposal=None, skip_reason="no_reusable_feedback")
        if not reusable_text(text):
            sensitive = redaction.PLACEHOLDER.search(text) or redaction.SECRET_PLACEHOLDER.search(
                text
            )
            return MemoryCandidateOutput(
                proposal=None, skip_reason="sensitive_only" if sensitive else "no_reusable_feedback"
            )
        prefix = (
            "适用性待审核的人工驳回反馈：\n"
            if request.event.kind == "card_rejected"
            else "适用性待审核的人工修改反馈：\n"
        )
        if len(prefix + text) > 2000:
            return MemoryCandidateOutput(proposal=None, skip_reason="no_reusable_feedback")
        return MemoryCandidateOutput(
            proposal=MemoryCandidateProposal(
                content=MemoryContent(
                    kind="rule",
                    conflict_key=f"feedback.{request.event.id}",
                    text=prefix + text,
                    tags=[
                        "human-feedback",
                        "rejection" if request.event.kind == "card_rejected" else "edit",
                    ],
                )
            )
        )


def worker(job):
    submitted = job.result["submission"]
    return Identity(
        UUID(submitted["actor_user_id"]),
        job.org_id,
        set(submitted["scopes"]),
        "viewer",
        UUID(submitted["actor_token_id"]) if submitted["actor_token_id"] else None,
        "worker",
    )


async def events_access(session, actor, task_id, event_ids, *, execute=True):
    actor = await access(session, actor, "memory:candidate:run" if execute else "memory:read")
    for scope in ("memory:read", "card:read", "task:read", *(["memory:write"] if execute else [])):
        actor.require(scope)
    from app.services.response_cards import require_card

    events = []
    for event_id in event_ids:
        event = await session.get(MemoryFeedbackEvent, event_id)
        if event is None or event.task_id != task_id or event.org_id != actor.org_id:
            raise not_found()
        card, _, _ = await require_card(session, event.card_id)
        if card.task_id != task_id:
            raise not_found()
        if event.kind == "card_confirmed":
            raise ServiceError("invalid_feedback", "Confirmation is evaluation-only", 400, 2)
        events.append(event)
    if len({event.document_id for event in events}) != 1:
        raise ServiceError("invalid_feedback_batch", "Feedback batch must use one document", 400, 2)
    return actor, events


async def create_job(session, actor, task_id, events, *, retry):
    from app.memory.feedback import digest

    manifest = {
        "org_id": str(actor.org_id),
        "task_id": str(task_id),
        "document_id": str(events[0].document_id),
        "event_ids": [str(event.id) for event in events],
        "event_hashes": [event.sanitized_sha256 for event in events],
        "generator_version": GENERATOR_VERSION,
    }
    key = digest(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    job = await session.scalar(select(Job).where(Job.cache_key == key).with_for_update())
    reused = job is not None
    if job is None:
        job = Job(
            id=uuid4(),
            org_id=actor.org_id,
            task_id=task_id,
            document_id=events[0].document_id,
            kind="memory_candidate",
            cache_key=key,
            status="queued",
            result={
                "submission": {
                    **manifest,
                    "input_hash": key,
                    "actor_user_id": str(actor.user_id),
                    "actor_token_id": str(actor.token_id) if actor.token_id else None,
                    "actor_kind": actor.actor_kind,
                    "scopes": sorted(actor.scopes),
                }
            },
        )
        session.add(job)
    elif retry and (
        job.status in {"failed", "cancelled"}
        or (job.status == "running" and job.lease_until and job.lease_until <= datetime.now(UTC))
    ):
        job.status, job.error, job.queue_id = "queued", None, None
        job.run_id, job.lease_until, job.finished_at, job.attempts = None, None, None, 0
        job.result = {
            "submission": {
                **job.result["submission"],
                "actor_user_id": str(actor.user_id),
                "actor_token_id": str(actor.token_id) if actor.token_id else None,
                "actor_kind": actor.actor_kind,
                "scopes": sorted(actor.scopes),
            }
        }
    await session.flush()
    return job, reused


async def submit_candidates(session, actor, task_id, body, settings):
    actor, events = await events_access(session, actor, task_id, body.event_ids)
    if body.action.dry_run:
        return Result(
            ok=True,
            command="memory candidates run",
            data=MemoryJobSubmissionData(
                job_id=None,
                task_id=task_id,
                reused=False,
                dry_run=True,
                event_count=len(events),
            ).model_dump(mode="json"),
        ), None
    await task_lock(session, task_id)
    job, reused = await create_job(session, actor, task_id, events, retry=body.action.retry)
    return Result(
        ok=True,
        command="memory candidates run",
        data=MemoryJobSubmissionData(
            job_id=job.id,
            task_id=task_id,
            reused=reused,
            dry_run=False,
            event_count=len(events),
        ).model_dump(mode="json"),
    ), job


async def job_access(session, actor, job, *, cancel=False):
    if job.task_id is None or job.kind != "memory_candidate":
        raise not_found()
    # Status requires read grants only; cancellation additionally requires the
    # original execution grants and rechecks every source card.
    actor, _ = await events_access(
        session,
        actor,
        job.task_id,
        [UUID(value) for value in job.result["submission"]["event_ids"]],
        execute=cancel,
    )
    return actor


async def dispatch(db, queue, org_id, job):
    if job.status != "queued" or job.queue_id is not None:
        return []
    try:
        queue_id = await queue.enqueue(str(org_id), str(job.id))
    except (OSError, ConnectorException, OperationalError):
        return [f"memory_candidate_dispatch_pending:{job.id}"]
    async with db.transaction(org_id) as session:
        saved = await session.scalar(select(Job).where(Job.id == job.id).with_for_update())
        if saved is not None and saved.queue_id is None:
            saved.queue_id = queue_id
    return []
