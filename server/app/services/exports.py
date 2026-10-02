"""Human export authorization, fixed manifests and immutable publication.

File I/O is performed before the short mutation transaction's task/card locks.
The locked pass reconstructs the manifest from immutable relations and current
selection/review state; no saved human identity authorizes a worker publication.
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.models.entities import (
    Job,
    Membership,
    Org,
    Task,
    TaskTemplate,
    Template,
    User,
)
from app.models.exports import (
    Export,
    ExportRenderCandidate,
    ExportRun,
    ExportRunEvidence,
    ExportRunItem,
    ExportTemplateBinding,
)
from app.models.response_cards import DraftRun, ResponseCard, ResponseCardRevision, ResponseItem
from app.providers.storage import Storage
from app.schemas.contracts import Cost
from app.schemas.export_contracts import (
    ExportBindingCreate,
    ExportBindingView,
    ExportFile,
    ExportIssue,
    ExportPrepare,
    ExportPreview,
    ExportRelease,
    ExportRunView,
    ExportView,
)
from app.schemas.response_card_contracts import EvidenceInput
from app.services import drafts, evidence_sources, templates
from app.services import response_cards as cards
from app.services.auth import Identity
from app.services.resources import audit

MANIFEST_VERSION = "human-export-manifest-v1"
REQUIRED_SCOPES = ("export", "task:read", "draft:read", "card:read", "template:read")


class ExportStorage:
    """Classify local filesystem failures at the same boundary as S3 failures."""

    def __init__(self, storage: Storage):
        self.storage = storage

    async def read(self, org_id: UUID, key: str) -> bytes:
        try:
            return await self.storage.read(org_id, key)
        except OSError as exc:
            raise self.failure(exc) from exc

    async def read_bounded(self, org_id: UUID, key: str, max_bytes: int) -> bytes:
        try:
            return await self.storage.read_bounded(org_id, key, max_bytes)
        except OSError as exc:
            raise self.failure(exc) from exc

    async def put(self, org_id: UUID, key: str, content: bytes) -> None:
        try:
            await self.storage.put(org_id, key, content)
        except OSError as exc:
            raise self.failure(exc) from exc

    @staticmethod
    def failure(error: OSError) -> ServiceError:
        denied = error.errno in {errno.EACCES, errno.EPERM}
        return ServiceError(
            "storage_denied" if denied else "storage_unavailable",
            "Export object storage is unavailable",
            503,
            4 if denied else 3,
        )


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def timestamp(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def issue(code: str, severity: str, *, requirements=(), evidence=(), revision=None) -> dict:
    identity = {
        "code": code,
        "severity": severity,
        "requirement_ids": [str(x) for x in requirements],
        "evidence_ids": [str(x) for x in evidence],
        "revision": revision,
    }
    return ExportIssue.model_validate(
        {key: value for key, value in identity.items() if key != "revision"}
        | {"issue_id": digest(identity)}
    ).model_dump(mode="json")


async def collect_additional_refusal_issues(
    session: AsyncSession, actor: Identity, fixed_manifest: dict
) -> list[dict]:
    """Single extension point for additional evidence-source refusal gates."""
    # TODO(docs/plan/screenshots.md): integrate prototype_decision_required,
    # prototype_replacement_pending and prototype_decision_stale after the
    # screenshot stream merges. Decisions must affect the fixed input hash;
    # their internal reasons must never be rendered as visible evidence labels.
    return []


async def human_access(
    session: AsyncSession, actor: Identity, *, binding: str | None = None
) -> Identity:
    if actor.actor_kind != "session" or actor.token_id is not None:
        raise ServiceError("forbidden", "Human session required", 403, 4)
    actor = await cards.access(
        session,
        actor,
        "template:write" if binding == "write" else "template:read" if binding else "export",
    )
    roles = {"admin"} if binding == "write" else {"admin", "bidder"} if binding else {"bidder"}
    if actor.role not in roles:
        raise ServiceError("forbidden", "Current role cannot perform this export action", 403, 4)
    for scope in ("template:read",) if binding else REQUIRED_SCOPES:
        actor.require(scope)
    return actor


async def worker_access(session: AsyncSession, job: Job, attempt_id: UUID) -> Identity:
    now = await session.scalar(select(func.clock_timestamp()))
    if (
        job.status != "running"
        or job.run_id != attempt_id
        or job.lease_until is None
        or now is None
        or job.lease_until <= now
    ):
        raise ServiceError(
            "job_attempt_stopped", "Export render attempt no longer owns this job", 409, 4
        )
    submitted = job.result["submission"]
    actor = Identity(
        UUID(submitted["actor_user_id"]),
        job.org_id,
        set(submitted["scopes"]),
        "bidder",
        actor_kind="worker",
    )
    actor = await cards.access(session, actor, "export")
    if actor.role != "bidder" or actor.token_id is not None:
        raise ServiceError("forbidden", "Export initiator is no longer authorized", 403, 4)
    for scope in REQUIRED_SCOPES:
        actor.require(scope)
    await session.execute(
        text(
            "SELECT set_config('app.export_run_id', :run, true), set_config('app.export_attempt_id', :attempt, true)"
        ),
        {"run": submitted["export_run_id"], "attempt": str(job.run_id)},
    )
    return actor


async def lock_inputs(
    session: AsyncSession, actor: Identity, task_id: UUID, *, initiated_by: UUID | None = None
) -> None:
    session.expire_all()
    await cards.task_lock(session, task_id)
    # Serialize with the same task -> sorted cards order as review/selection changes.
    await session.scalars(
        select(ResponseCard)
        .where(ResponseCard.task_id == task_id)
        .order_by(ResponseCard.id)
        .with_for_update()
    )
    # Concurrent disabling or role changes must serialize with final publication.
    await session.scalar(select(Org).where(Org.id == actor.org_id).with_for_update(read=True))
    user_ids = sorted(
        {actor.user_id, initiated_by} if initiated_by is not None else {actor.user_id}
    )
    await session.scalars(
        select(User).where(User.id.in_(user_ids)).order_by(User.id).with_for_update(read=True)
    )
    await session.scalars(
        select(Membership)
        .where(Membership.org_id == actor.org_id, Membership.user_id.in_(user_ids))
        .order_by(Membership.user_id)
        .with_for_update(read=True)
    )


def headings(data: dict) -> list[str]:
    def walk(entries):
        for entry in entries:
            yield entry["title"]
            yield from walk(entry["children"])

    return list(walk(data["chapters"] or []))


async def inspect_template(content: bytes, sections: list[dict], registered_headings: list[str]):
    from app.services.export_renderer import ExportRenderError, validate_template

    try:
        return await asyncio.to_thread(validate_template, content, sections, registered_headings)
    except ExportRenderError as exc:
        raise ServiceError(
            exc.code, "Template does not satisfy the export adapter", 400, 2
        ) from exc


def binding_view(row: ExportTemplateBinding) -> dict:
    return ExportBindingView.model_validate(row, from_attributes=True).model_dump(mode="json")


async def create_binding(
    session: AsyncSession, actor: Identity, body: ExportBindingCreate, storage: Storage
):
    from app.services.export_renderer import ADAPTER_VERSION

    actor = await human_access(session, actor, binding="write")
    revision = await templates.require_revision(session, actor, body.template_revision_id)
    content, descriptor = await templates.read_revision(session, actor, revision.id, storage)
    if descriptor.sha256 != body.expected_template_sha256:
        raise ServiceError("export_template_hash_conflict", "Template hash changed", 409, 2)
    sections = [entry.model_dump(mode="json") for entry in body.sections]
    report = await inspect_template(content, sections, headings(revision.data))
    preview = {
        "dry_run": True,
        "template_revision_id": str(revision.id),
        "template_sha256": descriptor.sha256,
        "static_content_hash": report.static_content_hash,
        "adapter_version": ADAPTER_VERSION,
        "anchors": report.anchors,
        "issues": [],
    }
    if body.dry_run:
        return preview
    if body.expected_static_content_hash != report.static_content_hash:
        raise ServiceError(
            "export_static_hash_conflict", "Review the exact static template content first", 409, 2
        )
    binding_hash = digest(
        {
            "template_sha256": descriptor.sha256,
            "sections": sections,
            "static_content_hash": report.static_content_hash,
            "adapter_version": ADAPTER_VERSION,
        }
    )
    # Serialize duplicate bindings on the parent template: revisions are immutable and the
    # runtime role has no UPDATE privilege on them, which any row lock would require.
    await session.scalar(
        select(Template).where(Template.id == revision.template_id).with_for_update()
    )
    existing = await session.scalar(
        select(ExportTemplateBinding).where(
            ExportTemplateBinding.template_revision_id == revision.id,
            ExportTemplateBinding.binding_hash == binding_hash,
        )
    )
    if existing is not None:
        return binding_view(existing)
    row = ExportTemplateBinding(
        id=uuid4(),
        org_id=actor.org_id,
        template_revision_id=revision.id,
        template_sha256=descriptor.sha256,
        binding_hash=binding_hash,
        sections=sections,
        static_content_hash=report.static_content_hash,
        adapter_version=ADAPTER_VERSION,
        reviewed_by=actor.user_id,
        reviewed_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    audit(
        session,
        actor,
        "export.binding_created",
        row.id,
        {
            "actor_kind": actor.actor_kind,
            "binding_id": str(row.id),
            "template_revision_id": str(revision.id),
            "binding_hash": binding_hash,
        },
    )
    return binding_view(row)


async def list_bindings(session: AsyncSession, actor: Identity, revision_id: UUID):
    actor = await human_access(session, actor, binding="read")
    await templates.require_revision(session, actor, revision_id)
    rows = await session.scalars(
        select(ExportTemplateBinding)
        .where(ExportTemplateBinding.template_revision_id == revision_id)
        .order_by(ExportTemplateBinding.reviewed_at, ExportTemplateBinding.id)
    )
    return [binding_view(row) for row in rows]


async def build_manifest(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    body: ExportPrepare,
    storage: Storage,
    *,
    verify_files: bool,
    max_requirements: int = 2000,
    max_attachments: int = 300,
    renderer_profile: str | None = None,
) -> dict:
    """Resolve only fixed typed relations; never copy gap candidate content."""
    from app.services.export_renderer import RENDERER_PROFILE

    task = await session.get(Task, task_id)
    draft = await session.get(DraftRun, body.draft_id)
    selection = await session.get(TaskTemplate, body.task_template_id)
    binding = await session.get(ExportTemplateBinding, body.binding_id)
    if (
        task is None
        or draft is None
        or draft.task_id != task_id
        or selection is None
        or selection.task_id != task_id
        or binding is None
    ):
        raise not_found()
    revision = await templates.require_revision(session, actor, selection.template_revision_id)
    if (
        binding.template_revision_id != revision.id
        or binding.template_sha256 != revision.file["sha256"]
    ):
        raise ServiceError(
            "export_binding_mismatch",
            "Binding must reference the selected template revision",
            409,
            2,
        )
    extraction, requirements = await cards.extraction_scope(
        session, task_id, draft.extraction_job_id
    )
    view = await drafts.show_draft(session, actor, draft.id)
    issues = []
    if view["validity"] != "current":
        issues.append(
            issue(
                "export_stale_draft",
                "block",
                requirements=view["invalidated_requirements"],
                revision=str(draft.id),
            )
        )
    if not selection.active:
        issues.append(issue("export_template_replaced", "block", revision=str(selection.id)))
    if len(requirements) > max_requirements:
        issues.append(issue("export_requirement_limit", "block", revision=max_requirements))
    if verify_files:
        content, _ = await templates.read_revision(session, actor, revision.id, storage)
        report = await inspect_template(content, binding.sections, headings(revision.data))
        for field in report.metadata_fields:
            value = task.name if field == "task_name" else task.tender_number
            if not value:
                raise ServiceError(
                    "export_missing_task_metadata",
                    "A required template task field is empty",
                    400,
                    2,
                )
        if report.static_content_hash != binding.static_content_hash:
            raise ServiceError(
                "export_template_integrity", "Template binding failed integrity checks", 500, 4
            )
    for name in ("name", "tender_number"):
        # Marker usage is checked by the adapter in the file validation pass; the
        # renderer also refuses null fields if the template actually consumes them.
        if name == "name" and not task.name:
            issues.append(issue("export_missing_task_metadata", "block", revision=name))
    rows = list(
        (await session.scalars(select(ResponseItem).where(ResponseItem.draft_id == draft.id))).all()
    )
    indexed = {row.requirement_id: row for row in rows}
    if set(indexed) != {row.id for row in requirements} or len(rows) != len(indexed):
        raise ServiceError(
            "export_coverage_integrity", "Draft requirement coverage is incomplete", 500, 4
        )
    positions = {
        UUID(entry["requirement_id"]): n
        for n, entry in enumerate(draft.input_manifest["requirements"])
    }
    rows.sort(key=lambda row: positions[row.requirement_id])
    adapter = TypeAdapter(EvidenceInput)
    items, attachments = [], []
    pages = {}
    for ordinal, row in enumerate(rows, 1):
        requirement = next(entry for entry in requirements if entry.id == row.requirement_id)
        if not await cards.citation_valid(session, requirement):
            issues.append(
                issue(
                    "export_invalid_citation",
                    "block",
                    requirements=[row.requirement_id],
                    revision=drafts.digest(cards.source(requirement)),
                )
            )
        entry = {
            "response_item_id": str(row.id),
            "requirement_id": str(row.requirement_id),
            "card_id": str(row.card_id) if row.card_id else None,
            "card_revision_id": str(row.card_revision_id) if row.card_revision_id else None,
            "kind": row.kind,
            "ordinal": ordinal,
            "source": row.source,
            "source_hash": digest(row.source),
            "location_label": row.location_label,
            "category": row.category,
            "starred": row.starred,
            "evidence": [],
        }
        for code in cards.warnings_for(requirement):
            issues.append(
                issue(
                    code,
                    "acknowledge",
                    requirements=[row.requirement_id],
                    revision=str(row.card_revision_id),
                )
            )
        if row.kind == "row":
            if row.card_revision_id is None:
                raise ServiceError(
                    "export_confirmation_integrity", "Response revision is missing", 500, 4
                )
            reviewed = await session.get(ResponseCardRevision, row.card_revision_id)
            if (
                reviewed is None
                or reviewed.state != "confirmed"
                or reviewed.confirmed_by is None
                or reviewed.confirmed_at is None
            ):
                raise ServiceError(
                    "export_confirmation_integrity", "Response confirmation is incomplete", 500, 4
                )
            entry |= {
                "table": row.table,
                **{key: getattr(row, key) for key in cards.CONTENT_FIELDS},
                "confirmed_by": str(reviewed.confirmed_by),
                "confirmed_at": timestamp(reviewed.confirmed_at),
                "disposition_by": str(reviewed.disposition_by),
                "disposition_at": timestamp(reviewed.disposition_at),
                "quote_sha256": reviewed.quote_sha256,
            }
            if row.deviation == "negative":
                issues.append(
                    issue(
                        "export_negative_deviation",
                        "acknowledge",
                        requirements=[row.requirement_id],
                        revision=str(row.card_revision_id),
                    )
                )
            links = await cards.linked_evidence(session, row.card_revision_id)
            if (row.response_kind == "commitment" and links) or (
                row.response_kind == "evidence" and not links
            ):
                raise ServiceError(
                    "export_evidence_integrity", "Response evidence links are incomplete", 500, 4
                )
            for evidence in links:
                if evidence.confirmed_by is None or evidence.confirmed_at is None:
                    raise ServiceError(
                        "export_unconfirmed_evidence", "Response links unconfirmed evidence", 500, 4
                    )
                material = await cards.evidence_view(session, actor, evidence)
                material["confirmed_at"] = timestamp(evidence.confirmed_at)
                if not material["active_selection"]:
                    issues.append(
                        issue(
                            "export_material_replaced",
                            "block",
                            requirements=[row.requirement_id],
                            evidence=[evidence.id],
                            revision=material["selection_id"],
                        )
                    )
                if verify_files and material["active_selection"]:
                    await cards.resolve_material(
                        session, actor, task_id, adapter.validate_python(material["input"]), storage
                    )
                if evidence.kind == "certificate_pdf_page":
                    if (
                        evidence.quote_check != "human_page_review"
                        or evidence.evidence_source_id is None
                    ):
                        raise ServiceError(
                            "export_evidence_integrity", "Certificate page was not reviewed", 500, 4
                        )
                    archive, selected, original = await evidence_sources.require_source(
                        session, actor, evidence.evidence_source_id
                    )
                    if (
                        archive.task_id != task_id
                        or archive.preview["sha256"] != evidence.source_sha256
                        or archive.page != evidence.page
                        or archive.render_profile != "pdf-page-preview-v1"
                    ):
                        raise ServiceError(
                            "export_evidence_integrity",
                            "Certificate page relation is inconsistent",
                            500,
                            4,
                        )
                    if verify_files:
                        await evidence_sources.read_preview(session, actor, archive.id, storage)
                    page_key = digest(
                        {
                            "selection": str(selected.id),
                            "revision": str(archive.certificate_revision_id),
                            "original": original.file["sha256"],
                            "page": archive.page,
                            "png": archive.preview["sha256"],
                        }
                    )
                    if page_key not in pages:
                        attachment = {
                            "ordinal": len(attachments) + 1,
                            "label": f"E{len(attachments) + 1:03d}",
                            "selection_id": str(selected.id),
                            "revision_id": str(archive.certificate_revision_id),
                            "original_sha256": original.file["sha256"],
                            "page": archive.page,
                            "png_sha256": archive.preview["sha256"],
                            "size_bytes": archive.preview["size_bytes"],
                            "width": archive.preview["width_px"],
                            "height": archive.preview["height_px"],
                            "evidence_source_id": str(archive.id),
                            "material_kind": material["material_kind"],
                            "evidence_ids": [],
                            "requirement_ids": [],
                        }
                        attachments.append(attachment)
                        pages[page_key] = attachment
                    attachment = pages[page_key]
                    if str(evidence.id) not in attachment["evidence_ids"]:
                        attachment["evidence_ids"].append(str(evidence.id))
                    if str(row.requirement_id) not in attachment["requirement_ids"]:
                        attachment["requirement_ids"].append(str(row.requirement_id))
                    material["attachment_ordinal"] = attachment["ordinal"]
                else:
                    issues.append(
                        issue(
                            "export_declaration_material",
                            "acknowledge",
                            requirements=[row.requirement_id],
                            evidence=[evidence.id],
                            revision=material["resource_revision_id"],
                        )
                    )
                entry["evidence"].append(material)
        elif row.kind == "comply_only":
            entry |= {
                "disposition_by": str(row.disposition_by),
                "disposition_at": timestamp(row.disposition_at),
            }
        elif row.kind == "gap":
            entry["gap_reasons"] = row.gap_reasons
        else:
            raise ServiceError("export_coverage_integrity", "Unsupported draft item kind", 500, 4)
        items.append(entry)
    if any(entry["kind"] == "gap" for entry in items) and body.mode == "final_section":
        issues.append(
            issue(
                "export_gaps_present",
                "block",
                requirements=[entry["requirement_id"] for entry in items if entry["kind"] == "gap"],
                revision=str(draft.id),
            )
        )
    if len(attachments) > max_attachments:
        issues.append(issue("export_attachment_limit", "block", revision=max_attachments))
    for warning in await cards.scope_warnings(session, extraction.id):
        issues.append(issue(warning.split(":")[0], "acknowledge", revision=warning))
    if extraction.result.get("gap_fill", {}).get("remaining"):
        issues.append(
            issue(
                "export_extraction_omissions",
                "acknowledge",
                revision=digest(extraction.result["gap_fill"]["remaining"]),
            )
        )
    fixed = {
        "version": MANIFEST_VERSION,
        "org_id": str(actor.org_id),
        "mode": body.mode,
        "renderer_profile": RENDERER_PROFILE if renderer_profile is None else renderer_profile,
        "rule_version": draft.input_manifest["rule_version"],
        "task": {"id": str(task.id), "name": task.name, "tender_number": task.tender_number},
        "draft": {
            "id": str(draft.id),
            "input_hash": draft.input_hash,
            "extraction_job_id": str(extraction.id),
            "document_id": str(extraction.document_id),
        },
        "template": {
            "task_template_id": str(selection.id),
            "template_revision_id": str(revision.id),
            "sha256": revision.file["sha256"],
            "size_bytes": revision.file["size_bytes"],
            "lot": selection.lot,
            "binding_id": str(binding.id),
            "binding_hash": binding.binding_hash,
            "sections": binding.sections,
            "static_content_hash": binding.static_content_hash,
            "adapter_version": binding.adapter_version,
            "registered_headings": headings(revision.data),
        },
        "items": items,
        "attachments": attachments,
        "issues": issues,
    }
    fixed["issues"].extend(await collect_additional_refusal_issues(session, actor, fixed))
    fixed["issues"] = sorted(
        {entry["issue_id"]: entry for entry in fixed["issues"]}.values(),
        key=lambda entry: entry["issue_id"],
    )
    return fixed


def preview(fixed: dict) -> dict:
    items = fixed["items"]
    return ExportPreview.model_validate(
        {
            "task_id": fixed["task"]["id"],
            "draft_id": fixed["draft"]["id"],
            "mode": fixed["mode"],
            "input_hash": digest(fixed),
            "ready": not any(entry["severity"] == "block" for entry in fixed["issues"]),
            "requirement_count": len(items),
            "table_rows": {
                key: sum(row.get("table") == key for row in items) for key in drafts.TABLES
            },
            "comply_only_count": sum(row["kind"] == "comply_only" for row in items),
            "gap_count": sum(row["kind"] == "gap" for row in items),
            "negative_count": sum(row.get("deviation") == "negative" for row in items),
            "attachment_pages": len(fixed["attachments"]),
            "issues": fixed["issues"],
            "estimated_cost": Cost(),
        }
    ).model_dump(mode="json")


def ensure_ready(fixed: dict) -> None:
    blocks = [entry for entry in fixed["issues"] if entry["severity"] == "block"]
    if blocks:
        raise ServiceError(
            blocks[0]["code"],
            "Export inputs do not meet the requested mode gates",
            409 if any("stale" in i["code"] or "replaced" in i["code"] for i in blocks) else 400,
            2,
        )


def request_for(run: ExportRun) -> ExportPrepare:
    return ExportPrepare.model_validate(
        {
            "draft_id": run.draft_run_id,
            "task_template_id": run.task_template_id,
            "binding_id": run.binding_id,
            "mode": run.mode,
            "dry_run": True,
        }
    )


async def initiator_active(session: AsyncSession, run: ExportRun) -> bool:
    return bool(
        await session.scalar(
            select(Membership.id)
            .join(User, User.id == Membership.user_id)
            .join(Org, Org.id == Membership.org_id)
            .where(
                Membership.org_id == run.org_id,
                Membership.user_id == run.initiated_by,
                Membership.active.is_(True),
                Membership.role == "bidder",
                User.active.is_(True),
                Org.active.is_(True),
            )
        )
    )


async def fresh_manifest(
    session: AsyncSession, actor: Identity, run: ExportRun, storage: Storage, *, verify_files: bool
) -> dict:
    fixed = await build_manifest(
        session,
        actor,
        run.task_id,
        request_for(run),
        storage,
        verify_files=verify_files,
        renderer_profile=run.renderer_profile,
    )
    if digest(fixed) != run.input_hash or not await initiator_active(session, run):
        raise ServiceError(
            "export_input_changed", "Inputs changed; preview and submit a new export run", 409, 3
        )
    ensure_ready(fixed)
    full = {
        **fixed,
        "input_hash": run.input_hash,
        "acknowledged_issue_ids": run.acknowledged_issue_ids,
    }
    if full != run.manifest or digest(full) != run.manifest_hash:
        raise ServiceError(
            "export_manifest_integrity", "Fixed manifest failed integrity checks", 500, 4
        )
    return fixed


async def submit_export(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    body: ExportPrepare,
    storage: Storage,
    settings,
):
    actor = await human_access(session, actor)
    fixed = await build_manifest(
        session,
        actor,
        task_id,
        body,
        storage,
        verify_files=True,
        max_requirements=settings.export_max_requirements,
        max_attachments=settings.export_max_attachments,
    )
    if body.dry_run:
        return preview(fixed), None
    ensure_ready(fixed)
    if body.expected_input_hash != digest(fixed):
        raise ServiceError(
            "export_input_hash_conflict",
            "Preview the current fixed inputs before preparing",
            409,
            2,
        )
    expected_ack = sorted(
        entry["issue_id"] for entry in fixed["issues"] if entry["severity"] == "acknowledge"
    )
    if sorted(body.acknowledged_issue_ids) != expected_ack:
        raise ServiceError(
            "export_acknowledgment_mismatch", "Acknowledge exactly the current warning IDs", 400, 2
        )
    await lock_inputs(session, actor, task_id)
    actor = await human_access(session, actor)
    current = await build_manifest(
        session,
        actor,
        task_id,
        body,
        storage,
        verify_files=False,
        max_requirements=settings.export_max_requirements,
        max_attachments=settings.export_max_attachments,
    )
    if current != fixed:
        raise ServiceError(
            "export_input_changed", "Inputs changed during preparation; preview again", 409, 3
        )
    input_hash = digest(fixed)
    manifest = {**fixed, "input_hash": input_hash, "acknowledged_issue_ids": expected_ack}
    cache_key = digest(
        {"kind": "export_render", "input_hash": input_hash, "actor_user_id": str(actor.user_id)}
    )
    job = await session.scalar(select(Job).where(Job.cache_key == cache_key).with_for_update())
    if job is not None:
        run = await session.scalar(select(ExportRun).where(ExportRun.render_job_id == job.id))
        if run is None:
            raise ServiceError(
                "export_relation_integrity", "Saved render job has no export run", 500, 4
            )
        if body.retry:
            if job.status not in {"failed", "cancelled"} and not (
                job.status == "running" and job.lease_until and job.lease_until < datetime.now(UTC)
            ):
                raise ServiceError(
                    "export_retry_state",
                    "Retry requires a failed, cancelled or expired attempt",
                    409,
                    2,
                )
            job.status, job.error, job.queue_id, job.attempts = "queued", None, None, 0
            job.lease_until, job.finished_at, job.run_id = None, None, None
        return await run_view(session, actor, run, storage, fixed=fixed), job
    run_id, job_id = uuid4(), uuid4()
    job = Job(
        id=job_id,
        org_id=actor.org_id,
        task_id=task_id,
        document_id=UUID(fixed["draft"]["document_id"]),
        kind="export_render",
        cache_key=cache_key,
        status="queued",
        result={
            "submission": {
                "export_run_id": str(run_id),
                "actor_user_id": str(actor.user_id),
                "scopes": sorted(actor.scopes),
            },
            "cost": Cost().model_dump(mode="json"),
        },
    )
    session.add(job)
    await session.flush()
    run = ExportRun(
        id=run_id,
        org_id=actor.org_id,
        task_id=task_id,
        extraction_job_id=UUID(fixed["draft"]["extraction_job_id"]),
        document_id=job.document_id,
        draft_run_id=body.draft_id,
        task_template_id=body.task_template_id,
        template_revision_id=UUID(fixed["template"]["template_revision_id"]),
        binding_id=body.binding_id,
        render_job_id=job.id,
        mode=body.mode,
        input_hash=input_hash,
        manifest_hash=digest(manifest),
        renderer_profile=fixed["renderer_profile"],
        manifest=manifest,
        issue_snapshot=fixed["issues"],
        acknowledged_issue_ids=expected_ack,
        initiated_by=actor.user_id,
        initiated_at=datetime.now(UTC),
    )
    session.add(run)
    await session.flush()
    for item in fixed["items"]:
        stored = ExportRunItem(
            id=uuid4(),
            org_id=actor.org_id,
            run_id=run.id,
            draft_run_id=run.draft_run_id,
            response_item_id=UUID(item["response_item_id"]),
            requirement_id=UUID(item["requirement_id"]),
            card_revision_id=UUID(item["card_revision_id"]) if item["card_revision_id"] else None,
            kind=item["kind"],
            ordinal=item["ordinal"],
        )
        session.add(stored)
        await session.flush()
        for evidence in item["evidence"]:
            session.add(
                ExportRunEvidence(
                    id=uuid4(),
                    org_id=actor.org_id,
                    run_id=run.id,
                    run_item_id=stored.id,
                    card_revision_id=stored.card_revision_id,
                    evidence_id=UUID(evidence["id"]),
                    attachment_ordinal=evidence.get("attachment_ordinal"),
                )
            )
    await session.flush()
    audit(
        session,
        actor,
        "export.prepared",
        run.id,
        {
            "actor_kind": actor.actor_kind,
            "task_id": str(task_id),
            "draft_id": str(body.draft_id),
            "binding_id": str(body.binding_id),
            "run_id": str(run.id),
            "input_hash": input_hash,
            "mode": body.mode,
        },
    )
    return await run_view(session, actor, run, storage, fixed=fixed), job


async def current_validity(
    session: AsyncSession, actor: Identity, run: ExportRun, storage: Storage
) -> tuple[str, list[dict], list[str]]:
    fixed = await build_manifest(
        session,
        actor,
        run.task_id,
        request_for(run),
        storage,
        verify_files=False,
        renderer_profile=run.renderer_profile,
    )
    active = await initiator_active(session, run)
    if digest(fixed) == run.input_hash and active:
        return "current", run.issue_snapshot, []
    changed = issue(
        "export_input_changed" if active else "export_initiator_inactive",
        "block",
        revision=run.input_hash,
    )
    old = {entry["requirement_id"]: entry for entry in run.manifest["items"]}
    affected = [
        entry["requirement_id"]
        for entry in fixed["items"]
        if old.get(entry["requirement_id"]) != entry
    ]
    affected_ids = set(affected)
    for finding in fixed["issues"]:
        if finding["severity"] == "block":
            affected_ids.update(finding["requirement_ids"])
    affected = [
        entry["requirement_id"]
        for entry in fixed["items"]
        if entry["requirement_id"] in affected_ids
    ]
    return "stale", [*fixed["issues"], changed], affected


async def run_view(
    session: AsyncSession,
    actor: Identity,
    run: ExportRun,
    storage: Storage,
    *,
    fixed: dict | None = None,
) -> dict:
    validity, issues, _ = (
        await current_validity(session, actor, run, storage)
        if fixed is None
        else ("current", fixed["issues"], [])
    )
    job = await session.get(Job, run.render_job_id)
    if job is None:
        raise ServiceError("export_relation_integrity", "Render job is missing", 500, 4)
    published = await session.scalar(select(Export).where(Export.run_id == run.id))
    candidate = (
        await session.scalar(
            select(ExportRenderCandidate).where(
                ExportRenderCandidate.run_id == run.id,
                ExportRenderCandidate.attempt_id == job.run_id,
            )
        )
        if job.run_id
        else None
    )
    if validity == "stale":
        state = "invalidated"
    elif published:
        state = "released"
    elif job.status == "succeeded" and candidate:
        state = "awaiting_release"
    else:
        state = {
            "queued": "queued",
            "running": "rendering",
            "failed": "failed",
            "cancelled": "cancelled",
        }.get(job.status)
        if state is None:
            raise ServiceError(
                "export_candidate_integrity", "Successful job has no candidate", 500, 4
            )
    return ExportRunView.model_validate(
        {
            "id": run.id,
            "org_id": run.org_id,
            "task_id": run.task_id,
            "draft_id": run.draft_run_id,
            "render_job_id": run.render_job_id,
            "mode": run.mode,
            "input_hash": run.input_hash,
            "state": state,
            "candidate_sha256": candidate.plaintext_sha256
            if candidate and state in {"awaiting_release", "released"}
            else None,
            "export_id": published.id if published else None,
            "issues": issues,
        }
    ).model_dump(mode="json")


async def get_run(
    session: AsyncSession, actor: Identity, run_id: UUID
) -> tuple[Identity, ExportRun]:
    actor = await human_access(session, actor)
    run = await session.get(ExportRun, run_id)
    if run is None:
        raise not_found()
    return actor, run


def file_view(row: Export) -> dict:
    return ExportFile(
        name=f"response-{row.mode}-{row.file_sha256[:12]}.docx",
        sha256=row.file_sha256,
        size_bytes=row.size_bytes,
    ).model_dump(mode="json")


async def export_view(
    session: AsyncSession, actor: Identity, row: Export, storage: Storage
) -> dict:
    run = await session.get(ExportRun, row.run_id)
    if run is None:
        raise not_found()
    validity, issues, affected = await current_validity(session, actor, run, storage)
    return ExportView.model_validate(
        {
            "id": row.id,
            "org_id": row.org_id,
            "task_id": row.task_id,
            "run_id": run.id,
            "draft_id": run.draft_run_id,
            "task_template_id": run.task_template_id,
            "template_revision_id": run.template_revision_id,
            "binding_id": run.binding_id,
            "mode": row.mode,
            "completion": "partial" if row.mode == "review_copy" else "complete",
            "validity": validity,
            "input_hash": row.input_hash,
            "manifest_hash": row.manifest_hash,
            "file": file_view(row),
            "released_by": row.released_by,
            "released_at": row.released_at,
            "issues": issues,
            "invalidated_requirement_ids": affected,
        }
    ).model_dump(mode="json")


async def get_export(
    session: AsyncSession, actor: Identity, export_id: UUID
) -> tuple[Identity, Export]:
    actor = await human_access(session, actor)
    row = await session.get(Export, export_id)
    if row is None:
        raise not_found()
    return actor, row


async def checked_content(
    actor: Identity, key: str, size: int, sha256: str, storage: Storage
) -> bytes:
    content = await storage.read(actor.org_id, key)
    if len(content) != size or hashlib.sha256(content).hexdigest() != sha256:
        raise ServiceError(
            "export_file_integrity", "Export file failed length or hash verification", 500, 4
        )
    return content


async def release(
    session: AsyncSession, actor: Identity, run_id: UUID, body: ExportRelease, storage: Storage
):
    actor, run = await get_run(session, actor, run_id)
    await fresh_manifest(session, actor, run, storage, verify_files=True)
    if body.expected_input_hash != run.input_hash:
        raise ServiceError(
            "export_input_hash_conflict", "Release input hash does not match the run", 409, 2
        )
    job = await session.get(Job, run.render_job_id)
    candidate = (
        await session.scalar(
            select(ExportRenderCandidate).where(
                ExportRenderCandidate.run_id == run.id,
                ExportRenderCandidate.attempt_id == job.run_id,
            )
        )
        if job
        else None
    )
    if job is None or job.status != "succeeded" or candidate is None:
        raise ServiceError(
            "export_not_ready", "A successful current render candidate is required", 409, 2
        )
    if candidate.plaintext_sha256 != body.expected_candidate_sha256:
        raise ServiceError(
            "export_candidate_hash_conflict",
            "Review the exact candidate hash before release",
            409,
            2,
        )
    different = await session.scalar(
        select(ExportRenderCandidate.id)
        .where(
            ExportRenderCandidate.input_hash == run.input_hash,
            ExportRenderCandidate.renderer_profile == run.renderer_profile,
            ExportRenderCandidate.plaintext_sha256 != candidate.plaintext_sha256,
        )
        .limit(1)
    )
    if different is not None:
        raise ServiceError(
            "export_nondeterministic", "Same fixed inputs produced different DOCX bytes", 500, 4
        )
    content = await checked_content(
        actor, candidate.object_key, candidate.size_bytes, candidate.plaintext_sha256, storage
    )
    existing = await session.scalar(select(Export).where(Export.run_id == run.id))
    export_id = existing.id if existing else uuid4()
    key = f"org/{actor.org_id}/exports/{export_id}/{candidate.plaintext_sha256}.docx"
    if existing is None:
        await storage.put(actor.org_id, key, content)
    await lock_inputs(session, actor, run.task_id, initiated_by=run.initiated_by)
    await session.refresh(run)
    actor = await human_access(session, actor)
    await fresh_manifest(session, actor, run, storage, verify_files=False)
    await session.refresh(candidate)
    # Export runs are append-only and cannot be row-locked by the runtime role; the run's
    # single render job row below serializes release instead.
    locked_job = await session.scalar(
        select(Job)
        .where(Job.id == run.render_job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        locked_job is None
        or locked_job.status != "succeeded"
        or locked_job.run_id != candidate.attempt_id
    ):
        raise ServiceError("export_input_changed", "Render attempt changed before release", 409, 3)
    existing = await session.scalar(select(Export).where(Export.run_id == run.id))
    if existing is not None:
        return await export_view(session, actor, existing, storage)
    row = Export(
        id=export_id,
        org_id=actor.org_id,
        run_id=run.id,
        candidate_id=candidate.id,
        task_id=run.task_id,
        mode=run.mode,
        input_hash=run.input_hash,
        manifest_hash=run.manifest_hash,
        file_sha256=candidate.plaintext_sha256,
        size_bytes=candidate.size_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        object_key=key,
        released_by=actor.user_id,
        released_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    audit(
        session,
        actor,
        "export.released",
        row.id,
        {
            "actor_kind": actor.actor_kind,
            "task_id": str(run.task_id),
            "draft_id": str(run.draft_run_id),
            "run_id": str(run.id),
            "export_id": str(row.id),
            "input_hash": run.input_hash,
            "file_sha256": row.file_sha256,
            "mode": row.mode,
            "acknowledged_issue_ids": run.acknowledged_issue_ids,
        },
    )
    return await export_view(session, actor, row, storage)


async def download_gate(session: AsyncSession, actor: Identity, row: Export, storage: Storage):
    run = await session.get(ExportRun, row.run_id)
    if run is None:
        raise not_found()
    await fresh_manifest(session, actor, run, storage, verify_files=True)
    content = await checked_content(actor, row.object_key, row.size_bytes, row.file_sha256, storage)
    await lock_inputs(session, actor, run.task_id, initiated_by=run.initiated_by)
    await session.refresh(run)
    actor = await human_access(session, actor)
    await fresh_manifest(session, actor, run, storage, verify_files=False)
    await session.refresh(row)
    return actor, content


async def job_access(
    session: AsyncSession, actor: Identity, job: Job, storage: Storage, *, cancel=False
) -> dict:
    actor = await human_access(session, actor)
    run = await session.scalar(select(ExportRun).where(ExportRun.render_job_id == job.id))
    if run is None:
        raise not_found()
    view = await run_view(session, actor, run, storage)
    if cancel:
        if view["export_id"] is not None:
            raise ServiceError("terminal_job", "Released exports cannot be cancelled", 409, 2)
        audit(
            session,
            actor,
            "export.render_cancelled",
            run.id,
            {
                "actor_kind": actor.actor_kind,
                "run_id": str(run.id),
                "render_job_id": str(job.id),
                "input_hash": run.input_hash,
            },
        )
    return view
