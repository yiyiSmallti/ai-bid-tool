"""Vendor page images taken from succeeded sandbox captures and their retained bytes.

The sandbox proxy, not the client, observed the URL, time and response bytes. Ingest
resolves those facts from the run and rechecks every stored hash before it records an
archive; a web capture qualifies only when its bundle retained the entry page.
"""

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC
from html.parser import HTMLParser
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import select

from app.core.errors import ServiceError, not_found
from app.core.security import Secrets
from app.models.entities import TaskResource
from app.models.sandbox import SandboxArtifact, SandboxFetchReceipt, SandboxInput, SandboxRun
from app.models.screenshots import ScreenshotVendorArchive
from app.providers.sandbox_fetch import canonical_url
from app.providers.storage import Storage
from app.schemas.sandbox_contracts import MAX_ARTIFACT_BYTES
from app.schemas.screenshot_contracts import VendorSource

REDIRECTS = {301, 302, 303, 307, 308}
MAX_TITLE = 500


def integrity() -> ServiceError:
    return ServiceError(
        "vendor_provenance_integrity", "Vendor capture provenance failed integrity checks", 409, 4
    )


@dataclass(frozen=True)
class VendorCapture:
    run: SandboxRun
    input: SandboxInput
    image: SandboxArtifact
    archive: SandboxArtifact
    entry: SandboxFetchReceipt
    entry_metadata: dict


