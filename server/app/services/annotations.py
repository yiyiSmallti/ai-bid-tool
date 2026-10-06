"""Cloud annotation admission, immutable history and live approval resolution.

A candidate is preparation, never approval. The canonical Evidence keeps its
candidate binding; only an exact current human decision can resolve release bytes.
"""

import hashlib
import json
from datetime import datetime
from typing import NoReturn, cast, get_args
from uuid import UUID, uuid4

from psycopg import InterfaceError as DriverInterfaceError
from psycopg import OperationalError as DriverOperationalError
from pydantic import ValidationError
from sqlalchemy import literal, select, tuple_
from sqlalchemy.exc import InterfaceError, OperationalError

from app.core.errors import ServiceError, not_found
from app.models.annotations import AnnotationMaterial, AnnotationRelease, AnnotationRequest
from app.models.entities import Job, Requirement
from app.models.response_cards import CardEvidenceLink, Evidence
from app.models.screenshots import (
    ScreenshotPrivacyReview,
    ScreenshotRendition,
    ScreenshotWithdrawal,
)
from app.providers.annotation_renderer import (
    AnnotationRenderer,
    approval_binding_sha256,
    provenance_sha256,
)
from app.schemas import annotation_contracts as schema
from app.schemas.screenshot_contracts import PixelRect, PNGDescriptor
from app.services import (
    budget_preflight,
    evidence_sources,
    requirement_consumption,
    task_cosign,
    task_events,
    task_workflow,
)
from app.services import response_cards as cards
from app.services.auth import HUMAN_ONLY_SCOPES, ROLE_SCOPES, Identity, set_actor_context
from app.services.versioned import audit

CANDIDATE_PROFILE = "annotation-candidate-v1"
RELEASE_PROFILE = "annotation-release-v1"
PROFILES = {CANDIDATE_PROFILE, RELEASE_PROFILE}
ENABLED_SOURCE_KINDS = schema.ENABLED_SOURCE_KINDS
READ_SCOPES = (
    "card:read",
    "screenshot:read",
    "evidence:source:read",
    "certificate:read",
    "certificate:file:read",
)


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()


def fail(code, message, status=409, exit_code=2) -> NoReturn:
    raise ServiceError(code, message, status, exit_code)


def renderer_for(processor=None):
    return getattr(processor, "annotation_renderer", None) or AnnotationRenderer()


async def access(session, actor, task_id, *, create=False, lock=False):
    result = await task_workflow.access(
        session,
        actor,
        task_id,
        scope="evidence:annotate" if create else "card:read",
        write=create,
        require_member=True,
        lock=lock,
    )
    for scope in READ_SCOPES:
        actor.require(scope)
    if create:
        actor.require("card:write")
    return result


async def worker_access(session, job, *, bind_context=True):
    """Check the saved human ceiling without granting a worker a human scope."""
    if (
        job.actor_kind != "session"
        or job.actor_token_id is not None
        or job.agent_principal_id is not None
        or job.actor_user_id is None
    ):
        fail("forbidden", "Annotation jobs require a fixed human request", 403, 4)
    actor = Identity(
        job.actor_user_id,
        job.org_id,
        set(job.actor_scopes) - HUMAN_ONLY_SCOPES,
        "viewer",
        actor_kind="worker",
        job_id=job.id,
        run_id=job.run_id,
    )
    actor = await task_workflow.live_actor(session, actor)
    _, workflow, member = await task_workflow.access(
        session,
        actor,
        job.task_id,
        scope="card:read",
        require_member=True,
        bind_context=bind_context,
    )
    if workflow.state == "archived":
        fail("task_archived", "Task is archived")
    if job.kind == "annotation_render" and (
        "evidence:annotate" not in job.actor_scopes
        or "evidence:annotate" not in ROLE_SCOPES[actor.role]
        or member is None
        or member.role not in {"owner", "contributor"}
    ):
        fail("forbidden", "Annotation initiator no longer has create authority", 403, 4)
    for scope in READ_SCOPES:
        actor.require(scope)
    if bind_context:
        await set_actor_context(session, actor)
    return actor


def card_content_hash(revision):
    return digest(
        {
            field: getattr(revision, field)
            for field in (*cards.CONTENT_FIELDS, "review_domain", "disposition", "quote_sha256")
        }
    )


async def requirement_pin(session, requirement, *, confirmed=False):
    reviews = await (
        requirement_consumption.require_confirmed(session, [requirement])
        if confirmed
        else requirement_consumption.effective(session, [requirement])
    )
    review = reviews[requirement.id]
    return schema.AnnotationRequirementPin(
        requirement_id=requirement.id,
        review_revision=review.revision,
        review_hash=review.review_hash,
        state=review.state,
        source_binding_sha256=review.source_pin.binding_sha256 if review.source_pin else None,
    )


