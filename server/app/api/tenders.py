"""Tasks, tender documents, parse/extract jobs and the extracted requirements."""

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import select

from app.api.common import check_signature, result, serial, signed_link
from app.core.config import Settings
from app.core.db import Database
from app.core.security import TokenSigner
from app.models.entities import Chunk
from app.providers.storage import Storage
from app.schemas.budget_contracts import BudgetTaskCreate
from app.schemas.citation_repair_contracts import CitationRepairRequest
from app.schemas.contracts import JobAction, Result
from app.services import citation_repair, documents, page_previews, requirements, tender_jobs

CHUNK_FIELDS = ("id", "document_id", "seq", "page", "text", "ocr", "citation_verified", "blocks")


def create_router(
    context: Callable[..., Any],
    settings: Settings,
    db: Database,
    storage: Storage,
    queue,
    crypto: TokenSigner,
    llm,
    resolve: Callable[..., Awaitable[Any]] | None,
    ocr,
) -> APIRouter:
    router = APIRouter()

    @router.post("/tasks", name="task_create", response_model=Result)
    async def task_create(body: BudgetTaskCreate, ctx=Depends(context, scope="function")):
        session, identity = ctx
        task = await documents.create_task(session, identity, body, settings.billing_currency)
        from app.services import budgets

        data = serial(task, documents.TASK_FIELDS)
        data["budget"] = (await budgets.view(session, task, settings.billing_currency)).model_dump(
            mode="json"
        )
        return result("task create", data)

    @router.get("/tasks", name="task_list", response_model=Result)
    async def task_list(ctx=Depends(context, scope="function")):
        session, identity = ctx
        tasks = await documents.list_tasks(session, identity)
        return result("task list", items=[serial(task, documents.TASK_FIELDS) for task in tasks])

    @router.post("/tasks/{task_id}/documents", name="tender_upload", response_model=Result)
    async def tender_upload(
        task_id: UUID, file: UploadFile = File(...), ctx=Depends(context, scope="function")
    ):
        session, identity = ctx
        document, duplicate = await documents.upload(
            session,
            identity,
            task_id,
            file.filename,
            file.read,
            storage,
            max_bytes=settings.max_upload_bytes,
            max_pages=settings.max_pages,
            settings=settings,
        )
        return result(
            "tender upload",
            {**serial(document, ("id", "name", "sha256", "task_id")), "duplicate": duplicate},
        )

    @router.get("/documents/{document_id}", name="document_get", response_model=Result)
    async def document_get(document_id: UUID, ctx=Depends(context, scope="function")):
        session, identity = ctx
        document = await documents.require_document(session, document_id)
        return result(
            "document get",
            serial(document, ("id", "name", "task_id", "status", "page_count", "citation_mode")),
        )

    @router.get(
        "/documents/{document_id}/download-link",
        name="document_download_link",
        response_model=Result,
    )
    async def download_link(document_id: UUID, ctx=Depends(context, scope="function")):
        session, identity = ctx
        await documents.require_document(session, document_id)
        return result(
            "document download link",
            signed_link(
                crypto,
                f"/documents/{document_id}/download",
                "download",
                identity.org_id,
                document_id=document_id,
            ),
        )

    @router.get("/documents/{document_id}/download", name="document_download")
    async def download(document_id: UUID, signature: str, ctx=Depends(context, scope="function")):
        session, identity = ctx
        document = await documents.require_document(session, document_id)
        check_signature(crypto, signature, "download", identity.org_id, document_id=document_id)
        return Response(
            await storage.read(identity.org_id, document.storage_key),
            media_type=document.media_type,
        )

    @router.get("/documents/{document_id}/pages/{page}/preview", name="document_page_preview")
    async def page_preview(
        document_id: UUID,
        page: int,
        zoom: int = Query(1, ge=1, le=2),
        ctx=Depends(context, scope="function"),
    ):
        session, identity = ctx
        png = await page_previews.document_page(
            session, identity, document_id, page, zoom, storage, settings
        )
        return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})

    async def start_job(document_id: UUID, kind: str, body: JobAction, ctx):
        session, identity = ctx
        data, warnings = await tender_jobs.submit(
            session,
            identity,
            document_id,
            kind,
            body,
            settings=settings,
            db=db,
            queue=queue,
            llm=llm,
            resolve=resolve,
            ocr=ocr,
            storage=storage,
        )
        return result("tender parse" if kind == "parse" else "req extract", data, warnings=warnings)

    @router.post("/documents/{document_id}/parse", name="tender_parse", response_model=Result)
    async def tender_parse(
        document_id: UUID, body: JobAction, ctx=Depends(context, scope="function")
    ):
        return await start_job(document_id, "parse", body, ctx)

    @router.post("/documents/{document_id}/extract", name="req_extract", response_model=Result)
    async def req_extract(
        document_id: UUID, body: JobAction, ctx=Depends(context, scope="function")
    ):
        return await start_job(document_id, "extract", body, ctx)

    @router.get("/documents/{document_id}/chunks", name="chunk_list", response_model=Result)
    async def chunk_list(document_id: UUID, ctx=Depends(context, scope="function")):
        session, identity = ctx
        await documents.require_document(session, document_id)
        chunks = (
            await session.scalars(
                select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.seq)
            )
        ).all()
        return result("chunk list", items=[serial(row, CHUNK_FIELDS) for row in chunks])

    @router.get("/tasks/{task_id}/requirements", name="req_list", response_model=Result)
    async def req_list(
        task_id: UUID, job: UUID | None = None, ctx=Depends(context, scope="function")
    ):
        session, identity = ctx
        items = await requirements.list_requirements(session, identity, task_id, job)
        return result("req list", items=items)

    @router.get(
        "/tasks/{task_id}/requirements/repair", name="req_repair_preview", response_model=Result
    )
    async def req_repair_preview(task_id: UUID, job: UUID, ctx=Depends(context, scope="function")):
        data, items = await citation_repair.repair_citations(ctx[0], ctx[1], task_id, job)
        return result("req repair-citations", data=data, items=items)

    @router.post(
        "/tasks/{task_id}/requirements/repair", name="req_repair_execute", response_model=Result
    )
    async def req_repair_execute(
        task_id: UUID, body: CitationRepairRequest, ctx=Depends(context, scope="function")
    ):
        data, items = await citation_repair.repair_citations(
            ctx[0], ctx[1], task_id, body.extraction_job_id, body
        )
        return result("req repair-citations", data=data, items=items)

    @router.get("/tasks/{task_id}/extractions", name="req_history", response_model=Result)
    async def req_history(
        task_id: UUID, document: UUID | None = None, ctx=Depends(context, scope="function")
    ):
        session, identity = ctx
        items = await requirements.extraction_history(session, identity, task_id, document)
        return result("req history", items=items)

    return router
