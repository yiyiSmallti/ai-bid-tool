"""Preview, submit, validate and publish scoring-rubric candidates."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ServiceError, not_found
from app.core.security import Secrets
from app.jobs.execution import job_cost
from app.models.entities import Job, OrgBalance, UsageRecord, VendorCall
from app.models.score import (
    ScoreRubricCoverage,
    ScoreRubricItem,
    ScoreRubricSection,
    ScoreRubricSet,
)
from app.providers import llm as llm_providers
from app.providers.base import ProviderFailure
from app.providers.configured import model_identity
from app.providers.llm import HTTPExtractor, with_reasoning
from app.providers.rubric import (
    ADAPTER_VERSION,
    ITEMS_PROMPT_VERSION,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    STRUCTURE_PROMPT_VERSION,
    HTTPRubricProvider,
    rubric_provider,
    supports_rubric,
)
from app.schemas.check_contracts import AssessmentJobAccepted, OutboundContext
from app.schemas.contracts import Cost
from app.schemas.score_contracts import (
    RubricAnsweredBatch,
    RubricGenerateRequest,
    RubricGenerateResult,
    RubricInput,
    RubricItemsRequest,
    RubricPreview,
    RubricProviderRequest,
    RubricProviderRequirement,
    RubricSectionContext,
    RubricStructureOutput,
)
from app.services import confidential, drafts, redaction, score_inputs, score_normalization
from app.services import response_cards as cards
from app.services.auth import Identity
from app.services.extraction import (
    locate_quote,
    locate_sent_source_quote,
    locate_source_citation_span,
)
from app.services.score_inputs import RubricSnapshot
from app.services.task_authorization import task_authorized
from app.services.versioned import audit

PARTIAL_STOPS = {
    "task_budget_exceeded",
    "task_budget_unpriced",
    "task_budget_currency_review_required",
    "insufficient_balance",
    "spend_cap_reached",
    "job_charge_limit_exceeded",
    "job_call_limit_exceeded",
}
HARD_STOPS = {
    "job_attempt_stopped",
    "job_heartbeat_failed",
    "usage_accounting_failed",
    "call_charge_bound_exceeded",
    "invalid_provider_usage",
    "billing_currency_mismatch",
    "billing_price_unavailable",
    "billing_bound_unavailable",
    "score_rubric_usage_integrity",
    "score_rubric_input_changed",
    "redaction_required",
    "provider_model_changed",
    "provider_config_unavailable",
    "score_rubric_input_integrity",
}


async def resolve(session: AsyncSession, settings: Settings, job: Job | None = None):
    """Resolve the tenant/catalog revision; tests replace this exact seam."""

    return await llm_providers.resolve_llm(session, settings, job=job)


def prices(llm, *, rubric: bool = False) -> dict:
    configured = getattr(llm, "settings", None)
    return {
        "sale": [str(value) for value in llm.sale]
        if getattr(llm, "sale", None) is not None
        else None,
        "vendor_input": str(configured.llm_input_usd_per_mtok)
        if configured and configured.llm_input_usd_per_mtok is not None
        else None,
        "vendor_output": str(configured.llm_output_usd_per_mtok)
        if configured and configured.llm_output_usd_per_mtok is not None
        else None,
        "org_owned": bool(getattr(llm, "org_owned", False)),
        "request_options_sha256": drafts.digest(configured.request_options())
        if configured
        else None,
        **(
            {
                "rubric_max_request_bytes": configured.rubric_max_request_bytes
                if configured
                else None,
                "batch_chars": configured.llm_batch_chars if configured else None,
                "concurrency": configured.llm_concurrency if configured else None,
            }
            if rubric
            else {"batch_chars": configured.llm_batch_chars if configured else None}
        ),
        "output_tokens": configured.llm_max_output_tokens if configured else None,
        "json_mode": configured.llm_json_mode if configured else None,
        "effort": configured.llm_effort if configured else None,
    }


async def secret_library(session: AsyncSession, task_id: UUID, settings: Settings):
    entries = await confidential.task_entries(session, task_id)
    crypto = Secrets.for_data(settings)
    library = confidential.library(entries, crypto)
    present = {entry.key for entry in library}
    for key, entry in entries.items():
        if entry.value is not None and key not in present:
            value = unicodedata.normalize(
                "NFKC", crypto.decrypt(entry.value.encrypted_value)
            ).strip()
            if value:
                library.append(redaction.LibraryValue(key, re.compile(re.escape(value))))
    return confidential.prompt_fields(entries), library


def build_outbound(secret: dict, fields: list[dict], library) -> dict:
    """Redact each fixed source quote and bind it to opaque local identifiers."""

    texts: list[dict] = []
    refs: dict[str, dict] = {}
    requirements: dict[str, str] = {}
    counts: Counter[str] = Counter({kind: 0 for kind in (*redaction.KINDS, "confidential")})
    for index, item in enumerate(secret["requirements"], 1):
        local = str(item["provider_id"])
        ref = f"r{index}.tender"
        requirements[local] = item["requirement_id"]
        source_position = {
            "page": item["source"]["page"],
            "location": item["source"]["location"],
        }
        sent_payload, payload_counts = redaction.redact_tree(
            {
                "requirement_text": item["text"],
                "source_position": source_position,
                "source_quote": item["source"]["quote"],
            },
            True,
            library,
        )
        sent_payload = cast(dict[str, Any], sent_payload)
        counts.update(payload_counts)
        parts = []
        if sent_payload["requirement_text"] != sent_payload["source_quote"]:
            parts.extend(("要求摘要：", sent_payload["requirement_text"]))
        parts.extend(
            (
                "来源位置：",
                json.dumps(sent_payload["source_position"], ensure_ascii=False),
                "招标原文：",
                sent_payload["source_quote"],
            )
        )
        sent_text = "\n".join(parts)
        texts.append({"ref": ref, "text": sent_text})
        refs[ref] = {
            "provider_id": local,
            "requirement_id": item["requirement_id"],
            "original": item["source"]["quote"],
            "location_original": item["source_original"],
            "source": item["source"],
            "sent": sent_text,
            "sent_source": sent_payload["source_quote"],
            "safe_source": {
                **item["source"],
                "quote": sent_payload["source_quote"],
                "location": sent_payload["source_position"]["location"],
            },
        }
    sent_fields, field_counts = redaction.redact_tree(fields, True, library)
    counts.update(field_counts)
    context = OutboundContext.model_validate({"texts": texts, "confidential_fields": sent_fields})
    redacted_requirements: set[str] = set()
    for binding in refs.values():
        if binding["safe_source"] != binding["source"]:
            redacted_requirements.add(binding["requirement_id"])
    return {
        "context": context.model_dump(mode="json"),
        "refs": refs,
        "requirements": requirements,
        "redacted_counts": dict(counts),
        "redacted_requirements": sorted(redacted_requirements),
        "hint_originals": [field["label"] for field in fields],
    }


def provider_request(outbound: dict) -> RubricProviderRequest:
    by_provider = {binding["provider_id"]: ref for ref, binding in outbound["refs"].items()}
    return RubricProviderRequest(
        requirements=[
            RubricProviderRequirement(requirement_id=UUID(local), tender_ref=by_provider[local])
            for local in outbound["requirements"]
        ],
        context=OutboundContext.model_validate(outbound["context"]),
    )


def safe_input(fixed: RubricSnapshot) -> dict:
    """The only encrypted job snapshot: redacted text and hashes, never raw values."""

    outbound = fixed.secret["outbound"]
    return {
        "requirements": fixed.manifest["requirements"],
        "context": outbound["context"],
        "requirements_by_provider": outbound["requirements"],
        "safe_sources": {ref: binding["safe_source"] for ref, binding in outbound["refs"].items()},
    }


async def prepare(
    session: AsyncSession,
    fixed: RubricSnapshot,
    llm,
    requested_reasoning: str | None,
    settings: Settings,
):
    fixed.manifest |= {
        "structure_prompt_version": STRUCTURE_PROMPT_VERSION,
        "items_prompt_version": ITEMS_PROMPT_VERSION,
    }
    if not fixed.manifest["model_redaction_enabled"]:
        fixed.manifest |= {
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "adapter_version": ADAPTER_VERSION,
            "model": None,
            "reasoning": None,
            "price": None,
            "provider_source": None,
            "outbound_sha256": None,
            "redacted_counts": {},
        }
        fixed.input_hash = drafts.digest(fixed.manifest)
        return None
    llm, reasoning, _ = with_reasoning(llm, requested_reasoning)
    fixed.manifest |= {
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "model": model_identity(llm),
        "reasoning": reasoning,
        "price": prices(llm, rubric=True),
        "provider_source": "org" if getattr(llm, "org_owned", False) else "platform",
    }
    if getattr(llm, "org_owned", False):
        fixed.manifest["warnings"] = [
            *fixed.manifest["warnings"],
            "org_key_max_charge_does_not_limit_vendor_bill",
        ]
    fields, library = await secret_library(session, fixed.task.id, settings)
    outbound = build_outbound(fixed.secret, fields, library)
    fixed.secret["outbound"] = outbound
    fixed.manifest["outbound_sha256"] = drafts.digest(outbound["context"])
    fixed.manifest["outbound_text_sha256"] = [
        {
            "ref": entry["ref"],
            "sha256": hashlib.sha256(entry["text"].encode()).hexdigest(),
        }
        for entry in outbound["context"]["texts"]
    ]
    fixed.manifest["redacted_counts"] = outbound["redacted_counts"]
    fixed.manifest["confidential_hints_sha256"] = drafts.digest({"fields": fields})
    fixed.input_hash = drafts.digest(fixed.manifest)
    return llm


async def preview_cost(
    session: AsyncSession,
    fixed: RubricSnapshot,
    llm,
    body: RubricGenerateRequest,
    settings: Settings,
) -> dict:
    unknown = {
        "estimated_cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": None},
        "estimated_charge": None,
        "cost_basis": "unknown",
        "cost_basis_reason": "model_unavailable",
        "admission_blocker": None,
    }
    if not fixed.manifest["model_redaction_enabled"]:
        return unknown | {
            "admission_blocker": "redaction_required",
            "cost_basis_reason": "redaction_required",
        }
    if fixed.secret["outbound"]["redacted_requirements"]:
        return unknown | {
            "admission_blocker": "sensitive_scoring_source",
            "cost_basis_reason": "sensitive_source_cannot_be_persisted",
        }
    if not supports_rubric(llm):
        return unknown | {"admission_blocker": "rubric_capability_unavailable"}
    adapter = rubric_provider(llm)
    request = provider_request(fixed.secret["outbound"])
    if not isinstance(adapter, HTTPRubricProvider):
        if getattr(adapter, "test_only", False):
            return {
                "estimated_cost": Cost().model_dump(mode="json"),
                "estimated_charge": Decimal(0),
                "cost_basis": "known",
                "cost_basis_reason": "test_provider",
                "admission_blocker": None,
            }
        return unknown | {"admission_blocker": "billing_bound_unavailable"}
    try:
        adapter.validate_request(request)
    except ProviderFailure as error:
        if error.code != "rubric_context_limit":
            raise
        return unknown | {
            "admission_blocker": error.code,
            "cost_basis_reason": "complete_rubric_request_exceeds_limit",
        }
    try:
        if adapter.llm.platform_model_id is None and not adapter.llm.org_owned:
            return unknown | {
                "admission_blocker": "billing_price_unavailable",
                "cost_basis_reason": "prices_unavailable",
            }
        inputs, outputs, bound, first = adapter.preview_bounds(request)
    except ProviderFailure as error:
        return unknown | {
            "admission_blocker": error.code,
            "cost_basis_reason": "prices_unavailable",
        }
    input_price = adapter.llm.settings.llm_input_usd_per_mtok
    output_price = adapter.llm.settings.llm_output_usd_per_mtok
    usd = (
        None
        if input_price is None or output_price is None
        else (inputs * input_price + outputs * output_price) / 1_000_000
    )
    blocker = None
    if first > settings.job_max_charge:
        blocker = "job_charge_limit_exceeded"
    elif body.max_charge is not None and first > body.max_charge:
        blocker = "spend_cap_below_first_call"
    if adapter.llm.platform_model_id is not None:
        balance = await session.scalar(select(OrgBalance))
        held = await session.scalar(
            select(func.coalesce(func.sum(VendorCall.reserved_charge), 0)).where(
                VendorCall.state != "completed"
            )
        )
        if balance is not None and balance.currency != settings.billing_currency:
            blocker = "billing_currency_mismatch"
        elif balance is None or balance.balance <= 0 or balance.balance - (held or 0) < first:
            blocker = "insufficient_balance"
    return {
        "estimated_cost": {"llm_tokens": inputs + outputs, "ocr_pages": 0, "usd": usd},
        "estimated_charge": bound,
        "cost_basis": "known",
        "cost_basis_reason": (
            "org_key_platform_charge_zero_vendor_cost_estimate"
            if adapter.llm.org_owned
            else "first_pass_token_bound"
        ),
        "admission_blocker": blocker,
    }


def rubric_input(manifest: dict, input_hash: str) -> dict:
    return RubricInput.model_validate(
        {
            "org_id": manifest["org_id"],
            "task_id": manifest["task_id"],
            "extraction_job_id": manifest["extraction_job_id"],
            "document_id": manifest["document_id"],
            "input_hash": input_hash,
        }
    ).model_dump(mode="json")


async def _submit_rubric(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    body: RubricGenerateRequest,
    settings: Settings,
) -> tuple[dict, Job | None]:
    actor = await score_inputs.access(session, actor, "score:rubric:generate")
    await score_inputs.lock_inputs(session, actor, task_id, body.extraction_job_id)
    fixed = await score_inputs.snapshot(session, actor, task_id, body.extraction_job_id)
    llm = await resolve(session, settings) if fixed.manifest["model_redaction_enabled"] else None
    llm = await prepare(session, fixed, llm, body.reasoning, settings)
    estimate = await preview_cost(session, fixed, llm, body, settings)
    cache_key = drafts.digest(
        {
            "kind": "score_rubric",
            "org_id": str(actor.org_id),
            "task_id": str(task_id),
            "actor_user_id": str(actor.user_id),
            "actor_token_id": str(actor.token_id) if actor.token_id else None,
            "actor_kind": actor.actor_kind,
            "input_hash": fixed.input_hash,
        }
    )
    requirement_ids = [UUID(entry["requirement_id"]) for entry in fixed.manifest["requirements"]]
    if body.dry_run:
        data = RubricPreview.model_validate(
            {
                "input": rubric_input(fixed.manifest, fixed.input_hash),
                "selected_item_ids": requirement_ids,
                "scoring_requirement_ids": requirement_ids,
                "billing_currency": settings.billing_currency,
                "redaction_revision": fixed.manifest["model_redaction_revision"],
                "redaction_rule_version": fixed.manifest["redaction_rule_version"],
                "redacted_counts": fixed.manifest.get("redacted_counts", {}),
                "prompt_version": PROMPT_VERSION,
                "schema_version": SCHEMA_VERSION,
                "normalization_rule_version": score_inputs.NORMALIZATION_RULE_VERSION,
                "max_charge": body.max_charge,
                **(
                    {
                        "provider_config_id": getattr(llm, "provider_config_id", None),
                        "provider_source": fixed.manifest["provider_source"],
                        "platform_model_id": fixed.manifest["model"].get("platform_model_id"),
                        "model_revision": fixed.manifest["model"].get("model_revision"),
                        "model": fixed.manifest["model"]["model"],
                        "reasoning": fixed.manifest["reasoning"],
                    }
                    if llm is not None
                    else {}
                ),
                **estimate,
            }
        ).model_dump(mode="json")
        from app.services import budget_preflight

        quotes = []
        if (
            isinstance(llm, HTTPExtractor)
            and estimate.get("estimated_charge") is not None
            and estimate.get("cost_basis_reason") != "test_provider"
        ):
            adapter = rubric_provider(llm)
            if isinstance(adapter, HTTPRubricProvider):
                quotes = adapter.preview_quotes(provider_request(fixed.secret["outbound"]))
        data = await budget_preflight.attach(
            session,
            data,
            command="score rubric generate",
            task_id=task_id,
            input_hash=fixed.input_hash,
            currency=settings.billing_currency,
            settings=settings,
            quotes=quotes,
            planned_calls=len(quotes)
            if quotes
            or estimate.get("cost_basis_reason") in {"no_assessable_items", "test_provider"}
            else None,
            max_charge=body.max_charge,
            cached_job=await session.scalar(select(Job).where(Job.cache_key == cache_key)),
        )
        return data, None
    if estimate.get("admission_blocker") == "redaction_required":
        cards.fail("redaction_required", "Rubric generation requires redaction", 409, 4)
    if fixed.input_hash != body.expected_input_hash:
        cards.fail(
            "score_rubric_input_changed",
            "Inputs changed since preview; preview the scoring requirements again",
            409,
        )
    job = await session.scalar(select(Job).where(Job.cache_key == cache_key).with_for_update())
    cached = job is not None
    if job is None:
        if estimate.get("admission_blocker"):
            cards.fail(
                estimate["admission_blocker"],
                "Rubric generation admission was refused; inspect a new preview",
                409,
                4,
            )
        assert llm is not None
        job = Job(
            id=uuid4(),
            org_id=actor.org_id,
            task_id=task_id,
            document_id=fixed.document.id,
            kind="score_rubric",
            cache_key=cache_key,
            status="queued",
            reasoning=fixed.manifest["reasoning"],
            provider_config_id=getattr(llm, "provider_config_id", None),
            provider_identity=fixed.manifest["model"],
            result={
                "submission": {
                    "input_manifest": fixed.manifest,
                    "input_hash": fixed.input_hash,
                    "encrypted_input": Secrets.for_data(settings).encrypt(
                        json.dumps(safe_input(fixed), ensure_ascii=False)
                    ),
                    "actor_user_id": str(actor.user_id),
                    "actor_token_id": str(actor.token_id) if actor.token_id else None,
                    "actor_kind": actor.actor_kind,
                    "scopes": sorted(actor.scopes),
                    "provider_source": fixed.manifest.get("provider_source"),
                    "max_charge": str(body.max_charge) if body.max_charge is not None else None,
                }
            },
        )
        session.add(job)
        await session.flush()
        audit(
            session,
            actor,
            "score_rubric.submitted",
            job.id,
            {
                "task_id": str(task_id),
                "job_id": str(job.id),
                "input_hash": fixed.input_hash,
                "requirement_count": len(requirement_ids),
            },
        )
    elif body.retry and (
        job.status in {"failed", "cancelled"}
        or (job.status == "running" and job.lease_until and job.lease_until < datetime.now(UTC))
    ):
        if estimate.get("admission_blocker"):
            cards.fail(
                estimate["admission_blocker"],
                "Rubric generation admission was refused",
                409,
                4,
            )
        job.status, job.error, job.queue_id, job.attempts = "queued", None, None, 0
        if body.max_charge is not None:
            job.result = {
                **job.result,
                "submission": {**job.result["submission"], "max_charge": str(body.max_charge)},
            }
        job.lease_until, job.finished_at, job.run_id = None, None, None
    elif body.max_charge is not None and job.status in {"queued", "running"}:
        cap = job.result["submission"].get("max_charge")
        if cap is None or Decimal(cap) > body.max_charge:
            cards.fail(
                "score_rubric_cap_conflict",
                "The existing rubric job has a higher charge cap",
                409,
                2,
            )
    await session.flush()
    return AssessmentJobAccepted.model_validate(
        {"job_id": job.id, "status": job.status, "cached": cached}
    ).model_dump(mode="json"), job


@task_authorized("score:rubric:generate", write=True)
async def submit_rubric(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    body: RubricGenerateRequest,
    settings: Settings,
) -> tuple[dict, Job | None]:
    """Attach a safe audit envelope only after a bound authenticated submission fails."""

    try:
        return await _submit_rubric(session, actor, task_id, body, settings)
    except ServiceError as error:
        if (
            not body.dry_run
            and error.status != 404
            and error.code not in {"forbidden", "human_required"}
        ):
            error.__dict__["rubric_audit"] = {
                "action": "score_rubric.failed",
                "object_id": str(task_id),
                "task_id": str(task_id),
                "extraction_job_id": str(body.extraction_job_id),
                "input_hash": body.expected_input_hash,
                "error_code": error.code,
                "actor_kind": actor.actor_kind,
            }
        raise


def worker(job: Job) -> Identity:
    submission = job.result["submission"]
    return Identity(
        UUID(submission["actor_user_id"]),
        job.org_id,
        set(submission["scopes"]),
        "viewer",
        UUID(submission["actor_token_id"]) if submission["actor_token_id"] else None,
        "worker",
    )


async def job_access(
    session: AsyncSession,
    actor: Identity,
    job: Job,
    *,
    cancel: bool = False,
) -> None:
    actor = await score_inputs.access(
        session, actor, "score:rubric:generate" if cancel else "score:read"
    )
    if job.kind != "score_rubric" or job.task_id is None:
        raise not_found()
    await score_inputs.require_dependencies(
        session,
        actor,
        job.task_id,
        job.result["submission"]["input_manifest"],
    )


def safe_stored_value(value: Any, library, allowed_placeholders: set[str]) -> bool:
    """Reject raw sensitive literals and placeholders outside the registered whitelist."""

    if isinstance(value, str):
        if redaction.PLACEHOLDER.search(value):
            return False
        placeholders = {
            "{{secret." + key + "}}" for key in redaction.SECRET_PLACEHOLDER.findall(value)
        }
        if not placeholders <= allowed_placeholders:
            return False
        return redaction.redact(value, True, library)[0] == value
    if isinstance(value, dict):
        return all(
            safe_stored_value(child, library, allowed_placeholders) for child in value.values()
        )
    if isinstance(value, list):
        return all(safe_stored_value(child, library, allowed_placeholders) for child in value)
    return True


def _safe_candidate_text(
    candidate: dict,
    fields: tuple[str, ...],
    library,
    allowed_placeholders: set[str],
) -> bool:
    """Inspect model-authored prose only; opaque UUIDs and refs are not content."""

    return all(
        safe_stored_value(candidate.get(field), library, allowed_placeholders) for field in fields
    )


@dataclass(frozen=True)
class _CitationScope:
    requested_requirement_ids: list[UUID]
    sent_refs: list[str]


def _verified_requirement(
    citation,
    batch: RubricAnsweredBatch | _CitationScope,
    outbound: dict,
) -> tuple[str | None, str | None]:
    if citation.ref not in batch.sent_refs or citation.ref not in outbound["refs"]:
        return None, "ref_not_sent"
    binding = outbound["refs"][citation.ref]
    if UUID(binding["provider_id"]) not in batch.requested_requirement_ids:
        return None, "requirement_not_requested"
    if redaction.PLACEHOLDER.search(citation.quote) or redaction.SECRET_PLACEHOLDER.search(
        citation.quote
    ):
        return None, "redacted_input_unresolved"
    sent_quote, reason = locate_sent_source_quote(binding["sent_source"], citation.quote)
    if sent_quote is None:
        return None, reason
    original_quote, reason = locate_quote(binding["original"], citation.quote)
    if original_quote is None:
        return None, reason
    span, reason = locate_source_citation_span(
        binding["location_original"], binding["original"], original_quote
    )
    if span is None:
        return None, reason
    return binding["requirement_id"], None


def _candidate_requirement(citations, batch, outbound) -> tuple[str | None, str | None]:
    if not citations:
        return None, "missing_citation"
    resolved = [_verified_requirement(citation, batch, outbound) for citation in citations]
    if any(reason is not None for _, reason in resolved):
        return None, next(reason for _, reason in resolved if reason is not None)
    requirements = {requirement for requirement, _ in resolved}
    if len(requirements) != 1:
        return None, "cross_requirement_citation"
    return requirements.pop(), None


def _require_safe_sources(outbound: dict) -> None:
    if outbound["redacted_requirements"]:
        cards.fail(
            "sensitive_scoring_source",
            "A redacted scoring source cannot be persisted as a verifiable Source",
            409,
            4,
        )


def accept_structure(
    secret: dict,
    outbound: dict,
    output: RubricStructureOutput,
    library=(),
) -> dict:
    """Verify the entire structure before any item request can use its section keys."""

    _require_safe_sources(outbound)
    known = {item["requirement_id"] for item in secret["requirements"]}
    scope = _CitationScope(
        requested_requirement_ids=[UUID(local) for local in outbound["requirements"]],
        sent_refs=list(outbound["refs"]),
    )
    allowed_placeholders = {
        entry["placeholder"] for entry in outbound["context"]["confidential_fields"]
    }
    canonical = output.model_dump(mode="json", exclude={"sections", "overall_citations"})
    if not _safe_candidate_text(canonical, ("overall_rule_text",), library, allowed_placeholders):
        cards.fail("sensitive_model_output", "Rubric structure contains sensitive text", 409, 4)
    overall_citations = []
    for citation in output.overall_citations:
        requirement_id, reason = _verified_requirement(citation, scope, outbound)
        if reason is not None or requirement_id not in known:
            cards.fail(
                "invalid_overall_citation", "The overall rule lacks a verified citation", 502, 4
            )
        overall_citations.append(
            {
                "requirement_id": requirement_id,
                "source": outbound["refs"][citation.ref]["source"],
                "quote": citation.quote,
            }
        )
    sections: list[dict] = []
    for section in output.sections:
        candidate = section.model_dump(mode="json")
        sources = []
        for citation in section.citations:
            requirement_id, reason = _verified_requirement(citation, scope, outbound)
            if reason is not None or requirement_id not in known:
                cards.fail(
                    "invalid_section_citation", "A rubric section lacks a verified citation", 502, 4
                )
            # Verification resolves normalized transport spelling to the exact
            # original span. Keep that spelling for immutable review selections.
            original_quote, _ = locate_quote(
                outbound["refs"][citation.ref]["original"], citation.quote
            )
            sources.append(
                {
                    "requirement_id": requirement_id,
                    "source": outbound["refs"][citation.ref]["source"],
                    "quote": original_quote,
                }
            )
        if not _safe_candidate_text(
            candidate,
            ("key", "title", "aggregation_rule_text", "ambiguity_reason"),
            library,
            allowed_placeholders,
        ):
            cards.fail("sensitive_model_output", "Rubric structure contains sensitive text", 409, 4)
        candidate.pop("citations")
        candidate |= {
            "requirement_id": sources[0]["requirement_id"],
            "source": sources[0]["source"],
            "sources": sources,
            "citation_valid": True,
        }
        candidate["fingerprint"] = score_normalization.content_fingerprint("section", candidate)
        sections.append(candidate)
    for field, code in (
        ("key", "duplicate_section_key"),
        ("fingerprint", "duplicate_section_fingerprint"),
    ):
        if len({row[field] for row in sections}) != len(sections):
            cards.fail(code, "Rubric structure has duplicate sections", 502, 4)
    # Citations, proposals and both prompt versions participate in the stage boundary.
    structure_hash = drafts.digest(
        {
            "output": output.model_dump(mode="json"),
            "structure_prompt_version": STRUCTURE_PROMPT_VERSION,
            "items_prompt_version": ITEMS_PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
        }
    )
    return {
        **canonical,
        "sections": sections,
        "overall_citations": overall_citations,
        "proposal": output.model_dump(mode="json"),
        "structure_hash": structure_hash,
    }


def items_request(outbound: dict, structure: dict) -> RubricItemsRequest:
    """Expose fixed section metadata, without other requirements' citation text."""

    fields = RubricSectionContext.model_fields
    return RubricItemsRequest(
        **provider_request(outbound).model_dump(),
        sections=[
            RubricSectionContext.model_validate({key: section[key] for key in fields})
            for section in structure["sections"]
        ],
        structure_hash=structure["structure_hash"],
    )


