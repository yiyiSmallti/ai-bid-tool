"""Job status and cancellation with each job kind's own access checks."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.jobs.execution import job_cost
from app.models.entities import Job
from app.providers.storage import Storage
from app.schemas.contracts import Result
from app.services.auth import Identity
from app.services.sandbox import guard_job as sandbox_guard_job
from app.services.task_authorization import task_authorized

FIELDS = ("id", "kind", "status", "result", "error", "attempts", "reasoning")


async def agent_access(session, identity, job):
    from sqlalchemy import select

    from app.models.agent import AgentJobLink, AgentSession

    linked = await session.scalar(
        select(AgentJobLink).where(AgentJobLink.job_id == job.id, AgentJobLink.owned.is_(True))
    )
    if linked is None:
        if job.kind == "agent" or job.agent_session_id is not None:
            raise not_found()
        return None
    row = await session.get(AgentSession, linked.session_id)
    if row is None or identity.user_id != row.owner_user_id:
        raise not_found()
    if (
        identity.actor_kind == "agent"
        and identity.principal_id == row.principal_id
        and identity.session_id == row.id
    ):
        return row
    if identity.actor_kind == "agent" and job.status == "succeeded":
        # A later session by the same owner may reference a completed job without
        # owning its cost or cancellation. Its broker must have saved that link.
        reference = await session.scalar(
            select(AgentSession)
            .join(AgentJobLink, AgentJobLink.session_id == AgentSession.id)
            .where(
                AgentSession.id == identity.session_id,
                AgentSession.principal_id == identity.principal_id,
                AgentSession.owner_user_id == identity.user_id,
                AgentJobLink.job_id == job.id,
                AgentJobLink.owned.is_(False),
            )
        )
        if reference is not None:
            return reference
    if identity.actor_kind != "session" or identity.token_id is not None:
        raise not_found()
    identity.require("agent:read")
    return row


def _public_result(job: Job) -> dict:
    # Submission authorization and encrypted snapshots are internal worker state.
    return {
        key: value
        for key, value in job.result.items()
        if key
        not in {"submission", "encrypted_input", "release_retries", "annotation_staged_objects"}
    }


async def read_access(
    session: AsyncSession, identity: Identity, job: Job, storage: Storage
) -> None:
    """Same kind-specific authorization for status, boards and event replay."""
    identity.require("job:read")
    await agent_access(session, identity, job)
    if job.task_id is not None:
        identity.require("task:read")
    if job.kind == "bid_review_report":
        from app.services.bid_review_report import job_access

        await job_access(session, identity, job)
    if job.kind == "bid_review":
        from app.services.bid_review_run import job_access

        await job_access(session, identity, job)
    if job.kind == "bid_review_prepare":
        from app.services.bid_preparation import job_access

        await job_access(session, identity, job)
    if job.kind == "export_render":
        from app.services.exports import job_access

        await job_access(session, identity, job, storage)
    if job.kind == "export_preview":
        from app.services.page_previews import job_access as preview_access

        await preview_access(session, identity, job)
    if job.kind == "check":
        from app.services.check import job_access as check_access

        await check_access(session, identity, job, storage)
    if job.kind == "score":
        from app.services.score_execution import job_access as score_access

        await score_access(session, identity, job, storage)
    if job.kind == "score_rubric":
        from app.services.score_generation import job_access as rubric_access

        await rubric_access(session, identity, job)
    if job.kind == "memory_candidate":
        from app.memory.candidates import job_access

        await job_access(session, identity, job)
    if job.kind in {"annotation_render", "annotation_release"}:
        from app.services.annotations import check_job_access

        await check_job_access(session, identity, job)
    await sandbox_guard_job(session, identity, job)
    if job.kind in {"screenshot_render", "screenshot_analyze"}:
        from app.services.screenshot_jobs import check_job_access

        await check_job_access(session, identity, job)
    if job.kind == "screenshot_search":
        from app.services.vendor_search import check_job_access

        await check_job_access(session, identity, job)
    if job.kind == "prototype_generate":
        from app.services.prototype_generation import check_job_access

        await check_job_access(session, identity, job)
    if job.kind == "provider_test":
        identity.require("provider:read")
    if job.kind in {"draft", "card_generate"}:
        identity.require("draft:read" if job.kind == "draft" else "card:read")
        if job.kind == "card_generate":
            from app.services.card_generation import check_input_access

            await check_input_access(
                session, identity, job.task_id, job.result["submission"]["input_manifest"]
            )
        if job.result.get("draft_id"):
            from app.services.drafts import show_draft

            await show_draft(session, identity, UUID(job.result["draft_id"]), storage)


@task_authorized("job:read", parent=("job_id", "jobs"), optional=True)
async def status(session: AsyncSession, identity: Identity, job_id: UUID, storage: Storage) -> dict:
    """Read a job only after its task and kind-specific dependency checks."""
    job = await session.get(Job, job_id)
    if job is None:
        raise not_found()
    await read_access(session, identity, job, storage)
    payload = Result(
        ok=job.status not in {"failed", "cancelled"} and job.result.get("completion") != "partial",
        command="job status",
        data=jsonable_encoder({field: getattr(job, field) for field in FIELDS}),
    ).model_dump(mode="json")
    from app.services.agent_tools import provenance

    origin = await provenance(session, job.id)
    if origin is not None:
        payload["data"]["agent_provenance"] = origin
    settings = session.info.get("memory_settings")
    payload["cost"] = await job_cost(
        session, job.id, settings.billing_currency if settings else None
    )
    public_budget = job.result.get("budget")
    if isinstance(public_budget, dict):
        refreshed = {**public_budget, "cost": payload["cost"]}
        payload["data"]["result"]["budget"] = refreshed
    if job.kind in {"screenshot_render", "screenshot_analyze"}:
        from app.services.screenshot_jobs import check_job_access

        await check_job_access(session, identity, job)
        payload["data"]["result"] = _public_result(job)
        payload["ok"] = job.result.get("completion") != "partial"

    if job.kind == "product_simulation":
        identity.require("task:read")
        payload["data"]["result"] = _public_result(job)
    if job.kind == "screenshot_search":
        from app.services.vendor_search import check_job_access as search_access

        await search_access(session, identity, job)
        payload["data"]["result"] = _public_result(job)
    if job.kind == "prototype_generate":
        from app.services.prototype_generation import check_job_access as prototype_access

        await prototype_access(session, identity, job)
        payload["data"]["result"] = _public_result(job)

    if job.kind == "sandbox" and job.status in {"failed", "cancelled"}:
        payload["ok"] = False
    if job.kind == "provider_test":
        identity.require("provider:read")
        payload["data"]["result"] = _public_result(job)

    if job.kind in {"check", "score_rubric", "score"}:
        public = _public_result(job)
        for internal_envelope_field in ("cost", "warnings", "exit_code"):
            public.pop(internal_envelope_field, None)
        payload["data"]["result"] = public
        payload["warnings"] = job.result.get("warnings", [])

        if job.status in {"failed", "cancelled"} or job.result.get("completion") == "partial":
            payload["ok"] = False
    if job.kind == "memory_candidate":
        payload["data"]["result"] = _public_result(job)
        payload["ok"] = (
            job.status not in {"failed", "cancelled"} and job.result.get("completion") != "partial"
        )

        payload["warnings"] = job.result.get("warnings", [])
    if job.kind == "export_render":
        payload["data"]["result"] = _public_result(job)
        if isinstance(payload["data"]["result"].get("budget"), dict):
            payload["data"]["result"]["budget"] = {
                **payload["data"]["result"]["budget"],
                "cost": payload["cost"],
            }
        return payload
    if job.kind == "export_preview":
        # The stored PDF location is served only through the export preview routes.
        preview = job.result.get("preview")
        payload["data"]["result"] = {
            **_public_result(job),
            "preview": {"page_count": preview["page_count"]} if preview else None,
        }
        if isinstance(payload["data"]["result"].get("budget"), dict):
            payload["data"]["result"]["budget"] = {
                **payload["data"]["result"]["budget"],
                "cost": payload["cost"],
            }
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

        payload["data"]["result"] = _public_result(job)
    if job.kind not in {"check", "score_rubric", "score"}:
        payload["data"]["result"] = _public_result(job)

    payload["warnings"] = job.result.get("warnings", payload["warnings"])
    if (
        not job.vendor_cost_history_complete
        and "historical_vendor_cost_unavailable" not in payload["warnings"]
    ):
        payload["warnings"] = [*payload["warnings"], "historical_vendor_cost_unavailable"]
    payload["items"] = job.result.get("items", [])
    payload["ok"] = (
        job.status not in {"failed", "cancelled"} and job.result.get("completion") != "partial"
    )
    if isinstance(payload["data"]["result"].get("budget"), dict):
        payload["data"]["result"]["budget"] = {
            **payload["data"]["result"]["budget"],
            "cost": payload["cost"],
        }
    if job.kind == "agent":
        from app.schemas.agent_contracts import AgentJobResult

        if "session_id" in job.result:
            payload["data"]["result"] = {
                key: value
                for key, value in payload["data"]["result"].items()
                if key in AgentJobResult.model_fields
            }
        if isinstance(job.result.get("budget"), dict):
            payload["data"]["budget"] = {**job.result["budget"], "cost": payload["cost"]}
    return payload


@task_authorized("job:cancel", parent=("job_id", "jobs"), write=True, optional=True)
async def cancel(session: AsyncSession, identity: Identity, job_id: UUID, storage: Storage) -> Job:
    from app.jobs.execution import locked_job

    job = await locked_job(session, job_id)
    if job is None:
        raise not_found()
    linked = await agent_access(session, identity, job)
    if linked is not None:
        from app.schemas.agent_contracts import AgentCancelRequest
        from app.services import agents

        settings = session.info.get("memory_settings")
        if settings is None:
            raise ServiceError(
                "agent_settings_unavailable", "Agent settings are unavailable", 503, 4
            )
        await agents.cancel(
            session,
            identity,
            linked.id,
            AgentCancelRequest(
                expected_revision=linked.revision,
                idempotency_key=identity.invocation_id or uuid4(),
            ),
            settings,
        )
        return job
    identity.require("job:cancel")
    if job.kind == "bid_review_report":
        from app.services.bid_review_report import job_access

        await job_access(session, identity, job, cancel=True)
    if job.kind == "bid_review":
        from app.services.bid_review_run import job_access
        from app.services.versioned import audit

        await job_access(session, identity, job, cancel=True)
        if job.status in {"queued", "running"}:
            audit(
                session,
                identity,
                "bid_review.cancelled",
                job.id,
                {"task_id": str(job.task_id), "job_id": str(job.id)},
            )
    if job.kind == "bid_review_prepare":
        from app.services.bid_preparation import job_access
        from app.services.versioned import audit

        await job_access(session, identity, job, cancel=True)
        if job.status in {"queued", "running"}:
            audit(
                session,
                identity,
                "bid_review.preparation.cancelled",
                job.id,
                {
                    "task_id": str(job.task_id),
                    "job_id": str(job.id),
                    "input_hash": job.result["submission"]["input_hash"],
                },
            )
    if job.kind == "export_render":
        from app.services.exports import job_access

        await job_access(session, identity, job, storage, cancel=True)
    if job.kind == "export_preview":
        from app.services.page_previews import job_access as preview_access

        await preview_access(session, identity, job)
    if job.kind == "check":
        from app.services.check import job_access as check_access

        await check_access(session, identity, job, storage, cancel=True)
    if job.kind == "score":
        from app.services.score_execution import job_access as score_access

        await score_access(session, identity, job, storage, cancel=True)
    if job.kind == "score_rubric":
        from app.services.score_generation import job_access as rubric_access

        await rubric_access(session, identity, job, cancel=True)
    if job.kind == "memory_candidate":
        from app.memory.candidates import job_access

        await job_access(session, identity, job, cancel=True)
    if job.kind in {"annotation_render", "annotation_release"}:
        from app.services.annotations import check_job_access
        from app.services.versioned import audit

        await check_job_access(session, identity, job, cancel=True)
        if job.status in {"queued", "running"}:
            audit(
                session,
                identity,
                "annotation.cancel",
                job.id,
                {"task_id": str(job.task_id), "kind": job.kind},
            )
    await sandbox_guard_job(session, identity, job, cancel=True)
    if job.status not in {"cancelled", "queued", "running"}:
        raise ServiceError("terminal_job", "Completed jobs cannot be cancelled", 409, 2)
    if job.kind in {"score_rubric", "score"} and job.status != "cancelled":
        from app.services.versioned import audit

        audit(
            session,
            identity,
            f"{job.kind}.cancelled",
            job.id,
            {
                "task_id": str(job.task_id),
                "job_id": str(job.id),
                "actor_kind": identity.actor_kind,
                "input_hash": job.result["submission"]["input_hash"],
            },
        )
    job.status, job.finished_at = "cancelled", datetime.now(UTC)
    return job
