"""Fixed, encrypted drafting inputs and worker-only unconfirmed publication."""

import asyncio
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.core.security import Secrets
from app.jobs.execution import JobExecution, job_cost
from app.models.entities import (
    EvidenceSource,
    Job,
    Requirement,
    Task,
    TaskCertificate,
    UsageRecord,
    VendorCall,
)
from app.models.response_cards import CardGenerationRun, ResponseCard, ResponseCardRevision
from app.providers.base import LLMProvider, ProviderFailure
from app.providers.configured import model_identity
from app.providers.drafting import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    DraftingOutput,
    groups,
    request_body,
)
from app.providers.llm import HTTPExtractor, billable, with_reasoning
from app.providers.storage import Storage
from app.schemas.response_card_contracts import (
    CardContent,
    CardGeneratePreview,
    CardGenerateRequest,
    CardGenerateResult,
    PageEvidenceInput,
    ResourceEvidenceInput,
)
from app.services import billing, redaction
from app.services import response_cards as cards
from app.services.auth import Identity
from app.services.drafts import digest
from app.services.evidence_sources import require_source
from app.services.extraction import locate_quote
from app.services.resources import audit

PROTECTED = {"confirmed", "pending_review", "comply_only"}


def protected(revision: ResponseCardRevision | None) -> str | None:
    if revision is not None:
        if revision.state in {"confirmed", "pending_review"}:
            return revision.state
        if revision.disposition == "comply_only":
            return "comply_only"
    return None


async def material_inputs(session: AsyncSession, actor: Identity, task_id: UUID, storage: Storage):
    entries: list[dict] = []
    originals: dict[str, str] = {}
    for kind, (
        selection_model,
        revision_model,
        _,
        revision_field,
        scope,
        fields,
    ) in cards.MATERIALS.items():
        selections = (
            await session.scalars(
                select(selection_model)
                .where(selection_model.task_id == task_id, selection_model.active.is_(True))
                .order_by(selection_model.id)
            )
        ).all()
        if selections:
            actor.require(scope)
        for selected in selections:
            revision = await session.get(revision_model, getattr(selected, revision_field))
            if revision is None:
                raise not_found()
            for field_path in sorted(fields):
                value = revision.data.get(field_path)
                if not isinstance(value, str) or not value.strip():
                    continue
                ref = f"m{len(entries) + 1}"
                entries.append(
                    {
                        "ref": ref,
                        "kind": kind,
                        "selection_id": str(selected.id),
                        "revision_id": str(revision.id),
                        "field_path": field_path,
                    }
                )
                originals[ref] = value
    sources = (
        await session.scalars(
            select(EvidenceSource)
            .join(TaskCertificate, TaskCertificate.id == EvidenceSource.task_certificate_id)
            .where(EvidenceSource.task_id == task_id, TaskCertificate.active.is_(True))
            .order_by(EvidenceSource.id)
        )
    ).all()
    unavailable, files = [], {}
    for source in sources:
        archived, selected, original = await require_source(session, actor, source.id)
        if original.id not in files:
            content = await storage.read(actor.org_id, original.storage_key)
            if (
                len(content) != original.file["size_bytes"]
                or hashlib.sha256(content).hexdigest() != original.file["sha256"]
            ):
                cards.fail("source_original_integrity", "Original failed integrity checks", 502, 4)
            files[original.id] = content
        text = await asyncio.to_thread(cards.page_text, files[original.id], archived.page)
        entry = {
            "kind": "certificate_pdf_page",
            "selection_id": str(selected.id),
            "revision_id": str(selected.certificate_revision_id),
            "evidence_source_id": str(archived.id),
            "original_sha256": original.file["sha256"],
            "preview_sha256": archived.preview["sha256"],
            "page": archived.page,
        }
        if not text.strip():
            unavailable.append({**entry, "reason": "page_text_unavailable"})
            continue
        ref = f"m{len(entries) + 1}"
        entries.append({"ref": ref, **entry})
        originals[ref] = text
    return entries, originals, unavailable