def accept_batches(
    secret: dict,
    outbound: dict,
    structure: dict,
    batches: list[RubricAnsweredBatch],
    library=(),
) -> dict:
    """Keep valid items only within each sent batch and the verified fixed structure."""

    _require_safe_sources(outbound)
    expected = {item["requirement_id"] for item in secret["requirements"]}
    allowed_placeholders = {
        entry["placeholder"] for entry in outbound["context"]["confidential_fields"]
    }
    section_keys = {section["key"] for section in structure["sections"]}
    provider_refs = {UUID(binding["provider_id"]): ref for ref, binding in outbound["refs"].items()}
    occurrences = Counter(local for batch in batches for local in batch.requested_requirement_ids)
    items: list[dict] = []
    errors: list[str] = []
    for batch in batches:
        requested = set(batch.requested_requirement_ids)
        if (
            not requested
            or any(occurrences[local] != 1 for local in requested)
            or not requested <= set(provider_refs)
            or len(batch.sent_refs) != len(requested)
            or set(batch.sent_refs)
            != {provider_refs[local] for local in requested if local in provider_refs}
            or batch.structure_hash != structure["structure_hash"]
        ):
            errors.append("invalid_batch_scope")
            continue
        for item in batch.output.items:
            local = str(item.requirement_id)
            expected_real = outbound["requirements"].get(local)
            requirement_id, reason = _candidate_requirement(item.citations, batch, outbound)
            if (
                item.requirement_id not in requested
                or reason is not None
                or expected_real is None
                or requirement_id != expected_real
                or requirement_id not in expected
            ):
                errors.append("invalid_item_citation")
                continue
            if item.section_key not in section_keys:
                errors.append("orphan_section_key")
                continue
            candidate = item.model_dump(mode="json")
            if not _safe_candidate_text(
                candidate,
                ("section_key", "key", "title", "rule_text", "ambiguity_reason"),
                library,
                allowed_placeholders,
            ):
                errors.append("sensitive_model_output")
                continue
            candidate.pop("citations")
            candidate |= {
                "requirement_id": requirement_id,
                "source": outbound["refs"][item.citations[0].ref]["source"],
                "citation_valid": True,
            }
            candidate["fingerprint"] = score_normalization.content_fingerprint("item", candidate)
            items.append(candidate)
    for field, code in (
        ("key", "duplicate_item_key"),
        ("fingerprint", "duplicate_item_fingerprint"),
    ):
        counts = Counter(row[field] for row in items)
        duplicates = {key for key, count in counts.items() if count > 1}
        if duplicates:
            errors.append(code)
            items = [row for row in items if row[field] not in duplicates]
    unresolved = sorted(expected - {item["requirement_id"] for item in items})
    if unresolved:
        errors.append("missing_requirement_output")
    for codes in score_normalization.rule_errors(
        structure, structure["sections"], items, candidate_keys=True
    ):
        errors.extend(codes)
    return {
        **structure,
        "items": items,
        "unresolved_requirement_ids": unresolved,
        "normalization_errors": sorted(set(errors)),
    }


