"""Human, append-only decisions over an exact set of confirmed prototype evidence."""

from datetime import UTC
from uuid import uuid4

from sqlalchemy import select, true

from app.core.errors import not_found
from app.models.entities import Requirement, TaskFeature
from app.models.response_cards import CardEvidenceLink, Evidence, ResponseCard, ResponseCardRevision
from app.models.screenshots import (
    PrototypeDecisionBatch,
    PrototypeEvidenceDecision,
    ScreenshotAsset,
    ScreenshotPrototypeRun,
    ScreenshotWithdrawal,
)
from app.schemas.screenshot_contracts import PrototypeDecisionPreviewInput
from app.services import response_cards as cards
from app.services import screenshots as images
from app.services.resources import audit


async def latest(session, evidence_id):
    return await session.scalar(
        select(PrototypeEvidenceDecision)
        .where(PrototypeEvidenceDecision.evidence_id == evidence_id)
        .order_by(PrototypeEvidenceDecision.created_at.desc(), PrototypeEvidenceDecision.id.desc())
        .limit(1)
    )


def target(row, revision, image, prototype, asset, previous):
    return {
        "evidence_id": str(row.id),
        "card_revision_id": str(revision.id),
        "rendition_id": str(image.id),
        "image_sha256": image.image_sha256,
        "html_sha256": prototype.html_sha256,
        "task_feature_id": str(asset.task_feature_id),
        "expected_previous_decision_id": str(previous.id) if previous else None,
    }


async def preview(session, actor, task_id, body: PrototypeDecisionPreviewInput, storage=None):
    actor = await cards.access(session, actor, "evidence:confirm")
    actor.require("card:read")
    actor.require("screenshot:read")
    actor.require("resource:read")
    if actor.actor_kind != "session" or actor.token_id:
        images.fail("forbidden", "Human decision required", 403, 4)
    await cards.extraction_scope(session, task_id, body.extraction_job_id)
    features = []
    for feature_id in sorted(body.task_feature_ids, key=str):
        feature = await session.get(TaskFeature, feature_id)
        if feature is None or feature.task_id != task_id:
            raise not_found()
        if not feature.active:
            images.fail("inactive_selection", "Module includes an inactive selection", 409)
        features.append({"id": str(feature.id), "revision_id": str(feature.feature_revision_id)})
    rows = (
        await session.execute(
            select(Evidence, ResponseCard, ResponseCardRevision, ScreenshotAsset)
            .join(ResponseCard, ResponseCard.id == Evidence.card_id)
            .join(ResponseCardRevision, ResponseCardRevision.id == ResponseCard.current_revision_id)
            .join(
                CardEvidenceLink,
                (CardEvidenceLink.revision_id == ResponseCardRevision.id)
                & (CardEvidenceLink.evidence_id == Evidence.id),
            )
            .join(ScreenshotAsset, ScreenshotAsset.id == Evidence.screenshot_asset_id)
            .where(
                ResponseCard.task_id == task_id,
                ResponseCard.extraction_job_id == body.extraction_job_id,
                ResponseCardRevision.state == "confirmed",
                Evidence.confirmed_by.is_not(None),
                ScreenshotAsset.origin == "prototype",
                ScreenshotAsset.task_feature_id.in_(body.task_feature_ids),
                Evidence.id.in_(body.evidence_ids) if body.evidence_ids is not None else true(),
            )
            .order_by(Evidence.id)
            .limit(501)
        )
    ).all()
    if len(rows) > 500:
        images.fail(
            "module_too_large",
            "Split the explicit module into groups of at most 500 evidence items",
        )
    targets = []
    for evidence, card, revision, asset in rows:
        if body.evidence_ids is not None and evidence.id not in body.evidence_ids:
            continue
        cards.human(actor, revision.review_domain)
        if revision.quote_sha256 != cards.quote_hash(
            (await session.get(Requirement, card.requirement_id)).quote
        ):
            images.fail(
                "stale_card", "Card needs confirmation against its current requirement", 409
            )
        await images.validate_image_evidence(session, actor, evidence, storage)
        _, rendition = await images.rendition_access(
            session, actor, evidence.screenshot_rendition_id
        )
        prototype = await session.get(ScreenshotPrototypeRun, asset.prototype_run_id)
        if prototype is None:
            images.fail(
                "prototype_provenance_missing", "Prototype provenance is incomplete", 409, 4
            )
        targets.append(
            target(
                evidence, revision, rendition, prototype, asset, await latest(session, evidence.id)
            )
        )
    if body.evidence_ids is not None and {str(x) for x in body.evidence_ids} != {
        x["evidence_id"] for x in targets
    }:
        raise not_found()
    if len(targets) > 500:
        images.fail(
            "module_too_large",
            "Split the explicit module into groups of at most 500 evidence items",
        )
    manifest = {
        "task_id": str(task_id),
        "extraction_job_id": str(body.extraction_job_id),
        "module_label": body.module_label,
        "features": features,
        "targets": targets,
    }
    return {
        "input_hash": images.digest(manifest),
        "module_label": body.module_label,
        "targets": targets,
    }