async def snapshot(session, actor, task, requirements, storage, llm, reasoning):
    by_requirement = {str(row.id): row for row in requirements}
    targets, selected, skipped, original_requirements = {}, [], {}, []
    for requirement in requirements:
        key = str(requirement.id)
        card = await session.scalar(
            select(ResponseCard).where(ResponseCard.requirement_id == requirement.id)
        )
        revision = (
            await session.get(ResponseCardRevision, card.current_revision_id) if card else None
        )
        targets[key] = str(revision.id) if revision else None
        reason = protected(revision)
        if reason:
            skipped[key] = reason
            continue
        if not await cards.citation_valid(session, requirement):
            skipped[key] = "invalid_citation"
            continue
        selected.append(key)
        original_requirements.append(
            {
                "requirement_id": key,
                "quote": requirement.quote,
                "location": {"page": requirement.page, "block": requirement.location},
            }
        )
    entries, originals, unavailable = (
        await material_inputs(session, actor, task.id, storage) if selected else ([], {}, [])
    )
    sent_requirements = []
    totals = Counter({kind: 0 for kind in redaction.KINDS})
    for requirement in original_requirements:
        sent, counts = redaction.redact_tree(
            {"quote": requirement["quote"], "location": requirement["location"]},
            task.model_redaction_enabled,
        )
        assert isinstance(sent, dict)
        sent_requirements.append({"requirement_id": requirement["requirement_id"], **sent})
        totals.update(counts)
    sent_materials = []
    for entry in entries:
        sent, hits = redaction.redact(originals[entry["ref"]], task.model_redaction_enabled)
        totals.update(hits)
        entry |= {
            "text_sha256": cards.quote_hash(originals[entry["ref"]]),
            "sent_sha256": cards.quote_hash(sent),
            "redacted_counts": hits,
        }
        sent_materials.append(
            {key: entry[key] for key in ("ref", "kind", "field_path", "page") if key in entry}
            | {"text": sent}
        )
    manifest = {
        "org_id": str(actor.org_id),
        "task_id": str(task.id),
        "requirements": [
            {
                "requirement_id": raw["requirement_id"],
                "document_id": str(by_requirement[raw["requirement_id"]].document_id),
                "chunk_id": str(by_requirement[raw["requirement_id"]].chunk_id),
                "page": raw["location"]["page"],
                "quote_sha256": cards.quote_hash(raw["quote"]),
                "location_sha256": digest(raw["location"]),
                "sent_sha256": cards.quote_hash(
                    json.dumps(sent, sort_keys=True, ensure_ascii=False)
                ),
            }
            for raw, sent in zip(original_requirements, sent_requirements, strict=True)
        ],
        "materials": entries,
        "unavailable_pages": unavailable,
        "model_redaction_enabled": task.model_redaction_enabled,
        "model_redaction_revision": task.model_redaction_revision,
        "redaction_rule_version": redaction.RULE_VERSION,
        "redacted_counts": dict(totals),
        "model": model_identity(llm),
        "reasoning": reasoning,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
    }
    secret = {
        "requirements": sent_requirements,
        "materials": sent_materials,
        "originals": originals,
    }
    return manifest, secret, targets, selected, skipped


