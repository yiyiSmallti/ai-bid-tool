"""Bounded image jobs with fixed inputs, live authorization and accounting fences."""

from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select, tuple_

from app.core.errors import ServiceError, not_found
from app.jobs.execution import job_cost
from app.models.entities import Job, Task, VendorCall
from app.models.screenshots import (
    ScreenshotAnalysisInput,
    ScreenshotAnalysisRun,
    ScreenshotRendition,
    ScreenshotSuggestion,
)
from app.providers.base import ProviderFailure
from app.providers.llm import HTTPExtractor, with_reasoning
from app.schemas.screenshot_contracts import ImagePlan, ScreenshotAnalyzeInput
from app.services import billing, redaction
from app.services import response_cards as cards
from app.services import screenshots as images
from app.services.card_generation import model_identity, worker
from app.services.task_authorization import task_authorized
from app.services.versioned import audit

RULE_VERSION = "screenshot-analysis-v1"


def submission(actor, manifest):
    return {
        "input_manifest": manifest,
        "input_hash": images.digest(manifest),
        "actor_user_id": str(actor.user_id),
        "actor_token_id": str(actor.token_id) if actor.token_id else None,
        "actor_kind": actor.actor_kind,
        "scopes": sorted(actor.scopes),
    }


async def create_job(session, actor, task_id, extraction, kind, manifest, retry, *, reasoning=None):
    cache_key = images.digest(
        {
            "org_id": str(actor.org_id),
            "task_id": str(task_id),
            "kind": kind,
            "manifest": manifest,
            "actor_user_id": str(actor.user_id),
            "actor_token_id": str(actor.token_id) if actor.token_id else None,
        }
    )
    job = await session.scalar(select(Job).where(Job.cache_key == cache_key).with_for_update())
    cached = job is not None
    if job is None:
        job = Job(
            id=uuid4(),
            org_id=actor.org_id,
            task_id=task_id,
            document_id=extraction.document_id,
            kind=kind,
            cache_key=cache_key,
            status="queued",
            reasoning=reasoning,
            provider_identity=manifest.get("model"),
            provider_config_id=UUID(manifest["model"]["provider_config_id"])
            if (manifest.get("model") or {}).get("provider_config_id")
            else None,
            result={"submission": submission(actor, manifest)},
        )
        session.add(job)
        await session.flush()
        audit(
            session,
            actor,
            f"{kind}.submit",
            job.id,
            {"input_hash": images.digest(manifest), "actor_kind": actor.actor_kind},
        )
    elif retry and (
        job.status in {"failed", "cancelled"}
        or (job.status == "running" and job.lease_until and job.lease_until < datetime.now(UTC))
    ):
        job.status, job.error, job.queue_id, job.run_id, job.lease_until, job.finished_at = (
            "queued",
            None,
            None,
            None,
            None,
            None,
        )
    return {"job_id": str(job.id), "status": job.status, "cached": cached}, job


async def render_manifest(session, actor, asset_id, parent_id, expected_hash, plan, storage=None):
    asset, parent = await images.rendition_access(session, actor, parent_id, storage=storage)
    if asset.source_kind == "attachment_page":
        images.fail(
            "annotation_adapter_not_enabled", "Attachment annotation adapter is not enabled", 409
        )
    if asset.id != asset_id:
        raise not_found()
    if parent.profile.startswith("annotation-"):
        images.fail(
            "annotation_parent_forbidden",
            "Create a new annotation preview from the archived source",
            400,
        )
    if parent.image_sha256 != expected_hash:
        images.fail("image_hash_mismatch", "Parent image changed", 409)
    images.validate_plan(plan, parent.mapping["content_width"], parent.mapping["content_height"])
    return (
        {
            "task_id": str(asset.task_id),
            "extraction_job_id": str(asset.extraction_job_id),
            "asset_id": str(asset.id),
            "parent_rendition_id": str(parent.id),
            "image_sha256": parent.image_sha256,
            "privacy_review_id": str(parent.privacy_review_id),
            "plan": plan.model_dump(mode="json"),
            "profile": parent.profile,
            "rule_version": RULE_VERSION,
        },
        asset,
        parent,
    )


