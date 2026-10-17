"""Draw a chapter's figures: model pages rendered locally to PNG, then one draft revision."""

from dataclasses import replace
from datetime import UTC, datetime

from app.core.errors import ServiceError
from app.jobs.execution import JobExecution, authorized_job, job_cost
from app.providers.calls import plan_calls
from app.providers.converter import WebKitConverter
from app.providers.solution_figures import MAX_CALLS, figures_provider
from app.services import solution_figures
from app.services.score_generation import usage_integrity


async def fixed(execution, session):
    job = await execution.owned_job(session)
    actor = replace(
        await authorized_job(session, job, bind_context=False),
        actor_kind="worker",
        job_id=job.id,
        run_id=execution.run_id,
    )
    provider, request, chapter = await solution_figures.current_snapshot(
        session, actor, job, execution.settings
    )
    return job, actor, provider, request, chapter


async def process(execution: JobExecution, storage) -> None:
    settings = execution.settings
    async with execution.db.transaction() as session:
        _, _, provider, request, _ = await fixed(execution, session)
    plan_calls(MAX_CALLS)

    async def before_admit(session):
        await fixed(execution, session)

    execution.before_admit = before_admit
    output = await figures_provider(provider).figures(request)
    # Submission refused a missing renderer; a later removal fails here as unavailable.
    if not settings.preview_renderer:
        raise ServiceError(
            "figure_renderer_unavailable", "Drawing figures needs the desktop renderer", 409, 4
        )
    renderer = WebKitConverter(settings.preview_renderer, settings.converter_timeout_seconds)
    figures = {}
    for page in output.pages:
        png = await renderer.html_to_png(page.html)
        figure = solution_figures.image_figure(png, page.caption, "generated")
        figures[page.subsection] = (figure, png)
    async with execution.db.transaction() as session:
        job, actor, _, _, chapter = await fixed(execution, session)
        await usage_integrity(
            session, job, execution.run_id, output.usages, kind="solution_figures"
        )
        for figure, png in figures.values():
            await storage.put(solution_figures.storage_key(chapter.task_id, figure), png)
        published = await solution_figures.publish(
            session,
            actor,
            chapter,
            {number: figure for number, (figure, _) in figures.items()},
            job_id=job.id,
        )
        job.status, job.error, job.finished_at = "succeeded", None, datetime.now(UTC)
        job.result = {
            "submission": job.result["submission"],
            "task_id": str(job.task_id),
            "document_id": str(job.document_id),
            "chapter_id": str(chapter.id),
            "revision": published["current"]["revision"],
            "figure_count": len(figures),
            "cost": await job_cost(session, job.id),
            "warnings": ([] if figures or output.failed else ["no_figures"])
            + [f"figure_failed:{number}" for number in output.failed],
        }
