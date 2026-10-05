"""Candidate publication fenced by the existing durable attempt lease."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from app.core.errors import ServiceError
from app.memory.candidates import GENERATOR_VERSION, FeedbackCopyProvider, events_access, worker
from app.memory.feedback import digest, unseal
from app.models.memory import Memory, MemoryEvalSample
from app.schemas.memory_contracts import (
    MemoryCandidateInput,
    MemoryCandidateItemResult,
    MemoryCandidateJobResult,
    MemoryFeedbackView,
    MemoryTarget,
)
from app.services.response_cards import task_lock


async def process(execution):
    items = []
    async with execution.db.transaction(execution.org_id) as session:
        job = await execution.owned_job(session)
        actor = worker(job)
        submission = dict(job.result["submission"])
        if submission["generator_version"] != GENERATOR_VERSION:
            raise ServiceError(
                "memory_generator_version", "Candidate generator version is not supported", 409, 4
            )
        task_id = job.task_id
        assert task_id is not None
        _, events = await events_access(
            session, actor, task_id, [UUID(value) for value in submission["event_ids"]]
        )
        prepared = []
        for event, expected in zip(events, submission["event_hashes"], strict=True):
            try:
                summary = unseal(
                    execution.settings, event.org_id, event.id, event.encrypted_summary
                )
                if digest(summary) != event.sanitized_sha256 or expected != event.sanitized_sha256:
                    raise ServiceError(
                        "memory_feedback_integrity", "Feedback integrity check failed", 500, 4
                    )
                prepared.append(
                    MemoryCandidateInput(
                        org_id=execution.org_id,
                        event=MemoryFeedbackView.model_validate(event, from_attributes=True),
                        target=MemoryTarget(scope="org"),
                        sanitized_summary=summary,
                        generator_version=submission["generator_version"],
                    )
                )
            except ServiceError as exc:
                items.append(
                    MemoryCandidateItemResult(
                        event_id=event.id, outcome="failed", error_code=exc.code
                    )
                )
    provider = FeedbackCopyProvider()
    for request in prepared:
        try:
            output = await provider.propose(request)
            async with execution.db.transaction(execution.org_id) as session:
                await task_lock(session, task_id)
                job = await execution.owned_job(session)
                actor, events = await events_access(
                    session, worker(job), task_id, [request.event.id]
                )
                event = events[0]
                sample_id = await session.scalar(
                    select(MemoryEvalSample.id).where(
                        MemoryEvalSample.feedback_event_id == event.id,
                        MemoryEvalSample.generator_version == submission["generator_version"],
                    )
                )
                prior = await session.scalar(
                    select(Memory).where(
                        Memory.source_feedback_event_id == event.id,
                        Memory.generator_version == submission["generator_version"],
                    )
                )
                if prior is not None:
                    item = MemoryCandidateItemResult(
                        event_id=event.id,
                        outcome="duplicate",
                        memory_id=prior.id,
                        sample_id=sample_id,
                    )
                elif output.proposal is None:
                    item = MemoryCandidateItemResult(
                        event_id=event.id,
                        outcome="skipped",
                        sample_id=sample_id,
                        error_code=output.skip_reason,
                    )
                else:
                    from app.memory.crud import create_system_candidate
                    from app.memory.safety import reject_sensitive

                    # Sanitized placeholders are allowed, but literals are still
                    # rejected if confidential values changed after the feedback.
                    await reject_sensitive(
                        session, actor, [output.proposal.content.text], execution.settings
                    )
                    memory = await create_system_candidate(
                        session,
                        actor,
                        event,
                        output.proposal,
                        submission["generator_version"],
                        job_id=job.id,
                        run_id=execution.run_id,
                    )
                    item = MemoryCandidateItemResult(
                        event_id=event.id,
                        outcome="created",
                        memory_id=memory.id,
                        sample_id=sample_id,
                    )
                items.append(item)
        except ServiceError as exc:
            items.append(
                MemoryCandidateItemResult(
                    event_id=request.event.id, outcome="failed", error_code=exc.code
                )
            )
    async with execution.db.transaction(execution.org_id) as session:
        job = await execution.owned_job(session)
        await events_access(
            session, worker(job), task_id, [UUID(value) for value in submission["event_ids"]]
        )
        positions = {value: position for position, value in enumerate(submission["event_ids"])}
        items.sort(key=lambda item: positions[str(item.event_id)])
        failures = sum(item.outcome == "failed" for item in items)
        completion = "partial" if 0 < failures < len(items) else "complete"
        result = MemoryCandidateJobResult(
            job_id=job.id,
            run_id=execution.run_id,
            completion=completion,
            items=items,
            generator_version=submission["generator_version"],
            stop_reason="candidate_event_failed" if failures else None,
        ).model_dump(mode="json")
        job.result = {
            "submission": submission,
            **result,
            "exit_code": 4 if failures == len(items) else 5 if failures else 0,
        }
        job.status = "failed" if failures == len(items) else "succeeded"
        job.error = (
            {
                "code": "candidate_events_failed",
                "message": "Candidate events failed",
                "exit_code": 4,
            }
            if failures == len(items)
            else None
        )
        job.finished_at = datetime.now(UTC)
