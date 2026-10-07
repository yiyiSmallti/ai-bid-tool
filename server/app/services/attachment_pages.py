"""Attachment branch of EvidenceSource and the existing screenshot privacy chain."""

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, true

from app.core.errors import not_found
from app.models.attachments import AttachmentPrivacyHold, TaskAttachment
from app.models.entities import EvidenceSource, Job
from app.models.screenshots import (
    ScreenshotAsset,
    ScreenshotPrivacyReview,
    ScreenshotRendition,
    ScreenshotWithdrawal,
)
from app.schemas import attachment_contracts as c
from app.schemas.evidence_source_contracts import EvidenceSourcePreview
from app.schemas.screenshot_contracts import ImagePlan
from app.services import attachments as a
from app.services import evidence_sources, screenshots, task_workflow
from app.services.versioned import audit


async def source_row(session, actor, source_id, *, raw=False, write=False, lock=False):
    row = await a.required(session, EvidenceSource, source_id)
    if row.source_kind != "user_supplied_attachment_pdf":
        raise not_found()
    await task_workflow.access(
        session,
        actor,
        row.task_id,
        scope="attachment:privacy" if write else "attachment:read",
        write=write,
        lock=lock,
    )
    if raw:
        actor.require("attachment:original:read")
        actor.require("evidence:source:read")
    if lock:
        row = await a.required(session, EvidenceSource, source_id, lock=True)
    return row


async def holds(session, source_ids):
    if not source_ids:
        return {}
    parents = (
        select(EvidenceSource.id, EvidenceSource.org_id)
        .where(EvidenceSource.id.in_(source_ids))
        .subquery()
    )
    latest = (
        select(AttachmentPrivacyHold.id)
        .where(
            AttachmentPrivacyHold.org_id == parents.c.org_id,
            AttachmentPrivacyHold.evidence_source_id == parents.c.id,
        )
        .correlate(parents)
        .order_by(AttachmentPrivacyHold.reviewed_at.desc(), AttachmentPrivacyHold.id.desc())
        .limit(1)
        .lateral()
    )
    rows = (
        await session.scalars(
            select(AttachmentPrivacyHold).select_from(
                parents.join(latest, true()).join(
                    AttachmentPrivacyHold, AttachmentPrivacyHold.id == latest.c.id
                )
            )
        )
    ).all()
    return {r.evidence_source_id: r for r in rows}


async def privacy_state(session, source_ids):
    held = await holds(session, source_ids)
    parents = (
        select(EvidenceSource.id, EvidenceSource.org_id)
        .where(EvidenceSource.id.in_(source_ids))
        .subquery()
    )
    latest = (
        select(ScreenshotAsset.id)
        .where(
            ScreenshotAsset.org_id == parents.c.org_id,
            ScreenshotAsset.evidence_source_id == parents.c.id,
            ScreenshotAsset.source_kind == "attachment_page",
        )
        .correlate(parents)
        .order_by(ScreenshotAsset.received_at.desc(), ScreenshotAsset.id.desc())
        .limit(1)
        .lateral()
    )
    rows = (
        await session.execute(
            select(parents.c.id, ScreenshotPrivacyReview, ScreenshotWithdrawal.id).select_from(
                parents.join(latest, true())
                .join(ScreenshotPrivacyReview, ScreenshotPrivacyReview.asset_id == latest.c.id)
                .outerjoin(ScreenshotWithdrawal, ScreenshotWithdrawal.asset_id == latest.c.id)
            )
        )
    ).all()
    return held, {source_id: (review, withdrawal) for source_id, review, withdrawal in rows}


