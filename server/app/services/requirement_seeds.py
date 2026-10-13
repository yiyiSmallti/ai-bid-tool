"""Publish initial encrypted requirement reviews inside the extract transaction."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from app.core.errors import ServiceError, not_found
from app.core.security import Secrets
from app.models.entities import Job, Requirement
from app.models.requirement_confirmation import (
    RequirementReview,
    RequirementReviewEvent,
    RequirementReviewSet,
)
from app.services.requirement_source import (
    canonical,
    content_of,
    digest,
    effective_reviews,
    raw_digest,
)
from app.services.task_access import access


def seal(settings, task_id, record_id, payload):
    return Secrets.for_data(settings).encrypt(
        canonical({"task_id": str(task_id), "record_id": str(record_id), "payload": payload})
    )


async def seed_reviews(
    session,
    actor,
    task_id,
    extraction_job_id,
    *,
    settings,
    origin="extracted",
    manual_request=None,
    manual_requirement_id=None,
):
    """Publish encrypted seeds in the extract transaction; never reset existing decisions."""
    if origin == "legacy":
        await access(session, actor, task_id, "req:confirm", write=True)
    job = await session.get(Job, extraction_job_id)
    if job is None or (job.task_id, job.kind, job.status) != (
        task_id,
        "extract",
        "succeeded",
    ):
        raise not_found()
    scope = await session.scalar(
        select(RequirementReviewSet).where(
            RequirementReviewSet.extraction_job_id == job.id,
        )
    )
    if scope is None:
        scope = RequirementReviewSet(
            id=uuid4(),
            task_id=task_id,
            extraction_job_id=job.id,
            document_id=job.document_id,
            origin="manual" if origin.startswith("manual") else "model",
            revision=1,
            membership_sha256=digest([]),
            confirmation_sha256=digest([]),
        )
        session.add(scope)
        await session.flush()
    reqs = list(
        (
            await session.scalars(
                select(Requirement).where(Requirement.job_id == job.id).order_by(Requirement.id)
            )
        ).all()
    )
    if manual_request is not None:
        # A manual add owns only its new ID, never historical model rows.
        if manual_requirement_id is None:
            raise ServiceError(
                "invalid_manual_entry",
                "Manual seed requires its newly inserted requirement",
                422,
                2,
            )
        reqs = [req for req in reqs if req.id == manual_requirement_id]
    states = await effective_reviews(session, reqs)
    for req in reqs:
        if states[req.id].stored is not None:
            continue
        eff = states[req.id]
        rid, eid = uuid4(), uuid4()
        snapshot = content_of(req).model_dump(mode="json")
        ciphertext = seal(settings, task_id, rid, snapshot)
        pin = eff.source_pin.model_dump(mode="json") if eff.source_pin else None
        row = RequirementReview(
            id=rid,
            task_id=task_id,
            extraction_job_id=job.id,
            requirement_id=req.id,
            origin=origin,
            revision=1,
            current_event_id=eid,
            state="legacy_unconfirmed" if origin == "legacy" else "unconfirmed",
            review_hash=eff.review_hash,
            source_pin=pin,
            snapshot_ciphertext=ciphertext,
            rejected_job_id=manual_request.rejected_item.job_id
            if manual_request and manual_request.rejected_item
            else None,
            rejected_index=manual_request.rejected_item.index
            if manual_request and manual_request.rejected_item
            else None,
            rejected_summary_sha256=manual_request.rejected_item.summary_sha256
            if manual_request and manual_request.rejected_item
            else None,
        )
        session.add(row)
        await session.flush()
        session.add(
            RequirementReviewEvent(
                id=eid,
                task_id=task_id,
                extraction_job_id=job.id,
                requirement_id=req.id,
                review_id=rid,
                revision=1,
                action="manual_add" if manual_request else "seed",
                state_after="legacy_unconfirmed" if origin == "legacy" else "unconfirmed",
                review_hash=eff.review_hash,
                snapshot_sha256=digest(snapshot),
                snapshot_ciphertext=ciphertext,
                source_pin=pin,
                actor_kind="session" if manual_request or origin == "legacy" else "worker",
                request_id=manual_request.request_id if manual_request else None,
                reason_sha256=raw_digest(manual_request.reason) if manual_request else None,
                reason_ciphertext=seal(settings, task_id, rid, {"reason": manual_request.reason})
                if manual_request
                else None,
                occurred_at=datetime.now(UTC),
            )
        )
        await session.flush()
    await session.refresh(scope)
    return scope