@task_authorized("screenshot:write", parent=("asset_id", "screenshot_assets"), write=True)
async def submit_render(session, actor, asset_id, body, storage, billing_currency):
    actor = await cards.access(session, actor, "screenshot:write")
    if body.dry_run and body.retry:
        images.fail("invalid_retry", "Dry-run cannot retry")
    manifest, asset, _ = await render_manifest(
        session,
        actor,
        asset_id,
        body.parent_rendition_id,
        body.expected_image_sha256,
        body.plan,
        storage,
    )
    extraction, _ = await cards.extraction_scope(session, asset.task_id, asset.extraction_job_id)
    if body.dry_run:
        from app.services import budget_preflight

        return await budget_preflight.attach(
            session,
            {
                "dry_run": True,
                "parent_rendition_id": str(body.parent_rendition_id),
                "input_hash": images.digest(manifest),
                "estimated_cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0},
                "estimated_charge": 0,
                "billing_currency": billing_currency,
                "cost_basis": "known",
                "estimated_duration_ms": None,
            },
            command="screenshot annotate",
            task_id=asset.task_id,
            input_hash=images.digest(manifest),
            currency=billing_currency,
            planned_calls=0,
        ), None
    await cards.task_lock(session, asset.task_id)
    await images.asset_access(session, actor, asset.id, active=True)
    if asset.source_kind == "attachment_page":
        images.fail(
            "annotation_adapter_not_enabled", "Attachment annotation adapter is not enabled", 409
        )
    return await create_job(
        session, actor, asset.task_id, extraction, "screenshot_render", manifest, body.retry
    )


async def check_job_access(session, actor, job, *, active=False, storage=None):
    actor = await cards.access(session, actor, "screenshot:read")
    manifest = job.result["submission"]["input_manifest"]
    refs = (
        manifest["images"]
        if job.kind == "screenshot_analyze"
        else [
            {
                "rendition_id": manifest["parent_rendition_id"],
                "image_sha256": manifest["image_sha256"],
            }
        ]
    )
    for entry in refs:
        asset, row = await images.rendition_access(
            session, actor, UUID(entry["rendition_id"]), active=active, storage=storage
        )
        if asset.task_id != job.task_id or row.image_sha256 != entry["image_sha256"]:
            images.fail("screenshot_input_changed", "Fixed image input changed", 409, 3)
    return actor


