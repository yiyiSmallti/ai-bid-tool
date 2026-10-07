"""Bounded, cancellable annotation attempts with atomic publication and fencing."""

import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select

from app.core.errors import ServiceError, log_unexpected, not_found
from app.jobs.execution import job_cost, locked_job
from app.models.annotations import AnnotationMaterial, AnnotationRelease, AnnotationRequest
from app.models.entities import EvidenceSource
from app.models.requirement_confirmation import RequirementReview
from app.models.screenshots import ScreenshotAsset, ScreenshotPrivacyReview, ScreenshotRendition
from app.providers.annotation_renderer import strip_candidate_content
from app.providers.screenshot_objects import discard
from app.schemas import annotation_contracts as schema
from app.services import annotations as service
from app.services.versioned import audit

ATTEMPT_DEADLINE_SECONDS = 60.0
CANCEL_POLL_SECONDS = 0.25
_PENDING_UPLOADS: set[asyncio.Task] = set()
_LOGGER = logging.getLogger(__name__)


async def _put_staged(execution, storage, queue, key, output):
    # Some storage SDKs perform blocking I/O in a thread. Keep that coroutine
    # alive after the attempt is cancelled so a late object gets a fresh durable
    # cleanup delivery after its real completion, in addition to the crash grace.
    upload = asyncio.create_task(storage.put(execution.org_id, key, output))
    _PENDING_UPLOADS.add(upload)
    upload.add_done_callback(_PENDING_UPLOADS.discard)
    try:
        await asyncio.shield(upload)
    except asyncio.CancelledError:

        async def late_cleanup():
            try:
                await upload
            except Exception as exc:
                log_unexpected(_LOGGER, "Cancelled annotation upload finished with an error", exc)
            try:
                async with execution.db.transaction(execution.org_id) as session:
                    await queue.enqueue_annotation_cleanup_in_transaction(
                        session, str(execution.org_id), str(execution.job_id), delay=300
                    )
            except Exception as exc:
                # The upload already has a committed cleanup ledger and delivery;
                # this additional delivery handles an SDK request that finishes late.
                log_unexpected(_LOGGER, "Annotation late-upload cleanup delivery failed", exc)

        cleanup = asyncio.create_task(late_cleanup())
        _PENDING_UPLOADS.add(cleanup)
        cleanup.add_done_callback(_PENDING_UPLOADS.discard)
        raise


async def _monitor(execution):
    while True:
        await asyncio.sleep(CANCEL_POLL_SECONDS)
        async with execution.db.transaction(execution.org_id) as session:
            observed = await locked_job(session, execution.job_id)
            if (
                observed is not None
                and observed.status == "succeeded"
                and observed.run_id == execution.run_id
            ):
                return
            job = await execution.owned_job(session)
            actor = await service.worker_access(session, job)
            await service.check_job_access(session, actor, job)
            if job.kind == "annotation_release":
                material = await session.get(
                    AnnotationMaterial, UUID(job.result["submission"]["annotation_id"])
                )
                if material is None:
                    raise not_found()
                approval = await service.approval_binding(
                    session,
                    actor,
                    material,
                    UUID(job.result["submission"]["approval"]["evidence_id"]),
                )
                if approval.model_dump(mode="json") != job.result["submission"]["approval"]:
                    service.fail("approval_stale", "The release approval changed during rendering")


async def process(execution, storage, renderer=None, queue=None):
    from app.providers.storage import annotation_storage

    storage = annotation_storage(storage, execution.settings)
    renderer = renderer or service.renderer_for()
    work = asyncio.create_task(_attempt(execution, storage, renderer, queue))
    monitor = asyncio.create_task(_monitor(execution))
    try:
        async with asyncio.timeout(ATTEMPT_DEADLINE_SECONDS):
            done, _ = await asyncio.wait({work, monitor}, return_when=asyncio.FIRST_COMPLETED)
            if work not in done:
                await monitor
            await work
    except TimeoutError as exc:
        raise ServiceError(
            "annotation_deadline", "Annotation attempt exceeded its 60-second deadline", 503, 3
        ) from exc
    finally:
        for task in (work, monitor):
            if not task.done():
                task.cancel()
        # Cancelling the renderer coroutine terminates, kills if necessary, and
        # reaps its process before this attempt can relinquish the execution slot.
        await asyncio.gather(work, monitor, return_exceptions=True)