async def source_binding(session, actor, task_id, source):
    if source.kind != "certificate_page":
        fail(
            "annotation_source_unavailable",
            "vendor_source_not_enabled: only certificate archive pages are enabled",
            400,
        )
    archived, selected, original = await evidence_sources.require_source(
        session, actor, source.evidence_source_id
    )
    if archived.task_id != task_id:
        raise not_found()
    archive = schema.EvidenceSourceArchive.model_validate(
        evidence_sources.source_data(archived, selected, original)
    )
    preview = archive.preview
    return schema.CertificatePageBinding(
        archive=archive,
        source_png=PNGDescriptor(
            sha256=preview.sha256,
            size_bytes=preview.size_bytes,
            width_px=preview.width_px,
            height_px=preview.height_px,
        ),
        source_time=archive.rendered_at,
        original_sha256=archive.original.sha256,
        selection_id=archive.task_certificate_id,
        resource_revision_id=archive.certificate_revision_id,
        authorized_content=PixelRect(x=0, y=0, width=preview.width_px, height=preview.height_px),
    )


async def manifest_for(session, actor, task_id, target, renderer, *, publication=False):
    card, revision, requirement = await cards.require_card(
        session, target.card_id, lock=publication
    )
    if (card.task_id, card.extraction_job_id) != (task_id, target.extraction_job_id):
        raise not_found()
    await cards.extraction_scope(session, task_id, target.extraction_job_id)
    if card.revision != target.expected_card_revision:
        fail("card_revision_changed", "Card changed; preserve the plan and preview again")
    if (
        revision.state not in {"draft", "rejected", "needs_material"}
        or revision.disposition == "comply_only"
    ):
        fail("card_not_editable", "Reopen or withdraw the card before preparing an annotation")
    binding = await source_binding(session, actor, task_id, target.source)
    if not binding.archive.active_selection:
        fail("source_inactive", "Source belongs to a replaced task selection")
    try:
        manifest = schema.AnnotationInputManifest(
            org_id=actor.org_id,
            task_id=task_id,
            target=target,
            card_content_sha256=card_content_hash(revision),
            requirement=await requirement_pin(session, requirement),
            source=binding,
            plan_sha256=target.plan.canonical_sha256(),
            renderer=renderer,
        )
    except ValidationError as exc:
        raise ServiceError(
            "invalid_annotation_plan",
            "Crop and boxes must be inside the authorized source content",
            422,
            2,
        ) from exc
    return manifest, card, revision, requirement


def render_request(manifest, *, renderer=None, approval=None, content_hash=None):
    identity = renderer or manifest.renderer
    payload = dict(
        source=manifest.source,
        plan=manifest.target.plan,
        plan_sha256=manifest.plan_sha256,
        renderer=identity,
        content_mode="marked_candidate_content" if approval else "source_png",
        input_content_sha256=content_hash or manifest.source.source_png.sha256,
        provenance_sha256="0" * 64,
        approval=approval,
    )
    request = schema.AnnotationRenderRequest.model_validate(payload)
    return request.model_copy(update={"provenance_sha256": provenance_sha256(request)})


async def preflight(session, actor, task_id, body, storage, settings, renderer=None):
    await access(session, actor, task_id, create=True)
    if body.input.source.kind != "certificate_page":
        fail(
            "annotation_source_unavailable",
            "vendor_source_not_enabled: only certificate archive pages are enabled",
            400,
        )
    renderer = renderer or renderer_for()
    manifest, _, _, _ = await manifest_for(
        session, actor, task_id, body.input, renderer.identity(CANDIDATE_PROFILE)
    )
    # Reading and validating the exact archive is part of preflight, without any
    # job, storage, review, audit or funds reservation side effect.
    content, _ = await evidence_sources.read_preview(
        session, actor, body.input.source.evidence_source_id, storage
    )
    if len(content) > min(schema.PNG_BYTE_LIMIT, settings.max_upload_bytes):
        fail("output_limit_exceeded", "Source PNG exceeds the configured byte limit", 413)
    canvas = await renderer.predict(render_request(manifest))
    data = await budget_preflight.attach(
        session,
        {},
        command="evidence stamp",
        task_id=task_id,
        input_hash=manifest.canonical_sha256(),
        currency=settings.billing_currency,
        settings=settings,
        quotes=[],
        planned_calls=0,
    )
    data["budget_preflight"]["maximum_calls"] = 0
    return schema.AnnotationPreflight(
        input_hash=manifest.canonical_sha256(),
        manifest=manifest,
        predicted_output=canvas,
        budget_preflight=data["budget_preflight"],
    )


def request_digest(body):
    return digest(body.model_dump(mode="json", exclude={"retry"}))


def job_receipt(job, card_id, duplicate=False):
    return schema.AnnotationJobAccepted(
        job_id=job.id,
        task_id=job.task_id,
        card_id=card_id,
        kind=job.kind,
        status=job.status,
        duplicate=duplicate,
    )


async def enqueue(session, queue, job, *, retry=False):
    try:
        if retry:
            if job.status not in {"failed", "cancelled"}:
                fail(
                    "annotation_retry_not_terminal", "Only a terminal job can be explicitly retried"
                )
            job.status, job.error, job.finished_at, job.run_id, job.lease_until, job.attempts = (
                "queued",
                None,
                None,
                None,
                None,
                0,
            )
            job.queue_id = await queue.enqueue_in_transaction(session, str(job.org_id), str(job.id))
        elif job.status == "queued":
            await queue.ensure_process_delivery(session, job)
    except (
        OSError,
        InterfaceError,
        OperationalError,
        DriverInterfaceError,
        DriverOperationalError,
    ) as exc:
        raise ServiceError(
            "annotation_queue_unavailable", "Annotation queue is temporarily unavailable", 503, 3
        ) from exc


