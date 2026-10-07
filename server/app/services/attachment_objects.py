"""Read-only reconciliation of recorded publication IDs; ciphertext is never deleted."""

import hashlib
import re
from uuid import UUID

from sqlalchemy import select

from app.core.errors import ServiceError
from app.models.attachments import AttachmentFile
from app.models.entities import EvidenceSource
from app.models.screenshots import ScreenshotRendition
from app.schemas.attachment_contracts import FILE_BYTE_LIMIT

SUFFIX = re.compile(
    r"^(?:attachment/(?P<archive>[0-9a-f-]{36})/(?P<revision>[0-9a-f-]{36})/(?P<pdf>[0-9a-f]{64})\.pdf"
    r"|evidence-source/(?P<source>[0-9a-f-]{36})/(?P<png>[0-9a-f]{64})\.png"
    r"|screenshots/(?P<asset>[0-9a-f-]{36})/(?P<rendition>[0-9a-f-]{36})/(?P<image>[0-9a-f]{64})\.png)$"
)


async def reconcile(session, org_id: UUID, records: list[dict], storage):
    """Operator-only helper over a bounded stage manifest in an org DB context.

    A stage record is an attempted immutable put, not proof of business commit or
    even proof that the put completed. Reconcile both stores before retry/recovery.
    Neither an unavailable object nor an orphan is a reason to remove ciphertext.
    """
    if not 1 <= len(records) <= 100:
        raise ServiceError(
            "attachment_reconciliation_limit", "Reconcile one through 100 IDs", 422, 2
        )
    output = []
    for record in records:
        key, expected = record.get("key", ""), record.get("sha256", "")
        prefix = f"org/{org_id}/"
        match = SUFFIX.fullmatch(key[len(prefix) :]) if key.startswith(prefix) else None
        if match is None:
            raise ServiceError(
                "invalid_reconciliation_record", "Stage record has invalid scope", 422, 2
            )
        values = match.groupdict()
        try:
            identifier = UUID(values["revision"] or values["source"] or values["rendition"])
            if str(identifier) != record.get("object_id") or expected != (
                values["pdf"] or values["png"] or values["image"]
            ):
                raise ValueError("binding")
        except (ValueError, TypeError) as exc:
            raise ServiceError(
                "invalid_reconciliation_record", "Stage identity or hash changed", 422, 2
            ) from exc
        model, column = (
            (AttachmentFile, AttachmentFile.attachment_revision_id)
            if values["revision"]
            else (
                (EvidenceSource, EvidenceSource.id)
                if values["source"]
                else (ScreenshotRendition, ScreenshotRendition.id)
            )
        )
        persisted = await session.scalar(
            select(model.storage_key).where(model.org_id == org_id, column == identifier)
        )
        try:
            content = await storage.read_bounded(org_id, key, FILE_BYTE_LIMIT)
            intact = hashlib.sha256(content).hexdigest() == expected
            status = (
                ("committed" if persisted == key else "retained_orphan")
                if intact and persisted in {None, key}
                else "integrity_failure"
            )
        except ServiceError as exc:
            if exc.code == "missing_file":
                status = "unavailable"
            elif exc.code in {"unreadable_file", "file_size_limit"}:
                status = "integrity_failure"
            else:
                raise
        output.append({"object_id": str(identifier), "sha256": expected, "status": status})
    return output
