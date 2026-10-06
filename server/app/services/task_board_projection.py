"""Metadata-only graph loaded in a fixed number of queries for board counts.

Consumes the same pure citation and card eligibility rules as CardReadBatch.
Byte-integrity consumption gates remain at review/draft/export, while this read
uses current relational lineage and never loads response prose into its output.
"""

from datetime import date
from uuid import UUID

from sqlalchemy import func, literal, select, union_all

from app.core.errors import ServiceError
from app.models.entities import (
    Chunk,
    EvidenceSource,
    Requirement,
    TaskCertificate,
    TaskFeature,
    TaskOrgProfile,
    TaskResource,
)
from app.models.response_cards import (
    CardEvidenceLink,
    CardGenerationRun,
    Evidence,
    ResponseCard,
    ResponseCardRevision,
)
from app.models.screenshots import (
    PrototypeEvidenceDecision,
    ScreenshotAsset,
    ScreenshotPrivacyReview,
    ScreenshotPrototypeRun,
    ScreenshotRendition,
    ScreenshotVendorArchive,
    ScreenshotWithdrawal,
)
from app.models.team_workflow import CardCommentMention, CardCommentThread, RequirementWorkflow
from app.schemas.screenshot_contracts import ContentMapping, ImageEvidenceInput
from app.services import response_cards


async def requirements_with_collaboration(session, actor, task_id, extraction, *, limit, member):
    """Load assignment, thread counts and personal mentions with the requirement query.

    Grouping is task-scoped and indexed; no card/comment bodies are loaded for the
    board. A mention only resolves for a current human member, never a recovery
    admin or a token acting for that user.
    """
    threads = (
        select(ResponseCard.requirement_id, func.count(CardCommentThread.id).label("count"))
        .join(
            CardCommentThread,
            (CardCommentThread.org_id == ResponseCard.org_id)
            & (CardCommentThread.task_id == ResponseCard.task_id)
            & (CardCommentThread.card_id == ResponseCard.id),
        )
        .where(
            ResponseCard.org_id == actor.org_id,
            ResponseCard.task_id == task_id,
            ResponseCard.extraction_job_id == extraction.id,
        )
        .group_by(ResponseCard.requirement_id)
        .subquery()
    )
    mentions = (
        select(ResponseCard.requirement_id, CardCommentMention.thread_id)
        .join(
            CardCommentMention,
            (CardCommentMention.org_id == ResponseCard.org_id)
            & (CardCommentMention.task_id == ResponseCard.task_id)
            & (CardCommentMention.card_id == ResponseCard.id),
        )
        .where(
            ResponseCard.org_id == actor.org_id,
            ResponseCard.task_id == task_id,
            ResponseCard.extraction_job_id == extraction.id,
            CardCommentMention.user_id == actor.user_id,
            literal(
                member is not None and actor.actor_kind == "session" and actor.token_id is None
            ),
        )
        .distinct(ResponseCard.requirement_id)
        .order_by(ResponseCard.requirement_id, CardCommentMention.created_at, CardCommentMention.id)
        .subquery()
    )
    return (
        await session.execute(
            select(
                Requirement,
                RequirementWorkflow,
                func.coalesce(threads.c.count, 0),
                mentions.c.thread_id,
            )
            .outerjoin(
                RequirementWorkflow,
                (RequirementWorkflow.org_id == Requirement.org_id)
                & (RequirementWorkflow.task_id == Requirement.task_id)
                & (RequirementWorkflow.extraction_job_id == Requirement.job_id)
                & (RequirementWorkflow.requirement_id == Requirement.id),
            )
            .outerjoin(threads, threads.c.requirement_id == Requirement.id)
            .outerjoin(mentions, mentions.c.requirement_id == Requirement.id)
            .where(
                Requirement.org_id == actor.org_id,
                Requirement.task_id == task_id,
                Requirement.job_id == extraction.id,
                Requirement.document_id == extraction.document_id,
            )
            .order_by(Requirement.id)
            .limit(limit)
        )
    ).all()