async def submit(session, actor, task_id, body, storage, queue, settings, renderer=None):
    await access(session, actor, task_id, create=True, lock=True)
    saved = await session.scalar(
        select(AnnotationRequest).where(
            AnnotationRequest.task_id == task_id,
            AnnotationRequest.actor_user_id == actor.user_id,
            AnnotationRequest.request_id == body.request_id,
        )
    )
    if saved is not None:
        if saved.request_sha256 != request_digest(body):
            fail("annotation_request_conflict", "Request ID was used with different input")
        job = await session.get(Job, saved.job_id)
        if job is None:
            raise not_found()
        await source_binding(session, actor, task_id, body.input.source)
        if body.retry:
            current, _, _, _ = await manifest_for(
                session,
                actor,
                task_id,
                body.input,
                schema.AnnotationRendererIdentity.model_validate(saved.renderer),
                publication=True,
            )
            if not preparation_matches(
                current, schema.AnnotationInputManifest.model_validate(saved.manifest)
            ):
                fail("annotation_input_changed", "Pinned annotation input changed; preview again")
        await enqueue(session, queue, job, retry=body.retry)
        if body.retry:
            audit(
                session,
                actor,
                "annotation.retry",
                job.id,
                {"task_id": str(task_id), "input_hash": saved.input_hash},
            )
        return job_receipt(job, saved.card_id, True)
    if body.retry:
        fail("annotation_retry_missing", "Retry must identify the original request")
    preview = await preflight(
        session,
        actor,
        task_id,
        schema.AnnotationPreflightRequest(input=body.input),
        storage,
        settings,
        renderer,
    )
    if (
        body.expected_input_hash != preview.input_hash
        or body.reviewed_source_png_sha256 != preview.manifest.source.source_png.sha256
    ):
        fail("annotation_input_changed", "Preview or reviewed source changed; preview again")
    manifest = preview.manifest
    _, card, revision, requirement = await manifest_for(
        session, actor, task_id, body.input, manifest.renderer, publication=True
    )
    extraction = await session.get(Job, body.input.extraction_job_id)
    assert extraction is not None
    request_id = uuid4()
    job = Job(
        id=uuid4(),
        org_id=actor.org_id,
        task_id=task_id,
        document_id=extraction.document_id,
        kind="annotation_render",
        status="queued",
        attempts=0,
        cache_key=digest(
            {
                "kind": "annotation_render",
                "org": str(actor.org_id),
                "task": str(task_id),
                "actor": str(actor.user_id),
                "request": str(body.request_id),
                "input": preview.input_hash,
            }
        ),
        actor_user_id=actor.user_id,
        actor_token_id=None,
        actor_kind="session",
        actor_scopes=sorted(actor.scopes),
        command="evidence stamp",
        result={
            "submission": {
                "annotation_request_id": str(request_id),
                "input_manifest": manifest.model_dump(mode="json"),
                "input_hash": preview.input_hash,
                "actor_user_id": str(actor.user_id),
                "actor_token_id": None,
                "actor_kind": "session",
                "scopes": sorted(actor.scopes),
            },
            "card_id": str(card.id),
            "extraction_job_id": str(extraction.id),
        },
    )
    session.add(job)
    await session.flush()
    source = manifest.source
    assert isinstance(source, schema.CertificatePageBinding)
    row = AnnotationRequest(
        id=request_id,
        org_id=actor.org_id,
        task_id=task_id,
        extraction_job_id=extraction.id,
        requirement_id=requirement.id,
        card_id=card.id,
        expected_card_revision=card.revision,
        expected_card_revision_id=revision.id,
        actor_user_id=actor.user_id,
        request_id=body.request_id,
        request_sha256=request_digest(body),
        input_hash=preview.input_hash,
        plan_sha256=manifest.plan_sha256,
        reviewed_source_sha256=body.reviewed_source_png_sha256,
        manifest=manifest.model_dump(mode="json"),
        plan=body.input.plan.model_dump(mode="json"),
        renderer=manifest.renderer.model_dump(mode="json"),
        privacy_attestation={
            "reviewed_source_sha256": body.reviewed_source_png_sha256,
            "reviewed_by": str(actor.user_id),
        },
        job_id=job.id,
        source_kind="certificate_page",
        evidence_source_id=source.archive.id,
        task_certificate_id=source.selection_id,
        certificate_id=source.archive.certificate_id,
        certificate_revision_id=source.resource_revision_id,
        certificate_file_id=source.archive.certificate_file_id,
    )
    session.add(row)
    await session.flush()
    await enqueue(session, queue, job)
    audit(
        session,
        actor,
        "annotation.submit",
        job.id,
        {
            "task_id": str(task_id),
            "card_id": str(card.id),
            "request_id": str(row.id),
            "input_hash": row.input_hash,
            "job_id": str(job.id),
            "source_id": str(source.archive.id),
            "plan_sha256": manifest.plan_sha256,
            "profile": manifest.renderer.profile,
        },
    )
    return job_receipt(job, card.id)