async def usage_integrity(
    session: AsyncSession, job: Job, run_id: UUID, reported_usages, *, kind: str = "score_rubric"
) -> None:
    calls = list(
        (
            await session.scalars(
                select(VendorCall).where(VendorCall.job_id == job.id, VendorCall.run_id == run_id)
            )
        ).all()
    )
    usages = list(
        (
            await session.scalars(
                select(UsageRecord).where(
                    UsageRecord.job_id == job.id, UsageRecord.run_id == run_id
                )
            )
        ).all()
    )
    completed = {call.id: call for call in calls if call.state == "completed"}
    if len(completed) != len(usages) or {usage.call_id for usage in usages} != set(completed):
        cards.fail(
            f"{kind}_usage_integrity",
            "Score capability usage does not match settled calls",
            500,
            4,
        )

    def signature(usage):
        return (
            usage.provider,
            usage.model,
            usage.version,
            usage.tokens,
            usage.input_tokens,
            usage.output_tokens,
            usage.provider_config_id,
            usage.platform_model_id,
            Decimal(str(usage.charge or 0)).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP),
        )

    for usage in usages:
        call_id = usage.call_id
        if (
            usage.org_id != job.org_id
            or usage.task_id != job.task_id
            or call_id is None
            or call_id != usage.id
            or usage.charge is None
            or Decimal(str(usage.charge)).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
            != completed[call_id].charge
        ):
            cards.fail(
                f"{kind}_usage_integrity",
                "Score capability usage settlement identity is invalid",
                500,
                4,
            )
    available = Counter(signature(usage) for usage in usages)
    for usage in reported_usages:
        key = signature(usage)
        if available[key] < 1:
            cards.fail(
                f"{kind}_usage_integrity",
                "Provider usage does not match the accounting ledger",
                500,
                4,
            )
        available[key] -= 1
    if any(available.values()):
        cards.fail(
            f"{kind}_usage_integrity",
            "Settled usage is missing from provider accounting metadata",
            500,
            4,
        )


