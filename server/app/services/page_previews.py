"""Page-image previews of tender originals, certificate originals and released exports.

Every preview is rendered from bytes whose stored hash was checked first, and only as a
bounded PNG. Exports are DOCX, so a worker converts the released file to PDF once per
file hash; the PDF is stored encrypted like the export and rendered page by page.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import pymupdf
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ServiceError, not_found
from app.models.entities import Document, Job
from app.models.exports import Export, ExportRun
from app.providers.base import ProviderFailure
from app.providers.storage import Storage
from app.schemas.contracts import Cost
from app.services import certificate_files, documents, exports
from app.services.auth import Identity
from app.services.versioned import audit

if TYPE_CHECKING:
    from app.jobs.execution import JobExecution

# Pixel and byte bounds for one rendered page, as for archived certificate pages.
ZOOM_DPI = {1: 110, 2: 200}
MAX_EDGE_PX = 8192
MAX_PIXELS = 20_000_000
MAX_PNG_BYTES = 40 * 1024 * 1024
JOB_KIND = "export_preview"


def render_pdf_page(content: bytes, page_number: int, zoom: int) -> bytes:
    dpi = ZOOM_DPI.get(zoom)
    if dpi is None:
        raise ServiceError("invalid_input", "Zoom must be 1 or 2", 422, 2)
    try:
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            if page_number < 1 or page_number > pdf.page_count:
                raise ServiceError("invalid_page", "Page is outside the document", 400, 2)
            page = pdf[page_number - 1]
            projected = (
                math.ceil(page.rect.width * dpi / 72),
                math.ceil(page.rect.height * dpi / 72),
            )
            if (
                min(projected) < 1
                or max(projected) > MAX_EDGE_PX
                or projected[0] * projected[1] > MAX_PIXELS
            ):
                raise ServiceError("preview_limits", "Page exceeds preview pixel limits", 400, 2)
            png = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False).tobytes("png")
    except ServiceError:
        raise
    except Exception as exc:
        raise ServiceError("preview_render_failed", "Cannot render the page", 422, 2) from exc
    if len(png) > MAX_PNG_BYTES:
        raise ServiceError("preview_limits", "Preview exceeds file limit", 413, 2)
    return png


def pdf_page_count(content: bytes, max_pages: int) -> int:
    """Open converter output as untrusted input and bound it before it is stored."""
    try:
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            if not pdf.is_pdf or pdf.needs_pass or pdf.is_encrypted:
                raise ValueError("not a plain PDF")
            count = pdf.page_count
    except Exception as exc:
        raise ProviderFailure(
            "Converter returned an unreadable PDF", code="converter_invalid_output"
        ) from exc
    if count < 1 or count > max_pages:
        raise ProviderFailure("Converted PDF has too many pages", code="preview_too_large")
    return count


async def document_page(
    session: AsyncSession,
    actor: Identity,
    document_id: UUID,
    page: int,
    zoom: int,
    storage: Storage,
) -> bytes:
    actor.require("task:read")
    document: Document = await documents.require_document(session, document_id)
    if document.media_type != "application/pdf" or document.citation_mode == "block":
        raise ServiceError("preview_not_pdf", "Only PDF originals have page previews", 400, 2)
    content = await storage.read(actor.org_id, document.storage_key)
    if hashlib.sha256(content).hexdigest() != document.sha256:
        raise ServiceError("content_mismatch", "Stored document hash does not match", 409, 4)
    return await asyncio.to_thread(render_pdf_page, content, page, zoom)


async def certificate_page(
    session: AsyncSession,
    actor: Identity,
    revision_id: UUID,
    page: int,
    zoom: int,
    storage: Storage,
) -> bytes:
    content, descriptor = await certificate_files.read_revision(
        session, actor, revision_id, storage
    )
    if page > descriptor.page_count:
        raise ServiceError("invalid_page", "Page is outside the document", 400, 2)
    return await asyncio.to_thread(render_pdf_page, content, page, zoom)


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
    session: AsyncSession, actor: Identity, export_id: UUID, page: int, zoom: int, storage: Storage
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
    return await asyncio.to_thread(render_pdf_page, content, page, zoom)


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
    page_count = await asyncio.to_thread(pdf_page_count, pdf, settings.preview_max_pages)
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