def estimate(llm, secret: dict) -> dict:
    if not secret["requirements"]:
        return {
            "estimated_cost": {"usd": 0},
            "estimated_charge": 0,
            "cost_basis": "known",
            "cost_basis_reason": "no_calls_planned",
        }
    if not isinstance(llm, HTTPExtractor):
        return {
            "estimated_cost": {"usd": None},
            "estimated_charge": None,
            "cost_basis": "unknown",
            "cost_basis_reason": "model_unavailable",
        }
    batches = groups(secret["requirements"], secret["materials"], llm.settings.llm_batch_chars)
    bodies = [request_body(llm, batch, secret["materials"]) for batch in batches]
    # A conservative first-pass allowance, not a promise about retries or halving.
    input_tokens = sum(len(json.dumps(body, ensure_ascii=False).encode()) + 4096 for body in bodies)
    output_tokens = sum(llm.output_token_bound(body) for body in bodies)
    prices = llm.settings.llm_input_usd_per_mtok, llm.settings.llm_output_usd_per_mtok
    usd = (
        None
        if prices[0] is None or prices[1] is None
        else (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000
    )
    charge = (
        sum((llm.reservation(body) for body in bodies), Decimal(0))
        if llm.sale is not None or llm.org_owned
        else None
    )
    return {
        "estimated_cost": {"llm_tokens": input_tokens + output_tokens, "usd": usd},
        "estimated_charge": charge,
        "cost_basis": "known" if charge is not None or usd is not None else "unknown",
        "cost_basis_reason": "first_pass_token_bound"
        if charge is not None or usd is not None
        else "prices_unavailable",
    }


async def submit_generation(
    session, actor, task_id, body: CardGenerateRequest, storage, llm, settings
):
    actor = await cards.access(session, actor, "card:generate")
    actor.require("card:read")
    task = await cards.task_lock(session, task_id)
    extraction, requirements = await cards.extraction_scope(
        session, task_id, body.extraction_job_id
    )
    if body.requirement_ids is not None:
        if not set(body.requirement_ids) <= {row.id for row in requirements}:
            raise not_found()
        requirements = [row for row in requirements if row.id in body.requirement_ids]
    llm, reasoning, warnings = with_reasoning(llm, body.reasoning)
    if reasoning is None and not warnings:
        warnings.append("The current model has no reasoning levels configured.")
    manifest, secret, targets, selected, skipped = await snapshot(
        session, actor, task, requirements, storage, llm, reasoning
    )
    manifest["extraction_job_id"] = str(extraction.id)
    input_hash = digest(manifest)
    if not task.model_redaction_enabled:
        warnings.append("model_redaction_disabled:unredacted_manifest_text_will_be_sent")
    warnings.extend(
        f"page_text_unavailable:{item['evidence_source_id']}"
        for item in manifest["unavailable_pages"]
    )
    warnings.extend(await cards.scope_warnings(session, extraction.id))
    if body.dry_run:
        blocker = None
        if selected and billable(llm):
            try:
                await billing.require_funds(session, settings.billing_currency)
            except ServiceError as exc:
                if exc.code != "insufficient_balance":
                    raise
                blocker = exc.code
                warnings.append(blocker)
            if blocker is None and isinstance(llm, HTTPExtractor):
                first = groups(
                    secret["requirements"], secret["materials"], llm.settings.llm_batch_chars
                )[0]
                reserved = llm.reservation(request_body(llm, first, secret["materials"]))
                held = await session.scalar(
                    select(func.coalesce(func.sum(VendorCall.reserved_charge), 0)).where(
                        VendorCall.state != "completed"
                    )
                )
                assert held is not None
                if await billing.current(session, settings.billing_currency) - held < reserved:
                    blocker = "insufficient_balance"
                elif reserved > settings.job_max_charge:
                    blocker = "job_charge_limit_exceeded"
                elif body.max_charge is not None and reserved > body.max_charge:
                    blocker = "spend_cap_below_first_call"
                if blocker:
                    warnings.append(blocker)
        return (
            CardGeneratePreview.model_validate(
                {
                    "task_id": task_id,
                    "extraction_job_id": extraction.id,
                    "selected_requirements": selected,
                    "skipped": skipped,
                    "input_hash": input_hash,
                    **{
                        key: manifest["model"][key]
                        for key in ("model", "platform_model_id", "model_revision")
                    },
                    "reasoning": reasoning,
                    "model_redaction_enabled": task.model_redaction_enabled,
                    "input_refs": [entry["ref"] for entry in manifest["materials"]],
                    "input_manifest": manifest,
                    "redaction_rule_version": redaction.RULE_VERSION,
                    "redacted_counts": manifest["redacted_counts"],
                    "billing_currency": settings.billing_currency,
                    "admission_blocker": blocker,
                    "max_charge": body.max_charge,
                    **estimate(llm, secret),
                }
            ).model_dump(mode="json"),
            None,
            warnings,
        )
    if body.expected_input_hash is not None and body.expected_input_hash != input_hash:
        raise ServiceError(
            "generation_input_changed",
            "Inputs, model or price changed since the preview; preview again",
            409,
            3,
        )
    # Repeating a successful generation without a human edit reuses its paid result.
    # A manual edit or a different model/input still makes a new cache key.
    cache_targets = dict(targets)
    for key, revision_id in targets.items():
        if revision_id is None:
            continue
        revision = await session.get(ResponseCardRevision, UUID(revision_id))
        if revision is not None and revision.origin == "model" and revision.model_job_id:
            prior = await session.scalar(
                select(CardGenerationRun).where(
                    CardGenerationRun.generation_job_id == revision.model_job_id
                )
            )
            if prior is not None and prior.input_hash == input_hash:
                cache_targets[key] = prior.target_revisions[key]
    cache_key = digest(
        {"kind": "card_generate", "input_hash": input_hash, "targets": cache_targets}
    )
    job = await session.scalar(select(Job).where(Job.cache_key == cache_key).with_for_update())
    cached = job is not None
    if job is not None:
        # Do not let a new submitter bypass the original material permissions on cache hits.
        requeue = body.retry and (
            job.status in {"failed", "cancelled"}
            or (job.status == "running" and job.lease_until and job.lease_until < datetime.now(UTC))
        )
        if requeue:
            job.status, job.error, job.queue_id, job.attempts = "queued", None, None, 0
            job.lease_until, job.finished_at, job.run_id = None, None, None
            if body.max_charge is not None:
                # The new cap bounds the job's cumulative charge, earlier attempts included.
                submission = {**job.result["submission"], "max_charge": str(body.max_charge)}
                job.result = {**job.result, "submission": submission}
        elif body.max_charge is not None and job.status in {"queued", "running"}:
            stored = job.result.get("submission", {}).get("max_charge")
            if stored is None or Decimal(stored) > body.max_charge:
                raise ServiceError(
                    "generation_cap_conflict",
                    "The same generation is already running with a higher or no charge cap",
                    409,
                    3,
                )
    else:
        if selected and billable(llm):
            await billing.require_funds(session, settings.billing_currency)
        crypto = Secrets.for_data(settings)
        job = Job(
            id=uuid4(),
            org_id=actor.org_id,
            task_id=task_id,
            document_id=extraction.document_id,
            kind="card_generate",
            cache_key=cache_key,
            status="queued",
            reasoning=reasoning,
            provider_config_id=getattr(llm, "provider_config_id", None),
            provider_identity=model_identity(llm),
            result={
                "submission": {
                    "input_manifest": manifest,
                    "input_hash": input_hash,
                    "target_revisions": targets,
                    "selected_requirements": selected,
                    "skipped": skipped,
                    "warnings": warnings,
                    "encrypted_input": crypto.encrypt(json.dumps(secret, ensure_ascii=False)),
                    "actor_user_id": str(actor.user_id),
                    "actor_token_id": str(actor.token_id) if actor.token_id else None,
                    "actor_kind": actor.actor_kind,
                    "scopes": sorted(actor.scopes),
                    "max_charge": str(body.max_charge) if body.max_charge is not None else None,
                }
            },
        )
        session.add(job)
        await session.flush()
        audit(
            session,
            actor,
            "card.generate.submit",
            job.id,
            {
                "task_id": str(task_id),
                "extraction_job_id": str(extraction.id),
                "input_hash": input_hash,
                "model_redaction_enabled": task.model_redaction_enabled,
                "redaction_rule_version": redaction.RULE_VERSION,
                "redacted_counts": manifest["redacted_counts"],
                "actor_kind": actor.actor_kind,
                "correlation_id": str(job.id),
                "max_charge": str(body.max_charge) if body.max_charge is not None else None,
            },
        )
    await session.flush()
    return (
        {
            "job_id": str(job.id),
            "generation_job_id": str(job.id),
            "status": job.status,
            "cached": cached,
        },
        job,
        warnings,
    )


def worker(job: Job) -> Identity:
    submitted = job.result["submission"]
    return Identity(
        UUID(submitted["actor_user_id"]),
        job.org_id,
        set(submitted["scopes"]),
        "viewer",
        UUID(submitted["actor_token_id"]) if submitted["actor_token_id"] else None,
        "worker",
    )


async def check_input_access(session, actor, task_id, manifest, *, active=False):
    for entry in manifest["materials"] + manifest["unavailable_pages"]:
        if entry["kind"] == "certificate_pdf_page":
            source, selected, _ = await require_source(
                session, actor, UUID(entry["evidence_source_id"])
            )
            if source.task_id != task_id:
                raise not_found()
        else:
            model, _, _, _, scope, _ = cards.MATERIALS[entry["kind"]]
            actor.require(scope)
            selected = await session.get(model, UUID(entry["selection_id"]))
            if selected is None or selected.task_id != task_id:
                raise not_found()
        if active and not selected.active:
            cards.fail(
                "generation_input_changed", "Fixed material selection changed; submit again", 409, 3
            )


async def generate(execution: JobExecution, llm: LLMProvider, storage: Storage):
    settings, db, org_id, job_id = (
        execution.settings,
        execution.db,
        execution.org_id,
        execution.job_id,
    )
    async with db.transaction(org_id) as session:
        job = await execution.owned_job(session)
        actor = await cards.access(session, worker(job), "card:generate")
        actor.require("card:read")
        submitted = job.result["submission"]
        manifest = submitted["input_manifest"]
        task = await session.get(Task, job.task_id)
        if task is None:
            raise not_found()
        if task.model_redaction_revision != manifest["model_redaction_revision"]:
            cards.fail(
                "generation_input_changed", "Redaction setting changed; submit again", 409, 3
            )
        await check_input_access(session, actor, job.task_id, manifest, active=True)
        llm, _, _ = with_reasoning(llm, job.reasoning)
        if model_identity(llm) != manifest["model"]:
            cards.fail("generation_model_changed", "Model catalog changed; submit again", 409, 3)
        if (
            manifest["prompt_version"] != PROMPT_VERSION
            or manifest["redaction_rule_version"] != redaction.RULE_VERSION
        ):
            cards.fail("generation_rules_changed", "Drafting rules changed; submit again", 409, 3)
        secret = json.loads(Secrets.for_data(settings).decrypt(submitted["encrypted_input"]))

    async def before_admit(session):
        # Recheck grants and the switch for retries/halves as well as the first call.
        try:
            live = await cards.access(session, actor, "card:generate")
            live.require("card:read")
            task = await session.get(Task, UUID(manifest["task_id"]))
            if (
                task is None
                or task.model_redaction_revision != manifest["model_redaction_revision"]
            ):
                raise ProviderFailure(
                    "Redaction setting changed; submit again", code="generation_input_changed"
                )
            await check_input_access(session, live, task.id, manifest, active=True)
        except ServiceError as exc:
            raise ProviderFailure(
                "Drafting authorization or inputs changed", code=exc.code
            ) from None

    execution.before_admit = before_admit
    if secret["requirements"]:
        if not getattr(llm, "records_calls", False):
            raise ProviderFailure(
                "Drafting requires per-call accounting", code="drafting_accounting_required"
            )
        output = await llm.draft(secret["requirements"], secret["materials"])
    else:
        output = DraftingOutput()
    if output.failure and (
        not output.batches
        or output.failure.code
        in {
            "job_attempt_stopped",
            "job_heartbeat_failed",
            "usage_accounting_failed",
            "call_charge_bound_exceeded",
        }
    ):
        raise output.failure
    async with db.transaction(org_id) as session:
        await cards.task_lock(session, UUID(manifest["task_id"]))
        job = await execution.owned_job(session)
        actor = await cards.access(session, worker(job), "card:generate")
        actor.require("card:read")
        await check_input_access(session, actor, job.task_id, manifest)
        result = await publish(session, actor, job, output, secret, storage, settings)
        # Resolving large local PDFs may take time while this transaction holds
        # the job lock. An expired lease must roll back the entire publication.
        await execution.owned_job(session)
        job.status, job.error, job.finished_at = "succeeded", None, datetime.now(UTC)
        # Retain the encrypted submission for permissions/cache/retry checks; never expose it.
        job.result = {"submission": submitted, **result, "cost": await job_cost(session, job_id)}


async def publish(session, actor, job, output, secret, storage, settings):
    submitted = job.result["submission"]
    manifest = submitted["input_manifest"]
    mappings = {entry["ref"]: entry for entry in manifest["materials"]}
    selected = set(submitted["selected_requirements"])
    skipped, rejected, needs_material, created, seen = dict(submitted["skipped"]), {}, [], [], set()
    warnings = list(submitted["warnings"])
    for batch in output.batches:
        sent_requirements = {entry["requirement_id"] for entry in batch.requirements}
        sent_materials = {entry["ref"]: entry["text"] for entry in batch.materials}
        duplicates = Counter(str(proposal.requirement_id) for proposal in batch.items)
        for proposal in batch.items:
            key = str(proposal.requirement_id)
            if key not in selected or key not in sent_requirements:
                warnings.append(f"unknown_requirement:{key}")
                continue
            if duplicates[key] > 1 or key in seen:
                skipped[key] = "duplicate_proposal"
                continue
            seen.add(key)
            requirement = await session.get(Requirement, proposal.requirement_id)
            if requirement is None or requirement.job_id != UUID(manifest["extraction_job_id"]):
                raise not_found()
            card = await session.scalar(
                select(ResponseCard)
                .where(ResponseCard.requirement_id == requirement.id)
                .with_for_update()
            )
            previous = (
                await session.get(ResponseCardRevision, card.current_revision_id) if card else None
            )
            reason = protected(previous)
            if reason:
                skipped[key] = reason
                continue
            if (str(previous.id) if previous else None) != submitted["target_revisions"][key]:
                skipped[key] = "revision_conflict"
                continue
            fixed_requirement = next(
                entry for entry in manifest["requirements"] if entry["requirement_id"] == key
            )
            if (
                fixed_requirement["quote_sha256"] != cards.quote_hash(requirement.quote)
                or fixed_requirement["location_sha256"]
                != digest({"page": requirement.page, "block": requirement.location})
                or fixed_requirement["chunk_id"] != str(requirement.chunk_id)
            ):
                skipped[key] = "requirement_input_changed"
                continue
            if not await cards.citation_valid(session, requirement):
                skipped[key] = "invalid_citation"
                continue
            if (
                previous is not None
                and previous.deviation == "negative"
                and proposal.deviation != "negative"
            ):
                skipped[key] = "negative_deviation_weakened"
                continue
            evidence = []
            for citation in proposal.evidence:
                ref = citation.ref
                entry = mappings.get(ref)
                reported_ref = ref if entry else "unknown-" + cards.quote_hash(ref)[:16]
                quote, reason = None, None
                if proposal.response_kind == "commitment":
                    reason = "commitment_evidence_dropped"
                elif redaction.PLACEHOLDER.search(citation.quote):
                    reason = "redacted_placeholder"
                elif entry is None:
                    reason = "unknown_ref"
                elif ref not in sent_materials:
                    reason = "unsent_ref"
                else:
                    sent_quote, sent_reason = locate_quote(sent_materials[ref], citation.quote)
                    quote, original_reason = locate_quote(secret["originals"][ref], citation.quote)
                    if sent_quote is None:
                        reason = (
                            "quote_not_sent"
                            if sent_reason == "quote_not_at_position"
                            else sent_reason
                        )
                    elif quote is None:
                        reason = original_reason
                    elif len(quote) > 20000:
                        reason = "quote_too_long"
                if reason is None:
                    assert entry is not None and quote is not None
                    if entry["kind"] == "certificate_pdf_page":
                        item = PageEvidenceInput(
                            kind="certificate_pdf_page",
                            evidence_source_id=UUID(entry["evidence_source_id"]),
                            quote=quote,
                        )
                    else:
                        item = ResourceEvidenceInput(
                            kind=entry["kind"],
                            selection_id=UUID(entry["selection_id"]),
                            field_path=entry["field_path"],
                            quote=quote,
                        )
                    try:
                        resolved = await cards.resolve_material(
                            session, actor, job.task_id, item, storage
                        )
                    except ServiceError as exc:
                        if exc.code not in {
                            "stale_material",
                            "invalid_evidence_quote",
                            "page_text_unavailable",
                        }:
                            raise
                        reason = exc.code
                    else:
                        revision_field = (
                            "certificate_revision_id"
                            if item.kind == "certificate_pdf_page"
                            else cards.MATERIALS[item.kind][3]
                        )
                        if str(resolved[revision_field]) != entry["revision_id"]:
                            reason = "stale_ref"
                        elif item not in evidence:
                            evidence.append(item)
                if reason:
                    rejected.setdefault(key, []).append(f"{reported_ref}:{reason}")
            if card is None:
                card = await cards.new_card(
                    session, actor, job.task_id, UUID(manifest["extraction_job_id"]), requirement
                )
            content = CardContent(
                **proposal.model_dump(
                    exclude={"requirement_id", "suggested_disposition", "evidence"}
                ),
                evidence=evidence,
            )
            materials = await cards.build_evidence(session, actor, card, content, storage)
            hint = (
                "needs_material" if proposal.response_kind == "evidence" and not materials else None
            )
            if hint:
                needs_material.append(key)
            revision = await cards.append_revision(
                session,
                actor,
                card,
                previous,
                action="generate",
                evidence=materials,
                values={
                    **content.model_dump(exclude={"evidence"}),
                    "state": "draft",
                    "suggested_disposition": proposal.suggested_disposition,
                    "review_hint": hint,
                    "model_job_id": job.id,
                    "review_domain": previous.review_domain
                    if previous
                    else {"technical": "technical", "qualification": "commercial"}.get(
                        requirement.category
                    ),
                },
            )
            created.append(str(revision.id))
            if proposal.deviation == "negative":
                warnings.append(f"negative_deviation:{key}")
    for key in selected - seen - set(skipped):
        skipped[key] = "generation_stopped" if output.failure else "missing_proposal"
    partial = bool(
        rejected
        or needs_material
        or any(reason not in PROTECTED for reason in skipped.values())
        or output.failure
        or any(warning.startswith("unknown_requirement:") for warning in warnings)
    )
    usages = list(
        (
            await session.scalars(
                select(UsageRecord)
                .where(UsageRecord.job_id == job.id)
                .order_by(UsageRecord.created_at, UsageRecord.id)
            )
        ).all()
    )
    charge = (
        None
        if any(row.charge is None for row in usages)
        else sum((row.charge or Decimal(0) for row in usages), Decimal(0))
    )
    result = CardGenerateResult(
        generation_job_id=job.id,
        completion="partial" if partial else "complete",
        created_revision_ids=created,
        skipped=skipped,
        rejected_references=rejected,
        needs_material=needs_material,
        usage_record_ids=[row.id for row in usages],
        charge=charge,
        billing_currency=settings.billing_currency,
        stop_reason=output.failure.code if output.failure else None,
    ).model_dump(mode="json")
    warnings.extend(f"{reason}:{key}" for key, reason in skipped.items())
    warnings.extend(
        f"{key}:{ref_reason}" for key, reasons in rejected.items() for ref_reason in reasons
    )
    warnings.extend(f"needs_material:{key}" for key in needs_material)
    if output.failure:
        warnings.append(output.failure.code)
        if output.failure.code == "provider_quota_exhausted":
            warnings.append(str(output.failure))
    result |= {"warnings": warnings, "exit_code": 5 if partial else 0}
    run = CardGenerationRun(
        id=uuid4(),
        org_id=job.org_id,
        task_id=job.task_id,
        extraction_job_id=UUID(manifest["extraction_job_id"]),
        generation_job_id=job.id,
        generation_run_id=job.run_id,
        input_hash=submitted["input_hash"],
        actor_user_id=actor.user_id,
        actor_token_id=actor.token_id,
        actor_kind="worker",
        input_manifest=manifest,
        target_revisions=submitted["target_revisions"],
        platform_model_id=manifest["model"]["platform_model_id"],
        model_revision=manifest["model"]["model_revision"],
        reasoning=job.reasoning,
        model_redaction_enabled=manifest["model_redaction_enabled"],
        model_redaction_revision=manifest["model_redaction_revision"],
        redaction_rule_version=manifest["redaction_rule_version"],
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        adapter_version=manifest["model"]["adapter_version"],
        encrypted_input=submitted["encrypted_input"],
        result=result,
    )
    session.add(run)
    await session.flush()
    audit(
        session,
        actor,
        "card.generate.complete",
        job.id,
        {
            "created_revision_ids": created,
            "skipped": skipped,
            "rejected_references": rejected,
            "completion": result["completion"],
            "actor_kind": "worker",
            "correlation_id": str(job.id),
        },
    )
    return result