async def source_summary(
    session, actor, row, *, context=None, privacy=None, extraction_exists=None, reviewer_live=None
):
    context = context or await a.selection_context(
        session, actor, row.task_attachment_id, require_active=False
    )
    selected, _, _, root, _, file, decision, reasons = context
    held, reviews = privacy or await privacy_state(session, [row.id])
    blockers = list(reasons)
    usable_ancestry = not any(reason != "task_archived" for reason in reasons)
    hold = held.get(row.id)
    review = reviews.get(row.id)
    if hold:
        blockers.append("needs_redaction")
    elif not review:
        if extraction_exists is None:
            extraction_exists = bool(
                await session.scalar(
                    select(Job.id)
                    .where(
                        Job.task_id == row.task_id, Job.kind == "extract", Job.status == "succeeded"
                    )
                    .limit(1)
                )
            )
        if not extraction_exists:
            blockers.append("extraction_required")
        blockers.append("privacy_pending")
        if reviewer_live is None:
            reviewer_live = await a.eligible(session, root.reviewer_user_id)
        if not reviewer_live:
            blockers.insert(0, "reviewer_unavailable")
    elif review[1]:
        blockers.append("privacy_withdrawn")
    else:
        blockers.append("annotation_adapter_not_enabled")
    return a.view(
        c.AttachmentSourceSummary,
        id=row.id,
        org_id=row.org_id,
        task_id=row.task_id,
        task_attachment_id=row.task_attachment_id,
        attachment_revision_id=row.attachment_revision_id,
        page=row.page,
        original_sha256=file.file["sha256"],
        source_png_sha256=row.preview["sha256"],
        rendered_at=row.rendered_at,
        active_selection=selected.active,
        reviewer_user_id=root.reviewer_user_id,
        custodian_user_id=root.custodian_user_id,
        latest_privacy_hold_id=hold.id if hold else None,
        cleared_asset_id=review[0].asset_id
        if review and not review[1] and not hold and usable_ancestry
        else None,
        cleared_rendition_id=review[0].rendition_id
        if review and not review[1] and not hold and usable_ancestry
        else None,
        readiness=a.readiness(root, decision, blockers=blockers),
    )


async def archive_view(session, actor, row):
    context = await a.selection_context(
        session, actor, row.task_attachment_id, require_active=False
    )
    selected, link, _, _, _, file, _, _ = context
    summary = await source_summary(session, actor, row, context=context)
    return a.view(
        c.AttachmentSourceArchive,
        **summary,
        task_org_profile_id=selected.task_org_profile_id,
        profile_revision_id=selected.profile_revision_id,
        profile_attachment_link_id=link.id,
        field=link.field,
        attachment_id=selected.attachment_id,
        file_id=file.id,
        approval_id=selected.approval_id,
        original=file.file,
        preview=row.preview,
        created_by=row.created_by,
    )


