"""Page-image previews of tender originals, certificate originals and released exports.

Every preview is rendered from bytes whose stored hash was checked first, and only as a
bounded PNG. Exports are DOCX, so a worker converts the released file to PDF once per
file hash; the PDF is stored encrypted like the export and rendered page by page.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import PDFSettings, Settings
from app.core.errors import ServiceError, not_found
from app.core.pdf_process import run_pdf_operation, run_pdf_operation_async
from app.models.entities import Document, Job
from app.models.exports import Export, ExportRun
from app.providers.base import ProviderFailure
from app.providers.storage import Storage
from app.schemas.contracts import Cost
from app.services import certificate_files, documents, exports
from app.services.auth import Identity
from app.services.task_authorization import task_authorized
from app.services.versioned import audit

if TYPE_CHECKING:
    from app.jobs.execution import JobExecution

# Byte bound for one rendered page, as for archived certificate pages.
MAX_PNG_BYTES = 40 * 1024 * 1024
JOB_KIND = "export_preview"


def _preview_arguments(page_number: int, zoom: int) -> dict:
    return {"page_number": page_number, "zoom": zoom, "max_png_bytes": MAX_PNG_BYTES}


def _preview_result(records: list[dict]) -> bytes:
    try:
        png = base64.b64decode(records[0]["image"], validate=True)
        if len(png) > MAX_PNG_BYTES or not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("invalid PNG")
        return png
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise ServiceError(
            "pdf_resource_limits", "PDF renderer returned invalid output", 502, 4
        ) from exc


def render_pdf_page(
    content: bytes,
    page_number: int,
    zoom: int,
    settings: PDFSettings | None = None,
) -> bytes:
    records = run_pdf_operation(
        content, "preview", _preview_arguments(page_number, zoom), settings=settings
    )
    return _preview_result(records)


async def render_pdf_page_async(
    content: bytes,
    page_number: int,
    zoom: int,
    settings: PDFSettings | None = None,
) -> bytes:
    records = await run_pdf_operation_async(
        content, "preview", _preview_arguments(page_number, zoom), settings=settings
    )
    return _preview_result(records)


def _provider_failure(exc: ServiceError) -> ProviderFailure | None:
    messages = {
        "converter_invalid_output": "Converter returned an unreadable PDF",
        "preview_too_large": "Converted PDF has too many pages",
    }
    if exc.code in messages:
        return ProviderFailure(messages[exc.code], code=exc.code)
    return None


def _page_count_result(records: list[dict], max_pages: int) -> int:
    try:
        count = records[0]["page_count"]
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= max_pages:
            raise TypeError("invalid page count")
        return count
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise ServiceError(
            "pdf_resource_limits", "PDF renderer returned invalid output", 502, 4
        ) from exc


def pdf_page_count(content: bytes, max_pages: int, settings: PDFSettings | None = None) -> int:
    """Open converter output as untrusted input and bound it before it is stored."""
    try:
        records = run_pdf_operation(
            content, "page_count", {"max_pages": max_pages}, settings=settings
        )
    except ServiceError as exc:
        if failure := _provider_failure(exc):
            raise failure from exc
        raise
    return _page_count_result(records, max_pages)


async def pdf_page_count_async(
    content: bytes, max_pages: int, settings: PDFSettings | None = None
) -> int:
    try:
        records = await run_pdf_operation_async(
            content, "page_count", {"max_pages": max_pages}, settings=settings
        )
    except ServiceError as exc:
        if failure := _provider_failure(exc):
            raise failure from exc
        raise
    return _page_count_result(records, max_pages)


async def document_page(
    session: AsyncSession,
    actor: Identity,
    document_id: UUID,
    page: int,
    zoom: int,
    storage: Storage,
    settings: PDFSettings | None = None,
) -> bytes:
    document: Document = await documents.require_document(session, document_id)
    if document.media_type != "application/pdf" or document.citation_mode == "block":
        raise ServiceError("preview_not_pdf", "Only PDF originals have page previews", 400, 2)
    content = await storage.read(actor.org_id, document.storage_key)
    if hashlib.sha256(content).hexdigest() != document.sha256:
        raise ServiceError("content_mismatch", "Stored document hash does not match", 409, 4)
    return await render_pdf_page_async(content, page, zoom, settings)


async def certificate_page(
    session: AsyncSession,
    actor: Identity,
    revision_id: UUID,
    page: int,
    zoom: int,
    storage: Storage,
    settings: PDFSettings | None = None,
) -> bytes:
    content, descriptor = await certificate_files.read_revision(
        session, actor, revision_id, storage
    )
    if page > descriptor.page_count:
        raise ServiceError("invalid_page", "Page is outside the document", 400, 2)
    return await render_pdf_page_async(content, page, zoom, settings)


def cache_key(row: Export) -> str:
    return exports.digest(
        {"kind": JOB_KIND, "export_id": str(row.id), "file_sha256": row.file_sha256}
    )


def preview_view(row: Export, job: Job | None) -> dict:
    preview = (job.result or {}).get("preview") if job is not None else None
    return {
        "export_id": str(row.id),
        "file_sha256": row.file_sha256,
        "job_id": str(job.id) if job is not None else None,
        "status": job.status if job is not None else "not_requested",
        "page_count": preview["page_count"] if preview else None,
        "error": job.error if job is not None and job.status == "failed" else None,
    }


async def current_job(session: AsyncSession, row: Export, *, lock: bool = False) -> Job | None:
    query = select(Job).where(Job.cache_key == cache_key(row), Job.kind == JOB_KIND)
    return await session.scalar(query.with_for_update() if lock else query)


@task_authorized("export", parent=("export_id", "exports"), write=True)
async def submit_export_preview(
    session: AsyncSession,
    actor: Identity,
    export_id: UUID,
    storage: Storage,
    settings: Settings,
    *,
    retry: bool = False,
) -> tuple[dict, Job]:
    """Open (or reuse) the conversion for this exact export file; the download gate applies."""
    actor, row = await exports.get_export(session, actor, export_id)
    actor, _ = await exports.download_gate(session, actor, row, storage)
    if not settings.converter_url:
        raise ServiceError("preview_unavailable", "Export previews are not configured", 503, 4)
    run = await session.get(ExportRun, row.run_id)
    if run is None:
        raise not_found()
    job = await current_job(session, row, lock=True)
    if job is not None and retry and job.status in {"failed", "cancelled"}:
        job.status, job.error, job.queue_id, job.attempts = "queued", None, None, 0
        job.lease_until, job.finished_at, job.run_id = None, None, None
    if job is None:
        job = Job(
            org_id=actor.org_id,
            task_id=row.task_id,
            document_id=run.document_id,
            kind=JOB_KIND,
            cache_key=cache_key(row),
            status="queued",
            result={
                # The worker cannot read exports (human-only); it gets the immutable file
                # reference here and checks the bytes against the released hash.
                "submission": {
                    "export_id": str(row.id),
                    "file_sha256": row.file_sha256,
                    "object_key": row.object_key,
                    "size_bytes": row.size_bytes,
                    "actor_user_id": str(actor.user_id),
                },
                "cost": Cost().model_dump(mode="json"),
            },
        )
        session.add(job)
        await session.flush()
    audit(
        session,
        actor,
        "export.preview_opened",
        row.id,
        {
            "actor_kind": actor.actor_kind,
            "export_id": str(row.id),
            "file_sha256": row.file_sha256,
            "job_id": str(job.id),
        },
    )
    return preview_view(row, job), job


async def job_access(session: AsyncSession, actor: Identity, job: Job) -> None:
    """A preview job is visible only to people who may open the export itself."""
    # The caller has resolved the job/task boundary; deny the export action before
    # its more restrictive RLS can turn this known job's permission error into 404.
    actor = await exports.human_access(session, actor)
    await exports.get_export(session, actor, UUID(job.result["submission"]["export_id"]))


async def show_export_preview(
    session: AsyncSession, actor: Identity, export_id: UUID, storage: Storage
) -> dict:
    actor, row = await exports.get_export(session, actor, export_id)
    export = await exports.export_view(session, actor, row, storage)
    view = {
        **preview_view(row, await current_job(session, row)),
        **{key: export[key] for key in ("validity", "issues", "invalidated_requirement_ids")},
    }
    if export["validity"] == "stale":
        view.update(status="invalidated", page_count=None)
    return view


async def export_page(
    session: AsyncSession,
    actor: Identity,
    export_id: UUID,
    page: int,
    zoom: int,
    storage: Storage,
    settings: PDFSettings | None = None,
) -> bytes:
    actor, row = await exports.get_export(session, actor, export_id)
    actor, _ = await exports.download_gate(session, actor, row, storage)
    job = await current_job(session, row)
    preview = (job.result or {}).get("preview") if job is not None else None
    if job is None or job.status != "succeeded" or not preview:
        raise ServiceError("preview_not_ready", "The export preview is not ready", 409, 2)
    if page < 1 or page > preview["page_count"]:
        raise ServiceError("invalid_page", "Page is outside the document", 400, 2)
    content = await exports.checked_content(
        actor, preview["object_key"], preview["size_bytes"], preview["sha256"], storage
    )
    return await render_pdf_page_async(content, page, zoom, settings)


async def convert(execution: JobExecution, storage: Storage, converter, settings: Settings) -> None:
    """Worker step: convert the released file once and publish only a bounded, readable PDF."""
    org_id = execution.org_id
    async with execution.db.transaction(org_id) as session:
        submission = (await execution.owned_job(session)).result["submission"]
    key, size, sha256 = (
        submission["object_key"],
        submission["size_bytes"],
        submission["file_sha256"],
    )
    content = await storage.read(org_id, key)
    if len(content) != size or hashlib.sha256(content).hexdigest() != sha256:
        raise ServiceError(
            "export_file_integrity", "Export file failed length or hash verification", 500, 4
        )
    if converter is None:
        raise ProviderFailure("Export previews are not configured", code="preview_unavailable")
    pdf = await converter.docx_to_pdf(content, "export.docx")
    page_count = await pdf_page_count_async(pdf, settings.preview_max_pages, settings)
    object_key = f"org/{org_id}/export-previews/{submission['export_id']}/{execution.run_id}.pdf"
    await storage.put(org_id, object_key, pdf)
    async with execution.db.transaction(org_id) as session:
        job = await execution.owned_job(session)
        job.status, job.error, job.finished_at = "succeeded", None, datetime.now(UTC)
        job.result = {
            **job.result,
            "preview": {
                "object_key": object_key,
                "sha256": hashlib.sha256(pdf).hexdigest(),
                "size_bytes": len(pdf),
                "page_count": page_count,
                "converter": converter.identity,
            },
        }
