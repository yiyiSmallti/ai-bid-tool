"""Model-generated single-page prototypes rendered in the offline sandbox.

A generation fixes one requirement and one selected feature revision, sends only the
redacted requirement text and the feature declaration, and keeps the job lease while the
returned HTML is rendered offline. The stored HTML, source PNG and sandbox receipt become
a ScreenshotPrototypeRun; turning it into evidence still needs prepare, human ingest and
card confirmation.
"""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.core.errors import ServiceError, not_found
from app.jobs.execution import job_cost
from app.models.entities import FeatureRevision, Task, TaskFeature, VendorCall
from app.models.screenshots import ScreenshotPrototypeRun
from app.providers import prototyping
from app.providers.base import ProviderFailure
from app.providers.configured import model_identity
from app.providers.llm import HTTPExtractor, billable, with_reasoning
from app.providers.sandbox_runtime import RunDescriptor, SandboxFailure
from app.services import billing, redaction
from app.services import response_cards as cards
from app.services import screenshots as images
from app.services.card_generation import worker
from app.services.screenshot_jobs import create_job
from app.services.versioned import audit

MAX_SOURCE_BYTES = 40 * 1024 * 1024


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


async def inputs(session, actor, task_id, body, llm):
    actor.require("resource:read")
    extraction, requirements = await cards.extraction_scope(
        session, task_id, body.extraction_job_id
    )
    requirement = next((row for row in requirements if row.id == body.requirement_id), None)
    if requirement is None:
        raise not_found()
    if not await cards.citation_valid(session, requirement):
        images.fail("invalid_citation", "Requirement citation needs repair", 409, 4)
    selected = await session.get(TaskFeature, body.task_feature_id)
    if selected is None or selected.task_id != task_id:
        raise not_found()
    if not selected.active:
        images.fail("inactive_selection", "Feature selection is no longer active", 409, 4)
    revision = await session.get(FeatureRevision, selected.feature_revision_id)
    task = await session.get(Task, task_id)
    if revision is None or task is None:
        raise not_found()
    # Requirement and declaration text always use the redaction rules, as image analysis does.
    quote, _ = redaction.redact(requirement.quote, True)
    feature, _ = redaction.redact_tree(
        {key: revision.data.get(key) for key in ("name", "description", "status")}, True
    )
    spec = {"requirement": {"text": quote}, "feature": feature}
    manifest = {
        "task_id": str(task_id),
        "extraction_job_id": str(extraction.id),
        "requirement_id": str(requirement.id),
        "quote_sha256": cards.quote_hash(requirement.quote),
        "source_sha256": images.digest(cards.source(requirement)),
        "task_feature_id": str(selected.id),
        "feature_revision_id": str(selected.feature_revision_id),
        "spec_sha256": images.digest(spec),
        "model": model_identity(llm),
        "sale_prices": [str(value) for value in llm.sale] if llm.sale is not None else None,
        "options_sha256": images.digest(llm.settings.llm_request_options),
        "prompt": prototyping.PROMPT_VERSION,
        "redaction_rules": redaction.RULE_VERSION,
        "reasoning": body.reasoning,
    }
    return extraction, manifest, spec


def http_model(llm) -> HTTPExtractor:
    if not isinstance(llm, HTTPExtractor):
        images.fail("model_unavailable", "No model is configured for prototype generation", 400, 4)
    return llm