def preparation_matches(current, pinned):
    left, right = current.model_dump(mode="json"), pinned.model_dump(mode="json")
    # B02 decision-only changes cannot discard a prepared material. Meaning and
    # verified citation/source hashes remain fixed, including during worker retry.
    for value in (left, right):
        value["requirement"].pop("review_revision")
        value["requirement"].pop("state")
    return left == right


async def material_row(session, actor, annotation_id):
    row = await session.get(AnnotationMaterial, annotation_id)
    if row is None:
        raise not_found()
    await access(session, actor, row.task_id)
    manifest = schema.AnnotationInputManifest.model_validate(row.manifest)
    await source_binding(session, actor, row.task_id, manifest.target.source)
    return row


def candidate_view(row):
    return schema.AnnotationCandidateView(
        id=row.id,
        org_id=row.org_id,
        task_id=row.task_id,
        job_id=row.job_id,
        input_hash=row.input_hash,
        manifest=row.manifest,
        asset_id=row.asset_id,
        rendition_id=row.rendition_id,
        rendering=row.rendering,
        created_at=row.created_at,
    )


async def material_validity(session, actor, row, *, storage=None):
    manifest = schema.AnnotationInputManifest.model_validate(row.manifest)
    binding = await source_binding(session, actor, row.task_id, manifest.target.source)
    warnings = []
    if not binding.archive.active_selection:
        warnings.append("source_inactive")
    withdrawn = bool(
        await session.scalar(
            select(ScreenshotWithdrawal.id).where(ScreenshotWithdrawal.asset_id == row.asset_id)
        )
    )
    if withdrawn:
        warnings.append("source_withdrawn")
    normalized = binding.model_dump(mode="json")
    normalized["archive"]["active_selection"] = True
    if normalized != manifest.source.model_dump(mode="json"):
        warnings.append("source_integrity_failure")
    requirement = await session.get(Requirement, row.requirement_id)
    if requirement is None:
        warnings.append("requirement_review_not_current")
    else:
        pin = await requirement_pin(session, requirement)
        if (
            pin.review_hash != manifest.requirement.review_hash
            or pin.source_binding_sha256 != manifest.requirement.source_binding_sha256
        ):
            warnings.append("requirement_review_not_current")
    rendition = await session.get(ScreenshotRendition, row.rendition_id)
    review = (
        await session.get(ScreenshotPrivacyReview, rendition.privacy_review_id)
        if rendition
        else None
    )
    if (
        review is None
        or review.annotation_request_id != row.request_id
        or review.reviewed_upload_sha256 != binding.source_png.sha256
    ):
        warnings.append("privacy_lineage_invalid")
    if storage is not None and not warnings:
        from app.models.screenshots import ScreenshotAsset
        from app.services.screenshots import read_rendition

        await evidence_sources.read_preview(session, actor, binding.archive.id, storage)
        asset = await session.get(ScreenshotAsset, row.asset_id)
        await read_rendition(storage, asset, rendition)
    return schema.AnnotationCurrentStatus(
        validity="stale" if warnings else "current",
        active_selection=binding.archive.active_selection,
        source_withdrawn=withdrawn,
        warning_codes=warnings,
    )


async def require_current_material(session, actor, row, *, storage=None):
    current = await material_validity(session, actor, row, storage=storage)
    if current.validity != "current":
        fail(current.warning_codes[0], "Annotation source or preparation inputs are stale")
    return current


async def show(session, actor, annotation_id):
    row = await material_row(session, actor, annotation_id)
    return schema.AnnotationShowData(
        candidate=candidate_view(row), current=await material_validity(session, actor, row)
    )


