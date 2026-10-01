"""Versioned metadata and immutable task selections; no source retrieval."""

from datetime import date
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.models.entities import Certificate, CertificateRevision, Task, TaskCertificate
from app.schemas.certificate_contracts import (
    CertificateCreate,
    CertificateData,
    CertificateUpdate,
    TaskCertificateSelection,
    TaskCertificateSnapshot,
)
from app.schemas.certificate_contracts import (
    CertificateRevision as RevisionContract,
)
from app.services.auth import Identity
from app.services.resources import audit


def revision_data(row: CertificateRevision) -> dict:
    return RevisionContract.model_validate(row).model_dump(mode="json")


def snapshot_data(row: TaskCertificate, revision: CertificateRevision) -> dict:
    return TaskCertificateSnapshot(
        id=row.id,
        org_id=row.org_id,
        task_id=row.task_id,
        certificate_revision_id=revision.id,
        revision=revision.revision,
        lot=row.lot or None,
        data=CertificateData.model_validate(revision.data),
    ).model_dump(mode="json")


async def create_certificate(
    session: AsyncSession, actor: Identity, body: CertificateCreate
) -> dict:
    actor.require("certificate:write")
    certificate = Certificate(org_id=actor.org_id, created_by=actor.user_id, current_revision=1)
    session.add(certificate)
    await session.flush()
    revision = CertificateRevision(
        org_id=actor.org_id,
        certificate_id=certificate.id,
        revision=1,
        data=body.data.model_dump(mode="json"),
    )
    session.add(revision)
    await session.flush()
    audit(
        session,
        actor,
        "resource.certificate.create",
        certificate.id,
        {"new_revision_id": str(revision.id), "revision": 1},
    )
    return revision_data(revision)


async def list_certificates(
    session: AsyncSession,
    actor: Identity,
    *,
    history: bool = False,
    certificate_id: UUID | None = None,
    as_of: date | None = None,
) -> tuple[dict, list[dict]]:
    actor.require("certificate:read")
    certificates = select(Certificate).order_by(Certificate.created_at, Certificate.id)
    if certificate_id is not None:
        certificates = certificates.where(Certificate.id == certificate_id)
    records = (await session.scalars(certificates)).all()
    if certificate_id is not None and not records:
        raise not_found()
    current = {str(row.id): row.current_revision for row in records}
    query = select(CertificateRevision).join(
        Certificate,
        and_(
            Certificate.org_id == CertificateRevision.org_id,
            Certificate.id == CertificateRevision.certificate_id,
        ),
    )
    if not history:
        query = query.where(CertificateRevision.revision == Certificate.current_revision)
    if certificate_id is not None:
        query = query.where(CertificateRevision.certificate_id == certificate_id)
    rows = (
        await session.scalars(
            query.order_by(CertificateRevision.created_at, CertificateRevision.revision)
        )
    ).all()
    return {
        "history": history,
        "current_revisions": current,
        "validity_by_revision": {
            str(row.id): inspect_dates(CertificateData.model_validate(row.data), as_of)
            for row in rows
        },
    }, [revision_data(row) for row in rows]


async def update_certificate(
    session: AsyncSession, actor: Identity, certificate_id: UUID, body: CertificateUpdate
) -> dict:
    actor.require("certificate:write")
    certificate = await session.scalar(
        select(Certificate).where(Certificate.id == certificate_id).with_for_update()
    )
    if certificate is None:
        raise not_found()
    if certificate.current_revision != body.expected_revision:
        raise ServiceError(
            "revision_conflict",
            "Certificate revision changed; read the current revision before updating",
            409,
            2,
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
        org_id=actor.org_id,
        certificate_id=certificate.id,
        revision=certificate.current_revision + 1,
        data=body.data.model_dump(mode="json"),
    )
    session.add(revision)
    await session.flush()
    certificate.current_revision = revision.revision
    audit(
        session,
        actor,
        "resource.certificate.update",
        certificate.id,
        {
            "old_revision_id": str(old.id),
            "new_revision_id": str(revision.id),
            "revision": revision.revision,
        },
    )
    return revision_data(revision)


