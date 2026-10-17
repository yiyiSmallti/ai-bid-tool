"""Solution draft jobs and human chapter review."""

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response

from app.api.common import result
from app.core.errors import not_found
from app.jobs.queue import dispatch_process_job
from app.schemas.contracts import Result
from app.schemas.solution import (
    SolutionConfirm,
    SolutionDraftRequest,
    SolutionEdit,
    SolutionFiguresRequest,
)
from app.schemas.whole_bid import ScoringReviewRequest
from app.services import (
    scoring_review,
    solution,
    solution_figures,
    solution_generation,
    task_access,
)


def create_router(context, queue, settings, llm, resolve, storage):
    router = APIRouter()

    @router.post("/tasks/{task_id}/solutions/draft", name="solution_draft", response_model=Result)
    async def draft(
        task_id: UUID, body: SolutionDraftRequest, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        await task_access.access(session, actor, task_id, "solution:draft", write=True)
        provider = await resolve(session, model=body.model) if resolve else llm
        data, job = await solution_generation.submit(
            session, actor, task_id, body, provider, settings
        )
        await dispatch_process_job(session, queue, job)
        return result("solution draft", data)

    @router.post(
        "/documents/{document_id}/outline/review", name="outline_review", response_model=Result
    )
    async def review(
        document_id: UUID, body: ScoringReviewRequest, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        provider = await resolve(session, model=body.model) if resolve else llm
        data, job = await scoring_review.submit(session, actor, document_id, provider, settings)
        await dispatch_process_job(session, queue, job)
        return result("tender outline review", data)

    @router.get("/tasks/{task_id}/solutions", name="solution_list", response_model=Result)
    async def list_chapters(
        task_id: UUID, document: UUID | None = None, ctx=Depends(context, scope="function")
    ):
        data, items = await solution.list_chapters(ctx[0], ctx[1], task_id, document)
        return result("solution list", data, items=items)

    @router.get("/solutions/{chapter_id}", name="solution_show", response_model=Result)
    async def show(chapter_id: UUID, ctx=Depends(context, scope="function")):
        return result("solution show", await solution.show_chapter(ctx[0], ctx[1], chapter_id))

    @router.put("/solutions/{chapter_id}", name="solution_edit", response_model=Result)
    async def edit(chapter_id: UUID, body: SolutionEdit, ctx=Depends(context, scope="function")):
        return result("solution edit", await solution.edit(ctx[0], ctx[1], chapter_id, body))

    @router.post("/solutions/{chapter_id}/confirm", name="solution_confirm", response_model=Result)
    async def confirm(
        chapter_id: UUID, body: SolutionConfirm, ctx=Depends(context, scope="function")
    ):
        return result("solution confirm", await solution.confirm(ctx[0], ctx[1], chapter_id, body))

    @router.post("/solutions/{chapter_id}/figures", name="solution_figures", response_model=Result)
    async def figures(
        chapter_id: UUID, body: SolutionFiguresRequest, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        provider = await resolve(session, model=body.model) if resolve else llm
        data, job = await solution_figures.submit(
            session, actor, chapter_id, body, provider, settings
        )
        await dispatch_process_job(session, queue, job)
        return result("solution figures", data)

    @router.post(
        "/solutions/{chapter_id}/figures/upload",
        name="solution_figure_upload",
        response_model=Result,
    )
    async def figure_upload(
        chapter_id: UUID,
        file: UploadFile = File(...),
        expected_revision: int = Form(..., ge=1),
        subsection: str = Form(..., pattern=r"^\d{1,2}\.\d{1,2}$"),
        caption: str = Form(..., min_length=1, max_length=60),
        ctx=Depends(context, scope="function"),
    ):
        content = await file.read(solution_figures.MAX_IMAGE_BYTES + 1)
        data = await solution_figures.upload(
            ctx[0], ctx[1], chapter_id, expected_revision, subsection, caption, content, storage
        )
        return result("solution figure-upload", data)

    @router.get("/solutions/{chapter_id}/figures/{sha256}", name="solution_figure")
    async def figure(chapter_id: UUID, sha256: str, ctx=Depends(context, scope="function")):
        chapter, revision = await solution.load(ctx[0], ctx[1], chapter_id)
        for found in solution.figure_numbers(revision.sections).values():
            if found["sha256"] == sha256:
                shown = solution_figures.SolutionFigure.model_validate(found)
                content = await storage.read(solution_figures.storage_key(chapter.task_id, shown))
                return Response(
                    content, media_type=shown.media_type, headers={"Cache-Control": "no-store"}
                )
        raise not_found()

    return router