async def publish(
    session: AsyncSession,
    actor: Identity,
    job: Job,
    fixed: RubricSnapshot,
    accepted: dict,
    settings: Settings,
    stop_reason: str | None,
) -> dict:
    if job.task_id is None or job.document_id is None or job.run_id is None:
        score_inputs.integrity()
    version = (
        await session.scalar(
            select(func.coalesce(func.max(ScoreRubricSet.version), 0)).where(
                ScoreRubricSet.extraction_job_id == fixed.extraction.id
            )
        )
        or 0
    ) + 1
    submission = job.result["submission"]
    # The queued cache key still binds the zero-call preview. The published input
    # also binds the verified structure used by every item batch. Update the job
    # envelope in this owned transaction so the existing database publication gate
    # checks exactly the same manifest/hash as the immutable rubric snapshot.
    preview_input_hash = fixed.input_hash
    fixed.manifest = {
        **fixed.manifest,
        "rubric_structure": {
            key: value
            for key, value in accepted.items()
            if key not in {"items", "unresolved_requirement_ids", "normalization_errors"}
        },
    }
    fixed.input_hash = drafts.digest(fixed.manifest)
    submission = {
        **submission,
        "preview_input_hash": preview_input_hash,
        "input_manifest": fixed.manifest,
        "input_hash": fixed.input_hash,
    }
    job.result = {**job.result, "submission": submission}
    await session.flush()
    rubric = ScoreRubricSet(
        id=uuid4(),
        org_id=job.org_id,
        task_id=job.task_id,
        job_id=job.id,
        run_id=job.run_id,
        extraction_job_id=fixed.extraction.id,
        document_id=fixed.document.id,
        prior_rubric_id=None,
        version=version,
        input_hash=fixed.input_hash,
        input_manifest=fixed.manifest,
        encrypted_input=submission["encrypted_input"],
        normalization_rule_version=score_inputs.NORMALIZATION_RULE_VERSION,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        overall_aggregation=accepted["overall_aggregation"],
        overall_rule_text=accepted["overall_rule_text"],
        overall_score_range=accepted["overall_score_range"],
        overall_cap=accepted["overall_cap"],
        normalization_errors=accepted["normalization_errors"],
        actor_user_id=actor.user_id,
        actor_token_id=actor.token_id,
        actor_kind="worker",
    )
    session.add(rubric)
    await session.flush()
    sections_by_key: dict[str, ScoreRubricSection] = {}
    for candidate in accepted["sections"]:
        section = ScoreRubricSection(
            id=uuid4(),
            org_id=job.org_id,
            task_id=job.task_id,
            rubric_id=rubric.id,
            requirement_id=UUID(candidate["requirement_id"]),
            key=candidate["key"],
            title=candidate["title"],
            order=candidate["order"],
            aggregation=candidate["aggregation"],
            aggregation_rule_text=candidate["aggregation_rule_text"],
            score_range=candidate["score_range"],
            weight=candidate["weight"],
            cap=candidate["cap"],
            included_in_overall_total=candidate["included_in_overall_total"],
            ambiguity_reason=candidate["ambiguity_reason"],
            source=candidate["source"],
            sources=candidate["sources"],
            fingerprint=candidate["fingerprint"],
            citation_valid=candidate["citation_valid"],
        )
        session.add(section)
        await session.flush()
        sections_by_key[section.key] = section
    for candidate in accepted["items"]:
        section = sections_by_key[candidate["section_key"]]
        session.add(
            ScoreRubricItem(
                id=uuid4(),
                org_id=job.org_id,
                task_id=job.task_id,
                rubric_id=rubric.id,
                section_id=section.id,
                requirement_id=UUID(candidate["requirement_id"]),
                key=candidate["key"],
                title=candidate["title"],
                rule_text=candidate["rule_text"],
                order=candidate["order"],
                assessment_mode=candidate["assessment_mode"],
                score_range=candidate["score_range"],
                weight=candidate["weight"],
                ambiguity_reason=candidate["ambiguity_reason"],
                source=candidate["source"],
                fingerprint=candidate["fingerprint"],
                citation_valid=candidate["citation_valid"],
            )
        )
    fixed_sources = {
        binding["requirement_id"]: binding["source"]
        for binding in fixed.secret["outbound"]["refs"].values()
    }
    for requirement in fixed.requirements:
        session.add(
            ScoreRubricCoverage(
                id=uuid4(),
                org_id=job.org_id,
                task_id=job.task_id,
                rubric_id=rubric.id,
                requirement_id=requirement.id,
                source=fixed_sources[str(requirement.id)],
            )
        )
    await session.flush()
    usage_rows = list(
        (await session.scalars(select(UsageRecord).where(UsageRecord.job_id == job.id))).all()
    )
    unresolved = len(accepted["unresolved_requirement_ids"])
    completion = (
        "partial" if stop_reason or unresolved or accepted["normalization_errors"] else "complete"
    )
    audit(
        session,
        actor,
        "score_rubric.completed",
        rubric.id,
        {
            "task_id": str(job.task_id),
            "job_id": str(job.id),
            "run_id": str(job.run_id),
            "rubric_id": str(rubric.id),
            "input_hash": fixed.input_hash,
            "version": version,
            "completion": completion,
            "usage_record_ids": [str(row.id) for row in usage_rows],
        },
    )
    public = RubricGenerateResult.model_validate(
        {
            "rubric_id": rubric.id,
            "job_id": job.id,
            "completion": completion,
            "version": version,
            "scoring_requirements": len(fixed.requirements),
            "candidate_items": len(accepted["items"]),
            "unresolved_requirements": unresolved,
            "usage_record_ids": [row.id for row in usage_rows],
            "charge": sum(
                (Decimal(str(row.charge)) for row in usage_rows if row.charge is not None),
                Decimal(0),
            ),
            "billing_currency": settings.billing_currency,
            "stop_reason": stop_reason,
        }
    ).model_dump(mode="json")
    return {
        **public,
        "cost": await job_cost(session, job.id),
        "warnings": fixed.manifest["warnings"] + accepted["normalization_errors"],
        "exit_code": 5 if completion == "partial" else 0,
    }
