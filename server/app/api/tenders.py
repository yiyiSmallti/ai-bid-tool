"""Tasks, tender documents, parse/extract jobs and the extracted requirements."""

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from sqlalchemy import select

from app.api.common import check_signature, result, serial, signed_link
from app.core.config import Settings
from app.core.db import Database
from app.core.security import TokenSigner
from app.models.entities import Chunk
from app.providers.storage import Storage
from app.schemas.citation_repair_contracts import CitationRepairRequest
from app.schemas.contracts import JobAction, Result, TaskCreate
from app.services import citation_repair, documents, requirements, tender_jobs

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
    async def task_create(body: TaskCreate, ctx=Depends(context, scope="function")):
        session, identity = ctx
        task = await documents.create_task(session, identity, body)
        return result("task create", serial(task, documents.TASK_FIELDS))

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
        )
        return result(
            "tender upload",
            {**serial(document, ("id", "name", "sha256", "task_id")), "duplicate": duplicate},
        )

    @router.get("/documents/{document_id}", name="document_get", response_model=Result)
    async def document_get(document_id: UUID, ctx=Depends(context, scope="function")):
        session, identity = ctx
        identity.require("task:read")
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
        identity.require("task:read")
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
        identity.require("task:read")
        document = await documents.require_document(session, document_id)
        check_signature(crypto, signature, "download", identity.org_id, document_id=document_id)
        return Response(
            await storage.read(identity.org_id, document.storage_key),
            media_type=document.media_type,
        )

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
        identity.require("task:read")
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