async def process_render(execution, storage):
    from app.providers.screenshot_renderer import render

    async with execution.db.transaction(execution.org_id) as session:
        job = await execution.owned_job(session)
        actor = await cards.access(session, worker(job), "screenshot:write")
        manifest = job.result["submission"]["input_manifest"]
        current, asset, parent = await render_manifest(
            session,
            actor,
            UUID(manifest["asset_id"]),
            UUID(manifest["parent_rendition_id"]),
            manifest["image_sha256"],
            ImagePlan.model_validate(manifest["plan"]),
            storage,
        )
        if current != manifest:
            images.fail("screenshot_input_changed", "Fixed input changed", 409, 3)
        png = await images.read_rendition(storage, asset, parent)
        task_id = asset.task_id
        source_hash = asset.source_sha256
        provenance = {
            "source_kind": ("user_diagram" if asset.image_kind == "diagram" else "user_screenshot")
            if asset.source_kind == "upload"
            else asset.source_kind,
            "source_sha256": source_hash,
            "plan_sha256": images.digest(manifest["plan"]),
            "source_time": asset.source.get("captured_at"),
            "source_time_kind": "provider_declared",
            "source_id": str(asset.evidence_source_id) if asset.evidence_source_id else None,
            "page": None,
        }
        if asset.evidence_source_id:
            from app.services.evidence_sources import require_source

            archive, _, _ = await require_source(session, actor, asset.evidence_source_id)
            provenance.update(
                page=archive.page,
                source_time=archive.rendered_at.astimezone(UTC).isoformat(),
                source_time_kind="rendered",
            )
        # The first pass removes only generated padding/footer; the second operates in
        # the parent's content coordinates. Neither pass can restore discarded pixels.
        content_plan = ImagePlan.model_validate(
            {
                "crop": {
                    "x": parent.mapping["content_offset_x"],
                    "y": parent.mapping["content_offset_y"],
                    "width": parent.mapping["content_width"],
                    "height": parent.mapping["content_height"],
                }
            }
        )
        parent_mapping = dict(parent.mapping)
        review_id, upload_hash = parent.privacy_review_id, parent.upload_sha256
    content, _ = await render(
        png,
        {
            "plan": content_plan.model_dump(mode="json"),
            "profile": "screenshot-privacy-v1",
            "provenance": {},
        },
    )
    output, rendered = await render(
        content,
        {"plan": manifest["plan"], "profile": manifest["profile"], "provenance": provenance},
    )
    if rendered["image"]["size_bytes"] > min(images.MAX_BYTES, execution.settings.max_upload_bytes):
        images.fail("image_limits", "Derived image exceeds configured image limit", 413, 2)
    rendition_id = uuid4()
    key = f"org/{execution.org_id}/screenshots/{asset.id}/{rendition_id}/{rendered['image']['sha256']}.png"
    await storage.put(execution.org_id, key, output)
    try:
        async with execution.db.transaction(execution.org_id) as session:
            await cards.task_lock(session, task_id)
            job = await execution.owned_job(session)
            actor = await cards.access(session, worker(job), "screenshot:write")
            current, asset, parent = await render_manifest(
                session,
                actor,
                UUID(manifest["asset_id"]),
                UUID(manifest["parent_rendition_id"]),
                manifest["image_sha256"],
                ImagePlan.model_validate(manifest["plan"]),
                storage,
            )
            if current != manifest:
                images.fail("screenshot_input_changed", "Fixed input changed", 409, 3)
            row = await session.scalar(
                select(ScreenshotRendition).where(
                    ScreenshotRendition.asset_id == asset.id,
                    ScreenshotRendition.parent_rendition_id == parent.id,
                    ScreenshotRendition.plan_sha256 == images.digest(manifest["plan"]),
                    ScreenshotRendition.profile == manifest["profile"],
                )
            )
            duplicate = row is not None
            if row is None:
                mapping = dict(rendered["mapping"])
                # Keep the immediate parent crop. Parent links compose back to the source.
                row = ScreenshotRendition(
                    id=rendition_id,
                    org_id=execution.org_id,
                    task_id=task_id,
                    extraction_job_id=asset.extraction_job_id,
                    asset_id=asset.id,
                    parent_rendition_id=parent.id,
                    privacy_review_id=review_id,
                    source_sha256=source_hash,
                    upload_sha256=upload_hash,
                    image_sha256=rendered["image"]["sha256"],
                    plan_sha256=images.digest(manifest["plan"]),
                    plan=manifest["plan"],
                    mapping=mapping,
                    image=rendered["image"],
                    profile=manifest["profile"],
                    storage_key=key,
                    generation_job_id=job.id,
                    actor_user_id=actor.user_id,
                )
                session.add(row)
                await session.flush()
            await execution.owned_job(session)
            audit(
                session,
                actor,
                "screenshot.render.publish",
                row.id,
                {
                    "job_id": str(job.id),
                    "run_id": str(execution.run_id),
                    "image_sha256": row.image_sha256,
                    "parent_mapping_sha256": images.digest(parent_mapping),
                },
            )
            job.status, job.finished_at = "succeeded", datetime.now(UTC)
            job.result = {
                "submission": job.result["submission"],
                "job_id": str(job.id),
                "asset_id": str(asset.id),
                "rendition": images.rendition_view(row),
                "duplicate": duplicate,
                "cost": await job_cost(session, job.id),
            }
    finally:
        from app.providers.screenshot_objects import discard

        async with execution.db.transaction(execution.org_id) as cleanup:
            retained = await cleanup.scalar(
                select(ScreenshotRendition.id).where(ScreenshotRendition.storage_key == key)
            )
        if retained is None:
            await discard(storage, execution.org_id, key)