class _Title(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.inside = False
        self.done = False
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "title" and not self.done:
            self.inside = True

    def handle_endtag(self, tag):
        if tag == "title" and self.inside:
            self.inside, self.done = False, True

    def handle_data(self, data):
        if self.inside:
            self.parts.append(data)


def page_title(body: bytes, content_type: str) -> str | None:
    """The first <title> of the archived entry page; absent stays absent, never guessed."""
    charset = "utf-8"
    for parameter in content_type.split(";")[1:]:
        name, _, value = parameter.partition("=")
        if name.strip().lower() == "charset" and value.strip():
            charset = value.strip().strip('"')
    try:
        text = body.decode(charset, errors="replace")
    except LookupError:
        text = body.decode("utf-8", errors="replace")
    parser = _Title()
    parser.feed(text)
    title = " ".join("".join(parser.parts).split())
    return title[:MAX_TITLE] if title else None


async def entry_receipt(
    session, capture_input: SandboxInput, attempt_record_id, crypto: Secrets
) -> tuple[SandboxFetchReceipt, dict]:
    """Follow the proxy's single GET chain from the selected URL to its HTTP 200 response."""
    if capture_input.encrypted_source_url is None:
        raise integrity()
    root_hash = hashlib.sha256(
        canonical_url(crypto.decrypt(capture_input.encrypted_source_url)).encode()
    ).hexdigest()
    rows = (
        await session.scalars(
            select(SandboxFetchReceipt).where(
                SandboxFetchReceipt.attempt_record_id == attempt_record_id,
                SandboxFetchReceipt.decision_code == "allowed",
            )
        )
    ).all()
    roots = [r for r in rows if r.parent_redirect_id is None and r.url_sha256 == root_hash]
    current = roots[0] if len(roots) == 1 else None
    for _ in range(6):
        if current is None or current.encrypted_request_metadata is None:
            break
        metadata = json.loads(crypto.decrypt(current.encrypted_request_metadata))
        if metadata.get("method") != "GET" or metadata.get("url_sha256") != current.url_sha256:
            break
        children = [r for r in rows if r.parent_redirect_id == current.id]
        if current.status_code == 200 and not children:
            return current, metadata
        if current.status_code not in REDIRECTS or len(children) != 1:
            break
        current = children[0]
    raise integrity()


async def resolve(session, actor, task_id, extraction_id, source: VendorSource, crypto: Secrets):
    """Authorize the named page image and pin its run, archive and entry response."""
    from app.services import sandbox

    actor.require("resource:read")
    image, attempt, run = await sandbox.require_artifact(session, actor, source.sandbox_artifact_id)
    capture_input = await session.get(SandboxInput, run.input_id)
    if (
        capture_input is None
        or run.task_id != task_id
        or capture_input.purpose != "vendor_capture"
        or capture_input.extraction_job_id != extraction_id
    ):
        raise not_found()
    pdf = capture_input.spec["format"] == "pdf"
    if source.kind != ("vendor_pdf" if pdf else "vendor_web") or image.kind != (
        "pdf_page_png" if pdf else "capture_png"
    ):
        raise ServiceError(
            "vendor_source_mismatch", "Artifact is not a page image of this capture kind", 400, 2
        )
    archive = await session.scalar(
        select(SandboxArtifact).where(
            SandboxArtifact.attempt_record_id == attempt.id,
            SandboxArtifact.kind == ("source_pdf" if pdf else "capture_archive"),
        )
    )
    if archive is None:
        raise ServiceError(
            "vendor_archive_required",
            "Web capture must use archive=bundle so the observed page bytes are retained",
            409,
            2,
        )
    selection = await session.get(TaskResource, capture_input.task_resource_id)
    if selection is None or selection.task_id != task_id:
        raise not_found()
    if not selection.active or selection.product_revision_id != capture_input.product_revision_id:
        raise ServiceError("inactive_selection", "Material selection is no longer active", 409, 4)
    entry, metadata = await entry_receipt(session, capture_input, attempt.id, crypto)
    if pdf and entry.response_sha256 != archive.plaintext_sha256:
        raise integrity()
    bindings = {
        "task_resource_id": selection.id,
        "product_revision_id": selection.product_revision_id,
        "origin": "vendor",
        "image_kind": "vendor_page",
    }
    return bindings, selection, VendorCapture(run, capture_input, image, archive, entry, metadata)


async def _read(storage: Storage, artifact: SandboxArtifact) -> bytes:
    try:
        content = await storage.read_bounded(
            artifact.org_id, artifact.object_key, MAX_ARTIFACT_BYTES[artifact.kind]
        )
    except ServiceError as exc:
        if exc.code in {"missing_file", "unreadable_file", "file_size_limit"}:
            raise integrity() from None
        raise
    if (
        len(content) != artifact.size_bytes
        or hashlib.sha256(content).hexdigest() != artifact.plaintext_sha256
    ):
        raise integrity()
    return content


async def source_png(storage: Storage, capture: VendorCapture) -> bytes:
    return await _read(storage, capture.image)


async def archived_entry(storage: Storage, capture: VendorCapture) -> tuple[str, str | None]:
    """Recheck the retained bytes and return the entry content hash and page title."""
    content = await _read(storage, capture.archive)
    if capture.archive.kind == "source_pdf":
        return capture.archive.plaintext_sha256, None
    name = capture.entry_metadata.get("archive_entry")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as bundle:
            info = bundle.getinfo(name) if isinstance(name, str) else None
            if info is None or info.file_size != capture.entry.response_bytes:
                raise integrity()
            body = bundle.read(info)
    except (KeyError, zipfile.BadZipFile):
        raise integrity() from None
    content_sha256 = capture.entry.response_sha256
    if content_sha256 is None or hashlib.sha256(body).hexdigest() != content_sha256:
        raise integrity()
    headers = capture.entry_metadata.get("headers") or {}
    return content_sha256, page_title(body, str(headers.get("content-type", "")))


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


async def record(
    session, actor, task_id, extraction_id, capture: VendorCapture, content_sha256, title
) -> ScreenshotVendorArchive:
    """One archive record per capture run; later pages of the same run reuse it."""
    existing = await session.scalar(
        select(ScreenshotVendorArchive).where(
            ScreenshotVendorArchive.sandbox_run_id == capture.run.id
        )
    )
    if existing is not None:
        if (
            existing.archive_artifact_id != capture.archive.id
            or existing.entry_receipt_id != capture.entry.id
            or existing.content_sha256 != content_sha256
        ):
            raise integrity()
        return existing
    pdf = capture.archive.kind == "source_pdf"
    row = ScreenshotVendorArchive(
        id=uuid4(),
        org_id=actor.org_id,
        task_id=task_id,
        extraction_job_id=extraction_id,
        task_resource_id=capture.input.task_resource_id,
        product_revision_id=capture.input.product_revision_id,
        search_candidate_id=None,
        content_sha256=content_sha256,
        archive_sha256=capture.archive.plaintext_sha256,
        storage_key=capture.archive.object_key,
        descriptor={
            "sha256": capture.archive.plaintext_sha256,
            "size_bytes": capture.archive.size_bytes,
            "media_type": "application/pdf" if pdf else "application/zip",
        },
        provenance={
            "format": "pdf" if pdf else "web",
            "source_field": capture.input.source_field,
            "source_url_sha256": capture.input.source_url_sha256,
            "final_url_sha256": capture.entry.url_sha256,
            "final_origin": _origin(str(capture.entry_metadata["url"])),
            "title": title,
            "captured_at": capture.entry.ended_at.astimezone(UTC).isoformat(),
            "policy_revision": capture.run.policy_revision,
            "provenance_manifest_hash": capture.archive.provenance_manifest_hash,
        },
        sandbox_run_id=capture.run.id,
        archive_artifact_id=capture.archive.id,
        entry_receipt_id=capture.entry.id,
    )
    session.add(row)
    await session.flush()
    return row


def view(row: ScreenshotVendorArchive) -> dict:
    return {
        "id": str(row.id),
        "sandbox_run_id": str(row.sandbox_run_id),
        "task_resource_id": str(row.task_resource_id),
        "product_revision_id": str(row.product_revision_id),
        "format": row.provenance["format"],
        "source_field": row.provenance["source_field"],
        "source_url_sha256": row.provenance["source_url_sha256"],
        "final_url_sha256": row.provenance["final_url_sha256"],
        "final_origin": row.provenance["final_origin"],
        "title": row.provenance["title"],
        "captured_at": row.provenance["captured_at"],
        "content_sha256": row.content_sha256,
        "archive": row.descriptor,
        "policy_revision": row.provenance["policy_revision"],
    }


async def verify(session, storage: Storage | None, row: ScreenshotVendorArchive, asset) -> None:
    """Material checks for card and export use; the policy file is not consulted again."""
    try:
        image_id = UUID(str(asset.source.get("sandbox_artifact_id")))
    except ValueError:
        raise integrity() from None
    image = await session.get(SandboxArtifact, image_id)
    archive = await session.get(SandboxArtifact, row.archive_artifact_id)
    if (
        image is None
        or archive is None
        or image.sandbox_run_id != row.sandbox_run_id
        or image.attempt_record_id != archive.attempt_record_id
        or image.plaintext_sha256 != asset.source_sha256
        or (row.task_resource_id, row.product_revision_id)
        != (asset.task_resource_id, asset.product_revision_id)
    ):
        raise integrity()
    if storage is not None:
        await _read(storage, archive)
