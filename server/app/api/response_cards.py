"""Response review routes share the authenticated tenant transaction dependency."""

from uuid import UUID

from fastapi import APIRouter, Depends
from procrastinate.exceptions import ConnectorException
from psycopg import OperationalError

from app.core.errors import ServiceError
from app.models.entities import Job
from app.schemas.contracts import Result
from app.schemas.response_card_contracts import (
    CardAction,
    CardClassify,
    CardCreate,
    CardGenerateRequest,
    CardUpdate,
    DispositionBatch,
    DraftRequest,
    TaskRedactionSet,
)
from app.services import card_generation, drafts
from app.services import response_cards as cards


def create_router(context, db, storage, queue, settings, llm, resolve):
    router = APIRouter()

    def result(command, data=None, items=None, warnings=None, *, partial=False):
        return Result(
            ok=not partial,
            command=command,
            data=data or {},
            items=items or [],
            warnings=warnings or [],
        )

    @router.get("/tasks/{task_id}/cards", name="card_list", response_model=Result)
    async def card_list(task_id: UUID, job: UUID, ctx=Depends(context, scope="function")):
        data, items = await cards.list_cards(ctx[0], ctx[1], task_id, job)
        return result("card list", data, items, await cards.scope_warnings(ctx[0], job))

    @router.get("/cards/{card_id}", name="card_show", response_model=Result)
    async def card_show(
        card_id: UUID, history: bool = False, ctx=Depends(context, scope="function")
    ):
        data = await cards.show_card(ctx[0], ctx[1], card_id, history=history)
        current = data["card"] if history else data
        return result(
            "card show",
            data,
            warnings=await cards.scope_warnings(ctx[0], UUID(current["extraction_job_id"])),
        )

    @router.post("/tasks/{task_id}/cards", name="card_create", response_model=Result)
    async def card_create(task_id: UUID, body: CardCreate, ctx=Depends(context, scope="function")):
        return result(
            "card create", await cards.create_card(ctx[0], ctx[1], task_id, body, storage)
        )

    async def dispatch_feedback(session, actor):
        from app.memory.candidates import dispatch
        from app.memory.feedback import take_pending_jobs

        jobs = take_pending_jobs(session)
        if not jobs:
            return []
        await session.commit()
        warnings = []
        for job in jobs:
            warnings.extend(await dispatch(db, queue, actor.org_id, job))
        return warnings

    @router.put("/cards/{card_id}", name="card_update", response_model=Result)
    async def card_update(card_id: UUID, body: CardUpdate, ctx=Depends(context, scope="function")):
        ctx[0].info["memory_settings"] = settings
        view = await cards.update_card(ctx[0], ctx[1], card_id, body, storage)
        warnings = await dispatch_feedback(ctx[0], ctx[1])
        return result("card update", view, warnings=warnings)

    @router.post("/cards/{card_id}/classification", name="card_classify", response_model=Result)
    async def card_classify(
        card_id: UUID, body: CardClassify, ctx=Depends(context, scope="function")
    ):
        return result("card classify", await cards.classify_card(ctx[0], ctx[1], card_id, body))

    @router.post(
        "/tasks/{task_id}/cards/dispositions", name="card_disposition", response_model=Result
    )
    async def card_disposition(
        task_id: UUID, body: DispositionBatch, ctx=Depends(context, scope="function")
    ):
        return result("card disposition", await cards.dispose_cards(ctx[0], ctx[1], task_id, body))

    @router.post("/cards/{card_id}/actions", name="card_action", response_model=Result)
    async def card_action(card_id: UUID, body: CardAction, ctx=Depends(context, scope="function")):
        ctx[0].info["memory_settings"] = settings
        view = await cards.card_action(ctx[0], ctx[1], card_id, body, storage, settings)
        warnings = list(view["warning_codes"])
        if body.action == "submit":
            if not all(view["content"][key] for key in cards.CONTENT_FIELDS):
                warnings.append("incomplete_response")
            if view["content"]["response_kind"] == "evidence" and not view["evidence"]:
                warnings.append("missing_evidence")
        warnings.extend(await dispatch_feedback(ctx[0], ctx[1]))
        return result(f"card {body.action.replace('_', '-')}", view, warnings=warnings)

    @router.put(
        "/tasks/{task_id}/model-redaction", name="task_redaction_set", response_model=Result
    )
    async def task_redaction_set(
        task_id: UUID, body: TaskRedactionSet, ctx=Depends(context, scope="function")
    ):
        return result(
            "task redaction set", await cards.set_redaction(ctx[0], ctx[1], task_id, body)
        )

    @router.post("/tasks/{task_id}/drafts", name="draft", response_model=Result)
    async def draft(task_id: UUID, body: DraftRequest, ctx=Depends(context, scope="function")):
        session, actor = ctx
        data, job = await drafts.submit_draft(session, actor, task_id, body, storage)
        warnings = await cards.scope_warnings(session, body.extraction_job_id)
        if job is not None and job.status == "queued" and job.queue_id is None:
            await session.commit()
            try:
                queue_id = await queue.enqueue(str(actor.org_id), str(job.id))
            except (OSError, ConnectorException, OperationalError) as exc:
                # Dispatch is a boundary: the durable job remains available for explicit retry.
                raise ServiceError(
                    "queue_unavailable", "Job saved; repeat request to schedule it", 503, 3
                ) from exc
            async with db.transaction(actor.org_id) as update:
                saved = await update.get(Job, job.id)
                if saved is not None:
                    saved.queue_id = queue_id
        return result("draft", data, warnings=warnings)

    @router.post("/tasks/{task_id}/cards/generations", name="card_generate", response_model=Result)
    async def card_generate(
        task_id: UUID, body: CardGenerateRequest, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        provider = await resolve(session) if resolve else llm
        data, job, warnings = await card_generation.submit_generation(
            session, actor, task_id, body, storage, provider, settings
        )
        if job is not None and job.status == "queued" and job.queue_id is None:
            await session.commit()
            try:
                queue_id = await queue.enqueue(str(actor.org_id), str(job.id))
            except (OSError, ConnectorException, OperationalError) as exc:
                raise ServiceError(
                    "queue_unavailable", "Job saved; repeat request to schedule it", 503, 3
                ) from exc
            async with db.transaction(actor.org_id) as update:
                saved = await update.get(Job, job.id)
                if saved is not None:
                    saved.queue_id = queue_id
        return result("card generate", data, warnings=warnings)

    @router.get("/drafts/{draft_id}", name="draft_show", response_model=Result)
    async def draft_show(draft_id: UUID, ctx=Depends(context, scope="function")):
        view = await drafts.show_draft(ctx[0], ctx[1], draft_id, storage)
        warnings = [
            f"negative_deviation:{row['requirement_id']}"
            for rows in view["tables"].values()
            for row in rows
            if row["deviation"] == "negative"
        ]
        warnings.extend(await cards.scope_warnings(ctx[0], UUID(view["extraction_job_id"])))
        if view["validity"] == "stale":
            warnings.append("stale_draft")
        return result(
            "draft show", view, warnings=warnings, partial=view["completion"] == "partial"
        )

    @router.get("/tasks/{task_id}/drafts", name="draft_list", response_model=Result)
    async def draft_list(task_id: UUID, job: UUID, ctx=Depends(context, scope="function")):
        data, items = await drafts.list_drafts(ctx[0], ctx[1], task_id, job, storage)
        return result("draft list", data, items, await cards.scope_warnings(ctx[0], job))

    return router
