"""Archive a genuine PDF page from a fixed task revision without confirming it."""

import asyncio
import base64
import hashlib
import struct
import threading
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pymupdf
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import PDFSettings
from app.core.errors import ServiceError, not_found
from app.core.pdf_process import run_pdf_operation, run_pdf_operation_async
from app.core.pdf_raster import MAX_EDGE_PX, MAX_PIXELS
from app.models.entities import CertificateFile, EvidenceSource, Task, TaskCertificate
from app.providers.storage import Storage
from app.schemas.certificate_file_contracts import CertificateScanFile
from app.schemas.evidence_source_contracts import (
    EvidenceSourceArchive,
    EvidenceSourceCreate,
    EvidenceSourcePreview,
)
from app.services.auth import Identity
from app.services.versioned import audit

RENDER_PROFILE = "pdf-page-preview-v1"
RENDER_SLOTS = threading.BoundedSemaphore(2)
MAX_PREVIEW_BYTES = 40 * 1024 * 1024
WARNINGS = [
    "Source is an unconfirmed user-supplied PDF page; authenticity and eligibility are not verified; never eligible for draft/export"
]
HISTORY_WARNING = "Source belongs to a replaced task selection; no current source is inherited"


def require_access(actor: Identity, scope: str):
    for needed in (scope, "task:read", "certificate:read", "certificate:file:read"):
        actor.require(needed)


def check_png(content: bytes, descriptor: EvidenceSourcePreview):
    try:
        if (
            len(content) != descriptor.size_bytes
            or hashlib.sha256(content).hexdigest() != descriptor.sha256
            or content[:8] != b"\x89PNG\r\n\x1a\n"
            or content[12:16] != b"IHDR"
            or len(content) < 24
        ):
            raise ValueError("invalid PNG")
        width, height = struct.unpack(">II", content[16:24])
        if (
            width != descriptor.width_px
            or height != descriptor.height_px
            or width * height > MAX_PIXELS
            or max(width, height) > MAX_EDGE_PX
        ):
            raise ValueError("invalid PNG dimensions")
        pixmap = pymupdf.Pixmap(content)
        if pixmap.width != width or pixmap.height != height:
            raise ValueError("invalid image")
    except Exception as exc:
        raise ServiceError(
            "source_preview_integrity", "Preview failed integrity checks", 502, 4
        ) from exc


def _render_arguments(
    original: CertificateScanFile,
    page_number: int,
    name: str,
    max_bytes: int,
) -> dict:
    return {
        "original": original.model_dump(mode="json"),
        "page_number": page_number,
        "name": name,
        "max_bytes": max_bytes,
        "max_preview_bytes": MAX_PREVIEW_BYTES,
    }


def _render_result(records: list[dict]) -> tuple[bytes, EvidenceSourcePreview, datetime]:
    try:
        result = records[0]
        png = base64.b64decode(result["image"], validate=True)
        descriptor = EvidenceSourcePreview.model_validate(result["preview"])
        rendered_at = datetime.fromisoformat(result["rendered_at"])
        if rendered_at.tzinfo is None or rendered_at.utcoffset() is None:
            raise ValueError("naive timestamp")
        check_png(png, descriptor)
        return png, descriptor, rendered_at.astimezone(UTC)
    except ServiceError as exc:
        raise ServiceError(
            "pdf_resource_limits", "PDF renderer returned invalid output", 502, 4
        ) from exc
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise ServiceError(
            "pdf_resource_limits", "PDF renderer returned invalid output", 502, 4
        ) from exc


def render_page(
    content: bytes,
    original: CertificateScanFile,
    page_number: int,
    name: str,
    max_bytes: int,
    settings: PDFSettings | None = None,
) -> tuple[bytes, EvidenceSourcePreview, datetime]:
    records = run_pdf_operation(
        content,
        "evidence",
        _render_arguments(original, page_number, name, max_bytes),
        settings=settings,
    )
    return _render_result(records)


async def render_page_async(
    content: bytes,
    original: CertificateScanFile,
    page_number: int,
    name: str,
    max_bytes: int,
    settings: PDFSettings | None = None,
) -> tuple[bytes, EvidenceSourcePreview, datetime]:
    records = await run_pdf_operation_async(
        content,
        "evidence",
        _render_arguments(original, page_number, name, max_bytes),
        settings=settings,
    )
    return _render_result(records)


def source_data(row: EvidenceSource, snapshot: TaskCertificate, original: CertificateFile) -> dict:
    return EvidenceSourceArchive.model_validate(
        {
            "id": row.id,
            "org_id": row.org_id,
            "task_id": row.task_id,
            "task_certificate_id": row.task_certificate_id,
            "certificate_id": row.certificate_id,
            "certificate_revision_id": row.certificate_revision_id,
            "certificate_file_id": row.certificate_file_id,
            "original": CertificateScanFile.model_validate(original.file),
            "page": row.page,
            "preview": EvidenceSourcePreview.model_validate(row.preview),
            "rendered_at": row.rendered_at.astimezone(UTC),
            "created_by": row.created_by,
            "active_selection": snapshot.active,
            "render_profile": row.render_profile,
            "dpi": row.dpi,
            "status": row.status,
            "confirmed_by": row.confirmed_by,
            "eligible_for_draft_export": row.eligible_for_draft_export,
        }
    ).model_dump(mode="json")


def joined_sources():
    return (
        select(EvidenceSource, TaskCertificate, CertificateFile)
        .join(
            TaskCertificate,
            and_(
                TaskCertificate.org_id == EvidenceSource.org_id,
                TaskCertificate.id == EvidenceSource.task_certificate_id,
            ),
        )
        .join(
            CertificateFile,
            and_(
                CertificateFile.org_id == EvidenceSource.org_id,
                CertificateFile.id == EvidenceSource.certificate_file_id,
            ),
        )
    )