async def analysis_inputs(session, actor, task_id, body, storage, provider):
    from app.providers.screenshot_vision import VisionImage

    extraction, available = await cards.extraction_scope(session, task_id, body.extraction_job_id)
    task = await session.get(Task, task_id)
    assert task is not None
    by_id = {row.id: row for row in available}
    if not set(body.requirement_ids) <= set(by_id):
        raise not_found()
    requirements, req_manifest = [], []
    for index, req_id in enumerate(body.requirement_ids):
        row = by_id[req_id]
        if not await cards.citation_valid(session, row):
            images.fail("invalid_citation", "Requirement citation needs repair", 409, 4)
        text, _ = redaction.redact_tree(row.quote, True)
        ref = f"requirement_{index}"
        requirements.append({"ref": ref, "text": text})
        req_manifest.append(
            {
                "ref": ref,
                "requirement_id": str(row.id),
                "quote_sha256": cards.quote_hash(row.quote),
                "source_sha256": images.digest(cards.source(row)),
                "text_sha256": images.digest(text),
            }
        )
    outgoing, image_manifest = [], []
    total_bytes = 0
    for index, request in enumerate(body.images):
        asset, row = await images.rendition_access(session, actor, request.rendition_id)
        if asset.source_kind == "attachment_page":
            images.fail(
                "attachment_model_consumer_not_enabled",
                "Attachment model processing is not enabled",
                409,
            )
        if asset.task_id != task_id or asset.extraction_job_id != extraction.id:
            raise not_found()
        if request.expected_image_sha256 != row.image_sha256:
            images.fail("image_hash_mismatch", "Requested image hash changed", 409)
        total_bytes += row.image["size_bytes"]
        if total_bytes > images.MAX_BYTES:
            images.fail(
                "analysis_payload_limit", "Split selected images into requests of at most 40 MiB"
            )
        content = await images.read_rendition(storage, asset, row)
        ref = f"image_{index}"
        outgoing.append(
            VisionImage(ref, content, row.image["width_px"], row.image["height_px"], row.mapping)
        )
        image_manifest.append(
            {
                "ref": ref,
                "asset_id": str(asset.id),
                "rendition_id": str(row.id),
                "image_sha256": row.image_sha256,
                "privacy_review_id": str(row.privacy_review_id),
                "mapping": row.mapping,
                "image": row.image,
            }
        )
    manifest = {
        "task_id": str(task_id),
        "extraction_job_id": str(extraction.id),
        "requirements": req_manifest,
        "images": image_manifest,
        "purposes": body.purposes,
        "model": model_identity(provider),
        "sale_prices": [str(value) for value in provider.sale]
        if isinstance(provider, HTTPExtractor) and provider.sale is not None
        else None,
        "rules": RULE_VERSION,
        "redaction_rules": redaction.RULE_VERSION,
        "redaction_revision": task.model_redaction_revision,
        "reasoning": body.reasoning,
        "options_sha256": images.digest(provider.settings.llm_request_options)
        if isinstance(provider, HTTPExtractor)
        else None,
    }
    return extraction, manifest, requirements, outgoing


@task_authorized("screenshot:write", write=True)
async def submit_analysis(session, actor, task_id, body, storage, provider, settings):
    from app.providers.screenshot_vision import preview

    actor = await cards.access(session, actor, "screenshot:write")
    if not isinstance(provider, HTTPExtractor) or provider.platform_model_id is None:
        images.fail("vision_catalog_required", "Choose a verified platform image model", 400, 4)
    provider, reasoning, _ = with_reasoning(provider, body.reasoning)
    body = body.model_copy(update={"reasoning": reasoning})
    extraction, manifest, requirements, outgoing = await analysis_inputs(
        session, actor, task_id, body, storage, provider
    )
    try:
        estimates = [preview(provider, requirements, [image], body.purposes) for image in outgoing]
    except ProviderFailure as error:
        if not body.dry_run or error.code not in {
            "billing_price_unavailable",
            "billing_bound_unavailable",
        }:
            raise
        from app.services import budget_preflight

        input_hash = images.digest(manifest)
        return await budget_preflight.attach(
            session,
            {
                "dry_run": True,
                "input_hash": input_hash,
                "outbound_image_hashes": [item["image_sha256"] for item in manifest["images"]],
                "outbound_text_hashes": [item["text_sha256"] for item in manifest["requirements"]],
                "admission_blocker": "billing_price_unavailable",
                "estimated_charge": None,
                "estimated_cost": {"usd": None},
                "cost_basis": "unknown",
            },
            command="screenshot analyze",
            task_id=task_id,
            input_hash=input_hash,
            currency=settings.billing_currency,
            settings=settings,
            quotes=[],
            planned_calls=len(outgoing),
        ), None
    manifest["price_revision"] = estimates[0]["price_revision"]
    input_hash = images.digest(manifest)
    bound = sum((Decimal(str(item["estimated_charge"])) for item in estimates), Decimal(0))
    held = await session.scalar(
        select(func.coalesce(func.sum(VendorCall.reserved_charge), 0)).where(
            VendorCall.state != "completed"
        )
    )
    balance = await billing.current(session, settings.billing_currency)
    blocker = (
        "insufficient_balance"
        if balance - held < Decimal(str(estimates[0]["estimated_charge"]))
        else "job_charge_limit_exceeded"
        if Decimal(str(estimates[0]["estimated_charge"])) > settings.job_max_charge
        else None
    )
    if body.dry_run:
        from app.providers.screenshot_vision import quote
        from app.services import budget_preflight

        return await budget_preflight.attach(
            session,
            {
                "dry_run": True,
                "input_hash": input_hash,
                "outbound_image_hashes": [x["image_sha256"] for x in manifest["images"]],
                "outbound_text_hashes": [x["text_sha256"] for x in manifest["requirements"]],
                "catalog_identity": images.digest(manifest["model"]),
                "price_revision": manifest["price_revision"],
                "input_image_count": len(outgoing),
                "planned_calls": len(outgoing),
                "estimated_cost": {
                    "llm_tokens": sum(e["input_tokens"] + e["output_tokens"] for e in estimates),
                    "ocr_pages": 0,
                    "usd": None,
                },
                "estimated_charge": str(bound),
                "billing_currency": settings.billing_currency,
                "cost_basis": "first_pass_upper_bound",
                "admission_blocker": blocker,
            },
            command="screenshot analyze",
            task_id=task_id,
            input_hash=input_hash,
            currency=settings.billing_currency,
            settings=settings,
            quotes=[quote(provider, requirements, [image], body.purposes) for image in outgoing],
            planned_calls=len(outgoing),
        ), None
    if body.expected_input_hash != input_hash:
        images.fail("screenshot_input_changed", "Preflight input changed; preview again", 409, 3)
    if blocker:
        images.fail(blocker, "Model call admission is blocked", 409, 4)
    await cards.task_lock(session, task_id)
    return await create_job(
        session,
        actor,
        task_id,
        extraction,
        "screenshot_analyze",
        manifest,
        body.retry,
        reasoning=reasoning,
    )