async def _attempt(execution, storage, renderer, queue):
    async with execution.db.transaction(execution.org_id) as session:
        job = await execution.owned_job(session)
        actor = await service.worker_access(session, job)
        submitted = job.result["submission"]
        manifest = schema.AnnotationInputManifest.model_validate(submitted["input_manifest"])
        release = job.kind == "annotation_release"
        if not release:
            request_row = await session.get(
                AnnotationRequest, UUID(submitted["annotation_request_id"])
            )
            if request_row is None or request_row.job_id != job.id:
                service.fail("annotation_input_changed", "Immutable request is missing")
            identity = manifest.renderer
            current, _, _, _ = await service.manifest_for(
                session, actor, manifest.task_id, manifest.target, identity, publication=True
            )
            if not service.preparation_matches(current, manifest):
                service.fail("annotation_input_changed", "Fixed annotation inputs changed")
            source = await session.get(EvidenceSource, request_row.evidence_source_id)
            assert source is not None
            input_key = source.storage_key
            descriptor = manifest.source.source_png
            material_id, asset_id, rendition_id, review_id = uuid4(), uuid4(), uuid4(), uuid4()
            parent_id, approval = None, None
        else:
            material = await session.get(AnnotationMaterial, UUID(submitted["annotation_id"]))
            if material is None:
                raise not_found()
            approval = await service.approval_binding(
                session, actor, material, UUID(submitted["approval"]["evidence_id"])
            )
            if approval.model_dump(mode="json") != submitted["approval"]:
                service.fail("approval_stale", "Release approval changed")
            identity = schema.AnnotationRendererIdentity.model_validate(submitted["renderer"])
            candidate = await session.get(ScreenshotRendition, material.rendition_id)
            assert candidate is not None
            input_key, descriptor = (
                candidate.storage_key,
                schema.PNGDescriptor.model_validate(candidate.image),
            )
            material_id, asset_id, rendition_id, review_id = (
                material.id,
                material.asset_id,
                uuid4(),
                candidate.privacy_review_id,
            )
            parent_id = candidate.id
            request_row = await session.get(AnnotationRequest, material.request_id)
            assert request_row is not None
        if renderer.identity(identity.profile) != identity:
            service.fail(
                "annotation_renderer_changed",
                "Pinned renderer build is unavailable; submit a new preview",
                503,
                4,
            )
        source_sha256 = manifest.source.source_png.sha256
        request_id = request_row.id
        task_id = job.task_id
        job_id = job.id
        created_by = job.actor_user_id
        assert task_id is not None and created_by is not None
        card_id = approval.card_id if approval else request_row.card_id
        requirement_id = (
            approval.requirement.requirement_id if approval else request_row.requirement_id
        )
        extraction_id = manifest.target.extraction_job_id
    # The job transaction is closed during storage I/O and renderer execution.
    content = await storage.read_bounded(
        execution.org_id,
        input_key,
        min(schema.PNG_BYTE_LIMIT, execution.settings.max_upload_bytes, descriptor.size_bytes),
    )
    if (
        len(content) != descriptor.size_bytes
        or service.hashlib.sha256(content).hexdigest() != descriptor.sha256
    ):
        service.fail(
            "source_integrity_failure", "Archived annotation input failed integrity checks", 409, 4
        )
    if len(content) > min(schema.PNG_BYTE_LIMIT, execution.settings.max_upload_bytes):
        service.fail("output_limit_exceeded", "Input exceeds configured byte limit", 413)
    if approval is not None:
        content = strip_candidate_content(content, approval.candidate_content_mapping)
    request = service.render_request(
        manifest,
        renderer=identity,
        approval=approval,
        content_hash=service.hashlib.sha256(content).hexdigest(),
    )
    output, rendering = await renderer.render(content, request)
    rendering = schema.AnnotationRendering.model_validate(rendering)
    if len(output) > min(schema.PNG_BYTE_LIMIT, execution.settings.max_upload_bytes):
        service.fail("output_limit_exceeded", "Rendered PNG exceeds configured byte limit", 413)
    # A fake adapter seam remains subject to descriptor and pinned-protocol checks.
    from app.providers.screenshot_renderer import validate_png

    if (
        validate_png(output) != rendering.image.model_dump(mode="json")
        or rendering.renderer != identity
        or rendering.plan_sha256 != manifest.plan_sha256
        or rendering.provenance_sha256 != request.provenance_sha256
    ):
        service.fail(
            "annotation_renderer_integrity", "Renderer receipt differs from actual output", 409, 4
        )
    if approval is not None and (
        rendering.content_pixel_sha256 != approval.content_pixel_sha256
        or rendering.root_mapping_sha256 != approval.root_mapping_sha256
    ):
        service.fail(
            "release_binding_changed", "Release changed approved pixels or source mapping", 409, 4
        )
    key = (
        f"org/{execution.org_id}/screenshots/{asset_id}/{rendition_id}/{rendering.image.sha256}.png"
    )
    from app.services.annotation_objects import stage

    async with execution.db.transaction(execution.org_id) as session:
        owned = await execution.owned_job(session)
        await stage(
            session,
            queue,
            owned,
            execution.run_id,
            key,
            asset_id,
            rendition_id,
            rendering.image.sha256,
        )
    try:
        await _put_staged(execution, storage, queue, key, output)
        # Immutable source bytes are independently checked again before publication.
        if approval is None:
            verified = await storage.read_bounded(
                execution.org_id,
                input_key,
                min(
                    schema.PNG_BYTE_LIMIT,
                    execution.settings.max_upload_bytes,
                    descriptor.size_bytes,
                ),
            )
            if service.hashlib.sha256(verified).hexdigest() != descriptor.sha256:
                service.fail(
                    "source_integrity_failure", "Archived source changed during render", 409, 4
                )
        async with execution.db.transaction(execution.org_id) as session:
            job = await execution.owned_job(session)
            actor = await service.worker_access(session, job)
            if not release:
                current, card, revision, requirement = await service.manifest_for(
                    session, actor, task_id, manifest.target, identity, publication=True
                )
                if not service.preparation_matches(current, manifest):
                    service.fail(
                        "annotation_input_changed",
                        "Fixed annotation input changed during rendering",
                    )
                existing = await session.scalar(
                    select(AnnotationMaterial).where(AnnotationMaterial.request_id == request_id)
                )
                if existing is not None:
                    material_id = existing.id
                    rendition_id = existing.rendition_id
                else:
                    source = manifest.source
                    assert isinstance(source, schema.CertificatePageBinding)
                    asset = ScreenshotAsset(
                        id=asset_id,
                        org_id=execution.org_id,
                        task_id=task_id,
                        extraction_job_id=extraction_id,
                        source_kind="certificate_page",
                        image_kind="certificate_page",
                        origin="certificate",
                        evidence_source_id=source.archive.id,
                        source_sha256=source_sha256,
                        source_hash_assurance="server_verified",
                        source_width=source.source_png.width_px,
                        source_height=source.source_png.height_px,
                        source={
                            "kind": "certificate_page",
                            "evidence_source_id": str(source.archive.id),
                            "annotation_request_id": str(request_id),
                        },
                        received_by=created_by,
                        received_at=datetime.now(UTC),
                        idempotency_key=request_id,
                        request_hash=manifest.canonical_sha256(),
                    )
                    session.add(asset)
                    await session.flush()
                    session.add(
                        _rendition(
                            execution,
                            manifest,
                            rendering,
                            key,
                            asset_id,
                            rendition_id,
                            review_id,
                            created_by,
                            parent_id,
                        )
                    )
                    await session.flush()
                    session.add(
                        ScreenshotPrivacyReview(
                            id=review_id,
                            org_id=execution.org_id,
                            task_id=task_id,
                            extraction_job_id=extraction_id,
                            asset_id=asset_id,
                            rendition_id=rendition_id,
                            reviewed_upload_sha256=source_sha256,
                            stored_image_sha256=rendering.image.sha256,
                            reviewed_by=created_by,
                            rule_version="annotation-certificate-privacy-v1",
                            annotation_request_id=request_id,
                        )
                    )
                    await session.flush()
                    existing = AnnotationMaterial(
                        id=material_id,
                        org_id=execution.org_id,
                        task_id=task_id,
                        extraction_job_id=extraction_id,
                        requirement_id=requirement_id,
                        card_id=card_id,
                        job_id=job_id,
                        request_id=request_id,
                        run_id=execution.run_id,
                        asset_id=asset_id,
                        rendition_id=rendition_id,
                        input_hash=manifest.canonical_sha256(),
                        manifest=manifest.model_dump(mode="json"),
                        rendering=rendering.model_dump(mode="json"),
                        content_pixel_sha256=rendering.content_pixel_sha256,
                        root_mapping_sha256=rendering.root_mapping_sha256,
                    )
                    session.add(existing)
                    await session.flush()
                data = {
                    "annotation_id": str(material_id),
                    "candidate": service.candidate_view(existing).model_dump(mode="json"),
                }
                action = "annotation.publish"
            else:
                material = await session.get(AnnotationMaterial, material_id)
                assert material is not None and approval is not None
                current_approval = await service.approval_binding(
                    session, actor, material, approval.evidence_id
                )
                if current_approval != approval:
                    service.fail("approval_stale", "Approval changed during release rendering")
                review = await session.scalar(
                    select(RequirementReview).where(
                        RequirementReview.requirement_id == requirement_id
                    )
                )
                assert review is not None
                session.add(
                    _rendition(
                        execution,
                        manifest,
                        rendering,
                        key,
                        asset_id,
                        rendition_id,
                        review_id,
                        created_by,
                        parent_id,
                    )
                )
                await session.flush()
                released = AnnotationRelease(
                    id=uuid4(),
                    org_id=execution.org_id,
                    task_id=task_id,
                    extraction_job_id=extraction_id,
                    requirement_id=requirement_id,
                    card_id=card_id,
                    job_id=job_id,
                    annotation_id=material.id,
                    evidence_id=approval.evidence_id,
                    card_revision_id=approval.card_revision_id,
                    requirement_review_id=review.id,
                    requirement_review_revision=approval.requirement.review_revision,
                    run_id=execution.run_id,
                    asset_id=asset_id,
                    rendition_id=rendition_id,
                    approval_sha256=approval.approval_binding_sha256,
                    approval=approval.model_dump(mode="json"),
                    renderer=identity.model_dump(mode="json"),
                    renderer_identity=service.digest(identity.model_dump(mode="json")),
                    confirmed_by=approval.confirmed_by,
                    review_round_id=approval.co_sign.round_id if approval.co_sign else None,
                    commercial_signature_id=next(
                        (s.id for s in approval.co_sign.signatures if s.domain == "commercial"),
                        None,
                    )
                    if approval.co_sign
                    else None,
                    technical_signature_id=next(
                        (s.id for s in approval.co_sign.signatures if s.domain == "technical"), None
                    )
                    if approval.co_sign
                    else None,
                    rendering=rendering.model_dump(mode="json"),
                    content_pixel_sha256=rendering.content_pixel_sha256,
                    root_mapping_sha256=rendering.root_mapping_sha256,
                )
                session.add(released)
                await session.flush()
                data = {"annotation_id": str(material.id), "release_id": str(released.id)}
                action = "annotation.release.publish"
            await execution.owned_job(session)
            audit(
                session,
                actor,
                action,
                job.id,
                {
                    "task_id": str(task_id),
                    "card_id": str(card_id),
                    "annotation_id": str(material_id),
                    "run_id": str(execution.run_id),
                    "image_sha256": rendering.image.sha256,
                    "job_id": str(job.id),
                    "input_hash": manifest.canonical_sha256(),
                    "source_id": str(manifest.source.archive.id),
                    "plan_sha256": manifest.plan_sha256,
                    "profile": rendering.renderer.profile,
                    "approval_sha256": approval.approval_binding_sha256 if approval else None,
                },
            )
            job.status, job.finished_at, job.error = "succeeded", datetime.now(UTC), None
            job.result = {
                **job.result,
                **data,
                "cost": await job_cost(session, job.id, execution.settings.billing_currency),
            }
    finally:
        # Only this attempt's fresh key is considered. A lost lease cannot delete
        # bytes retained by any committed rendition, even after duplicate delivery.
        async def cleanup():
            async with execution.db.transaction(execution.org_id) as session:
                retained = await session.scalar(
                    select(ScreenshotRendition.id).where(ScreenshotRendition.storage_key == key)
                )
            if retained is None:
                await discard(storage, execution.org_id, key)

        await asyncio.shield(cleanup())


def _rendition(
    execution, manifest, rendering, key, asset_id, rendition_id, review_id, actor_id, parent_id
):
    return ScreenshotRendition(
        id=rendition_id,
        org_id=execution.org_id,
        task_id=manifest.task_id,
        extraction_job_id=manifest.target.extraction_job_id,
        asset_id=asset_id,
        parent_rendition_id=parent_id,
        privacy_review_id=review_id,
        source_sha256=manifest.source.source_png.sha256,
        upload_sha256=manifest.source.source_png.sha256,
        image_sha256=rendering.image.sha256,
        plan_sha256=manifest.plan_sha256,
        plan=manifest.target.plan.model_dump(mode="json"),
        mapping=rendering.canvas.mapping.model_dump(mode="json"),
        image=rendering.image.model_dump(mode="json"),
        profile=rendering.renderer.profile,
        storage_key=key,
        generation_job_id=execution.job_id,
        actor_user_id=actor_id,
        renderer_identity=rendering.renderer.model_dump(mode="json"),
        provenance_sha256=rendering.provenance_sha256,
    )
