"""Age-gated reconciliation of annotation objects saved before process death.

The immutable attempt ledger and delayed queue delivery commit before upload.
Cleanup reads only that ledger under org RLS; it never enumerates storage. Losing
an immutable run fence permits deletion only when no rendition references its key.
"""

import asyncio
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from psycopg import InterfaceError as DriverInterfaceError
from psycopg import OperationalError as DriverOperationalError
from pydantic import AwareDatetime, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import InterfaceError, OperationalError

from app.core.errors import ServiceError
from app.jobs.execution import locked_job
from app.models.screenshots import ScreenshotRendition
from app.providers.screenshot_objects import discard
from app.schemas.contracts import Contract

GRACE_SECONDS = 300
MAX_STAGED_OBJECTS = 64
DELETE_TIMEOUT_SECONDS = 5.0
FIELD = "annotation_staged_objects"
KINDS = {"annotation_render", "annotation_release"}


class StagedObject(Contract):
    run_id: UUID
    key: str = Field(min_length=1, max_length=400)
    asset_id: UUID
    rendition_id: UUID
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: AwareDatetime


def _failure(code: str, message: str, *, retryable: bool = False):
    return ServiceError(code, message, 503 if retryable else 409, 3 if retryable else 4)


def _expected_key(org_id, asset_id, rendition_id, image_sha256):
    return f"org/{org_id}/screenshots/{asset_id}/{rendition_id}/{image_sha256}.png"


async def _clock(session) -> datetime:
    now = await session.scalar(select(func.clock_timestamp()))
    if now is None or now.tzinfo is None:
        raise _failure("annotation_cleanup_clock", "Cleanup requires a verified database clock")
    return now.astimezone(UTC)


def _objects(job) -> list[StagedObject]:
    saved = job.result.get(FIELD, [])
    if not isinstance(saved, list) or len(saved) > MAX_STAGED_OBJECTS:
        raise _failure("annotation_cleanup_ledger", "Staged annotation ledger is invalid")
    try:
        objects = [StagedObject.model_validate(item) for item in saved]
    except (ValidationError, TypeError) as exc:
        raise _failure("annotation_cleanup_ledger", "Staged annotation ledger is invalid") from exc
    if len({item.key for item in objects}) != len(objects) or any(
        item.key != _expected_key(job.org_id, item.asset_id, item.rendition_id, item.image_sha256)
        for item in objects
    ):
        raise _failure("annotation_cleanup_ledger", "Staged annotation key differs from its scope")
    return objects


async def stage(session, queue, job, run_id, key, asset_id, rendition_id, image_sha256) -> None:
    """Register one owned attempt object and its cleanup in the upload transaction."""
    owned = await locked_job(session, job.id)
    now = await _clock(session)
    if (
        owned is None
        or owned.org_id != job.org_id
        or owned.kind not in KINDS
        or owned.status != "running"
        or owned.run_id != run_id
        or owned.lease_until is None
        or owned.lease_until <= now
    ):
        raise _failure("job_attempt_stopped", "Only the current annotation attempt can stage bytes")
    if (
        not isinstance(image_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", image_sha256) is None
        or key != _expected_key(owned.org_id, asset_id, rendition_id, image_sha256)
    ):
        raise _failure("annotation_cleanup_ledger", "Annotation staging key is invalid")
    try:
        descriptor = StagedObject(
            run_id=run_id,
            key=key,
            asset_id=asset_id,
            rendition_id=rendition_id,
            image_sha256=image_sha256,
            created_at=now,
        )
    except ValidationError as exc:
        raise _failure(
            "annotation_cleanup_ledger", "Annotation staging descriptor is invalid"
        ) from exc
    saved = _objects(owned)
    previous = next((item for item in saved if item.key == key), None)
    if previous is not None:
        if previous.model_dump(exclude={"created_at"}) != descriptor.model_dump(
            exclude={"created_at"}
        ):
            raise _failure(
                "annotation_cleanup_ledger", "A staged key cannot change its attempt binding"
            )
    else:
        if len(saved) == MAX_STAGED_OBJECTS:
            # Keep recovery evidence rather than dropping an unresolved orphan.
            raise _failure(
                "annotation_staging_limit", "Start a new preview after the staged-attempt limit"
            )
        saved.append(descriptor)
        owned.result = {**owned.result, FIELD: [item.model_dump(mode="json") for item in saved]}
    try:
        await queue.enqueue_annotation_cleanup_in_transaction(
            session, str(owned.org_id), str(owned.id), delay=GRACE_SECONDS
        )
    except (
        OSError,
        InterfaceError,
        OperationalError,
        DriverInterfaceError,
        DriverOperationalError,
    ) as exc:
        raise _failure(
            "annotation_queue_unavailable",
            "Annotation cleanup queue is temporarily unavailable",
            retryable=True,
        ) from exc


async def reconcile(processor, org_id: UUID, job_id: UUID) -> dict[str, int]:
    """Remove only aged, fenced, unreferenced keys from this job's saved ledger."""
    candidates = []
    receipt = {"deleted": 0, "retained": 0, "pending": 0}
    async with processor.db.transaction(org_id) as session:
        job = await locked_job(session, job_id)
        if job is None:
            return receipt
        if job.org_id != org_id or job.kind not in KINDS:
            raise _failure("annotation_cleanup_ledger", "Cleanup requires an annotation job")
        now = await _clock(session)
        for item in _objects(job):
            referenced = await session.scalar(
                select(ScreenshotRendition.id)
                .where(ScreenshotRendition.storage_key == item.key)
                .limit(1)
            )
            if referenced is not None:
                receipt["retained"] += 1
                continue
            live = (
                job.status == "running"
                and job.run_id == item.run_id
                and job.lease_until is not None
                and job.lease_until > now
            )
            if now < item.created_at + timedelta(seconds=GRACE_SECONDS) or live:
                receipt["pending"] += 1
                continue
            candidates.append(item)
        if receipt["pending"]:
            await processor.queue.enqueue_annotation_cleanup_in_transaction(
                session, str(org_id), str(job_id), delay=GRACE_SECONDS
            )
    # No network operation holds Task/Job locks. Every candidate has irreversibly
    # lost its run fence; retries claim a new UUID and cannot publish this key.
    for item in candidates:
        async with processor.db.transaction(org_id) as session:
            job = await locked_job(session, job_id)
            if job is None or job.org_id != org_id:
                receipt["retained"] += 1
                continue
            if job.kind not in KINDS or item not in _objects(job):
                raise _failure("annotation_cleanup_ledger", "Cleanup attempt binding changed")
            now = await _clock(session)
            if (
                job.status == "running"
                and job.run_id == item.run_id
                and job.lease_until is not None
                and job.lease_until > now
            ):
                receipt["pending"] += 1
                await processor.queue.enqueue_annotation_cleanup_in_transaction(
                    session, str(org_id), str(job_id), delay=GRACE_SECONDS
                )
                continue
            referenced = await session.scalar(
                select(ScreenshotRendition.id)
                .where(ScreenshotRendition.storage_key == item.key)
                .limit(1)
            )
            if referenced is not None:
                receipt["retained"] += 1
                continue
        try:
            async with asyncio.timeout(DELETE_TIMEOUT_SECONDS):
                from app.providers.storage import annotation_storage

                await discard(
                    annotation_storage(processor.storage, processor.settings), org_id, item.key
                )
        except TimeoutError as exc:
            raise _failure(
                "annotation_cleanup_timeout", "Annotation object cleanup timed out", retryable=True
            ) from exc
        receipt["deleted"] += 1
    return receipt