async def create_source(session, actor, task_id, body, storage):
    await task_workflow.access(
        session, actor, task_id, scope="task:attachment", write=True, lock=False
    )
    for scope in (
        "attachment:original:read",
        "evidence:source:write",
        "task:profile",
        "profile:read",
    ):
        actor.require(scope)
    context = await a.selection_context(session, actor, body.task_attachment_id)
    selected, _, _, _, _, file, _, _ = context
    if selected.task_id != task_id:
        raise not_found()
    hash_, old = await a.replay(session, actor, "attachment.source.create", body, str(task_id))
    if old:
        row = await source_row(session, actor, old["source"]["id"])
        return {"source": await archive_view(session, actor, row), "duplicate": True}
    existing = await session.scalar(
        select(EvidenceSource).where(
            EvidenceSource.task_attachment_id == selected.id,
            EvidenceSource.page == body.page,
            EvidenceSource.render_profile == evidence_sources.RENDER_PROFILE,
        )
    )
    if existing:
        await a.selection_context(session, actor, selected.id, write=True, lock=True)
        receipt = {"source": await archive_view(session, actor, existing), "duplicate": True}
        await a.record(
            session,
            actor,
            "attachment.source.create",
            existing.id,
            body,
            hash_,
            receipt,
            duplicate=True,
        )
        return receipt
    content, descriptor = await a.read_original(
        session, actor, selected.attachment_revision_id, storage
    )
    identifier = uuid4()
    png, preview, rendered_at = await evidence_sources.bounded_render_async(
        content,
        descriptor,
        body.page,
        f"source-{identifier}-page-{body.page}.png",
        min(c.FILE_BYTE_LIMIT, a.settings_for(session).max_upload_bytes),
        a.settings_for(session),
    )
    # No mutable authority lock spans CPU work. Recheck the exact dependency graph
    # after rendering and lock it until both source and audit commit.
    context = await a.selection_context(session, actor, selected.id, write=True, lock=True)
    selected, link, profile, root, revision, file, _, _ = context
    existing = await session.scalar(
        select(EvidenceSource).where(
            EvidenceSource.task_attachment_id == selected.id,
            EvidenceSource.page == body.page,
            EvidenceSource.render_profile == evidence_sources.RENDER_PROFILE,
        )
    )
    if existing:
        await a.selection_context(session, actor, selected.id, write=True, lock=True)
        receipt = {"source": await archive_view(session, actor, existing), "duplicate": True}
        await a.record(
            session,
            actor,
            "attachment.source.create",
            existing.id,
            body,
            hash_,
            receipt,
            duplicate=True,
        )
        return receipt
    key = f"org/{actor.org_id}/evidence-source/{identifier}/{preview.sha256}.png"
    await a.put_retained(session, actor, storage, key, png, identifier)
    row = EvidenceSource(
        id=identifier,
        org_id=actor.org_id,
        task_id=task_id,
        source_kind="user_supplied_attachment_pdf",
        task_attachment_id=selected.id,
        task_org_profile_id=profile.id,
        profile_revision_id=profile.profile_revision_id,
        profile_attachment_link_id=link.id,
        attachment_id=root.id,
        attachment_revision_id=revision.id,
        attachment_file_id=file.id,
        approval_id=selected.approval_id,
        created_by=actor.user_id,
        page=body.page,
        preview=preview.model_dump(mode="json"),
        storage_key=key,
        rendered_at=rendered_at,
    )
    session.add(row)
    await session.flush()
    receipt = {"source": await archive_view(session, actor, row), "duplicate": False}
    await a.record(session, actor, "attachment.source.create", row.id, body, hash_, receipt)
    return receipt


async def source(session, actor, source_id):
    row = await source_row(session, actor, source_id)
    return await source_summary(session, actor, row)


async def sources(session, actor, task_id, query):
    await task_workflow.access(session, actor, task_id, scope="attachment:read")
    stmt = (
        select(EvidenceSource)
        .join(TaskAttachment, TaskAttachment.id == EvidenceSource.task_attachment_id)
        .where(
            EvidenceSource.task_id == task_id,
            EvidenceSource.source_kind == "user_supplied_attachment_pdf",
        )
    )
    if query.selection_id:
        selected = await a.required(session, TaskAttachment, query.selection_id)
        if selected.task_id != task_id:
            raise not_found()
        stmt = stmt.where(EvidenceSource.task_attachment_id == query.selection_id)
    if not query.history:
        stmt = stmt.where(TaskAttachment.active.is_(True))
    data, rows = await a.page_rows(
        session, actor, stmt, EvidenceSource, query, "sources", task_id, task_id=task_id
    )
    privacy = await privacy_state(session, [r.id for r in rows])
    contexts = await a.selection_contexts(session, list({r.task_attachment_id for r in rows}))
    from app.models.entities import Membership, User

    reviewer_ids = {ctx[3].reviewer_user_id for ctx in contexts.values()}
    live = set(
        (
            await session.scalars(
                select(Membership.user_id)
                .join(User, User.id == Membership.user_id)
                .where(
                    Membership.user_id.in_(reviewer_ids),
                    Membership.active.is_(True),
                    User.active.is_(True),
                    Membership.role.in_(["admin", "bidder"]),
                )
            )
        ).all()
    )
    extraction_exists = bool(
        await session.scalar(
            select(Job.id)
            .where(Job.task_id == task_id, Job.kind == "extract", Job.status == "succeeded")
            .limit(1)
        )
    )
    items = [
        await source_summary(
            session,
            actor,
            row,
            context=contexts[row.task_attachment_id],
            privacy=privacy,
            extraction_exists=extraction_exists,
            reviewer_live=contexts[row.task_attachment_id][3].reviewer_user_id in live,
        )
        for row in rows
    ]
    return data, items


