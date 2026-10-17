"""Publish independent unconfirmed chapters with attempt and revision fences."""

from dataclasses import replace
from datetime import UTC, datetime

from app.core.errors import ServiceError
from app.jobs.execution import JobExecution, authorized_job, job_cost
from app.providers.calls import plan_calls
from app.providers.solution import MAX_CALLS_PER_ITEM, solution_provider
from app.services import solution_generation
from app.services.score_generation import usage_integrity


async def fixed(execution, session):
    job = await execution.owned_job(session)
    actor = replace(
        await authorized_job(session, job, bind_context=False),
        actor_kind="worker",
        job_id=job.id,
        run_id=execution.run_id,
    )
    provider, requests, targets = await solution_generation.current_snapshot(
        session, actor, job, execution.settings
    )
    return job, actor, provider, requests, targets


async def process(execution: JobExecution) -> None:
    async with execution.db.transaction() as session:
        job, _, provider, requests, targets = await fixed(execution, session)
        titles = job.result["submission"]["input_manifest"]["titles"]
    plan_calls(len(requests) * MAX_CALLS_PER_ITEM)

    async def before_admit(session):
        await fixed(execution, session)

    execution.before_admit = before_admit
    adapter = solution_provider(provider)
    reported_usages = []
    for index, request in enumerate(requests):
        title = titles[index]
        async with execution.db.transaction() as session:
            job, _, _, _, targets = await fixed(execution, session)
            if targets[title]["job_id"] == str(job.id):
                continue
        output = await adapter.draft(request)
        if len(output.usages) < 1 + len(output.sections):
            raise ServiceError(
                "solution_draft_usage_integrity",
                "Each solution call requires one accounting receipt",
                500,
                4,
            )
        reported_usages.extend(output.usages)
        async with execution.db.transaction() as session:
            job, actor, _, _, _ = await fixed(execution, session)
            await usage_integrity(
                session, job, execution.run_id, reported_usages, kind="solution_draft"
            )
            from app.services.solution import publish_model

            manifest = job.result["submission"]["input_manifest"]
            await publish_model(
                session,
                actor,
                job.task_id,
                job.document_id,
                title,
                manifest["score_ranges"][index],
                [section.model_dump(mode="json") for section in output.sections],
                job_id=job.id,
                expected_revision=job.result["submission"]["target_revisions"][title],
            )
            await execution.owned_job(session)
    async with execution.db.transaction() as session:
        job, _, _, _, _ = await fixed(execution, session)
        job.status, job.error, job.finished_at = "succeeded", None, datetime.now(UTC)
        job.result = {
            "submission": job.result["submission"],
            "task_id": str(job.task_id),
            "document_id": str(job.document_id),
            "titles": titles,
            "chapter_count": len(titles),
            "cost": await job_cost(session, job.id),
            "warnings": ["needs_context_truncated"]
            if job.result["submission"]["input_manifest"]["needs_truncated"]
            else [],
        }
