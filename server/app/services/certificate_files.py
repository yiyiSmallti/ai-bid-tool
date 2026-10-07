"""Retain certificate originals bound to immutable certificate/task versions.

A single uploaded PDF is kept as the original unchanged. Several files, images or
a rotation are composed into one PDF original in upload order, one page per
image; every uploaded file is kept unchanged as a part of that original."""

import hashlib
import struct
from collections.abc import Sequence
from pathlib import Path, PurePath
from uuid import UUID, uuid4

import pymupdf
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.core.pdf_files import validate_file as validate_file
from app.models.entities import (
    Certificate,
    CertificateFile,
    CertificateFilePart,
    CertificateRevision,
    Task,
    TaskCertificate,
)
from app.providers.storage import Storage
from app.schemas.certificate_contracts import CertificateData
from app.schemas.certificate_file_contracts import (
    MAX_PARTS,
    CertificateFileCreate,
    CertificateFileRevision,
    CertificatePart,
    CertificatePartOptions,
    CertificateScanFile,
    TaskCertificateFileSnapshot,
)
from app.services.auth import Identity
from app.services.certificates import snapshot_data
from app.services.task_authorization import task_authorized
from app.services.versioned import audit

MAX_FILE_BYTES = 40 * 1024 * 1024
WARNINGS = [
    "Certificate file is user-supplied; authenticity, eligibility and metadata matching have not been verified"
]
MISSING_WARNING = (
    "Selected/current certificate revision has no original PDF; no prior file is inherited"
)


# Decoding happens only after the header's dimensions pass this bound, so a small
# file cannot declare an image that exhausts memory.
MAX_IMAGE_PIXELS = 40_000_000
# An image page's longer side, in points (the long side of A4); previews render
# every page at the same resolution, so photos and scans come out alike.
IMAGE_PAGE_LONG_SIDE = 842
JPEG_QUALITY = 92
EXTENSIONS = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def invalid(message: str = "Files must be readable PDF, PNG or JPEG within file/page limits"):
    return ServiceError("invalid_certificate_file", message, 400, 2)


def media_type(content: bytes, name: str) -> str:
    declared = EXTENSIONS.get(PurePath(name).suffix.lower())
    signatures = {
        "application/pdf": content.lstrip().startswith(b"%PDF-"),
        "image/png": content.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": content.startswith(b"\xff\xd8\xff"),
    }
    if declared is None or not signatures[declared]:
        raise invalid()
    return declared