async def read_source(session, actor, source_id, storage, *, history=False):
    row = await source_row(session, actor, source_id, raw=True)
    if not history:
        await a.selection_context(session, actor, row.task_attachment_id)
    descriptor = EvidenceSourcePreview.model_validate(row.preview)
    content = await storage.read_bounded(
        actor.org_id,
        row.storage_key,
        min(c.FILE_BYTE_LIMIT, descriptor.size_bytes, a.settings_for(session).max_upload_bytes),
    )
    evidence_sources.check_png(content, descriptor)
    audit(
        session,
        actor,
        "attachment.download.read",
        row.id,
        {"task_id": str(row.task_id), "sha256": descriptor.sha256, "kind": "raw_page"},
    )
    await session.flush()
    return content, descriptor


async def extraction(session, task_id, identifier):
    job = await a.required(session, Job, identifier)
    if job.task_id != task_id or job.kind != "extract":
        raise not_found()
    if job.status != "succeeded":
        a.fail("extraction_required", "Select a successful extraction for privacy review")


async def privacy_render(png, request):
    from app.providers.base import ProviderFailure
    from app.providers.screenshot_renderer import render

    if not evidence_sources.RENDER_SLOTS.acquire(blocking=False):
        a.fail("source_render_busy", "Page rendering is busy", 503, 3)
    try:
        return await render(png, request)
    except ProviderFailure as exc:
        a.fail(
            "attachment_privacy_render_failed",
            "Page privacy rendering failed",
            503 if exc.retryable else 502,
            3 if exc.retryable else 4,
        )
    finally:
        evidence_sources.RENDER_SLOTS.release()