async def page(session, actor, task_id, query, settings, *, annotation=None):
    _, workflow, _ = await access(session, actor, task_id)
    model = AnnotationRelease if annotation else AnnotationMaterial
    purpose = "annotation-releases" if annotation else "annotations"
    binding = {
        "annotation": str(annotation.id) if annotation else None,
        "card": str(query.card_id) if query.card_id else None,
    }
    statement = select(model).where(model.task_id == task_id)
    if annotation:
        statement = statement.where(AnnotationRelease.annotation_id == annotation.id)
    elif query.card_id:
        card, _, _ = await cards.require_card(session, query.card_id)
        if card.task_id != task_id:
            raise not_found()
        statement = statement.where(model.card_id == query.card_id)
    if query.cursor:
        cursor = task_events.open_cursor(
            query.cursor, actor, task_id, workflow, settings, purpose=purpose
        )
        if cursor.get("b") != binding:
            fail("invalid_annotation_cursor", "Cursor does not match this list", 422)
        try:
            when, ident = datetime.fromisoformat(cursor["at"]), UUID(cursor["id"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ServiceError("invalid_annotation_cursor", "Invalid cursor", 422, 2) from exc
        statement = statement.where(
            tuple_(model.created_at, model.id) > tuple_(literal(when), literal(ident))
        )
    rows = list(
        await session.scalars(statement.order_by(model.created_at, model.id).limit(query.limit + 1))
    )
    selected = rows[: query.limit]
    more = len(rows) > query.limit
    cursor = (
        task_events.issue_cursor(
            actor,
            task_id,
            workflow,
            settings,
            purpose=purpose,
            b=binding,
            at=selected[-1].created_at.isoformat(),
            id=str(selected[-1].id),
        )
        if more
        else None
    )
    data = dict(task_id=task_id, next_cursor=cursor, returned=len(selected), has_more=more)
    if annotation:
        return schema.AnnotationReleaseListData.model_validate(
            {"annotation_id": annotation.id, **data}
        ), [await release_view(session, actor, row) for row in selected]
    # Source grants are common to the task; no storage reads or signed links here.
    return schema.AnnotationListData.model_validate(data), [candidate_view(row) for row in selected]


async def list_candidates(session, actor, task_id, query, settings):
    return await page(session, actor, task_id, query, settings)


async def list_releases(session, actor, annotation_id, query, settings):
    row = await material_row(session, actor, annotation_id)
    return await page(session, actor, row.task_id, query, settings, annotation=row)


async def approval_binding(session, actor, material, evidence_id, *, storage=None):
    """Resolve the actual current card decision; never choose latest history."""
    await require_current_material(session, actor, material, storage=storage)
    evidence = await session.get(Evidence, evidence_id, populate_existing=True)
    if evidence is None or evidence.task_id != material.task_id:
        raise not_found()
    card, revision, requirement = await cards.require_card(session, evidence.card_id)
    linked = await session.scalar(
        select(CardEvidenceLink.id).where(
            CardEvidenceLink.card_id == card.id,
            CardEvidenceLink.revision_id == revision.id,
            CardEvidenceLink.evidence_id == evidence.id,
        )
    )
    if (
        linked is None
        or revision.state != "confirmed"
        or revision.confirmed_by is None
        or revision.confirmed_at is None
        or evidence.confirmed_by is None
        or evidence.confirmed_at is None
    ):
        fail(
            "approval_not_complete",
            "The exact linked card revision needs complete human confirmation",
        )
    candidate = candidate_view(material)
    if evidence.kind != "image_region" or (
        evidence.screenshot_asset_id,
        evidence.screenshot_rendition_id,
        evidence.image_sha256,
    ) != (material.asset_id, material.rendition_id, candidate.rendering.image.sha256):
        fail("release_binding_changed", "Evidence does not bind this candidate image")
    review = (await task_cosign.projections(session, actor.org_id, [card.id]))[card.id]
    if not review["approved"]:
        fail("approval_stale", "Review policy, signers or source inputs changed")
    pin = await requirement_pin(session, requirement, confirmed=True)
    co_sign = task_cosign.manifest_fields(review).get("co_sign")
    if revision.review_domain not in {"commercial", "technical"}:
        fail("approval_not_complete", "A classified review domain is required")
    values = dict(
        evidence_id=evidence.id,
        card_id=card.id,
        card_revision=card.revision,
        card_revision_id=revision.id,
        confirmed_by=revision.confirmed_by,
        confirmed_at=revision.confirmed_at,
        candidate_annotation_id=material.id,
        candidate_rendition_id=material.rendition_id,
        candidate_image_sha256=candidate.rendering.image.sha256,
        candidate_plan_sha256=candidate.rendering.plan_sha256,
        candidate_content_mapping=candidate.rendering.canvas.mapping,
        content_pixel_sha256=candidate.rendering.content_pixel_sha256,
        root_mapping_sha256=candidate.rendering.root_mapping_sha256,
        reviewed_region=PixelRect.model_validate(evidence.region),
        requirement=pin,
        decision_kind="cosign"
        if len(review["manifest"]["required_domains"]) > 1
        else "single_domain",
        review_domain=revision.review_domain,
        co_sign=co_sign,
        card_content_sha256=card_content_hash(revision),
        evidence_binding_sha256=digest(
            {
                "id": str(evidence.id),
                "asset_id": str(evidence.screenshot_asset_id),
                "rendition_id": str(evidence.screenshot_rendition_id),
                "image_sha256": evidence.image_sha256,
                "region": evidence.region,
                "claim_scope": evidence.claim_scope,
                "visual_observation": evidence.visual_observation,
                "source_sha256": evidence.source_sha256,
            }
        ),
        approval_binding_sha256="0" * 64,
    )
    approval = schema.AnnotationApprovalBinding.model_validate(values)
    return approval.model_copy(
        update={"approval_binding_sha256": approval_binding_sha256(approval)}
    )


async def enqueue_releases(session, actor, card, revision, *, queue=None):
    """Called only inside the final successful human confirmation transaction."""
    if actor.actor_kind != "session" or actor.token_id is not None:
        fail("forbidden", "Only a completed human decision can admit release rendering", 403, 4)
    materials = list(
        (
            await session.execute(
                select(AnnotationMaterial, Evidence)
                .join(Evidence, Evidence.screenshot_rendition_id == AnnotationMaterial.rendition_id)
                .join(CardEvidenceLink, CardEvidenceLink.evidence_id == Evidence.id)
                .where(
                    CardEvidenceLink.card_id == card.id, CardEvidenceLink.revision_id == revision.id
                )
            )
        ).all()
    )
    if not materials:
        return
    queue = queue or session.info.get("annotation_queue")
    if queue is None:
        fail("annotation_queue_unavailable", "Release queue is unavailable", 503, 3)
    # Signers may be reviewers, without evidence:annotate. Approval, not annotation
    # creation authority, is the authorization for these fixed automatic jobs.
    for material, evidence in materials:
        approval = await approval_binding(session, actor, material, evidence.id)
        manifest = schema.AnnotationInputManifest.model_validate(material.manifest)
        renderer = manifest.renderer.model_copy(update={"profile": RELEASE_PROFILE})
        cache_key = digest(
            {
                "kind": "annotation_release",
                "org_id": str(actor.org_id),
                "task_id": str(card.task_id),
                "evidence": str(evidence.id),
                "card_revision_id": str(revision.id),
                "approval": approval.approval_binding_sha256,
                "renderer": renderer.model_dump(mode="json"),
            }
        )
        existing = await session.scalar(select(Job).where(Job.cache_key == cache_key))
        if existing is not None:
            await enqueue(session, queue, existing)
            continue
        extraction = await session.get(Job, card.extraction_job_id)
        assert extraction is not None
        job = Job(
            id=uuid4(),
            org_id=actor.org_id,
            task_id=card.task_id,
            document_id=extraction.document_id,
            kind="annotation_release",
            status="queued",
            attempts=0,
            cache_key=cache_key,
            actor_user_id=actor.user_id,
            actor_token_id=None,
            actor_kind="session",
            actor_scopes=sorted(actor.scopes),
            command="evidence annotation release",
            result={
                "submission": {
                    "input_manifest": manifest.model_dump(mode="json"),
                    "input_hash": approval.approval_binding_sha256,
                    "annotation_id": str(material.id),
                    "approval": approval.model_dump(mode="json"),
                    "renderer": renderer.model_dump(mode="json"),
                    "actor_user_id": str(actor.user_id),
                    "actor_token_id": None,
                    "actor_kind": "session",
                    "scopes": sorted(actor.scopes),
                },
                "annotation_id": str(material.id),
                "evidence_id": str(evidence.id),
                "card_id": str(card.id),
                "expected_card_revision": card.revision,
                "expected_approval_binding_hash": approval.approval_binding_sha256,
                "extraction_job_id": str(card.extraction_job_id),
            },
        )
        session.add(job)
        await session.flush()
        await enqueue(session, queue, job)
        audit(
            session,
            actor,
            "annotation.release.enqueue",
            job.id,
            {
                "task_id": str(card.task_id),
                "card_id": str(card.id),
                "annotation_id": str(material.id),
                "approval_sha256": approval.approval_binding_sha256,
                "job_id": str(job.id),
                "source_id": str(manifest.source.archive.id),
                "plan_sha256": manifest.plan_sha256,
                "profile": renderer.profile,
            },
        )


async def retry_release(session, actor, annotation_id, body, queue):
    material = await material_row(session, actor, annotation_id)
    await access(session, actor, material.task_id, create=True, lock=True)
    job = await session.get(Job, body.job_id, populate_existing=True)
    if (
        job is None
        or job.task_id != material.task_id
        or job.kind != "annotation_release"
        or job.result.get("annotation_id") != str(material.id)
    ):
        raise not_found()
    approval = await approval_binding(session, actor, material, body.evidence_id)
    if (approval.card_id, approval.card_revision, approval.approval_binding_sha256) != (
        body.card_id,
        body.expected_card_revision,
        body.expected_approval_binding_hash,
    ) or job.result["submission"]["approval"] != approval.model_dump(mode="json"):
        fail("approval_stale", "Retry must retain the exact complete human approval")
    # Keep the original final signer as execution initiator, including when another
    # owner recovers delivery. Retry identity belongs only to the audit receipt.
    replay_hash = digest(body.model_dump(mode="json"))
    previous = (job.result.get("release_retries") or {}).get(str(body.request_id))
    if previous:
        if previous != replay_hash:
            fail("annotation_request_conflict", "Retry request ID was used for different input")
        return job_receipt(job, body.card_id, True)
    await worker_access(session, job, bind_context=False)
    await enqueue(session, queue, job, retry=True)
    job.result = {
        **job.result,
        "release_retries": {
            **job.result.get("release_retries", {}),
            str(body.request_id): replay_hash,
        },
    }
    audit(
        session,
        actor,
        "annotation.retry",
        job.id,
        {
            "task_id": str(material.task_id),
            "annotation_id": str(material.id),
            "approval_sha256": approval.approval_binding_sha256,
        },
    )
    return job_receipt(job, body.card_id)


async def release_row(session, actor, release_id):
    row = await session.get(AnnotationRelease, release_id)
    if row is None:
        raise not_found()
    await material_row(session, actor, row.annotation_id)
    return row


async def validate_release(session, actor, row, *, storage=None):
    material = await material_row(session, actor, row.annotation_id)
    approval = await approval_binding(session, actor, material, row.evidence_id, storage=storage)
    if (
        row.approval != approval.model_dump(mode="json")
        or row.approval_sha256 != approval.approval_binding_sha256
    ):
        fail("approval_stale", "This release belongs to an obsolete approval")
    rendered = schema.AnnotationRendering.model_validate(row.rendering)
    if (
        rendered.content_pixel_sha256 != material.content_pixel_sha256
        or rendered.root_mapping_sha256 != material.root_mapping_sha256
    ):
        fail("release_binding_changed", "Release content differs from approved candidate", 409, 4)
    if storage is not None:
        from app.models.screenshots import ScreenshotAsset
        from app.providers.annotation_renderer import strip_candidate_content
        from app.services.screenshots import read_rendition

        asset = await session.get(ScreenshotAsset, row.asset_id)
        rendition = await session.get(ScreenshotRendition, row.rendition_id)
        png = await read_rendition(storage, asset, rendition)
        # The adapter independently validates decoded pixels and content digest.
        content = strip_candidate_content(png, rendered.canvas.mapping)
        from app.providers.annotation_renderer import decoded_content_sha256

        if decoded_content_sha256(content) != material.content_pixel_sha256:
            fail(
                "release_binding_changed", "Stored release content failed integrity checks", 409, 4
            )
    return approval


async def release_view(session, actor, row):
    blockers: list[schema.AnnotationBlocker] = []
    try:
        await validate_release(session, actor, row)
    except ServiceError as exc:
        if exc.status in {401, 403, 404}:
            raise
        blockers = (
            [cast(schema.AnnotationBlocker, exc.code)]
            if exc.code in get_args(schema.AnnotationBlocker)
            else ["approval_stale"]
        )
    return schema.AnnotationReleaseView(
        id=row.id,
        annotation_id=row.annotation_id,
        job_id=row.job_id,
        asset_id=row.asset_id,
        rendition_id=row.rendition_id,
        approval=row.approval,
        renderer=row.renderer,
        rendering=row.rendering,
        created_at=row.created_at,
        decision_validity="stale" if blockers else "current",
        releasable=not blockers,
        blocker_codes=blockers,
    )


async def release_for_evidence(session, actor, evidence, *, storage=None):
    material = await session.scalar(
        select(AnnotationMaterial).where(
            AnnotationMaterial.rendition_id == evidence.screenshot_rendition_id
        )
    )
    if material is None:
        return None
    approval = await approval_binding(session, actor, material, evidence.id, storage=storage)
    manifest = schema.AnnotationInputManifest.model_validate(material.manifest)
    renderer_identity = digest(
        manifest.renderer.model_copy(update={"profile": RELEASE_PROFILE}).model_dump(mode="json")
    )
    row = await session.scalar(
        select(AnnotationRelease).where(
            AnnotationRelease.evidence_id == evidence.id,
            AnnotationRelease.card_revision_id == approval.card_revision_id,
            AnnotationRelease.approval_sha256 == approval.approval_binding_sha256,
            AnnotationRelease.renderer_identity == renderer_identity,
        )
    )
    if row is None:
        fail(
            "annotation_release_pending",
            "Confirmed release is not ready; inspect or retry its release job",
        )
    await validate_release(session, actor, row, storage=storage)
    return row


async def check_job_access(session, actor, job, *, cancel=False):
    await access(session, actor, job.task_id, create=cancel, lock=cancel)
    submitted = job.result.get("submission", {})
    manifest = schema.AnnotationInputManifest.model_validate(submitted["input_manifest"])
    await source_binding(session, actor, job.task_id, manifest.target.source)
    if cancel:
        _, _, member = await access(session, actor, job.task_id, create=True)
        if actor.user_id != job.actor_user_id and (member is None or member.role != "owner"):
            fail("forbidden", "Only the initiating human or task owner can cancel", 403, 4)


async def attachment_gate(session, actor, rendition_id, *, storage=None):
    row = await session.get(ScreenshotRendition, rendition_id)
    if row is None or row.profile not in PROFILES:
        return
    if row.profile == RELEASE_PROFILE:
        fail(
            "annotation_release_not_source",
            "A confirmed release cannot be attached as new Evidence",
            400,
        )
    material = await session.scalar(
        select(AnnotationMaterial).where(AnnotationMaterial.rendition_id == row.id)
    )
    if material is None:
        fail("source_integrity_failure", "Annotation material binding is missing", 409, 4)
    await access(session, actor, material.task_id, create=True)
    await require_current_material(session, actor, material, storage=storage)


async def rendition_gate(session, actor, row, *, storage=None, active=True):
    if row.profile == RELEASE_PROFILE:
        release = await session.scalar(
            select(AnnotationRelease).where(AnnotationRelease.rendition_id == row.id)
        )
        if release is None:
            fail("release_binding_changed", "Release binding is missing", 409, 4)
        await validate_release(session, actor, release, storage=storage)
    elif row.profile == CANDIDATE_PROFILE and active:
        material = await session.scalar(
            select(AnnotationMaterial).where(AnnotationMaterial.rendition_id == row.id)
        )
        if material is None:
            fail("source_integrity_failure", "Annotation material binding is missing", 409, 4)
        await require_current_material(session, actor, material, storage=storage)


async def board_metadata(session, actor, task_id, card_ids):
    """One bounded metadata query; never reads image objects or signs URLs."""
    if not card_ids or not set(READ_SCOPES) <= actor.scopes:
        return {}
    from sqlalchemy import text

    rows = await session.execute(
        text("""
        SELECT c.id AS card_id, m.id AS annotation_id,
          public.annotation_source_current(c.org_id,m.request_id)
            AND NOT EXISTS(SELECT 1 FROM public.screenshot_withdrawals sw
              WHERE sw.org_id=c.org_id AND sw.asset_id=m.asset_id) AS source_current,
          EXISTS(SELECT 1 FROM public.card_evidence_links l JOIN public.evidence e
            ON (e.org_id,e.id)=(l.org_id,l.evidence_id)
            WHERE l.org_id=c.org_id AND l.revision_id=c.current_revision_id
              AND e.screenshot_rendition_id=m.rendition_id) AS attached,
          j.id AS job_id,j.status AS job_status,
          EXISTS(SELECT 1 FROM public.annotation_releases rel
            WHERE rel.org_id=c.org_id AND rel.annotation_id=m.id
              AND rel.card_revision_id=c.current_revision_id
              AND public.annotation_release_current(c.org_id,rel.id)) AS released
        FROM public.response_cards c
        JOIN LATERAL (
          SELECT mat.* FROM public.annotation_materials mat
          WHERE mat.org_id=c.org_id AND mat.task_id=c.task_id AND (mat.card_id=c.id OR EXISTS(
            SELECT 1 FROM public.card_evidence_links l JOIN public.evidence e
              ON (e.org_id,e.id)=(l.org_id,l.evidence_id)
            WHERE l.org_id=c.org_id AND l.revision_id=c.current_revision_id
              AND e.screenshot_rendition_id=mat.rendition_id))
          ORDER BY mat.created_at DESC,mat.id DESC LIMIT 1
        ) m ON true
        LEFT JOIN LATERAL (
          SELECT job.id,job.status FROM public.jobs job
          WHERE job.org_id=c.org_id AND job.task_id=c.task_id AND job.kind='annotation_release'
            AND job.result->>'annotation_id'=m.id::text
            AND job.result->>'card_id'=c.id::text
            AND job.result->>'expected_card_revision'=c.revision::text
          ORDER BY job.created_at DESC,job.id DESC LIMIT 1
        ) j ON true
        WHERE c.org_id=:org AND c.task_id=:task AND c.id=ANY(CAST(:cards AS uuid[]))
    """),
        {"org": actor.org_id, "task": task_id, "cards": list(card_ids)},
    )
    return {row["card_id"]: dict(row) for row in rows.mappings()}


async def list_jobs(
    session,
    actor,
    task_id,
    kind,
    settings,
    *,
    annotation_id=None,
    extraction_job_id=None,
    cursor=None,
    limit=20,
):
    """Bounded auxiliary job reads for the editor, with every job's live source ACL."""
    _, workflow, _ = await access(session, actor, task_id)
    actor.require("job:read")
    if kind not in {"annotation_render", "annotation_release"}:
        fail("invalid_annotation_job_kind", "Unsupported annotation job kind", 422)
    if limit > 20:
        fail("annotation_job_limit", "Read at most 20 annotation jobs at a time", 422)
    if annotation_id:
        material = await material_row(session, actor, annotation_id)
        if material.task_id != task_id:
            raise not_found()
    if extraction_job_id:
        await cards.extraction_scope(session, task_id, extraction_job_id)
    binding = {
        "kind": kind,
        "annotation_id": str(annotation_id) if annotation_id else None,
        "extraction_job_id": str(extraction_job_id) if extraction_job_id else None,
    }
    statement = select(Job).where(Job.task_id == task_id, Job.kind == kind)
    if annotation_id:
        statement = statement.where(Job.result["annotation_id"].astext == str(annotation_id))
    if extraction_job_id:
        statement = statement.where(
            Job.result["extraction_job_id"].astext == str(extraction_job_id)
        )
    if cursor:
        saved = task_events.open_cursor(
            cursor, actor, task_id, workflow, settings, purpose="annotation-jobs"
        )
        if saved.get("b") != binding:
            fail("invalid_annotation_cursor", "Job cursor filters changed", 422)
        try:
            when, ident = datetime.fromisoformat(saved["at"]), UUID(saved["id"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ServiceError("invalid_annotation_cursor", "Invalid job cursor", 422, 2) from exc
        statement = statement.where(
            tuple_(Job.created_at, Job.id) > tuple_(literal(when), literal(ident))
        )
    rows = list(await session.scalars(statement.order_by(Job.created_at, Job.id).limit(limit + 1)))
    selected, more = rows[:limit], len(rows) > limit
    for job in selected:
        await check_job_access(session, actor, job)
    next_cursor = (
        task_events.issue_cursor(
            actor,
            task_id,
            workflow,
            settings,
            purpose="annotation-jobs",
            b=binding,
            at=selected[-1].created_at.isoformat(),
            id=str(selected[-1].id),
        )
        if more
        else None
    )
    return {
        "task_id": str(task_id),
        "kind": kind,
        "next_cursor": next_cursor,
        "returned": len(selected),
        "has_more": more,
    }, [
        {
            "id": str(job.id),
            "kind": job.kind,
            "status": job.status,
            "attempts": job.attempts,
            "error": job.error,
        }
        for job in selected
    ]