async def rebuild_analysis(session, actor, job, storage, provider):
    manifest = job.result["submission"]["input_manifest"]
    body = ScreenshotAnalyzeInput.model_validate(
        {
            "extraction_job_id": manifest["extraction_job_id"],
            "requirement_ids": [x["requirement_id"] for x in manifest["requirements"]],
            "images": [
                {"rendition_id": x["rendition_id"], "expected_image_sha256": x["image_sha256"]}
                for x in manifest["images"]
            ],
            "purposes": manifest["purposes"],
            "reasoning": manifest["reasoning"],
            "dry_run": True,
        }
    )
    _, current, requirements, outgoing = await analysis_inputs(
        session, actor, job.task_id, body, storage, provider
    )
    from app.providers.screenshot_vision import preview

    current["price_revision"] = preview(provider, requirements, [outgoing[0]], body.purposes)[
        "price_revision"
    ]
    if current != manifest:
        images.fail("screenshot_input_changed", "Fixed analysis inputs changed", 409, 3)
    return manifest, requirements, outgoing


async def process_analysis(execution, storage, provider):
    from app.providers.screenshot_vision import analyze

    async with execution.db.transaction(execution.org_id) as session:
        job = await execution.owned_job(session)
        provider, _, _ = with_reasoning(provider, job.reasoning)
        actor = await cards.access(session, worker(job), "screenshot:write")
        manifest, requirements, outgoing = await rebuild_analysis(
            session, actor, job, storage, provider
        )
        task_id = job.task_id

    async def before_admit(session):
        try:
            job = await execution.owned_job(session)
            actor = await cards.access(session, worker(job), "screenshot:write")
            await rebuild_analysis(session, actor, job, storage, provider)
        except ServiceError as exc:
            raise ProviderFailure(
                "Screenshot authorization or fixed inputs changed", code=exc.code
            ) from None

    execution.before_admit = before_admit
    execution.plan(len(outgoing))
    proposals, rejected = [], []
    failure = None
    for image in outgoing:
        try:
            output = await analyze(provider, requirements, [image], manifest["purposes"])
        except ProviderFailure as exc:
            if (
                exc.code
                in {
                    "job_attempt_stopped",
                    "job_heartbeat_failed",
                    "usage_accounting_failed",
                    "call_charge_bound_exceeded",
                    "screenshot_input_changed",
                    "forbidden",
                    "not_found",
                }
                or not proposals
            ):
                raise
            failure = exc.code
            break
        proposals.extend(output.proposals)
        rejected.extend({"image_ref": image.ref, **asdict(item)} for item in output.rejected)
    if rejected and not proposals:
        raise ProviderFailure("No usable image suggestions", code="invalid_vision_suggestions")
    async with execution.db.transaction(execution.org_id) as session:
        await cards.task_lock(session, task_id)
        job = await execution.owned_job(session)
        actor = await cards.access(session, worker(job), "screenshot:write")
        await rebuild_analysis(session, actor, job, storage, provider)
        run = ScreenshotAnalysisRun(
            id=uuid4(),
            org_id=execution.org_id,
            task_id=task_id,
            extraction_job_id=UUID(manifest["extraction_job_id"]),
            job_id=job.id,
            run_id=execution.run_id,
            input_hash=images.digest(manifest),
            input_manifest=manifest,
            completion="partial" if rejected or failure else "complete",
            rejected=rejected,
        )
        session.add(run)
        await session.flush()
        refs = {}
        for entry in manifest["requirements"] + manifest["images"]:
            req = "requirement_id" in entry
            record = ScreenshotAnalysisInput(
                id=uuid4(),
                org_id=execution.org_id,
                task_id=task_id,
                extraction_job_id=run.extraction_job_id,
                analysis_run_id=run.id,
                ref=entry["ref"],
                requirement_id=UUID(entry["requirement_id"]) if req else None,
                asset_id=None if req else UUID(entry["asset_id"]),
                rendition_id=None if req else UUID(entry["rendition_id"]),
                privacy_review_id=None if req else UUID(entry["privacy_review_id"]),
                content_sha256=entry["quote_sha256"] if req else entry["image_sha256"],
            )
            session.add(record)
            refs[entry["ref"]] = record.id
        await session.flush()
        for proposal in proposals:
            saved_proposal = asdict(proposal)
            if saved_proposal["region"] is not None:
                mapping = next(
                    item["mapping"]
                    for item in manifest["images"]
                    if item["ref"] == proposal.image_ref
                )
                saved_proposal["region"]["x"] -= mapping["content_offset_x"]
                saved_proposal["region"]["y"] -= mapping["content_offset_y"]
            session.add(
                ScreenshotSuggestion(
                    id=uuid4(),
                    org_id=execution.org_id,
                    task_id=task_id,
                    extraction_job_id=run.extraction_job_id,
                    analysis_run_id=run.id,
                    image_input_id=refs[proposal.image_ref],
                    requirement_input_id=refs[proposal.requirement_ref]
                    if proposal.requirement_ref
                    else None,
                    proposal=saved_proposal,
                )
            )
        await execution.owned_job(session)
        audit(
            session,
            actor,
            "screenshot.analysis.publish",
            run.id,
            {
                "job_id": str(job.id),
                "run_id": str(execution.run_id),
                "input_hash": run.input_hash,
                "suggestions": len(proposals),
                "rejected_count": len(rejected),
            },
        )
        job.status, job.finished_at = "succeeded", datetime.now(UTC)
        job.result = {
            "submission": job.result["submission"],
            "analysis_run_id": str(run.id),
            "completion": run.completion,
            "created": len(proposals),
            "rejected": rejected,
            "stop_reason": failure,
            "cost": await job_cost(session, job.id),
        }


