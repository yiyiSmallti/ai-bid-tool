"""Retain original PDF bytes bound to immutable certificate/task versions."""

import hashlib
from pathlib import Path
from uuid import UUID, uuid4

import pymupdf
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.models.entities import (
    Certificate,
    CertificateFile,
    CertificateRevision,
    Task,
    TaskCertificate,
)
from app.providers.storage import Storage
from app.schemas.certificate_contracts import CertificateData
from app.schemas.certificate_file_contracts import (
    CertificateFileCreate,
    CertificateFileRevision,
    CertificateScanFile,
    TaskCertificateFileSnapshot,
)
from app.services.auth import Identity
from app.services.certificates import snapshot_data
from app.services.versioned import audit

MAX_FILE_BYTES = 40 * 1024 * 1024
WARNINGS = [
    "Certificate file is user-supplied; authenticity, eligibility and metadata matching have not been verified"
]
MISSING_WARNING = (
    "Selected/current certificate revision has no original PDF; no prior file is inherited"
)


def validate_file(content: bytes, name: str) -> CertificateScanFile:
    try:
        if (
            not content
            or len(content) > MAX_FILE_BYTES
            or not content.lstrip().startswith(b"%PDF-")
        ):
            raise ValueError("invalid PDF")
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            if (
                not pdf.is_pdf
                or pdf.is_repaired
                or pdf.is_encrypted
                or pdf.needs_pass
                or pdf.xref_get_key(-1, "Encrypt")[0] != "null"
                or not 1 <= len(pdf) <= 200
            ):
                raise ValueError("unsupported PDF")
            for page in pdf:
                if page.rect.is_empty:
                    raise ValueError("invalid page")
            return CertificateScanFile(
                name=name,
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                page_count=len(pdf),
            )
    except Exception as exc:
        raise ServiceError(
            "invalid_certificate_file",
            "File must be a readable unencrypted PDF within file/page limits",
            400,
            2,
        ) from exc