async def select_certificate(
    session: AsyncSession, actor: Identity, task_id: UUID, body: TaskCertificateSelection
) -> dict:
    actor.require("task:certificate")
    # Serialize only selections on this task; a concurrent duplicate has one result.
    task = await session.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None:
        raise not_found()
    certificate = await session.scalar(
        select(Certificate).where(Certificate.id == body.certificate_id).with_for_update(read=True)
    )
    if certificate is None:
        raise not_found()
    revision = await session.scalar(
        select(CertificateRevision).where(
            CertificateRevision.certificate_id == certificate.id,
            CertificateRevision.revision == (body.revision or certificate.current_revision),
        )
    )
    if revision is None:
        raise not_found()
    lot = (body.lot or "").strip()
    previous = await session.scalar(
        select(TaskCertificate).where(
            TaskCertificate.task_id == task_id,
            TaskCertificate.certificate_id == certificate.id,
            TaskCertificate.lot == lot,
            TaskCertificate.active.is_(True),
        )
    )
    if previous is not None and previous.certificate_revision_id == revision.id:
        return {
            **snapshot_data(previous, revision),
            "duplicate": True,
            "replaced_snapshot_id": None,
        }
    if previous is not None:
        previous.active = False
        await session.flush()
    snapshot = TaskCertificate(
        org_id=actor.org_id,
        task_id=task_id,
        certificate_id=certificate.id,
        certificate_revision_id=revision.id,
        lot=lot,
        active=True,
    )
    session.add(snapshot)
    await session.flush()
    audit(
        session,
        actor,
        "task.certificate.select",
        snapshot.id,
        {
            "task_id": str(task_id),
            "certificate_id": str(certificate.id),
            "old_snapshot_id": str(previous.id) if previous else None,
            "old_revision_id": str(previous.certificate_revision_id) if previous else None,
            "new_revision_id": str(revision.id),
        },
    )
    return {
        **snapshot_data(snapshot, revision),
        "duplicate": False,
        "replaced_snapshot_id": str(previous.id) if previous else None,
    }


async def list_selections(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    *,
    history: bool = False,
    as_of: date | None = None,
) -> tuple[dict, list[dict]]:
    actor.require("certificate:read")
    actor.require("task:read")
    if await session.get(Task, task_id) is None:
        raise not_found()
    rows = (
        await session.execute(
            select(TaskCertificate, CertificateRevision)
            .join(
                CertificateRevision,
                and_(
                    TaskCertificate.org_id == CertificateRevision.org_id,
                    TaskCertificate.certificate_revision_id == CertificateRevision.id,
                ),
            )
            .where(TaskCertificate.task_id == task_id)
            .order_by(TaskCertificate.created_at, TaskCertificate.id)
        )
    ).all()
    active_ids = [str(row.id) for row, _ in rows if row.active]
    items = [snapshot_data(row, revision) for row, revision in rows if history or row.active]
    return {
        "history": history,
        "active_snapshot_ids": active_ids,
        "validity_by_revision": {
            str(revision.id): inspect_dates(CertificateData.model_validate(revision.data), as_of)
            for row, revision in rows
            if history or row.active
        },
    }, items


def inspect_dates(data: CertificateData, as_of: date | None) -> dict:
    """Classify declared dates only; no current date or task deadline is inferred."""
    state = "unknown"
    if as_of is not None:
        if data.valid_until is not None and data.valid_until < as_of:
            state = "expired"
        elif data.valid_from is not None and data.valid_from > as_of:
            state = "not_yet_valid"
        elif data.valid_from is not None and data.valid_until is not None:
            state = "valid"
    return {"as_of": as_of.isoformat() if as_of else None, "state": state}