async def load(session, actor, requirements, task_id, assessment_day):
    required = {row.id: row for row in requirements}
    # Requirement/chunk IDs are already scope-validated by the board extraction.
    chunks = {
        row.id: row
        for row in await session.scalars(
            select(Chunk).where(
                Chunk.org_id == actor.org_id, Chunk.id.in_({row.chunk_id for row in requirements})
            )
        )
    }
    citations = response_cards.citation_validity_batch(requirements, chunks)
    session.info["board_requirement_citations"] = citations
    pairs = list(
        await session.execute(
            select(ResponseCard, ResponseCardRevision)
            .join(ResponseCardRevision, ResponseCardRevision.id == ResponseCard.current_revision_id)
            .where(
                ResponseCard.org_id == actor.org_id,
                ResponseCard.task_id == task_id,
                ResponseCard.requirement_id.in_(required),
            )
        )
    )
    selections = [
        select(literal(kind).label("kind"), model.id, model.active).where(
            model.org_id == actor.org_id, model.task_id == task_id
        )
        for kind, model in [
            ("product", TaskResource),
            ("feature", TaskFeature),
            ("certificate", TaskCertificate),
            ("org_profile", TaskOrgProfile),
        ]
    ]
    selections.append(
        select(literal("certificate_pdf_page"), EvidenceSource.id, TaskCertificate.active)
        .join(TaskCertificate, TaskCertificate.id == EvidenceSource.task_certificate_id)
        .where(EvidenceSource.org_id == actor.org_id, EvidenceSource.task_id == task_id)
    )
    material_query = union_all(*selections)
    materials = {
        (kind, ident): active for kind, ident, active in await session.execute(material_query)
    }
    manifests = {
        row.generation_job_id: row.input_manifest
        for row in await session.scalars(
            select(CardGenerationRun).where(
                CardGenerationRun.org_id == actor.org_id,
                CardGenerationRun.generation_job_id.in_(
                    {revision.model_job_id for _, revision in pairs if revision.model_job_id}
                ),
            )
        )
    }
    from app.models.entities import CertificateRevision

    certificate_dates = dict(
        (
            await session.execute(
                select(TaskCertificate.id, CertificateRevision.data)
                .join(
                    CertificateRevision,
                    CertificateRevision.id == TaskCertificate.certificate_revision_id,
                )
                .where(TaskCertificate.org_id == actor.org_id, TaskCertificate.task_id == task_id)
            )
        ).all()
    )
    latest = (
        select(PrototypeEvidenceDecision)
        .where(
            PrototypeEvidenceDecision.org_id == actor.org_id,
            PrototypeEvidenceDecision.task_id == task_id,
        )
        .distinct(PrototypeEvidenceDecision.evidence_id)
    )
    latest = latest.order_by(
        PrototypeEvidenceDecision.evidence_id,
        PrototypeEvidenceDecision.created_at.desc(),
        PrototypeEvidenceDecision.id.desc(),
    ).subquery()
    withdrawn = (
        select(ScreenshotWithdrawal.id)
        .where(ScreenshotWithdrawal.asset_id == ScreenshotAsset.id)
        .exists()
    )
    # All image parents, privacy reviews and fixed provenance are loaded together;
    # no per-requirement SQL is issued by the following projection loop.
    evidence_rows = list(
        await session.execute(
            select(
                CardEvidenceLink.revision_id,
                Evidence,
                ScreenshotAsset,
                ScreenshotRendition,
                ScreenshotPrivacyReview,
                ScreenshotPrototypeRun,
                ScreenshotVendorArchive,
                withdrawn.label("withdrawn"),
                latest.c.decision,
                latest.c.card_revision_id,
                latest.c.image_sha256,
                latest.c.html_sha256,
            )
            .join(Evidence, Evidence.id == CardEvidenceLink.evidence_id)
            .outerjoin(ScreenshotAsset, ScreenshotAsset.id == Evidence.screenshot_asset_id)
            .outerjoin(
                ScreenshotRendition, ScreenshotRendition.id == Evidence.screenshot_rendition_id
            )
            .outerjoin(
                ScreenshotPrivacyReview,
                ScreenshotPrivacyReview.id == ScreenshotRendition.privacy_review_id,
            )
            .outerjoin(
                ScreenshotPrototypeRun,
                ScreenshotPrototypeRun.id == ScreenshotAsset.prototype_run_id,
            )
            .outerjoin(
                ScreenshotVendorArchive,
                ScreenshotVendorArchive.id == ScreenshotAsset.vendor_archive_id,
            )
            .outerjoin(latest, latest.c.evidence_id == Evidence.id)
            .where(
                CardEvidenceLink.org_id == actor.org_id,
                CardEvidenceLink.revision_id.in_({revision.id for _, revision in pairs}),
            )
        )
    )
    evidence_by_revision = {}
    for entry in evidence_rows:
        evidence_by_revision.setdefault(entry[0], []).append(entry)
    views = {}
    for card, revision in pairs:
        requirement = required[card.requirement_id]
        generation_stale = False
        if revision.model_job_id:
            manifest = manifests.get(revision.model_job_id)
            if manifest is None:
                raise ServiceError(
                    "missing_generation_run", "Model response has no fixed input record", 500, 4
                )
            for entry in manifest["materials"]:
                kind = entry["kind"]
                if kind == "certificate_pdf_page":
                    actor.require("certificate:read")
                    actor.require("certificate:file:read")
                    actor.require("evidence:source:read")
                    generation_stale |= not materials.get(
                        (kind, UUID(entry["evidence_source_id"])), False
                    )
                    continue
                scope = response_cards.MATERIALS[kind][4]
                actor.require(scope)
                generation_stale |= not materials.get((kind, UUID(entry["selection_id"])), False)
        evidence = []
        invalid_image = False
        blockers = []
        date_advisory = False
        prototype_blocked = False
        for (
            _,
            row,
            asset,
            rendition,
            privacy,
            prototype,
            vendor,
            is_withdrawn,
            decision,
            decision_revision,
            decision_image,
            decision_html,
        ) in evidence_by_revision.get(revision.id, []):
            kind = "certificate" if row.kind == "certificate_pdf_page" else row.kind
            if row.kind == "certificate_pdf_page" or row.evidence_source_id is not None:
                for scope in ("certificate:read", "certificate:file:read", "evidence:source:read"):
                    actor.require(scope)
            if kind == "image_region":
                actor.require("screenshot:read")
                if asset and (asset.task_feature_id or asset.task_resource_id):
                    actor.require("resource:read")
                selected_kind = (
                    "feature"
                    if asset and asset.task_feature_id
                    else "product"
                    if asset and asset.task_resource_id
                    else "certificate"
                )
                selected_id = (
                    (asset.task_feature_id or asset.task_resource_id or row.task_certificate_id)
                    if asset
                    else None
                )
                active = materials.get((selected_kind, selected_id), False) and not is_withdrawn
                invalid_image |= not bool(
                    asset
                    and rendition
                    and privacy
                    and active
                    and rendition.asset_id == asset.id
                    and privacy.asset_id == asset.id
                    and rendition.image_sha256 == row.image_sha256
                    and asset.task_id == card.task_id
                    and asset.extraction_job_id == card.extraction_job_id
                )
                if rendition and asset:
                    spec = ImageEvidenceInput.model_validate(
                        dict(
                            kind="image_region",
                            asset_id=asset.id,
                            rendition_id=rendition.id,
                            expected_image_sha256=row.image_sha256,
                            region=row.region,
                            claim_scope=row.claim_scope,
                            visual_observation=row.visual_observation,
                        )
                    )
                    mapping = ContentMapping.model_validate(rendition.mapping)
                    allowed = {
                        "screenshot": "functional_observation",
                        "prototype": "functional_observation",
                        "diagram": "design_explanation",
                        "certificate_page": "document_excerpt",
                        "vendor_page": "hardware_documentation",
                    }
                    invalid_image |= not spec.region.within(
                        mapping.content_width, mapping.content_height
                    )
                    invalid_image |= spec.claim_scope != allowed.get(asset.image_kind)
                    invalid_image |= asset.image_kind == "screenshot" and asset.source.get(
                        "environment"
                    ) not in ("production", "test", "development")
                if asset and asset.prototype_run_id:
                    invalid_image |= not bool(
                        prototype
                        and prototype.task_feature_id == asset.task_feature_id
                        and prototype.feature_revision_id == asset.feature_revision_id
                        and prototype.source_image_sha256 == asset.source_sha256
                    )
                    valid_decision = bool(
                        prototype
                        and revision.state == "confirmed"
                        and row.confirmed_by is not None
                        and active
                        and not is_withdrawn
                        and decision_revision == revision.id
                        and decision_image == row.image_sha256
                        and decision_html == prototype.html_sha256
                    )
                    code = (
                        "prototype_undecided"
                        if not valid_decision
                        else "prototype_replacement_pending"
                        if decision == "replace"
                        else None
                    )
                    if code:
                        blockers.append(code)
                        prototype_blocked = True
                if asset and asset.vendor_archive_id:
                    invalid_image |= not bool(
                        vendor
                        and vendor.id == asset.vendor_archive_id
                        and vendor.product_revision_id == asset.product_revision_id
                    )
            else:
                _, _, selection_field, _, scope, _ = response_cards.MATERIALS[kind]
                actor.require(scope)
                selected_id = getattr(row, selection_field)
                active = materials.get((kind, selected_id), False)
            if row.task_certificate_id:
                actor.require("certificate:read")
                data = certificate_dates.get(row.task_certificate_id, {})
                expired = (
                    data.get("valid_until")
                    and date.fromisoformat(data["valid_until"]) < assessment_day
                )
                inactive = not materials.get(("certificate", row.task_certificate_id), False)
                if expired:
                    blockers.append("expired_certificate")
                    date_advisory = True
                if inactive:
                    blockers.append("certificate_inactive")
            evidence.append({"confirmed_by": row.confirmed_by, "active_selection": active})
        eligibility = response_cards.card_eligibility(
            revision,
            requirement,
            valid_citation=citations[requirement.id],
            generation_stale=generation_stale,
            invalid_image=invalid_image,
            inactive_evidence=any(not entry["active_selection"] for entry in evidence),
        )
        views[requirement.id] = {
            "id": card.id,
            "revision": revision.revision,
            "state": revision.state,
            "review_domain": revision.review_domain,
            "review_hint": revision.review_hint,
            "eligibility": eligibility,
            "evidence": evidence,
            "blockers": blockers,
            "date_advisory": date_advisory,
            "prototype_blocked": prototype_blocked,
        }
    return views
