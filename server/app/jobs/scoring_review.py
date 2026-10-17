"""Run a scoring review and keep only the items verified against the lines sent."""

from dataclasses import replace
from datetime import UTC, datetime

from app.jobs.execution import JobExecution, authorized_job, job_cost
from app.providers.calls import plan_calls
from app.providers.scoring_review import scoring_review_provider
from app.services import bid_outline, scoring_review
from app.services.score_generation import usage_integrity


async def fixed(execution, session):
    job = await execution.owned_job(session)
    actor = replace(
        await authorized_job(session, job, bind_context=False),
        actor_kind="worker",
        job_id=job.id,
        run_id=execution.run_id,
    )
    provider, text = await scoring_review.current_snapshot(session, actor, job, execution.settings)
    return job, provider, text


async def process(execution: JobExecution) -> None:
    async with execution.db.transaction() as session:
        _, provider, text = await fixed(execution, session)
    plan_calls(1)

    async def before_admit(session):
        await fixed(execution, session)

    execution.before_admit = before_admit
    output, usages = await scoring_review_provider(provider).review(text)
    async with execution.db.transaction() as session:
        job, _, _ = await fixed(execution, session)
        await usage_integrity(session, job, execution.run_id, usages, kind="scoring_review")
        manifest = job.result["submission"]["input_manifest"]
        items, total, rejected = bid_outline.verify_review(scoring_review.shown_lines(text), output)
        job.status, job.error, job.finished_at = "succeeded", None, datetime.now(UTC)
        job.result = {
            "submission": job.result["submission"],
            "task_id": str(job.task_id),
            "document_id": str(job.document_id),
            "chunks_sha256": manifest["chunks_sha256"],
            "review": {
                **manifest["region"],
                "total": total,
                "multiple_packages": output.multiple_packages,
                "items": items,
            },
            "item_count": len(items),
            "rejected_count": rejected,
            "cost": await job_cost(session, job.id),
            "warnings": ["region_truncated"] if manifest["region"]["truncated"] else [],
        }
