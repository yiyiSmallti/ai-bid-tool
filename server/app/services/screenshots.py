"""Released pixels, immutable derivations and live material gates."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, NoReturn
from uuid import uuid4

from sqlalchemy import select

from app.core.errors import ServiceError, not_found
from app.models.entities import TaskFeature
from app.models.response_cards import ResponseCard
from app.models.screenshots import (
    ScreenshotAsset,
    ScreenshotPrivacyReview,
    ScreenshotPrototypeRun,
    ScreenshotRendition,
    ScreenshotVendorArchive,
    ScreenshotWithdrawal,
)
from app.providers.base import ProviderFailure
from app.schemas.screenshot_contracts import (
    ContentMapping,
    ImageEvidenceInput,
    ImagePlan,
    PixelRect,
    PreparedScreenshot,
    ScreenshotIngest,
)
from app.services import response_cards as cards
from app.services import vendor_screenshots as vendor
from app.services.evidence_sources import read_preview, require_source, source_data
from app.services.versioned import audit

PRIVACY_VERSION = "screenshot-privacy-v1"
MAX_BYTES = 40 * 1024 * 1024
INVALID_IMAGE_CODES = frozenset(
    {
        "image_integrity",
        "image_hash_mismatch",
        "invalid_region",
        "invalid_image_claim",
        "unknown_environment",
        "withdrawn_image",
        "inactive_selection",
        "prototype_integrity",
        "prototype_provenance_missing",
        "vendor_provenance_integrity",
        "missing_privacy_review",
        "source_integrity",
    }
)


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def fail(code, message, status=400, exit_code=2) -> NoReturn:
    raise ServiceError(code, message, status, exit_code)


def ingest_human(actor):
    actor.require("screenshot:ingest")
    if (
        actor.actor_kind != "session"
        or actor.token_id is not None
        or actor.role not in {"admin", "bidder", "technical"}
    ):
        fail("forbidden", "Human privacy review required", 403, 4)


def validate_plan(plan: ImagePlan, width: int, height: int):
    crop = plan.crop or PixelRect(x=0, y=0, width=width, height=height)
    if (
        width < 1
        or height < 1
        or max(width, height) > 8192
        or width * height > 20_000_000
        or not crop.within(width, height)
    ):
        fail("image_limits", "Image or crop exceeds pixel limits")
    for region in [*plan.redact, *plan.boxes]:
        if not region.within(width, height):
            fail("invalid_region", "Region is outside the source image")
    for box in plan.boxes:
        if (
            box.x < crop.x
            or box.y < crop.y
            or box.x + box.width > crop.x + crop.width
            or box.y + box.height > crop.y + crop.height
        ):
            fail("invalid_region", "Every box must be wholly within crop")
    return crop


async def prepare(content: bytes, source, plan: ImagePlan):
    from app.providers.screenshot_renderer import render

    png, rendered = await render(
        content,
        {"plan": plan.model_dump(mode="json"), "profile": PRIVACY_VERSION, "provenance": {}},
    )
    receipt = PreparedScreenshot(
        source=source,
        source_sha256=hashlib.sha256(content).hexdigest(),
        source_width=rendered["source_width"],
        source_height=rendered["source_height"],
        plan=plan,
        plan_sha256=digest(plan.model_dump(mode="json")),
        preparation_profile=PRIVACY_VERSION,
        prepared_at=datetime.now(UTC),
        image=rendered["image"],
        mapping=rendered["mapping"],
    )
    return png, receipt


async def resolve_source(session, actor, task_id, extraction_id, source, crypto=None):
    await cards.extraction_scope(session, task_id, extraction_id)
    if source.kind in {"vendor_web", "vendor_pdf"}:
        if crypto is None:
            fail("vendor_source_unavailable", "Vendor sources require the server key", 503, 3)
        return await vendor.resolve(session, actor, task_id, extraction_id, source, crypto)
    if source.kind == "certificate_page":
        archive, selected, original = await require_source(
            session, actor, source.evidence_source_id
        )
        if archive.task_id != task_id:
            raise not_found()
        if not selected.active:
            fail("inactive_selection", "Material selection is no longer active", 409, 4)
        return (
            {
                "evidence_source_id": archive.id,
                "origin": "certificate",
                "image_kind": "certificate_page",
            },
            selected,
            archive,
        )
    if source.kind not in {"upload", "prototype_render"}:
        fail("phase_b_source_required", "Local browser capture is not available", 400, 4)
    prototype_id = source.prototype_run_id
    prototype = None
    if prototype_id:
        prototype = await session.get(ScreenshotPrototypeRun, prototype_id)
        if (
            prototype is None
            or prototype.task_id != task_id
            or prototype.extraction_job_id != extraction_id
        ):
            raise not_found()
        feature_id = prototype.task_feature_id
    else:
        feature_id = source.task_feature_id
    actor.require("resource:read")
    selected = await session.get(TaskFeature, feature_id)
    if selected is None or selected.task_id != task_id:
        raise not_found()
    if not selected.active:
        fail("inactive_selection", "Material selection is no longer active", 409, 4)
    if prototype and (
        selected.feature_revision_id != prototype.feature_revision_id
        or (source.kind == "upload" and source.task_feature_id != feature_id)
    ):
        fail("prototype_binding_changed", "Prototype does not match its fixed feature", 409, 4)
    return (
        {
            "task_feature_id": selected.id,
            "feature_revision_id": selected.feature_revision_id,
            "prototype_run_id": prototype_id,
            "origin": "prototype" if prototype else "user",
            "image_kind": "prototype" if prototype else source.image_kind,
        },
        selected,
        prototype,
    )


def rendition_view(row):
    return {
        "id": str(row.id),
        "asset_id": str(row.asset_id),
        "parent_rendition_id": str(row.parent_rendition_id) if row.parent_rendition_id else None,
        "image": row.image,
        "plan": row.plan,
        "plan_sha256": row.plan_sha256,
        "profile": row.profile,
        "mapping": row.mapping,
        "privacy_review_id": str(row.privacy_review_id),
        "privacy_basis": "safe_derivation" if row.parent_rendition_id else "human_upload_review",
        "prototype_watermark": False,
    }


async def selection(session, actor, asset):
    if asset.task_feature_id:
        actor.require("resource:read")
        selected = await session.get(TaskFeature, asset.task_feature_id)
        if (
            selected is None
            or selected.task_id != asset.task_id
            or selected.feature_revision_id != asset.feature_revision_id
        ):
            raise not_found()
        return selected.id, selected.feature_revision_id, selected.active, None
    if asset.evidence_source_id:
        archive, selected, original = await require_source(session, actor, asset.evidence_source_id)
        if archive.task_id != asset.task_id:
            raise not_found()
        return (
            selected.id,
            selected.certificate_revision_id,
            selected.active,
            source_data(archive, selected, original),
        )
    if asset.task_resource_id:
        from app.models.entities import TaskResource

        actor.require("resource:read")
        selected = await session.get(TaskResource, asset.task_resource_id)
        if (
            selected is None
            or selected.task_id != asset.task_id
            or selected.product_revision_id != asset.product_revision_id
        ):
            raise not_found()
        return selected.id, selected.product_revision_id, selected.active, None
    fail("source_integrity", "Material source is incomplete", 409, 4)


async def asset_access(session, actor, asset_id, *, active=False):
    actor = await cards.access(session, actor, "screenshot:read")
    asset = await session.get(ScreenshotAsset, asset_id)
    if asset is None:
        raise not_found()
    _, _, selected, _ = await selection(session, actor, asset)
    withdrawn = await session.scalar(
        select(ScreenshotWithdrawal.id).where(ScreenshotWithdrawal.asset_id == asset.id)
    )
    if active and (withdrawn or not selected):
        fail(
            "withdrawn_image" if withdrawn else "inactive_selection",
            "Material is no longer available for use",
            409,
            4,
        )
    return asset


async def asset_view(session, actor, asset):
    selected_id, revision_id, active, archive = await selection(session, actor, asset)
    withdrawn = await session.scalar(
        select(ScreenshotWithdrawal.id).where(ScreenshotWithdrawal.asset_id == asset.id)
    )
    prototype = (
        await session.get(ScreenshotPrototypeRun, asset.prototype_run_id)
        if asset.prototype_run_id
        else None
    )
    archive_row = (
        await session.get(ScreenshotVendorArchive, asset.vendor_archive_id)
        if asset.vendor_archive_id
        else None
    )
    return {
        "id": str(asset.id),
        "org_id": str(asset.org_id),
        "task_id": str(asset.task_id),
        "extraction_job_id": str(asset.extraction_job_id),
        "source": asset.source,
        "image_kind": asset.image_kind,
        "origin": asset.origin,
        "selection_id": str(selected_id),
        "resource_revision_id": str(revision_id),
        "source_sha256": asset.source_sha256,
        "source_hash_assurance": asset.source_hash_assurance,
        "source_archive": archive,
        "vendor_archive_id": str(asset.vendor_archive_id) if asset.vendor_archive_id else None,
        "vendor_archive": vendor.view(archive_row) if archive_row else None,
        "prototype_generation": (
            {
                "id": str(prototype.id),
                "html_sha256": prototype.html_sha256,
                "sandbox_receipt_sha256": prototype.sandbox_receipt_sha256,
                **prototype.provenance,
            }
            if prototype
            else None
        ),
        "received_at": asset.received_at.astimezone(UTC).isoformat(),
        "active_selection": active,
        "withdrawn": bool(withdrawn),
        "status": "unconfirmed_material",
        "confirmed_by": None,
        "eligible_for_draft_export": False,
        "warning_codes": (["withdrawn_image"] if withdrawn else [])
        + ([] if active else ["inactive_selection"])
        + ["provider_declared_source"],
    }


async def rendition_access(session, actor, rendition_id, *, active=True, storage=None):
    row = await session.get(ScreenshotRendition, rendition_id)
    if row is None:
        raise not_found()
    asset = await asset_access(session, actor, row.asset_id, active=active)
    review = await session.get(ScreenshotPrivacyReview, row.privacy_review_id)
    if review is None or review.asset_id != asset.id:
        fail("missing_privacy_review", "Privacy review is missing", 409, 4)
    if storage is not None:
        await read_rendition(storage, asset, row)
    return asset, row


async def read_rendition(storage, asset, row):
    from app.providers.screenshot_renderer import validate_png

    try:
        png = await storage.read(asset.org_id, row.storage_key)
        descriptor = validate_png(png)
    except ProviderFailure:
        fail("image_integrity", "Stored image failed integrity checks", 409, 4)
    except ServiceError as exc:
        if exc.code in {"missing_file", "unreadable_file", "storage_conflict"}:
            fail("image_integrity", "Stored image failed integrity checks", 409, 4)
        raise
    if descriptor != row.image or descriptor["sha256"] != row.image_sha256:
        fail("image_integrity", "Stored image failed integrity checks", 409, 4)
    return png


async def show(session, actor, asset_id):
    asset = await asset_access(session, actor, asset_id)
    rows = (
        await session.scalars(
            select(ScreenshotRendition)
            .where(ScreenshotRendition.asset_id == asset_id)
            .order_by(ScreenshotRendition.created_at, ScreenshotRendition.id)
        )
    ).all()
    return {
        "asset": await asset_view(session, actor, asset),
        "renditions": [rendition_view(r) for r in rows],
    }


async def ingest(
    session,
    actor,
    task_id,
    body: ScreenshotIngest,
    png,
    storage,
    staged_objects=None,
    *,
    max_bytes=MAX_BYTES,
    crypto=None,
):
    from app.providers.screenshot_renderer import render, validate_png

    actor = await cards.access(session, actor, "screenshot:ingest")
    ingest_human(actor)
    prepared = body.prepared
    if len(png) > min(max_bytes, MAX_BYTES):
        fail("image_limits", "Upload exceeds configured image limit", 413, 2)
    try:
        actual = validate_png(png)
    except ProviderFailure:
        fail("invalid_upload_png", "Upload must be a bounded, normalized RGB PNG", 400, 2)
    if (
        actual != prepared.image.model_dump(mode="json")
        or actual["sha256"] != body.reviewed_upload_sha256
    ):
        fail("upload_hash_mismatch", "Upload does not match the reviewed image", 409)
    plan = prepared.plan.model_dump(mode="json")
    if digest(plan) != prepared.plan_sha256:
        fail("plan_hash_mismatch", "Preparation plan hash changed", 409)
    crop = validate_plan(prepared.plan, prepared.source_width, prepared.source_height)
    mapping = prepared.mapping
    if (
        mapping.crop != crop
        or mapping.content_width != crop.width
        or mapping.content_height != crop.height
        or mapping.content_offset_x
        or mapping.content_offset_y
        or mapping.footer_height
        or actual["width_px"] != crop.width
        or actual["height_px"] != crop.height
    ):
        fail("invalid_mapping", "Preparation mapping does not match the content")
    bindings, _, fixed = await resolve_source(
        session, actor, task_id, body.extraction_job_id, prepared.source, crypto
    )
    vendor_entry = None
    if isinstance(fixed, vendor.VendorCapture):
        if prepared.source_sha256 != fixed.image.plaintext_sha256:
            fail("source_image_mismatch", "Vendor page image changed", 409, 4)
        if body.reviewed_archive_sha256 != fixed.archive.plaintext_sha256:
            fail("archive_hash_mismatch", "Review must name the archived capture", 409)
        original = await vendor.source_png(storage, fixed)
        vendor_entry = await vendor.archived_entry(storage, fixed)
        expected, expected_receipt = await prepare(original, prepared.source, prepared.plan)
        if (
            expected != png
            or expected_receipt.source_width != prepared.source_width
            or expected_receipt.source_height != prepared.source_height
        ):
            fail("source_image_mismatch", "Prepared image differs from the captured page", 409, 4)
    elif prepared.source.kind == "certificate_page":
        original, _ = await read_preview(
            session, actor, prepared.source.evidence_source_id, storage
        )
        expected, expected_receipt = await prepare(original, prepared.source, prepared.plan)
        if (
            expected != png
            or expected_receipt.source_sha256 != prepared.source_sha256
            or expected_receipt.source_width != prepared.source_width
            or expected_receipt.source_height != prepared.source_height
        ):
            fail("source_image_mismatch", "Prepared image does not match the fixed source", 409, 4)
    elif fixed is not None:
        if prepared.source_sha256 != fixed.source_image_sha256:
            fail("source_image_mismatch", "Prototype source hash changed", 409, 4)
        original = await storage.read(actor.org_id, fixed.source_image_key)
        expected, expected_receipt = await prepare(original, prepared.source, prepared.plan)
        html = await storage.read(actor.org_id, fixed.html_storage_key)
        if hashlib.sha256(html).hexdigest() != fixed.html_sha256:
            fail("prototype_integrity", "Prototype HTML failed integrity checks", 409, 4)
        if (
            expected != png
            or expected_receipt.source_sha256 != prepared.source_sha256
            or expected_receipt.source_width != prepared.source_width
            or expected_receipt.source_height != prepared.source_height
        ):
            fail("source_image_mismatch", "Prepared image differs from the fixed render", 409, 4)
    request_hash = digest(body.model_dump(mode="json"))
    profile = "prototype-clean-v1" if bindings["origin"] == "prototype" else "screenshot-markup-v1"
    provenance = {
        "source_kind": (
            "user_diagram" if bindings["image_kind"] == "diagram" else "user_screenshot"
        )
        if prepared.source.kind == "upload"
        else prepared.source.kind,
        "source_sha256": prepared.source_sha256,
        "plan_sha256": prepared.plan_sha256,
        "source_time": prepared.source.model_dump(mode="json").get("captured_at"),
        "source_time_kind": "provider_declared",
        "page": None,
        "source_id": None,
    }
    if isinstance(fixed, vendor.VendorCapture):
        provenance.update(
            source_time=fixed.entry.ended_at.astimezone(UTC).isoformat(),
            source_time_kind="proxy_captured",
            page=fixed.image.page,
            source_id=str(fixed.run.id),
        )
    elif prepared.source.kind == "certificate_page":
        archive: Any = fixed
        provenance.update(
            source_time=archive.rendered_at.astimezone(UTC).isoformat(),
            source_time_kind="rendered",
            page=archive.page,
            source_id=str(prepared.source.evidence_source_id),
        )
    stored, rendered = await render(
        png,
        {"plan": ImagePlan().model_dump(mode="json"), "profile": profile, "provenance": provenance},
    )
    image = rendered["image"]
    if image["size_bytes"] > min(max_bytes, MAX_BYTES):
        fail("image_limits", "Stored rendition exceeds configured image limit", 413, 2)
    asset_id, rendition_id, review_id = uuid4(), uuid4(), uuid4()
    key = f"org/{actor.org_id}/screenshots/{asset_id}/{rendition_id}/{image['sha256']}.png"
    if staged_objects is not None:
        staged_objects.append(key)
    await storage.put(actor.org_id, key, stored)

    # Rendering finishes before taking the task lock; publication rechecks all mutable inputs.
    await cards.task_lock(session, task_id)
    bindings, _, locked = await resolve_source(
        session, actor, task_id, body.extraction_job_id, prepared.source, crypto
    )
    existing = await session.scalar(
        select(ScreenshotAsset).where(
            ScreenshotAsset.task_id == task_id,
            ScreenshotAsset.idempotency_key == body.idempotency_key,
        )
    )
    if existing:
        if existing.request_hash != request_hash:
            fail("idempotency_conflict", "Idempotency key has different input", 409)
        view = await show(session, actor, existing.id)
        return {"asset": view["asset"], "rendition": view["renditions"][0], "duplicate": True}
    if isinstance(locked, vendor.VendorCapture):
        assert vendor_entry is not None and isinstance(fixed, vendor.VendorCapture)
        if (locked.image.id, locked.archive.id, locked.entry.id) != (
            fixed.image.id,
            fixed.archive.id,
            fixed.entry.id,
        ):
            fail("source_image_mismatch", "Vendor capture changed during ingest", 409, 4)
        archive_row = await vendor.record(
            session, actor, task_id, body.extraction_job_id, locked, *vendor_entry
        )
        bindings["vendor_archive_id"] = archive_row.id
    asset = ScreenshotAsset(
        id=asset_id,
        org_id=actor.org_id,
        task_id=task_id,
        extraction_job_id=body.extraction_job_id,
        source_kind=prepared.source.kind,
        **bindings,
        source_sha256=prepared.source_sha256,
        source_hash_assurance="server_verified"
        if prepared.source.kind
        in {"certificate_page", "prototype_render", "vendor_web", "vendor_pdf"}
        else "client_declared",
        source_width=prepared.source_width,
        source_height=prepared.source_height,
        source=prepared.source.model_dump(mode="json"),
        received_by=actor.user_id,
        received_at=datetime.now(UTC),
        idempotency_key=body.idempotency_key,
        request_hash=request_hash,
    )
    session.add(asset)
    await session.flush()
    final_mapping = dict(rendered["mapping"])
    final_mapping["crop"] = crop.model_dump(mode="json")
    row = ScreenshotRendition(
        id=rendition_id,
        org_id=actor.org_id,
        task_id=task_id,
        extraction_job_id=body.extraction_job_id,
        asset_id=asset_id,
        privacy_review_id=review_id,
        source_sha256=asset.source_sha256,
        upload_sha256=body.reviewed_upload_sha256,
        image_sha256=image["sha256"],
        plan_sha256=prepared.plan_sha256,
        plan=plan,
        mapping=final_mapping,
        image=image,
        profile=profile,
        storage_key=key,
        actor_user_id=actor.user_id,
    )
    session.add(row)
    await session.flush()
    session.add(
        ScreenshotPrivacyReview(
            id=review_id,
            org_id=actor.org_id,
            task_id=task_id,
            extraction_job_id=body.extraction_job_id,
            asset_id=asset_id,
            rendition_id=rendition_id,
            reviewed_upload_sha256=body.reviewed_upload_sha256,
            stored_image_sha256=image["sha256"],
            reviewed_by=actor.user_id,
            rule_version=PRIVACY_VERSION,
        )
    )
    audit(
        session,
        actor,
        "screenshot.privacy.ingest",
        asset_id,
        {
            "upload_sha256": body.reviewed_upload_sha256,
            "image_sha256": image["sha256"],
            "plan_sha256": prepared.plan_sha256,
            "actor_kind": actor.actor_kind,
        },
    )
    await session.flush()
    return {
        "asset": await asset_view(session, actor, asset),
        "rendition": rendition_view(row),
        "duplicate": False,
    }


async def withdraw(session, actor, asset_id, reason):
    actor = await cards.access(session, actor, "screenshot:ingest")
    ingest_human(actor)
    asset = await asset_access(session, actor, asset_id)
    await cards.task_lock(session, asset.task_id)
    existing = await session.scalar(
        select(ScreenshotWithdrawal).where(ScreenshotWithdrawal.asset_id == asset.id)
    )
    if existing:
        return {"asset_id": str(asset.id), "withdrawal_id": str(existing.id), "duplicate": True}
    row = ScreenshotWithdrawal(
        id=uuid4(),
        org_id=actor.org_id,
        task_id=asset.task_id,
        extraction_job_id=asset.extraction_job_id,
        asset_id=asset.id,
        reason=reason,
        withdrawn_by=actor.user_id,
    )
    session.add(row)
    audit(
        session,
        actor,
        "screenshot.withdraw",
        asset.id,
        {
            "reason_sha256": hashlib.sha256(reason.encode()).hexdigest(),
            "actor_kind": actor.actor_kind,
        },
    )
    return {"asset_id": str(asset.id), "withdrawal_id": str(row.id), "duplicate": False}


async def resolve_image_material(
    session, actor, task_id, extraction_job_id, item: ImageEvidenceInput, storage=None
):
    asset, row = await rendition_access(session, actor, item.rendition_id, storage=storage)
    if (
        asset.id != item.asset_id
        or asset.task_id != task_id
        or asset.extraction_job_id != extraction_job_id
    ):
        raise not_found()
    if row.image_sha256 != item.expected_image_sha256:
        fail("image_hash_mismatch", "Image changed", 409)
    mapping = ContentMapping.model_validate(row.mapping)
    if not item.region.within(mapping.content_width, mapping.content_height):
        fail("invalid_region", "Evidence must refer only to visible image content")
    if asset.prototype_run_id:
        prototype = await session.get(ScreenshotPrototypeRun, asset.prototype_run_id)
        if prototype is None or (
            prototype.task_feature_id,
            prototype.feature_revision_id,
            prototype.source_image_sha256,
        ) != (asset.task_feature_id, asset.feature_revision_id, asset.source_sha256):
            fail("prototype_integrity", "Prototype provenance changed", 409, 4)
        if storage is not None:
            html = await storage.read(actor.org_id, prototype.html_storage_key)
            if hashlib.sha256(html).hexdigest() != prototype.html_sha256:
                fail("prototype_integrity", "Prototype HTML failed integrity checks", 409, 4)
    if asset.vendor_archive_id:
        archive_row = await session.get(ScreenshotVendorArchive, asset.vendor_archive_id)
        if archive_row is None:
            fail("vendor_provenance_integrity", "Vendor capture provenance changed", 409, 4)
        await vendor.verify(session, storage, archive_row, asset)
    allowed = {
        "screenshot": "functional_observation",
        "prototype": "functional_observation",
        "diagram": "design_explanation",
        "certificate_page": "document_excerpt",
        "vendor_page": "hardware_documentation",
    }
    if item.claim_scope != allowed[asset.image_kind]:
        fail("invalid_image_claim", "Image kind cannot support this claim scope")
    if asset.image_kind == "screenshot" and asset.source.get("environment") not in {
        "production",
        "test",
        "development",
    }:
        fail("unknown_environment", "Resolve the screenshot environment before use")
    kinds = {
        "user": "user_diagram" if asset.image_kind == "diagram" else "user_screenshot",
        "prototype": "prototype",
        "certificate": "certificate_image",
        "vendor": asset.source_kind,
        "browser": "browser_screenshot",
    }
    fields = {
        "screenshot_asset_id": asset.id,
        "screenshot_rendition_id": row.id,
        "image_sha256": row.image_sha256,
        "region": item.region.model_dump(mode="json"),
        "claim_scope": item.claim_scope,
        "visual_observation": item.visual_observation,
        "quote": None,
        "material_kind": kinds[asset.origin],
        "quote_check": "unreviewed_image",
        "source_sha256": asset.source_sha256,
        "task_feature_id": asset.task_feature_id,
        "feature_revision_id": asset.feature_revision_id,
        "task_resource_id": asset.task_resource_id,
        "product_revision_id": asset.product_revision_id,
    }
    if asset.evidence_source_id:
        archive, selected, _ = await require_source(session, actor, asset.evidence_source_id)
        fields.update(
            evidence_source_id=archive.id,
            task_certificate_id=selected.id,
            certificate_revision_id=selected.certificate_revision_id,
            page=archive.page,
        )
    return fields


async def image_evidence_view(session, actor, row):
    asset = await asset_access(session, actor, row.screenshot_asset_id)
    selected_id, revision_id, active, archive = await selection(session, actor, asset)
    withdrawn = await session.scalar(
        select(ScreenshotWithdrawal.id).where(ScreenshotWithdrawal.asset_id == asset.id)
    )
    rendition = await session.get(ScreenshotRendition, row.screenshot_rendition_id)
    if rendition is None:
        raise not_found()
    return {
        "image_rendition": rendition_view(rendition),
        "id": str(row.id),
        "org_id": str(row.org_id),
        "task_id": str(row.task_id),
        "card_id": str(row.card_id),
        "input": {
            "kind": "image_region",
            "asset_id": str(asset.id),
            "rendition_id": str(row.screenshot_rendition_id),
            "expected_image_sha256": row.image_sha256,
            "region": row.region,
            "claim_scope": row.claim_scope,
            "visual_observation": row.visual_observation,
        },
        "selection_id": str(selected_id),
        "resource_revision_id": str(revision_id),
        "material_kind": row.material_kind,
        "quote_check": row.quote_check,
        "source_archive": archive,
        "screenshot_asset_id": str(asset.id),
        "screenshot_rendition_id": str(row.screenshot_rendition_id),
        "image_sha256": row.image_sha256,
        "region": row.region,
        "claim_scope": row.claim_scope,
        "visual_observation": row.visual_observation,
        "confirmed_by": str(row.confirmed_by) if row.confirmed_by else None,
        "confirmed_at": row.confirmed_at.astimezone(UTC).isoformat() if row.confirmed_at else None,
        "active_selection": active and not withdrawn,
    }


async def validate_image_evidence(session, actor, row, storage=None):
    view = await image_evidence_view(session, actor, row)
    card = await session.get(ResponseCard, row.card_id)
    if card is None:
        raise not_found()
    await resolve_image_material(
        session,
        actor,
        row.task_id,
        card.extraction_job_id,
        ImageEvidenceInput.model_validate(view["input"]),
        storage,
    )


async def review_warnings(session, evidence):
    """Warnings describe this fixed lineage, never hidden source pixels."""
    asset = await session.get(ScreenshotAsset, evidence.screenshot_asset_id)
    if asset is None:
        raise not_found()
    warnings = {"image_visible_scope_only"}
    if asset.origin == "prototype":
        warnings.add("prototype_delivery_obligation")
    else:
        warnings.add("image_source_claim")
    if asset.origin == "vendor":
        warnings.add("vendor_model_scope")
        archive_row = await session.get(ScreenshotVendorArchive, asset.vendor_archive_id)
        if archive_row is None:
            fail("vendor_provenance_integrity", "Vendor capture provenance changed", 409, 4)
        if archive_row.provenance.get("incomplete"):
            warnings.add("vendor_capture_incomplete")
    if asset.source.get("environment") in {"test", "development"}:
        warnings.add("image_test_environment")
    if asset.image_kind == "diagram":
        warnings.add("image_design_only")
    rendition_id = evidence.screenshot_rendition_id
    while rendition_id is not None:
        row = await session.get(ScreenshotRendition, rendition_id)
        if row is None or row.asset_id != asset.id:
            fail("image_integrity", "Image lineage changed", 409, 4)
        if row.plan["redact"]:
            warnings.add("image_redaction_review")
        if row.plan["crop"] is not None:
            warnings.add("image_crop_review")
        rendition_id = row.parent_rendition_id
    return sorted(warnings)