async def submit(session, actor, task_id, body, llm, settings, browser):
    actor = await cards.access(session, actor, "screenshot:write")
    if body.dry_run and body.retry:
        images.fail("invalid_retry", "Dry-run cannot retry")
    llm, reasoning, _ = with_reasoning(http_model(llm), body.reasoning)
    body = body.model_copy(update={"reasoning": reasoning})
    extraction, manifest, spec = await inputs(session, actor, task_id, body, llm)
    input_hash = images.digest(manifest)
    request = prototyping.request_body(llm, spec)
    try:
        reserved = llm.reservation(request)
    except ProviderFailure as error:
        if not body.dry_run or error.code not in {
            "billing_price_unavailable",
            "billing_bound_unavailable",
        }:
            raise
        from app.services import budget_preflight

        return await budget_preflight.attach(
            session,
            {
                "dry_run": True,
                "input_hash": input_hash,
                "outbound_image_hashes": [],
                "outbound_text_hashes": [manifest["spec_sha256"]],
                "admission_blocker": "billing_price_unavailable",
                "estimated_charge": None,
                "estimated_cost": {"usd": None},
                "cost_basis": "unknown",
            },
            command="ui mock",
            task_id=task_id,
            input_hash=input_hash,
            currency=settings.billing_currency,
            settings=settings,
            quotes=[],
            planned_calls=2,
        ), None
    tokens = (
        len(json.dumps(request, ensure_ascii=False).encode())
        + 4096
        + llm.output_token_bound(request)
    )
    blocker = None
    if billable(llm):
        held = await session.scalar(
            select(func.coalesce(func.sum(VendorCall.reserved_charge), 0)).where(
                VendorCall.state != "completed"
            )
        )
        if await billing.current(session, settings.billing_currency) - held < reserved:
            blocker = "insufficient_balance"
    if blocker is None and reserved > settings.job_max_charge:
        blocker = "job_charge_limit_exceeded"
    if blocker is None and not browser.available:
        blocker = "sandbox_runtime_unavailable"
    if body.dry_run:
        from app.services import budget_preflight

        return await budget_preflight.attach(
            session,
            {
                "dry_run": True,
                "input_hash": input_hash,
                "outbound_image_hashes": [],
                "outbound_text_hashes": [manifest["spec_sha256"]],
                "catalog_identity": images.digest(manifest["model"]),
                "price_revision": str(manifest["model"]["model_revision"]),
                "input_image_count": 0,
                "planned_calls": 1,
                "estimated_cost": {"llm_tokens": tokens, "ocr_pages": 0, "usd": None},
                "estimated_charge": str(reserved),
                "billing_currency": settings.billing_currency,
                "cost_basis": "first_pass_upper_bound",
                "admission_blocker": blocker,
            },
            command="ui mock",
            task_id=task_id,
            input_hash=input_hash,
            quotes=[llm.quote(request)],
            planned_calls=2,
            settings=settings,
            currency=settings.billing_currency,
        ), None
    if body.expected_input_hash != input_hash:
        images.fail("prototype_input_changed", "Inputs changed since the preview", 409, 3)
    if blocker:
        images.fail(blocker, "Prototype generation admission is blocked", 409, 4)
    await cards.task_lock(session, task_id)
    return await create_job(
        session,
        actor,
        task_id,
        extraction,
        "prototype_generate",
        manifest,
        body.retry,
        reasoning=reasoning,
    )


async def rebuild(session, actor, job, llm):
    from app.schemas.screenshot_contracts import PrototypeGenerateInput

    manifest = job.result["submission"]["input_manifest"]
    body = PrototypeGenerateInput.model_validate(
        {
            "extraction_job_id": manifest["extraction_job_id"],
            "requirement_id": manifest["requirement_id"],
            "task_feature_id": manifest["task_feature_id"],
            "reasoning": manifest["reasoning"],
            "dry_run": True,
        }
    )
    _, current, spec = await inputs(session, actor, job.task_id, body, llm)
    if current != manifest:
        images.fail("prototype_input_changed", "Fixed prototype inputs changed", 409, 3)
    return manifest, spec


async def check_job_access(session, actor, job):
    actor = await cards.access(session, actor, "screenshot:read")
    actor.require("resource:read")
    manifest = job.result["submission"]["input_manifest"]
    selected = await session.get(TaskFeature, UUID(manifest["task_feature_id"]))
    if selected is None or selected.task_id != job.task_id:
        raise not_found()
    return actor


def view(row: ScreenshotPrototypeRun) -> dict:
    return {
        "id": str(row.id),
        "org_id": str(row.org_id),
        "task_id": str(row.task_id),
        "generation_job_id": str(row.generation_job_id),
        "requirement_id": str(row.requirement_id),
        "task_feature_id": str(row.task_feature_id),
        "origin": "prototype",
        "provider": row.provenance["provider"],
        "model": row.provenance["model"],
        "catalog_identity": row.provenance["catalog_identity"],
        "input_hash": row.input_hash,
        "html": {
            "sha256": row.html_sha256,
            "size_bytes": row.provenance["html_size_bytes"],
            "media_type": "text/html",
        },
        "html_sha256": row.html_sha256,
        "sandbox_receipt_id": row.sandbox_receipt_id,
        "sandbox_receipt_sha256": row.sandbox_receipt_sha256,
        "source_image_sha256": row.source_image_sha256,
    }