def image_size(content: bytes, kind: str) -> tuple[int, int]:
    """Width and height from the file header, before any pixel is decoded."""
    try:
        if kind == "image/png":
            if content[12:16] != b"IHDR":
                raise ValueError("no header")
            return struct.unpack(">II", content[16:24])
        index = 2
        while index + 9 < len(content):
            if content[index] != 0xFF:
                raise ValueError("bad marker")
            marker = content[index + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                index += 2
                continue
            length = struct.unpack(">H", content[index + 2 : index + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", content[index + 5 : index + 9])
                return width, height
            index += 2 + length
    except (ValueError, struct.error):
        pass
    raise invalid()


def image_pdf(content: bytes, kind: str) -> bytes:
    """One upright page; the pixels are re-encoded, so EXIF, GPS and other metadata
    never reach the original."""
    width, height = image_size(content, kind)
    if not (0 < width and 0 < height and width * height <= MAX_IMAGE_PIXELS):
        raise invalid("Images must be at most 40 megapixels")
    try:
        # As a document MuPDF applies the EXIF orientation; the raw pixmap does not.
        with pymupdf.open(
            stream=content, filetype="png" if kind == "image/png" else "jpg"
        ) as image:
            rect = image[0].rect
            scale = max(width, height) / max(rect.width, rect.height)
            pixmap = image[0].get_pixmap(
                matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False
            )
        encoded = (
            pixmap.tobytes("png")
            if kind == "image/png"
            else pixmap.tobytes("jpg", jpg_quality=JPEG_QUALITY)
        )
        factor = IMAGE_PAGE_LONG_SIDE / max(pixmap.width, pixmap.height)
        with pymupdf.open() as pdf:
            page = pdf.new_page(width=pixmap.width * factor, height=pixmap.height * factor)
            page.insert_image(page.rect, stream=encoded)
            return pdf.tobytes(garbage=3, deflate=True)
    except ServiceError:
        raise
    except Exception as exc:
        raise invalid() from exc


def compose(
    uploads: Sequence[tuple[str, bytes]],
    options: Sequence[CertificatePartOptions],
    title: str,
) -> tuple[CertificateScanFile, bytes, list[tuple[dict, bytes]]]:
    """The original to store and the parts it was composed from (none for a single
    unrotated PDF, which is stored as uploaded). A composed original is named after
    the certificate."""
    if not 1 <= len(uploads) <= MAX_PARTS:
        raise invalid(f"Upload between 1 and {MAX_PARTS} files")
    if options and len(options) != len(uploads):
        raise ServiceError(
            "invalid_input", "Give one part option per uploaded file, or none", 422, 2
        )
    if sum(len(content) for _, content in uploads) > MAX_FILE_BYTES:
        raise invalid("Files together exceed the upload limit")
    rotations = [item.rotation for item in options] or [0] * len(uploads)
    kinds = [media_type(content, name) for name, content in uploads]
    if len(uploads) == 1 and kinds[0] == "application/pdf" and rotations[0] == 0:
        name, content = uploads[0]
        return validate_file(content, name), content, []
    parts: list[tuple[dict, bytes]] = []
    with pymupdf.open() as composed:
        for ordinal, ((name, content), kind, rotation) in enumerate(
            zip(uploads, kinds, rotations, strict=True), 1
        ):
            if kind == "application/pdf":
                validate_file(content, name)
                source = content
            else:
                source = image_pdf(content, kind)
            start = len(composed) + 1
            with pymupdf.open(stream=source, filetype="pdf") as pdf:
                composed.insert_pdf(pdf)
            if len(composed) > 200:
                raise invalid("Files together exceed 200 pages")
            for index in range(start - 1, len(composed)):
                composed[index].set_rotation((composed[index].rotation + rotation) % 360)
            descriptor = CertificatePart(
                ordinal=ordinal,
                name=name,
                media_type=kind,  # type: ignore[arg-type]
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                page_start=start,
                page_count=len(composed) - start + 1,
                rotation=rotation,  # type: ignore[arg-type]
            )
            parts.append((descriptor.model_dump(mode="json"), content))
        content = composed.tobytes(garbage=3, deflate=True)
    stem = "".join(char for char in title if char not in "/\\" and 32 <= ord(char) != 127).strip()
    return validate_file(content, (stem or "certificate")[:196] + ".pdf"), content, parts


def read_file(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        raise ServiceError(
            "invalid_certificate_input", "Cannot read certificate file", 400, 2
        ) from exc
    if media_type(content, path.name) == "application/pdf":
        validate_file(content, path.name)
    else:
        image_size(content, media_type(content, path.name))
    return content


def file_data(
    row: CertificateFile, revision: CertificateRevision, parts: Sequence[CertificateFilePart] = ()
) -> dict:
    return CertificateFileRevision(
        id=row.id,
        org_id=row.org_id,
        certificate_id=row.certificate_id,
        certificate_revision_id=revision.id,
        revision=revision.revision,
        data=CertificateData.model_validate(revision.data),
        file=CertificateScanFile.model_validate(row.file),
        parts=[CertificatePart.model_validate(part) for part in parts],
    ).model_dump(mode="json")


async def parts_for(session: AsyncSession, file_ids: Sequence[UUID]) -> dict[UUID, list]:
    found: dict[UUID, list] = {file_id: [] for file_id in file_ids}
    if file_ids:
        rows = await session.scalars(
            select(CertificateFilePart)
            .where(CertificateFilePart.certificate_file_id.in_(file_ids))
            .order_by(CertificateFilePart.ordinal)
        )
        for part in rows:
            found[part.certificate_file_id].append(part)
    return found


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
    parts: Sequence[tuple[dict, bytes]] = (),
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
    stored = []
    for part, part_content in parts:
        record = CertificateFilePart(
            id=uuid4(),
            org_id=actor.org_id,
            certificate_id=certificate_id,
            certificate_revision_id=revision.id,
            certificate_file_id=row.id,
            storage_key=f"org/{actor.org_id}/certificate/{certificate_id}/{revision.id}/parts/{part['ordinal']}-{part['sha256']}",
            **part,
        )
        await storage.put(actor.org_id, record.storage_key, part_content)
        session.add(record)
        stored.append(record)
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
            "part_count": len(stored),
        },
    )
    return file_data(row, revision, stored)


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
            [file_data(row, revision, (await parts_for(session, [row.id]))[row.id])],
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
    parts = await parts_for(session, [row.id for row, _ in rows])
    return (
        {"history": history, "current_revisions": current},
        [file_data(row, revision, parts[row.id]) for row, revision in rows],
        warnings,
    )


@task_authorized("certificate:file:read")
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
