"""Confirmed-draft assessment inputs, with no candidate text or model access."""

import asyncio
from dataclasses import dataclass
from datetime import date
from typing import NoReturn
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import not_found
from app.models.confidential import ConfidentialField
from app.models.entities import (
    ApiToken,
    CertificateRevision,
    Job,
    Membership,
    Org,
    Task,
    TaskCertificate,
    User,
)
from app.models.response_cards import DraftRun, Evidence, ResponseCard, ResponseItem
from app.providers.storage import Storage
from app.schemas.certificate_contracts import CertificateData
from app.schemas.response_card_contracts import EvidenceInput
from app.services import certificates, confidential, drafts, redaction
from app.services import response_cards as cards
from app.services.auth import Identity
from app.services.card_generation import check_input_access as generation_input_access
from app.services.evidence_sources import require_source
from app.services.extraction import locate_quote

RULE_VERSION = "check-rules-v2"
SCHEMA_VERSION = "check-v1"
ADAPTER_VERSION = "check-local-v1"
MAX_REQUIREMENTS = 2000
LIMITATIONS = [
    "confirmed_draft_only",
    "semantic_not_checked",
    "coverage_relative_to_saved_requirements",
    "comply_only_is_not_material_proof",
    "certificate_dates_are_declarations",
    "document_layout_signatures_and_attachments_not_checked",
]


@dataclass
class CheckSnapshot:
    draft: DraftRun
    extraction: Job
    manifest: dict
    secret: dict
    input_hash: str
    limitations: list[str]


async def access(session: AsyncSession, actor: Identity, scope: str) -> Identity:
    actor = await cards.access(session, actor, scope)
    actor.require("draft:read")
    actor.require("card:read")
    return actor


async def lock_inputs(session: AsyncSession, actor: Identity, task_id: UUID) -> None:
    """Match review's task/card ordering and freeze live authorization/value revisions."""
    session.expire_all()
    await cards.task_lock(session, task_id)
    await session.scalars(
        select(ResponseCard)
        .where(ResponseCard.task_id == task_id)
        .order_by(ResponseCard.id)
        .with_for_update(read=True)
    )
    await session.scalar(select(Org).where(Org.id == actor.org_id).with_for_update(read=True))
    await session.scalar(select(User).where(User.id == actor.user_id).with_for_update(read=True))
    await session.scalar(
        select(Membership).where(Membership.user_id == actor.user_id).with_for_update(read=True)
    )
    if actor.token_id is not None:
        await session.scalar(
            select(ApiToken).where(ApiToken.id == actor.token_id).with_for_update(read=True)
        )
    # Value writers lock their field before appending a version. Include archived
    # fields so restoring one cannot race the final input comparison.
    await session.scalars(
        select(ConfidentialField).order_by(ConfidentialField.id).with_for_update(read=True)
    )


def integrity() -> NoReturn:
    cards.fail(
        "check_input_integrity", "Draft input relationships or confirmation are invalid", 409, 4
    )


async def require_dependencies(
    session: AsyncSession, actor: Identity, task_id: UUID, manifest: dict, storage: Storage
) -> DraftRun:
    """Authorize the fixed graph even when an old report's inputs are now stale."""
    draft = await session.get(DraftRun, UUID(manifest["draft_id"]))
    if draft is None or draft.task_id != task_id or str(draft.org_id) != manifest["org_id"]:
        raise not_found()
    extraction = await session.get(Job, draft.extraction_job_id)
    if (
        extraction is None
        or extraction.task_id != task_id
        or str(extraction.id) != manifest["extraction_job_id"]
        or str(extraction.document_id) != manifest["document_id"]
    ):
        raise not_found()
    if manifest["certificates"]:
        actor.require("certificate:read")
    for entry in manifest["certificates"]:
        selected = await session.get(TaskCertificate, UUID(entry["task_certificate_id"]))
        if (
            selected is None
            or selected.task_id != task_id
            or str(selected.certificate_revision_id) != entry["certificate_revision_id"]
        ):
            raise not_found()
    if manifest["confidential"]:
        actor.require("confidential:read")
    for entry in manifest["items"]:
        row = await session.get(ResponseItem, UUID(entry["response_item_id"]))
        if (
            row is None
            or row.draft_id != draft.id
            or str(row.requirement_id) != entry["requirement_id"]
        ):
            raise not_found()
        for dependency in entry["evidence"]:
            evidence = await session.get(Evidence, UUID(dependency["id"]))
            if evidence is None or evidence.task_id != task_id or evidence.card_id != row.card_id:
                raise not_found()
            if entry["partition"] == "response" and (
                evidence.confirmed_by is None or evidence.confirmed_at is None
            ):
                integrity()
            # This checks material scopes and parent locations, but an inactive
            # historical selection remains readable. No candidate view is returned.
            await cards.evidence_view(session, actor, evidence)
        if entry.get("generation_dependencies"):
            await generation_input_access(session, actor, task_id, entry["generation_dependencies"])
    return draft


