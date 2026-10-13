"""Read-only configuration and work-size preview; never admits or dispatches calls."""

import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select, true

from app.models.entities import Job, VendorCall
from app.schemas.contracts import Cost
from app.schemas.preflight_contracts import CallPlan, PreflightData


def estimate_quotes(quotes: list[CallPlan]) -> Cost:
    return Cost(
        calls=len(quotes),
        llm_tokens=sum(q.input_tokens_upper_bound + q.output_tokens_upper_bound for q in quotes),
        ocr_pages=sum(q.ocr_pages_upper_bound for q in quotes),
        search_requests=sum(q.search_requests for q in quotes),
        browser_units=sum(q.browser_units for q in quotes),
    )


async def attach(
    session,
    data: dict,
    *,
    command: str,
    task_id: UUID | None,
    input_hash: str,
    settings=None,
    quotes: list[CallPlan] | None = None,
    planned_calls: int | None = None,
    dynamic: bool = False,
    cached_job: Job | None = None,
    quote_sources: list[Callable[[], CallPlan]] | None = None,
) -> dict:
    """Attach work counts only after the command has validated access and inputs."""
    if quote_sources is not None:
        quotes = [source() for source in quote_sources]
    if cached_job is None and task_id is not None:
        kinds = {
            "check run": "check",
            "review run": "bid_review",
            "score run": "score",
            "score rubric generate": "score_rubric",
            "draft": "draft",
            "screenshot annotate": "screenshot_render",
            "evidence stamp": "annotation_render",
            "evidence annotation release": "annotation_release",
            "screenshot analyze": "screenshot_analyze",
            "ui mock": "prototype_generate",
            "evidence search": "screenshot_search",
            "product simulate": "product_simulation",
        }
        kind = kinds.get(command)
        if kind is not None:
            actor = session.info.get("actor")
            cached_job = await session.scalar(
                select(Job)
                .where(
                    Job.task_id == task_id,
                    Job.kind == kind,
                    Job.result["submission"]["input_hash"].astext == input_hash,
                    Job.actor_token_id == actor.token_id
                    if actor is not None and kind != "draft"
                    else true(),
                    Job.actor_kind == actor.actor_kind
                    if actor is not None
                    and kind in {"check", "score", "score_rubric", "bid_review"}
                    else true(),
                )
                .order_by(Job.created_at.desc())
                .limit(1)
            )
    quotes = quotes or []
    blocker = data.get("admission_blocker")
    estimate = estimate_quotes(quotes)
    uncertainty: list[Literal["retry_or_split", "dynamic_search", "unresolved_usage"]] = (
        ["dynamic_search" if dynamic else "retry_or_split"]
        if dynamic or planned_calls is None
        else []
    )
    if planned_calls != 0 and not quotes and data.get("input_blocker") != "not_parsed":
        blocker = blocker or "provider_unavailable"
    maximum_calls = (
        max(
            settings.job_max_vendor_calls,
            math.ceil((planned_calls or 0) * settings.job_vendor_calls_per_batch),
        )
        if settings is not None and planned_calls is not None
        else None
    )
    if cached_job is not None and cached_job.status != "succeeded" and maximum_calls is not None:
        calls = (
            await session.scalars(
                select(VendorCall).where(
                    VendorCall.job_id == cached_job.id, VendorCall.state != "not_sent"
                )
            )
        ).all()
        if quotes and len(calls) >= maximum_calls:
            blocker = blocker or "job_call_limit_exceeded"
    cached_cost = None
    if cached_job is not None and cached_job.status == "succeeded":
        from app.jobs.execution import job_cost

        cached_cost = Cost.model_validate(await job_cost(session, cached_job.id))
        estimate, blocker, planned_calls, uncertainty = Cost(), None, 0, []
    preflight = PreflightData(
        command=command,
        task_id=task_id,
        input_hash=input_hash,
        as_of=datetime.now(UTC),
        planned_calls=planned_calls,
        maximum_calls=maximum_calls,
        estimate=estimate,
        next_call=quotes[0] if quotes and cached_cost is None else None,
        admission_blocker=blocker,
        cached_job_id=cached_job.id if cached_job is not None and cached_cost is not None else None,
        cached_result_cost=cached_cost,
        uncertainty=uncertainty,
    )
    return {**data, "preflight": preflight.model_dump(mode="json")}