async def source_inputs(
    session: AsyncSession, task_id: UUID, body: EvidenceSourceCreate
) -> tuple[TaskCertificate, CertificateFile, EvidenceSource | None]:
    snapshot = await session.scalar(
        select(TaskCertificate)
        .where(TaskCertificate.id == body.task_certificate_id, TaskCertificate.task_id == task_id)
        # The re-check after locking must see committed changes, not the identity map.
        .execution_options(populate_existing=True)
    )
    if snapshot is None:
        raise not_found()
    if not snapshot.active:
        raise ServiceError(
            "inactive_snapshot", "Select an active task revision before archiving", 409, 4
        )
    original = await session.scalar(
        select(CertificateFile).where(
            CertificateFile.certificate_revision_id == snapshot.certificate_revision_id
        )
    )
    if original is None:
        raise ServiceError(
            "missing_source_original", "Selected task revision has no original PDF", 400, 2
        )
    existing = await session.scalar(
        select(EvidenceSource).where(
            EvidenceSource.task_certificate_id == snapshot.id,
            EvidenceSource.page == body.page,
            EvidenceSource.render_profile == RENDER_PROFILE,
        )
    )
    return snapshot, original, existing


async def bounded_render_async(*args) -> tuple[bytes, EvidenceSourcePreview, datetime]:
    # The slot covers child launch, wait and reap, so stalled renders cannot
    # accumulate past this service-level concurrency limit.
    if not RENDER_SLOTS.acquire(blocking=False):
        raise ServiceError("source_render_busy", "Page rendering is busy; try again", 503, 3)
    try:
        return await render_page_async(*args)
    finally:
        RENDER_SLOTS.release()


async def create_source(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    body: EvidenceSourceCreate,
    storage: Storage,
    max_bytes: int,
    settings: PDFSettings | None = None,
) -> dict:
    require_access(actor, "evidence:source:write")
    if await session.get(Task, task_id) is None:
        raise not_found()
    snapshot, original, existing = await source_inputs(session, task_id, body)
    if existing is not None:
        return {"source": source_data(existing, snapshot, original), "duplicate": True}
    descriptor = CertificateScanFile.model_validate(original.file)
    if body.page > descriptor.page_count:
        raise ServiceError("invalid_source_page", "Page is outside original PDF", 400, 2)
    # The original revision is immutable, so reading and rendering it needs no task lock.
    content = await storage.read(actor.org_id, original.storage_key)
    identifier = uuid4()
    name = f"source-{identifier}-page-{body.page}.png"
    png, preview, rendered_at = await bounded_render_async(
        content, descriptor, body.page, name, max_bytes, settings
    )
    # Certificate selection locks the task first; after taking the same lock, re-read
    # what selection or a concurrent archive may have changed during rendering.
    if await session.scalar(select(Task).where(Task.id == task_id).with_for_update()) is None:
        raise not_found()
    snapshot, original, existing = await source_inputs(session, task_id, body)
    if existing is not None:
        return {"source": source_data(existing, snapshot, original), "duplicate": True}
    key = f"org/{actor.org_id}/evidence-source/{identifier}/{preview.sha256}.png"
    await storage.put(actor.org_id, key, png)
    row = EvidenceSource(
        id=identifier,
        org_id=actor.org_id,
        task_id=task_id,
        task_certificate_id=snapshot.id,
        certificate_id=snapshot.certificate_id,
        certificate_revision_id=snapshot.certificate_revision_id,
        certificate_file_id=original.id,
        created_by=actor.user_id,
        page=body.page,
        preview=preview.model_dump(mode="json"),
        storage_key=key,
        rendered_at=rendered_at,
    )
    session.add(row)
    await session.flush()
    audit(
        session,
        actor,
        "evidence.source.create",
        row.id,
        {
            "task_id": str(task_id),
            "task_certificate_id": str(snapshot.id),
            "certificate_revision_id": str(snapshot.certificate_revision_id),
            "render_profile": RENDER_PROFILE,
        },
    )
    return {"source": source_data(row, snapshot, original), "duplicate": False}


async def list_sources(
    session: AsyncSession, actor: Identity, task_id: UUID, *, history: bool = False
):
    require_access(actor, "evidence:source:read")
    if await session.get(Task, task_id) is None:
        raise not_found()
    rows = (
        await session.execute(
            joined_sources()
            .where(EvidenceSource.task_id == task_id)
            .order_by(EvidenceSource.created_at, EvidenceSource.page, EvidenceSource.id)
        )
    ).all()
    active = [str(row.id) for row, snapshot, _ in rows if snapshot.active]
    warnings = (
        [*WARNINGS, HISTORY_WARNING]
        if history and any(not snapshot.active for _, snapshot, _ in rows)
        else WARNINGS
    )
    return (
        {"history": history, "active_source_ids": active},
        [
            source_data(row, snapshot, original)
            for row, snapshot, original in rows
            if history or snapshot.active
        ],
        warnings,
    )


async def require_source(session: AsyncSession, actor: Identity, source_id: UUID):
    require_access(actor, "evidence:source:read")
    row = (await session.execute(joined_sources().where(EvidenceSource.id == source_id))).first()
    if row is None:
        raise not_found()
    return row


async def read_preview(session: AsyncSession, actor: Identity, source_id: UUID, storage: Storage):
    row, snapshot, original = await require_source(session, actor, source_id)
    descriptor = EvidenceSourcePreview.model_validate(row.preview)
    content = await storage.read(actor.org_id, row.storage_key)
    await asyncio.to_thread(check_png, content, descriptor)
    return content, descriptor