async def confirmed_material(
    session: AsyncSession,
    actor: Identity,
    row: Evidence,
    extraction_id: UUID,
    storage: Storage,
    *,
    semantic: bool = False,
) -> tuple[dict, dict]:
    if row.confirmed_by is None or row.confirmed_at is None:
        integrity()
    view = await cards.evidence_view(session, actor, row)
    if not view["active_selection"]:
        cards.fail("check_stale_draft", "Draft material selection changed; assemble again", 409)
    if row.kind == "image_region":
        from app.services.screenshots import validate_image_evidence

        await validate_image_evidence(session, actor, row, storage=storage)
        # Human visual observations are not literal image text and cannot become
        # a verified text citation in a check report.
        content = {"id": str(row.id), "kind": row.kind, "quote": None}
    else:
        body = TypeAdapter(EvidenceInput).validate_python(view["input"])
        await cards.resolve_material(
            session, actor, row.task_id, body, storage, extraction_job_id=extraction_id
        )
        if row.kind == "certificate_pdf_page":
            assert row.evidence_source_id is not None
            archive, _, original = await require_source(session, actor, row.evidence_source_id)
            content_bytes = await storage.read(actor.org_id, original.storage_key)
            original_text = await asyncio.to_thread(cards.page_text, content_bytes, archive.page)
        else:
            _, revision_model, _, revision_field, _, _ = cards.MATERIALS[row.kind]
            revision = await session.get(revision_model, getattr(row, revision_field))
            if revision is None:
                raise not_found()
            original_text = revision.data[row.field_path]
        if row.quote is None or locate_quote(original_text, row.quote)[0] != row.quote:
            cards.fail(
                "invalid_input_citation", "Confirmed evidence quote cannot be verified", 409, 4
            )
        content = {
            "id": str(row.id),
            "kind": row.kind,
            "quote": row.quote,
            **({"original_text": original_text} if semantic else {}),
        }
    content |= {
        "task_certificate_id": str(row.task_certificate_id) if row.task_certificate_id else None,
        "certificate_revision_id": (
            str(row.certificate_revision_id) if row.certificate_revision_id else None
        ),
    }
    return view, content