def read_file(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        raise ServiceError(
            "invalid_certificate_input", "Cannot read certificate file", 400, 2
        ) from exc
    validate_file(content, path.name)
    return content


def file_data(row: CertificateFile, revision: CertificateRevision) -> dict:
    return CertificateFileRevision(
        id=row.id,
        org_id=row.org_id,
        certificate_id=row.certificate_id,
        certificate_revision_id=revision.id,
        revision=revision.revision,
        data=CertificateData.model_validate(revision.data),
        file=CertificateScanFile.model_validate(row.file),
    ).model_dump(mode="json")


def require_read(actor: Identity):
    actor.require("certificate:read")
    actor.require("certificate:file:read")


async def create_file(
    session: AsyncSession,
    actor: Identity,
    certificate_id: UUID,
    body: CertificateFileCreate,
    descriptor: CertificateScanFile,
    content: bytes,
    storage: Storage,
) -> dict:
    actor.require("certificate:write")
    actor.require("certificate:file:write")
    certificate = await session.scalar(
        select(Certificate).where(Certificate.id == certificate_id).with_for_update()
    )
    if certificate is None:
        raise not_found()
    if certificate.current_revision != body.expected_revision:
        raise ServiceError(
            "revision_conflict",
            "Certificate revision changed; read current revision before uploading",
            409,
            4,
        )
    old = await session.scalar(
        select(CertificateRevision).where(
            CertificateRevision.certificate_id == certificate_id,
            CertificateRevision.revision == certificate.current_revision,
        )
    )
    if old is None:
        raise not_found()
    revision = CertificateRevision(
        id=uuid4(),
        org_id=actor.org_id,
        certificate_id=certificate_id,
        revision=certificate.current_revision + 1,
        data=body.data.model_dump(mode="json"),
    )
    row = CertificateFile(
        id=uuid4(),
        org_id=actor.org_id,
        certificate_id=certificate_id,
        certificate_revision_id=revision.id,
        created_by=actor.user_id,
        file=descriptor.model_dump(mode="json"),
        storage_key=f"org/{actor.org_id}/certificate/{certificate_id}/{revision.id}/{descriptor.sha256}.pdf",
    )
    await storage.put(actor.org_id, row.storage_key, content)
    session.add(revision)
    await session.flush()
    session.add(row)
    await session.flush()
    certificate.current_revision = revision.revision
    audit(
        session,
        actor,
        "resource.certificate.file.create",
        certificate_id,
        {
            "old_revision_id": str(old.id),
            "new_revision_id": str(revision.id),
            "file_id": str(row.id),
            "revision": revision.revision,
        },
    )
    return file_data(row, revision)


async def require_file(session: AsyncSession, actor: Identity, revision_id: UUID):
    require_read(actor)
    record = (
        await session.execute(
            select(CertificateFile, CertificateRevision)
            .join(
                CertificateRevision,
                and_(
                    CertificateRevision.org_id == CertificateFile.org_id,
                    CertificateRevision.id == CertificateFile.certificate_revision_id,
                ),
            )
            .where(CertificateFile.certificate_revision_id == revision_id)
        )
    ).first()
    if record is None:
        raise not_found()
    return record


async def list_files(
    session: AsyncSession,
    actor: Identity,
    *,
    certificate_id: UUID | None = None,
    history: bool = False,
    revision_id: UUID | None = None,
) -> tuple[dict, list[dict], list[str]]:
    require_read(actor)
    if revision_id is not None and (certificate_id is not None or history):
        raise ServiceError(
            "invalid_filter", "Revision cannot be combined with id or history", 400, 2
        )
    if revision_id is not None:
        row, revision = await require_file(session, actor, revision_id)
        certificate = await session.get(Certificate, revision.certificate_id)
        if certificate is None:
            raise not_found()
        return (
            {
                "history": False,
                "current_revisions": {str(revision.certificate_id): certificate.current_revision},
            },
            [file_data(row, revision)],
            WARNINGS,
        )
    query = select(Certificate).order_by(Certificate.created_at, Certificate.id)
    if certificate_id is not None:
        query = query.where(Certificate.id == certificate_id)
    certificates = (await session.scalars(query)).all()
    if certificate_id is not None and not certificates:
        raise not_found()
    current = {str(row.id): row.current_revision for row in certificates}
    query = (
        select(CertificateFile, CertificateRevision)
        .join(
            CertificateRevision,
            and_(
                CertificateRevision.org_id == CertificateFile.org_id,
                CertificateRevision.id == CertificateFile.certificate_revision_id,
            ),
        )
        .join(
            Certificate,
            and_(
                Certificate.org_id == CertificateRevision.org_id,
                Certificate.id == CertificateRevision.certificate_id,
            ),
        )
    )
    if certificate_id is not None:
        query = query.where(Certificate.id == certificate_id)
    if not history:
        query = query.where(CertificateRevision.revision == Certificate.current_revision)
    rows = (
        await session.execute(query.order_by(CertificateRevision.created_at, CertificateFile.id))
    ).all()
    has_current = {
        str(revision.certificate_id)
        for _, revision in rows
        if revision.revision == current[str(revision.certificate_id)]
    }
    warnings = [*WARNINGS, MISSING_WARNING] if len(has_current) < len(current) else WARNINGS
    return (
        {"history": history, "current_revisions": current},
        [file_data(row, revision) for row, revision in rows],
        warnings,
    )


async def list_task_files(
    session: AsyncSession, actor: Identity, task_id: UUID, *, history: bool = False
) -> tuple[dict, list[dict], list[str]]:
    require_read(actor)
    actor.require("task:read")
    if await session.get(Task, task_id) is None:
        raise not_found()
    rows = (
        await session.execute(
            select(TaskCertificate, CertificateRevision, CertificateFile)
            .join(
                CertificateRevision,
                and_(
                    TaskCertificate.org_id == CertificateRevision.org_id,
                    TaskCertificate.certificate_revision_id == CertificateRevision.id,
                ),
            )
            .outerjoin(
                CertificateFile,
                and_(
                    CertificateFile.org_id == CertificateRevision.org_id,
                    CertificateFile.certificate_revision_id == CertificateRevision.id,
                ),
            )
            .where(TaskCertificate.task_id == task_id)
            .order_by(TaskCertificate.created_at, TaskCertificate.id)
        )
    ).all()
    items = [
        TaskCertificateFileSnapshot(
            **snapshot_data(snapshot, revision),
            certificate_file_id=row.id if row else None,
            file=CertificateScanFile.model_validate(row.file) if row else None,
        ).model_dump(mode="json")
        for snapshot, revision, row in rows
        if history or snapshot.active
    ]
    warnings = (
        [*WARNINGS, MISSING_WARNING] if any(item["file"] is None for item in items) else WARNINGS
    )
    return (
        {"history": history, "active_snapshot_ids": [str(s.id) for s, _, _ in rows if s.active]},
        items,
        warnings,
    )


async def read_revision(
    session: AsyncSession, actor: Identity, revision_id: UUID, storage: Storage
):
    row, _ = await require_file(session, actor, revision_id)
    descriptor = CertificateScanFile.model_validate(row.file)
    content = await storage.read(actor.org_id, row.storage_key)
    if (
        len(content) != descriptor.size_bytes
        or hashlib.sha256(content).hexdigest() != descriptor.sha256
    ):
        raise ServiceError(
            "certificate_file_integrity", "Certificate file failed integrity checks", 500, 4
        )
    return content, descriptor
