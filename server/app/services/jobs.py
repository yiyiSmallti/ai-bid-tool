"""Job status and cancellation with each job kind's own access checks."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.models.entities import Job
from app.providers.storage import Storage
from app.schemas.contracts import Result
from app.services.auth import Identity
from app.services.sandbox import guard_job as sandbox_guard_job

FIELDS = ("id", "kind", "status", "result", "error", "attempts", "reasoning")


def _public_result(job: Job) -> dict:
    # Submission authorization data is internal worker state.
    return {key: value for key, value in job.result.items() if key != "submission"}


async def status(session: AsyncSession, identity: Identity, job_id: UUID, storage: Storage) -> dict:
    """The `job status` Result after the access checks of the job's kind; some kinds
    report partial completion, warnings or cost of their own."""
    identity.require("job:read")
    job = await session.get(Job, job_id)
    if job is None:
        raise not_found()
    if job.kind == "export_render":
        from app.services.exports import job_access

        await job_access(session, identity, job, storage)
    await sandbox_guard_job(session, identity, job)
    payload = Result(
        ok=True,
        command="job status",
        data=jsonable_encoder({field: getattr(job, field) for field in FIELDS}),
    ).model_dump(mode="json")
    if job.kind in {"screenshot_render", "screenshot_analyze"}:
        from app.services.screenshot_jobs import check_job_access

        await check_job_access(session, identity, job)
        payload["data"]["result"] = _public_result(job)
        payload["ok"] = job.result.get("completion") != "partial"
        payload["cost"] = job.result.get("cost", payload["cost"])
    if job.kind == "screenshot_search":
        from app.services.vendor_search import check_job_access as search_access

        await search_access(session, identity, job)
        payload["data"]["result"] = _public_result(job)
    if job.kind == "prototype_generate":
        from app.services.prototype_generation import check_job_access as prototype_access

        await prototype_access(session, identity, job)
        payload["data"]["result"] = _public_result(job)
        payload["cost"] = job.result.get("cost", payload["cost"])
    if job.kind == "sandbox" and job.status in {"failed", "cancelled"}:
        payload["ok"] = False
    if job.kind == "provider_test":
        identity.require("provider:read")
        payload["data"]["result"] = _public_result(job)
        payload["cost"] = job.result.get("cost", payload["cost"])
    if job.kind == "export_render":
        payload["data"]["result"] = _public_result(job)
        return payload
    if job.kind in {"draft", "card_generate"}:
        identity.require("draft:read" if job.kind == "draft" else "card:read")
        identity.require("task:read")
        if job.kind == "card_generate":
            from app.services.card_generation import check_input_access

            await check_input_access(
                session, identity, job.task_id, job.result["submission"]["input_manifest"]
            )
        if job.result.get("draft_id"):
            from app.services.drafts import show_draft

            await show_draft(session, identity, UUID(job.result["draft_id"]), storage)
        if job.result.get("completion") == "partial":
            payload["ok"] = False
        payload["warnings"] = job.result.get("warnings", [])
        payload["cost"] = job.result.get("cost", payload["cost"])
        payload["data"]["result"] = _public_result(job)
    return payload


async def cancel(session: AsyncSession, identity: Identity, job_id: UUID, storage: Storage) -> Job:
    identity.require("job:cancel")
    job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None:
        raise not_found()
    if job.kind == "export_render":
        from app.services.exports import job_access

        await job_access(session, identity, job, storage, cancel=True)
    await sandbox_guard_job(session, identity, job, cancel=True)
    if job.status not in {"cancelled", "queued", "running"}:
        raise ServiceError("terminal_job", "Completed jobs cannot be cancelled", 409, 2)
    job.status, job.finished_at = "cancelled", datetime.now(UTC)
    return job