async def snapshot(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    draft_id: UUID,
    assessment_date: date,
    storage: Storage,
    *,
    semantic: bool = False,
) -> CheckSnapshot:
    task = await session.get(Task, task_id)
    draft = await session.get(DraftRun, draft_id)
    if task is None or draft is None or draft.task_id != task_id:
        raise not_found()
    extraction, requirements = await cards.extraction_scope(
        session, task_id, draft.extraction_job_id
    )
    if len(requirements) > MAX_REQUIREMENTS:
        cards.fail("check_requirement_limit", "A check supports at most 2000 requirements")
    rows = list(
        (await session.scalars(select(ResponseItem).where(ResponseItem.draft_id == draft.id))).all()
    )
    by_requirement = {row.requirement_id: row for row in rows}
    ids = {requirement.id for requirement in requirements}
    manifest_ids = [entry["requirement_id"] for entry in draft.input_manifest["requirements"]]
    if (
        len(rows) != len(ids)
        or set(by_requirement) != ids
        or len(manifest_ids) != len(ids)
        or set(manifest_ids) != {str(value) for value in ids}
        or draft.input_manifest.get("task_id") != str(task_id)
        or draft.input_manifest.get("extraction_job_id") != str(extraction.id)
        or drafts.digest(draft.input_manifest) != draft.input_hash
    ):
        integrity()
    # Check this before projecting a draft: a corrupt row must be a hard input
    # failure even if a current-card projection would merely mark it stale.
    for row in rows:
        if row.kind == "row" and row.card_revision_id is not None:
            linked = await cards.linked_evidence(session, row.card_revision_id)
            if any(e.confirmed_by is None or e.confirmed_at is None for e in linked):
                integrity()
    batch, grouped, current = await drafts.load_draft_reads(
        session, actor, [draft], requirements, storage
    )
    for requirement in requirements:
        if not batch.citation_valid(requirement):
            cards.fail("invalid_input_citation", "Requirement source cannot be verified", 409, 4)
    view = drafts.draft_view(draft, grouped[draft.id], batch, current)
    if view["validity"] != "current":
        cards.fail(
            "check_stale_draft", "Choose a current draft; assemble changed inputs again", 409
        )
    items, fixed_items = [], []
    limitations = list(LIMITATIONS)
    if any(value.state != "confirmed" for value in batch.requirement_reviews.values()):
        limitations.append("unconfirmed_tender_interpretations_are_provisional")
    for requirement in requirements:
        row = by_requirement[requirement.id]
        revision = batch.revisions.get(row.card_revision_id) if row.card_revision_id else None
        if (
            row.kind not in {"row", "comply_only", "gap"}
            or row.source != cards.source(requirement)
            or row.category != requirement.category
            or row.starred != requirement.starred
            or (row.card_id is not None and (revision is None or revision.card_id != row.card_id))
        ):
            integrity()
        domain = revision.review_domain if revision is not None else None
        item = {
            "requirement_id": str(requirement.id),
            "response_item_id": str(row.id),
            "card_revision_id": str(row.card_revision_id) if row.card_revision_id else None,
            "partition": "response" if row.kind == "row" else row.kind,
            "source": cards.source(requirement),
            "category": requirement.category,
            "starred": requirement.starred,
            "review_domain": domain,
            "gap_reasons": row.gap_reasons or [],
            "evidence": [],
        }
        if semantic:
            item["tender_original"] = (
                batch.chunks[requirement.chunk_id].text
                if requirement.page is not None
                else next(
                    block["text"]
                    for block in batch.chunks[requirement.chunk_id].blocks or []
                    if {key: value for key, value in block.items() if key != "text"}
                    == requirement.location
                )
            )
        dependencies = []
        for evidence in batch.links.get(row.card_revision_id, []) if row.card_revision_id else []:
            material_view = batch.evidence_view(evidence)
            dependencies.append(drafts.evidence_dependency(material_view))
            if row.kind == "row":
                _, content = await confirmed_material(
                    session, actor, evidence, extraction.id, storage, semantic=semantic
                )
                item["evidence"].append(content)
                if (
                    evidence.kind == "image_region"
                    and "image_contents_not_checked" not in limitations
                ):
                    limitations.append("image_contents_not_checked")
        generation_dependencies = None
        if revision is not None and revision.model_job_id is not None:
            generation_manifest = batch.generation_manifests.get(revision.model_job_id)
            if generation_manifest is None:
                integrity()
            assert generation_manifest is not None
            generation_dependencies = {
                "materials": generation_manifest["materials"],
                "unavailable_pages": generation_manifest["unavailable_pages"],
            }
        if row.kind == "row":
            if (
                revision is None
                or revision.state != "confirmed"
                or revision.confirmed_by is None
                or any(getattr(row, key) != getattr(revision, key) for key in cards.CONTENT_FIELDS)
                or (row.response_kind == "evidence" and not item["evidence"])
                or (row.response_kind == "commitment" and item["evidence"])
            ):
                integrity()
            item |= {key: getattr(row, key) for key in cards.CONTENT_FIELDS}
        items.append(item)
        fixed_items.append(
            {
                "requirement_id": str(requirement.id),
                "document_id": str(requirement.document_id),
                "chunk_id": str(requirement.chunk_id),
                "chunk_sha256": drafts.digest(
                    {
                        "text": batch.chunks[requirement.chunk_id].text,
                        "blocks": batch.chunks[requirement.chunk_id].blocks,
                        "page": batch.chunks[requirement.chunk_id].page,
                        "citation_verified": batch.chunks[requirement.chunk_id].citation_verified,
                    }
                ),
                "source_sha256": drafts.digest(cards.source(requirement)),
                "requirement_sha256": drafts.digest(
                    {
                        "text": requirement.text,
                        "condition": requirement.condition,
                        "category": requirement.category,
                        "starred": requirement.starred,
                    }
                ),
                "response_item_id": str(row.id),
                "card_id": str(row.card_id) if row.card_id else None,
                "card_revision_id": item["card_revision_id"],
                "review_domain": domain,
                "partition": item["partition"],
                "gap_reasons": item["gap_reasons"],
                "content_sha256": drafts.digest(item),
                "evidence": dependencies,
                "generation_dependencies": generation_dependencies,
            }
        )
    selected_certificates = list(
        (
            await session.scalars(
                select(TaskCertificate)
                .where(TaskCertificate.task_id == task_id, TaskCertificate.active.is_(True))
                .order_by(TaskCertificate.id)
            )
        ).all()
    )
    if selected_certificates:
        actor.require("certificate:read")
    certificate_inputs = []
    for selected in selected_certificates:
        revision = await session.get(CertificateRevision, selected.certificate_revision_id)
        if revision is None:
            raise not_found()
        data = CertificateData.model_validate(revision.data)
        requirement_ids = [
            item["requirement_id"]
            for item in items
            if any(
                evidence["task_certificate_id"] == str(selected.id) for evidence in item["evidence"]
            )
        ]
        certificate_inputs.append(
            {
                "task_certificate_id": str(selected.id),
                "certificate_revision_id": str(revision.id),
                "data_sha256": drafts.digest(revision.data),
                "valid_from": data.valid_from.isoformat() if data.valid_from else None,
                "valid_until": data.valid_until.isoformat() if data.valid_until else None,
                "date_status": certificates.inspect_dates(data, assessment_date)["state"],
                "requirement_ids": requirement_ids,
            }
        )
        if not requirement_ids and "unmapped_certificate_dates" not in limitations:
            limitations.append("unmapped_certificate_dates")
    registered = await confidential.task_entries(session, task_id)
    if registered:
        actor.require("confidential:read")
    fixed_fields = [
        {
            "field_id": str(entry.field.id),
            "field_revision": entry.field.revision,
            "value_id": str(entry.value.id) if entry.value else None,
        }
        for _, entry in sorted(registered.items())
    ]
    warning_codes = await cards.scope_warnings(session, extraction.id)
    limitations.extend(warning_codes)
    manifest = {
        "org_id": str(actor.org_id),
        "task_id": str(task_id),
        "draft_id": str(draft.id),
        "draft_input_hash": draft.input_hash,
        "requirement_reviews": {key: entry["requirement_review"] for key, entry in current.items()},
        "extraction_job_id": str(extraction.id),
        "document_id": str(extraction.document_id),
        "document_sha256": batch.documents[requirements[0].document_id].sha256,
        "assessment_date": assessment_date.isoformat(),
        "mode": "rules",
        "rule_version": RULE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "prompt_version": None,
        "model": None,
        "reasoning": None,
        "items": fixed_items,
        "certificates": certificate_inputs,
        "warnings": warning_codes,
        "model_redaction_enabled": task.model_redaction_enabled,
        "model_redaction_revision": task.model_redaction_revision,
        "redaction_rule_version": redaction.RULE_VERSION,
        "confidential": fixed_fields,
        "review_roles": {"commercial": "bidder", "technical": "technical"},
    }
    return CheckSnapshot(
        draft,
        extraction,
        manifest,
        {"items": items, "certificates": certificate_inputs},
        drafts.digest(manifest),
        limitations,
    )