async def apply(session, actor, task_id, body, storage):
    actor = await cards.access(session, actor, "evidence:confirm")
    if actor.actor_kind != "session" or actor.token_id:
        images.fail("forbidden", "Human decision required", 403, 4)
    await cards.task_lock(session, task_id)
    existing = await session.scalar(
        select(PrototypeDecisionBatch).where(
            PrototypeDecisionBatch.task_id == task_id,
            PrototypeDecisionBatch.idempotency_key == body.idempotency_key,
        )
    )
    request_hash = images.digest(body.model_dump(mode="json"))
    if existing:
        if existing.request_hash != request_hash:
            images.fail("idempotency_conflict", "Decision request differs", 409)
        rows = (
            await session.scalars(
                select(PrototypeEvidenceDecision).where(
                    PrototypeEvidenceDecision.batch_id == existing.id
                )
            )
        ).all()
        for row in rows:
            revision = await session.get(ResponseCardRevision, row.card_revision_id)
            if revision is None:
                raise not_found()
            cards.human(actor, revision.review_domain)
        return {
            "batch_id": str(existing.id),
            "decisions": [await decision_view(session, actor, row) for row in rows],
            "duplicate": True,
        }
    preflight = await preview(
        session,
        actor,
        task_id,
        PrototypeDecisionPreviewInput(
            extraction_job_id=body.extraction_job_id,
            module_label=body.module_label,
            task_feature_ids=body.task_feature_ids,
            evidence_ids=body.evidence_ids,
        ),
        storage,
    )
    if preflight["input_hash"] != body.expected_input_hash:
        images.fail("prototype_decision_stale", "Decision inputs changed; preview again", 409)
    submitted = [
        {
            k: value
            for k, value in item.model_dump(mode="json").items()
            if k not in {"decision", "keep_basis", "reason"}
        }
        for item in body.items
    ]
    if sorted(submitted, key=lambda x: x["evidence_id"]) != preflight["targets"]:
        images.fail(
            "decision_set_mismatch", "Decision items must exactly cover the preflight set", 409
        )
    batch = PrototypeDecisionBatch(
        id=uuid4(),
        org_id=actor.org_id,
        task_id=task_id,
        extraction_job_id=body.extraction_job_id,
        module_label=body.module_label,
        task_feature_ids=[str(x) for x in body.task_feature_ids],
        target_manifest=preflight["targets"],
        input_hash=body.expected_input_hash,
        request_hash=request_hash,
        idempotency_key=body.idempotency_key,
        decided_by=actor.user_id,
    )
    session.add(batch)
    await session.flush()
    rows = []
    for item in body.items:
        evidence = await session.get(Evidence, item.evidence_id)
        assert evidence is not None
        asset = await session.get(ScreenshotAsset, evidence.screenshot_asset_id)
        assert asset is not None
        row = PrototypeEvidenceDecision(
            id=uuid4(),
            org_id=actor.org_id,
            task_id=task_id,
            extraction_job_id=body.extraction_job_id,
            batch_id=batch.id,
            evidence_id=evidence.id,
            card_id=evidence.card_id,
            card_revision_id=item.card_revision_id,
            asset_id=asset.id,
            rendition_id=item.rendition_id,
            image_sha256=item.image_sha256,
            html_sha256=item.html_sha256,
            task_feature_id=item.task_feature_id,
            feature_revision_id=asset.feature_revision_id,
            previous_decision_id=item.expected_previous_decision_id,
            decision=item.decision,
            keep_basis=item.keep_basis,
            reason=item.reason,
            decided_by=actor.user_id,
        )
        session.add(row)
        rows.append(row)
        audit(
            session,
            actor,
            "screenshot.prototype.decide",
            row.id,
            {
                "batch_id": str(batch.id),
                "evidence_id": str(evidence.id),
                "decision": item.decision,
                "keep_basis": item.keep_basis,
                "input_hash": body.expected_input_hash,
                "previous_decision_id": str(item.expected_previous_decision_id)
                if item.expected_previous_decision_id
                else None,
                "reason_sha256": images.digest(item.reason) if item.reason else None,
                "actor_kind": actor.actor_kind,
            },
        )
    await session.flush()
    return {
        "batch_id": str(batch.id),
        "decisions": [await decision_view(session, actor, row) for row in rows],
        "duplicate": False,
    }