async def privacy_review(session, actor, source_id, body, storage):
    row = await source_row(session, actor, source_id, raw=True, write=True)
    actor.require("screenshot:ingest")
    context = await a.selection_context(session, actor, row.task_attachment_id)
    root = context[3]
    if actor.user_id != root.reviewer_user_id or not await a.eligible(
        session, root.reviewer_user_id
    ):
        a.fail("forbidden", "Only the assigned live reviewer may review page privacy", 403, 4)
    if body.mode == "redacted":
        a.fail("attachment_redaction_not_enabled", "Redacted page adoption is not enabled")
    if body.reviewed_source_png_sha256 != row.preview["sha256"]:
        a.fail("source_integrity_failure", "Privacy review must name the exact page hash")
    command = (
        "attachment.privacy.hold" if body.mode == "needs_redaction" else "attachment.privacy.clear"
    )
    hash_, old = await a.replay(session, actor, command, body, str(source_id))
    if old:
        if body.mode == "clear":
            await rendition_gate(session, actor, UUID_(old["rendition"]["asset_id"]))
        return {**old, **({"duplicate": True} if body.mode == "clear" else {})}
    if body.mode == "clear":
        await extraction(session, row.task_id, body.extraction_job_id)
        png, descriptor = await read_source(session, actor, source_id, storage)

        # Unchanged raw pixels are the attested upload. The existing renderer adds
        # only its neutral provenance footer; no client-supplied prepared receipt.
        stored, rendered = await privacy_render(
            png,
            {
                "plan": ImagePlan().model_dump(mode="json"),
                "profile": "screenshot-markup-v1",
                "provenance": {
                    "source_kind": "attachment_page",
                    "source_sha256": descriptor.sha256,
                    "plan_sha256": screenshots.digest(ImagePlan().model_dump(mode="json")),
                    "source_time": row.rendered_at.isoformat(),
                    "source_time_kind": "rendered",
                    "page": row.page,
                    "source_id": str(row.id),
                },
            },
        )
    # Repeat live membership, task, selection, assignee and exact hold under locks.
    context = await a.selection_context(
        session, actor, row.task_attachment_id, write=True, lock=True
    )
    root = context[3]
    row = await source_row(session, actor, source_id, raw=True, write=True, lock=True)
    if actor.user_id != root.reviewer_user_id or not await a.eligible(
        session, root.reviewer_user_id
    ):
        a.fail("reviewer_unavailable", "Reviewer assignment changed during processing")
    prior = (await holds(session, [row.id])).get(row.id)
    if body.expected_hold_id != (prior.id if prior else None):
        a.fail(
            "needs_redaction" if prior else "stale_privacy_hold",
            "Privacy hold changed; refresh the page",
        )
    if body.mode == "needs_redaction":
        hold = AttachmentPrivacyHold(
            org_id=actor.org_id,
            task_id=row.task_id,
            evidence_source_id=row.id,
            source_png_sha256=row.preview["sha256"],
            prior_hold_id=body.expected_hold_id,
            reviewed_by=actor.user_id,
            reviewed_at=datetime.now(UTC),
            reason=body.reason,
            request_id=body.request_id,
            payload_hash=hash_,
        )
        session.add(hold)
        await session.flush()
        receipt = a.view(
            c.AttachmentPrivacyHoldView,
            id=hold.id,
            org_id=actor.org_id,
            task_id=row.task_id,
            evidence_source_id=row.id,
            source_png_sha256=hold.source_png_sha256,
            prior_hold_id=hold.prior_hold_id,
            reviewed_by=hold.reviewed_by,
            reviewed_at=hold.reviewed_at,
            responsible_user_id=root.custodian_user_id,
        )
        await a.record(session, actor, command, hold.id, body, hash_, receipt)
        return receipt
    if prior:
        a.fail("needs_redaction", "A held page cannot be cleared by the unchanged-page workflow")
    await extraction(session, row.task_id, body.extraction_job_id)
    asset_id, rendition_id, review_id = uuid4(), uuid4(), uuid4()
    image = rendered["image"]
    if image["size_bytes"] > min(c.FILE_BYTE_LIMIT, a.settings_for(session).max_upload_bytes):
        a.fail("attachment_file_limit", "Rendition exceeds the byte limit", 413)
    key = f"org/{actor.org_id}/screenshots/{asset_id}/{rendition_id}/{image['sha256']}.png"
    await a.put_retained(session, actor, storage, key, stored, rendition_id)
    plan = ImagePlan().model_dump(mode="json")
    asset = ScreenshotAsset(
        id=asset_id,
        org_id=actor.org_id,
        task_id=row.task_id,
        extraction_job_id=body.extraction_job_id,
        source_kind="attachment_page",
        image_kind="attachment_page",
        origin="attachment",
        evidence_source_id=row.id,
        source_sha256=row.preview["sha256"],
        source_hash_assurance="server_verified",
        source_width=row.preview["width_px"],
        source_height=row.preview["height_px"],
        source={"kind": "attachment_page", "evidence_source_id": str(row.id)},
        received_by=actor.user_id,
        received_at=datetime.now(UTC),
        idempotency_key=body.request_id,
        request_hash=hash_,
    )
    session.add(asset)
    await session.flush()
    rendition = ScreenshotRendition(
        id=rendition_id,
        org_id=actor.org_id,
        task_id=row.task_id,
        extraction_job_id=body.extraction_job_id,
        asset_id=asset_id,
        privacy_review_id=review_id,
        source_sha256=row.preview["sha256"],
        upload_sha256=row.preview["sha256"],
        image_sha256=image["sha256"],
        plan_sha256=screenshots.digest(plan),
        plan=plan,
        mapping=rendered["mapping"],
        image=image,
        profile="screenshot-markup-v1",
        storage_key=key,
        actor_user_id=actor.user_id,
    )
    session.add(rendition)
    await session.flush()
    review = ScreenshotPrivacyReview(
        id=review_id,
        org_id=actor.org_id,
        task_id=row.task_id,
        extraction_job_id=body.extraction_job_id,
        asset_id=asset_id,
        rendition_id=rendition_id,
        reviewed_upload_sha256=row.preview["sha256"],
        stored_image_sha256=image["sha256"],
        reviewed_by=actor.user_id,
        rule_version=screenshots.PRIVACY_VERSION,
        attachment_source_id=row.id,
        resolved_hold_id=None,
    )
    session.add(review)
    await session.flush()
    receipt = a.view(
        c.AttachmentPrivacyReceipt,
        org_id=actor.org_id,
        evidence_source_id=row.id,
        task_id=row.task_id,
        extraction_job_id=body.extraction_job_id,
        source_png_sha256=row.preview["sha256"],
        rendition=screenshots.rendition_view(rendition),
        reviewed_by=actor.user_id,
        reviewed_at=review.created_at,
        reviewed_upload_sha256=row.preview["sha256"],
        resolved_hold_id=None,
        duplicate=False,
    )
    await a.record(session, actor, command, review.id, body, hash_, receipt)
    return receipt