async def process(execution, storage, llm, browser):
    llm = http_model(llm)
    async with execution.db.transaction(execution.org_id) as session:
        job = await execution.owned_job(session)
        llm, _, _ = with_reasoning(llm, job.reasoning)
        actor = await cards.access(session, worker(job), "screenshot:write")
        manifest, spec = await rebuild(session, actor, job, llm)
        task_id, job_id = job.task_id, job.id

    async def before_admit(session):
        try:
            current = await execution.owned_job(session)
            await rebuild(
                session,
                await cards.access(session, worker(current), "screenshot:write"),
                current,
                llm,
            )
        except ServiceError as exc:
            raise ProviderFailure(
                "Prototype authorization or fixed inputs changed", code=exc.code
            ) from None

    execution.before_admit = before_admit
    execution.plan(2)
    html, usage = await prototyping.generate(llm, spec)

    prototype_id = uuid4()
    prefix = f"org/{execution.org_id}/prototypes/{prototype_id}"
    html_key, png_key = f"{prefix}/prototype.html", f"{prefix}/source.png"
    descriptor = RunDescriptor(
        execution.org_id, job_id, execution.run_id, sha(html), "prototype_offline"
    )
    try:
        rendered = await browser.render_prototype(descriptor, html)
    except SandboxFailure as exc:
        raise ServiceError(exc.code, "Sandbox could not render the prototype", 503, 3) from None
    png = next((a for a in rendered.artifacts if a.kind == "prototype_png"), None)
    if (
        rendered.cleanup_state != "complete"
        or rendered.descriptor_hash != descriptor.digest
        or png is None
        or not 0 < len(png.data) <= MAX_SOURCE_BYTES
    ):
        raise ServiceError(
            "sandbox_receipt_invalid", "Sandbox render receipt is incomplete", 502, 4
        )
    receipt = {
        "descriptor_hash": descriptor.digest,
        "artifacts": [
            {"kind": item.kind, "sha256": item.sha256, "size_bytes": len(item.data)}
            for item in rendered.artifacts
        ],
        "runtime_versions": rendered.runtime_versions,
        "issues": list(rendered.issues),
        "metrics": rendered.metrics,
    }
    await storage.put(execution.org_id, html_key, html)
    await storage.put(execution.org_id, png_key, png.data)

    async with execution.db.transaction(execution.org_id) as session:
        await cards.task_lock(session, task_id)
        job = await execution.owned_job(session)
        actor = await cards.access(session, worker(job), "screenshot:write")
        await rebuild(session, actor, job, llm)
        row = ScreenshotPrototypeRun(
            id=prototype_id,
            org_id=execution.org_id,
            task_id=task_id,
            extraction_job_id=UUID(manifest["extraction_job_id"]),
            requirement_id=UUID(manifest["requirement_id"]),
            task_feature_id=UUID(manifest["task_feature_id"]),
            feature_revision_id=UUID(manifest["feature_revision_id"]),
            generation_job_id=job.id,
            input_hash=images.digest(manifest),
            html_sha256=sha(html),
            html_storage_key=html_key,
            source_image_sha256=png.sha256,
            source_image_key=png_key,
            sandbox_receipt_id=descriptor.digest,
            sandbox_receipt_sha256=images.digest(receipt),
            provenance={
                "provider": llm.name,
                "model": usage.model,
                "catalog_identity": llm.version,
                "prompt_version": prototyping.PROMPT_VERSION,
                "html_size_bytes": len(html),
                "render_issues": list(rendered.issues),
                "runtime_versions": rendered.runtime_versions,
            },
        )
        session.add(row)
        await session.flush()
        audit(
            session,
            actor,
            "screenshot.prototype.generate",
            row.id,
            {
                "job_id": str(job.id),
                "run_id": str(execution.run_id),
                "input_hash": row.input_hash,
                "html_sha256": row.html_sha256,
                "source_image_sha256": row.source_image_sha256,
                "sandbox_receipt_sha256": row.sandbox_receipt_sha256,
            },
        )
        job.status, job.finished_at = "succeeded", datetime.now(UTC)
        job.result = {
            "submission": job.result["submission"],
            "prototype_generation": view(row),
            "render_issues": list(rendered.issues),
            "cost": await job_cost(session, job.id),
        }


async def show(session, actor, prototype_id):
    actor = await cards.access(session, actor, "screenshot:read")
    actor.require("resource:read")
    row = await session.get(ScreenshotPrototypeRun, prototype_id)
    if row is None:
        raise not_found()
    selected = await session.get(TaskFeature, row.task_feature_id)
    if selected is None or selected.task_id != row.task_id:
        raise not_found()
    return row


async def source(session, actor, prototype_id, storage) -> tuple[ScreenshotPrototypeRun, bytes]:
    row = await show(session, actor, prototype_id)
    content = await storage.read(actor.org_id, row.source_image_key)
    if sha(content) != row.source_image_sha256 or len(content) > MAX_SOURCE_BYTES:
        images.fail("image_integrity", "Stored prototype image failed integrity checks", 409, 4)
    return row, content
