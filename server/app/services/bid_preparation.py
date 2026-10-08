"""Local, bounded uploaded-bid preparation without transcription or model calls."""

import hashlib
import json
from collections.abc import AsyncIterator
from importlib.metadata import version
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.core.config import Settings
from app.core.errors import ServiceError, not_found
from app.core.pdf_process import PDFOperation
from app.core.pdf_raster import MAX_EDGE_PX, MAX_PIXELS, MIN_OCR_DPI, OCR_DPI
from app.core.security import Secrets
from app.models.bid_review import BidPreparation, BidSubmission
from app.providers.converter import MAX_PDF_BYTES
from app.services import task_workflow
from app.services.auth import HUMAN_ONLY_SCOPES, ROLE_SCOPES, Identity, set_actor_context

PREPARATION_VERSION = "bid-prepare-v2"
RENDER_PROFILE = "bid-pages-v1"
PAGE_LIMIT = 1000
STRUCTURAL_MAPPING_WORK_LIMIT = 32 * 1024 * 1024
PDF_MEDIA_TYPE = "application/pdf"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def preparation_identity(settings: Settings) -> dict:
    """Pin installed parsers and active resource/render limits without importing PDF code."""
    return {
        "preparation_version": PREPARATION_VERSION,
        "render_profile": RENDER_PROFILE,
        "signature_validator": "local-pdf-cms-v1",
        "signing_clause_scan": "signing-clauses-v1",
        "structural_mapping_work_limit": STRUCTURAL_MAPPING_WORK_LIMIT,
        "pdf_parser": "pymupdf:" + version("PyMuPDF"),
        "docx_parser": "python-docx:" + version("python-docx"),
        "office_profile": settings.review_office_profile,
        "office_limits": {
            "timeout_seconds": settings.converter_timeout_seconds,
            "output_bytes": MAX_PDF_BYTES,
        },
        "pdf_limits": {
            field: getattr(settings, field)
            for field in (
                "pdf_timeout_seconds",
                "pdf_cpu_seconds",
                "pdf_memory_bytes",
                "pdf_output_bytes",
            )
        },
        "raster_limits": {
            "max_edge_px": MAX_EDGE_PX,
            "max_pixels": MAX_PIXELS,
            "minimum_dpi": MIN_OCR_DPI,
            "target_dpi": OCR_DPI,
        },
    }


def temporary_root(settings: Settings) -> Path:
    # The configured writable data volume also works for installed packages.
    root = settings.data_dir / "work/bid-review-upload/pdf"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def seal(settings: Settings, org_id, identifier, value) -> str:
    """Bind restricted text/maps to an immutable row, as attachment envelopes do."""
    return Secrets.for_data(settings).encrypt(
        json.dumps(
            {"org_id": str(org_id), "id": str(identifier), "value": value}, ensure_ascii=True
        )
    )


async def isolated(content: bytes, operation: str, settings: Settings) -> dict:
    with PDFOperation(
        content,
        operation,
        {"max_pages": PAGE_LIMIT},
        settings,
        temporary_root=temporary_root(settings),
    ) as child:
        await child.wait_async()
        return next(child.records())


async def validate_upload(content: bytes, media_type: str, settings: Settings) -> None:
    """Validate exact bytes in a disposable child before any persistent upload write."""
    if media_type == PDF_MEDIA_TYPE:
        if not content.startswith(b"%PDF-"):
            raise ServiceError("bid_file_type", "PDF content does not match its type", 400, 2)
        await isolated(content, "bid_validate", settings)
    elif media_type == DOCX_MEDIA_TYPE:
        if not content.startswith(b"PK\x03\x04"):
            raise ServiceError("bid_file_type", "DOCX content does not match its type", 400, 2)
        await isolated(content, "bid_docx_validate", settings)
    else:
        raise ServiceError("bid_file_type", "Only PDF and DOCX files are supported", 400, 2)


async def native_docx_structure(content: bytes, settings: Settings) -> dict:
    return await isolated(content, "bid_docx_structure", settings)


async def pdf_pages(content: bytes, max_pages: int, settings: Settings) -> AsyncIterator[dict]:
    """Consume one page at a time only after the whole isolated operation succeeds."""
    with PDFOperation(
        content,
        "bid_prepare",
        {"max_pages": max_pages},
        settings,
        temporary_root=temporary_root(settings),
    ) as child:
        await child.wait_async()
        for record in child.records():
            yield record


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def worker_access(session, job, *, bind_context=True) -> Identity:
    """A worker checks the human initiator's ceiling without acquiring human scopes."""
    if (
        job.kind != "bid_review_prepare"
        or job.actor_kind != "session"
        or job.actor_token_id is not None
        or job.agent_principal_id is not None
        or job.actor_user_id is None
        or job.task_id is None
        or "bid-review:prepare" not in job.actor_scopes
    ):
        raise ServiceError("forbidden", "Preparation requires a fixed human request", 403, 4)
    actor = Identity(
        job.actor_user_id,
        job.org_id,
        set(job.actor_scopes) - HUMAN_ONLY_SCOPES,
        "viewer",
        actor_kind="worker",
        job_id=job.id,
        run_id=job.run_id,
    )
    _, _, member = await task_workflow.access(
        session,
        actor,
        job.task_id,
        scope="task:read",
        write=True,
        require_member=True,
        bind_context=False,
    )
    if (
        actor.role not in {"admin", "bidder"}
        or "bid-review:prepare" not in ROLE_SCOPES[actor.role]
        or member is None
        or member.role not in {"owner", "contributor"}
    ):
        raise ServiceError("forbidden", "Preparation authority is no longer valid", 403, 4)
    if bind_context:
        await set_actor_context(session, actor)
    return actor


async def job_access(session, actor, job, *, cancel=False) -> None:
    """Generic status/events carry only metadata; cancellation rechecks the human gate."""
    try:
        submission_id = UUID(job.result["submission"]["submission_id"])
    except (KeyError, ValueError, TypeError) as exc:
        raise not_found() from exc
    submission = await session.scalar(
        select(BidSubmission).where(
            BidSubmission.org_id == actor.org_id,
            BidSubmission.task_id == job.task_id,
            BidSubmission.id == submission_id,
        )
    )
    preparation = await session.scalar(
        select(BidPreparation).where(
            BidPreparation.org_id == actor.org_id,
            BidPreparation.task_id == job.task_id,
            BidPreparation.submission_id == submission_id,
            BidPreparation.job_id == job.id,
        )
    )
    if submission is None or preparation is None:
        raise not_found()
    _, _, member = await task_workflow.access(
        session,
        actor,
        submission.task_id,
        scope="bid-review:prepare" if cancel else "bid-review:read",
        write=cancel,
        require_member=cancel,
    )
    if cancel and (
        actor.actor_kind != "session"
        or actor.token_id is not None
        or actor.role not in {"admin", "bidder"}
        or member is None
        or member.role not in {"owner", "contributor"}
    ):
        raise ServiceError("forbidden", "Human preparation authority required", 403, 4)


async def pdf_signatures(content: bytes, anchors: list[dict], settings: Settings) -> dict:
    """Validate the unchanged original in the same constrained disposable process."""
    with PDFOperation(
        content,
        "bid_signatures",
        {"anchors": anchors},
        settings,
        temporary_root=temporary_root(settings),
    ) as child:
        await child.wait_async()
        return next(child.records())