@task_authorized("screenshot:read", parent=("analysis_id", "screenshot_analysis_runs"))
async def suggestions(session, actor, analysis_id, cursor):
    actor = await cards.access(session, actor, "screenshot:read")
    run = await session.get(ScreenshotAnalysisRun, analysis_id)
    if run is None:
        raise not_found()
    job = await session.get(Job, run.job_id)
    assert job is not None
    await check_job_access(session, actor, job)
    query = select(ScreenshotSuggestion).where(ScreenshotSuggestion.analysis_run_id == run.id)
    if cursor:
        anchor = await session.get(ScreenshotSuggestion, cursor)
        if anchor is None or anchor.analysis_run_id != run.id:
            raise not_found()
        query = query.where(
            tuple_(ScreenshotSuggestion.created_at, ScreenshotSuggestion.id)
            > tuple_(anchor.created_at, anchor.id)
        )
    rows = (
        await session.scalars(
            query.order_by(ScreenshotSuggestion.created_at, ScreenshotSuggestion.id).limit(101)
        )
    ).all()
    output = []
    for row in rows[:100]:
        image = await session.get(ScreenshotAnalysisInput, row.image_input_id)
        requirement = (
            await session.get(ScreenshotAnalysisInput, row.requirement_input_id)
            if row.requirement_input_id
            else None
        )
        assert image is not None
        output.append(
            {
                "id": str(row.id),
                "analysis_run_id": str(run.id),
                "rendition_id": str(image.rendition_id),
                "image_sha256": image.content_sha256,
                "requirement_id": str(requirement.requirement_id) if requirement else None,
                "proposal": row.proposal,
                "status": "unreviewed",
                "confirmed_by": None,
            }
        )
    return {
        "analysis_run_id": str(run.id),
        "next_cursor": str(rows[99].id) if len(rows) > 100 else None,
    }, output