def UUID_(value):
    from uuid import UUID

    return UUID(str(value))


async def rendition_gate(session, actor, asset_or_id, *, history=False):
    asset = (
        asset_or_id
        if isinstance(asset_or_id, ScreenshotAsset)
        else await a.required(session, ScreenshotAsset, asset_or_id)
    )
    if asset.source_kind != "attachment_page":
        return
    await task_workflow.access(session, actor, asset.task_id, scope="attachment:page:read")
    for scope in ("screenshot:read", "evidence:source:read"):
        actor.require(scope)
    row = await source_row(session, actor, asset.evidence_source_id)
    context = await a.selection_context(
        session, actor, row.task_attachment_id, require_active=not history
    )
    if history:
        actor.require("attachment:original:read")
        return context
    held = await holds(session, [row.id])
    if row.id in held:
        a.fail("needs_redaction", "Page requires redaction before team use")
    if await session.scalar(
        select(ScreenshotWithdrawal.id).where(ScreenshotWithdrawal.asset_id == asset.id)
    ):
        a.fail("privacy_withdrawn", "Page privacy clearance was withdrawn")
    review = await session.scalar(
        select(ScreenshotPrivacyReview).where(
            ScreenshotPrivacyReview.asset_id == asset.id,
            ScreenshotPrivacyReview.attachment_source_id == row.id,
        )
    )
    if not review or review.reviewed_upload_sha256 != row.preview["sha256"]:
        a.fail("privacy_pending", "This exact page has no human clearance")
    return context


async def resolve_annotation(session, actor, task_id, source):
    row = await source_row(session, actor, source.evidence_source_id)
    if row.task_id != task_id:
        raise not_found()
    a.fail("annotation_adapter_not_enabled", "Attachment annotation adapter is not enabled")


async def verify_ancestry(session, actor, asset, storage):
    """Validate authorized fixed ancestry without granting raw-byte permission."""
    context = await rendition_gate(session, actor, asset)
    assert context is not None
    file = context[5]
    limit = min(c.FILE_BYTE_LIMIT, a.settings_for(session).max_upload_bytes)
    original = await storage.read_bounded(
        actor.org_id, file.storage_key, min(limit, file.file["size_bytes"])
    )
    if (
        len(original) != file.file["size_bytes"]
        or hashlib.sha256(original).hexdigest() != file.file["sha256"]
    ):
        a.fail("source_integrity_failure", "Original ancestry failed integrity checks", 502, 4)
    source = await a.required(session, EvidenceSource, asset.evidence_source_id)
    descriptor = EvidenceSourcePreview.model_validate(source.preview)
    raw = await storage.read_bounded(
        actor.org_id, source.storage_key, min(limit, descriptor.size_bytes)
    )
    evidence_sources.check_png(raw, descriptor)
    if asset.source_sha256 != descriptor.sha256:
        a.fail("source_integrity_failure", "Page ancestry failed integrity checks", 502, 4)
