"""Versioned metadata and immutable task selections; no source retrieval."""

from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Certificate, CertificateRevision, TaskCertificate
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
from app.services import versioned
from app.services.auth import Identity
from app.services.versioned import VersionedKind


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


CERTIFICATES = VersionedKind(
    root=Certificate,
    revision=CertificateRevision,
    selection=TaskCertificate,
    key="certificate_id",
    revision_key="certificate_revision_id",
    label="Certificate",
    read_scope="certificate:read",
    write_scope="certificate:write",
    select_scope="task:certificate",
    selection_list_scopes=("certificate:read", "task:read"),
    audit="resource.certificate",
    select_audit="task.certificate",
    snapshot_view=snapshot_data,
)


async def create_certificate(
    session: AsyncSession, actor: Identity, body: CertificateCreate
) -> dict:
    revision = await versioned.create(
        session, actor, CERTIFICATES, body.data.model_dump(mode="json")
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
    data, rows = await versioned.list_revisions(
        session, actor, CERTIFICATES, history=history, root_id=certificate_id
    )
    return {
        **data,
        "validity_by_revision": {
            str(row.id): inspect_dates(CertificateData.model_validate(row.data), as_of)
            for row in rows
        },
    }, [revision_data(row) for row in rows]


async def update_certificate(
    session: AsyncSession, actor: Identity, certificate_id: UUID, body: CertificateUpdate
) -> dict:
    revision = await versioned.update(
        session,
        actor,
        CERTIFICATES,
        certificate_id,
        body.expected_revision,
        body.data.model_dump(mode="json"),
    )
    return revision_data(revision)


async def select_certificate(
    session: AsyncSession, actor: Identity, task_id: UUID, body: TaskCertificateSelection
) -> dict:
    return await versioned.select_revision(
        session, actor, CERTIFICATES, task_id, body.certificate_id, body.revision, body.lot
    )


async def list_selections(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    *,
    history: bool = False,
    as_of: date | None = None,
) -> tuple[dict, list[dict]]:
    data, rows = await versioned.list_selections(
        session, actor, CERTIFICATES, task_id, history=history
    )
    return {
        **data,
        "validity_by_revision": {
            str(revision.id): inspect_dates(CertificateData.model_validate(revision.data), as_of)
            for _, revision in rows
        },
    }, [snapshot_data(row, revision) for row, revision in rows]


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