async def decision_view(session, actor, row):
    actor.require("card:read")
    asset = await images.asset_access(session, actor, row.asset_id)
    card = await session.get(ResponseCard, row.card_id)
    _, _, active, _ = await images.selection(session, actor, asset)
    withdrawn = await session.scalar(
        select(ScreenshotWithdrawal.id).where(ScreenshotWithdrawal.asset_id == asset.id)
    )
    revision = await session.get(ResponseCardRevision, row.card_revision_id)
    requirement = await session.get(Requirement, card.requirement_id) if card else None
    evidence = await session.get(Evidence, row.evidence_id)
    prototype = (
        await session.get(ScreenshotPrototypeRun, asset.prototype_run_id)
        if asset.prototype_run_id
        else None
    )
    bindings_current = (
        revision is not None
        and requirement is not None
        and evidence is not None
        and prototype is not None
        and revision.state == "confirmed"
        and cards.revision_quote_hash(revision, requirement) == cards.quote_hash(requirement.quote)
        and evidence.confirmed_by is not None
        and (evidence.screenshot_asset_id, evidence.screenshot_rendition_id, evidence.image_sha256)
        == (row.asset_id, row.rendition_id, row.image_sha256)
        and (asset.task_feature_id, asset.feature_revision_id, prototype.html_sha256)
        == (row.task_feature_id, row.feature_revision_id, row.html_sha256)
    )
    current = await latest(session, row.evidence_id)
    validity = "superseded" if current and current.id != row.id else "current"
    if validity == "current" and (
        not active
        or withdrawn
        or not bindings_current
        or card is None
        or card.current_revision_id != row.card_revision_id
    ):
        validity = "stale"
    return {
        "id": str(row.id),
        "batch_id": str(row.batch_id),
        "target": {
            "evidence_id": str(row.evidence_id),
            "card_revision_id": str(row.card_revision_id),
            "rendition_id": str(row.rendition_id),
            "image_sha256": row.image_sha256,
            "html_sha256": row.html_sha256,
            "task_feature_id": str(row.task_feature_id),
            "expected_previous_decision_id": str(row.previous_decision_id)
            if row.previous_decision_id
            else None,
        },
        "decision": row.decision,
        "keep_basis": row.keep_basis,
        "decided_by": str(row.decided_by),
        "decided_at": row.created_at.astimezone(UTC).isoformat(),
        "validity": validity,
    }


async def export_decision_manifest(session, actor, evidence_ids, *, final: bool, storage=None):
    """Maintainer integration seam: no export routes are wired in Phase A."""
    decisions, issues = [], []
    for evidence_id in sorted(evidence_ids, key=str):
        evidence = await session.get(Evidence, evidence_id)
        if evidence is None:
            raise not_found()
        if evidence.kind != "image_region":
            continue
        if evidence.confirmed_by is None:
            issues.append({"evidence_id": str(evidence.id), "code": "unconfirmed_evidence"})
            continue
        await images.validate_image_evidence(session, actor, evidence, storage)
        asset = await session.get(ScreenshotAsset, evidence.screenshot_asset_id)
        if asset is None or asset.origin != "prototype" or not final:
            continue
        row = await latest(session, evidence.id)
        view = await decision_view(session, actor, row) if row else None
        code = (
            "prototype_decision_required"
            if not row
            else "prototype_decision_stale"
            if view is not None and view["validity"] != "current"
            else "prototype_replacement_pending"
            if row.decision == "replace"
            else None
        )
        if code:
            issues.append({"evidence_id": str(evidence.id), "code": code})
        if view:
            decisions.append(view)
    return {
        "decisions": decisions,
        "issues": issues,
        "decision_set_sha256": images.digest(decisions),
    }
