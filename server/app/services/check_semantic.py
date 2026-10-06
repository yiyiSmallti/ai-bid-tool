"""Outbound-only semantic assessment and local acceptance of untrusted conclusions."""

import json
import re
import unicodedata
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import func, select

from app.core.security import Secrets
from app.models.entities import OrgBalance, UsageRecord, VendorCall
from app.providers import llm as llm_providers
from app.providers.base import ProviderFailure
from app.providers.checking import (
    CHECK_ADAPTER_VERSION as ADAPTER_VERSION,
)
from app.providers.checking import (
    CHECK_PROMPT_VERSION as PROMPT_VERSION,
)
from app.providers.checking import (
    CHECK_SCHEMA_VERSION as SCHEMA_VERSION,
)
from app.providers.checking import (
    CheckProviderRequest,
    CheckWireOutput,
    check_provider,
    supports_check,
)
from app.providers.configured import model_identity
from app.providers.llm import with_reasoning
from app.schemas.check_contracts import OutboundContext
from app.services import confidential, drafts, redaction
from app.services.check_inputs import CheckSnapshot
from app.services.extraction import (
    locate_quote,
    locate_sent_source_quote,
    locate_source_citation_span,
)
from app.services.response_cards import fail

# Only these admission stops permit retaining already completed deterministic coverage.
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
    "check_usage_integrity",
    "check_input_changed",
    "redaction_required",
    "provider_model_changed",
    "provider_config_unavailable",
    "check_input_integrity",
}


async def resolve(session, settings, job=None):
    """Use the tenant/catalog resolver, including its immutable job revision gate."""
    return await llm_providers.resolve_llm(session, settings, job=job)


def prices(llm) -> dict:
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
        "batch_chars": configured.llm_batch_chars if configured else None,
        "output_tokens": configured.llm_max_output_tokens if configured else None,
        "json_mode": configured.llm_json_mode if configured else None,
        "effort": configured.llm_effort if configured else None,
    }


async def secret_library(session, task_id, settings):
    entries = await confidential.task_entries(session, task_id)
    crypto = Secrets.for_data(settings)
    library = confidential.library(entries, crypto)
    present = {entry.key for entry in library}
    # Check cannot expose even short registered values which the general drafting
    # matcher deliberately skips to avoid broad replacements.
    for key, entry in entries.items():
        if entry.value is not None and key not in present:
            value = unicodedata.normalize(
                "NFKC", crypto.decrypt(entry.value.encrypted_value)
            ).strip()
            if value:
                library.append(redaction.LibraryValue(key, re.compile(re.escape(value))))
    return confidential.prompt_fields(entries), library


def eligible(item: dict) -> bool:
    """Only verified response rows supply bid text for semantic comparison."""
    return item["partition"] == "response" and bool((item.get("response_text") or "").strip())


def build_outbound(secret: dict, fields: list[dict], library) -> dict:
    texts, refs, requirements = [], {}, {}
    for index, item in enumerate(secret["items"], 1):
        if not eligible(item):
            continue
        local = f"r{index}"
        requirements[local] = item["requirement_id"]

        def add(suffix, text, kind, *, local=local, **binding):
            if not text:
                return
            ref = f"{local}.{suffix}"
            texts.append({"ref": ref, "text": text})
            refs[ref] = {"requirement": local, "kind": kind, "original": text, **binding}

        add(
            "tender",
            item["source"]["quote"],
            "tender",
            source=item["source"],
            location_original=item.get("tender_original", item["source"]["quote"]),
        )
        # Location labels are data too; real document/chunk IDs never leave the service.
        if item["source"].get("location"):
            add("location", json.dumps(item["source"]["location"], ensure_ascii=False), "metadata")
        add(
            "context",
            json.dumps(
                {
                    key: item.get(key)
                    for key in ("partition", "category", "starred", "response_kind", "deviation")
                },
                ensure_ascii=False,
            ),
            "metadata",
        )
        if item["partition"] == "response":
            for field in ("response_text", "deviation_note"):
                add(
                    field,
                    item.get(field),
                    "draft",
                    field=field,
                    response_item_id=item["response_item_id"],
                    card_revision_id=item["card_revision_id"],
                )
            for number, evidence in enumerate(item["evidence"], 1):
                if evidence["kind"] != "image_region":
                    add(
                        f"e{number}",
                        evidence["quote"],
                        "evidence",
                        evidence_id=evidence["id"],
                        location_original=evidence.get("original_text", evidence["quote"]),
                    )
    raw = {"texts": texts, "confidential_fields": fields}
    # One traversal covers every externally supplied text, including hints and labels.
    sent, counts = redaction.redact_tree(raw, True, library)
    context = OutboundContext.model_validate(sent)
    if [row.ref for row in context.texts] != [row["ref"] for row in texts]:
        fail(
            "check_outbound_ref_changed",
            "Confidential values overlap local reference labels",
            409,
            4,
        )
    redacted = set()
    for row in context.texts:
        refs[row.ref]["sent"] = row.text
        if refs[row.ref]["kind"] == "tender":
            refs[row.ref]["sent_source"] = row.text
        if redaction.PLACEHOLDER.search(row.text) or redaction.SECRET_PLACEHOLDER.search(row.text):
            if refs[row.ref]["kind"] != "metadata":
                redacted.add(refs[row.ref]["requirement"])
    return {
        "context": context.model_dump(mode="json"),
        "refs": refs,
        "requirements": requirements,
        "redacted_counts": counts,
        "redacted_requirements": sorted(redacted),
        "hint_originals": [field["label"] for field in fields],
    }


def requests_for(outbound: dict, llm) -> list[CheckProviderRequest]:
    budget = llm.settings.llm_batch_chars
    context = outbound["context"]
    batches: list[CheckProviderRequest] = []
    ids: list[str] = []

    def request(keys):
        return CheckProviderRequest(
            requested_requirement_ids=keys,
            context=OutboundContext.model_validate(
                {
                    "texts": [
                        row
                        for row in context["texts"]
                        if outbound["refs"][row["ref"]]["requirement"] in keys
                    ],
                    "confidential_fields": context["confidential_fields"],
                }
            ),
        )

    for local in outbound["requirements"]:
        trial = request([*ids, local])
        if len(trial.model_dump_json()) > budget:
            if ids:
                batches.append(request(ids))
            ids = [local]
            if len(request(ids).model_dump_json()) > budget:
                fail(
                    "check_context_limit",
                    "A complete requirement exceeds the configured context batch limit",
                    400,
                    2,
                )
        else:
            ids.append(local)
    if ids:
        batches.append(request(ids))
    return batches


async def prepare(session, fixed: CheckSnapshot, llm, requested_reasoning, settings):
    if not fixed.manifest["model_redaction_enabled"]:
        # This admission blocker takes precedence even when no model can resolve.
        # It never decrypts values or changes the administrator's setting.
        fixed.manifest |= {
            "mode": "combined",
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
        fixed.limitations = [
            value for value in fixed.limitations if value != "semantic_not_checked"
        ]
        fixed.input_hash = drafts.digest(fixed.manifest)
        return None
    llm, reasoning, warnings = with_reasoning(llm, requested_reasoning)
    fixed.manifest |= {
        "mode": "combined",
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "model": model_identity(llm),
        "reasoning": reasoning,
        "price": prices(llm),
        "provider_source": "org" if getattr(llm, "org_owned", False) else "platform",
    }
    fixed.limitations = [value for value in fixed.limitations if value != "semantic_not_checked"]
    fixed.limitations += warnings
    if getattr(llm, "org_owned", False):
        fixed.limitations.append("org_owned_key_max_charge_does_not_bound_vendor_bill")
    fields, library = await secret_library(session, fixed.draft.task_id, settings)
    outbound = build_outbound(fixed.secret, fields, library)
    fixed.secret["outbound"] = outbound
    fixed.manifest["outbound_sha256"] = drafts.digest(outbound["context"])
    fixed.manifest["redacted_counts"] = outbound["redacted_counts"]
    fixed.manifest["confidential_hints_sha256"] = drafts.digest({"fields": fields})
    fixed.input_hash = drafts.digest(fixed.manifest)
    return llm


async def preview(session, fixed, llm, body, settings) -> dict:
    summary = {
        "estimated_cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": None},
        "estimated_charge": None,
        "cost_basis": "unknown",
        "cost_basis_reason": "model_unavailable",
        "admission_blocker": None,
    }
    if not fixed.manifest["model_redaction_enabled"]:
        return summary | {
            "admission_blocker": "redaction_required",
            "cost_basis_reason": "redaction_required",
        }
    if not fixed.secret["outbound"]["requirements"]:
        return {
            "estimated_cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0},
            "estimated_charge": Decimal(0),
            "cost_basis": "known",
            "cost_basis_reason": "no_model_calls",
            "admission_blocker": None,
        }
    if not supports_check(llm):
        return summary | {"admission_blocker": "check_capability_unavailable"}
    adapter = check_provider(llm)
    requests = requests_for(fixed.secret["outbound"], llm)
    bodies = [adapter.request_body(request) for request in requests]
    try:
        if llm.platform_model_id is None and not llm.org_owned:
            return summary | {
                "admission_blocker": "billing_price_unavailable",
                "cost_basis_reason": "prices_unavailable",
            }
        bounds = [adapter.reservation(request) for request in requests]
    except ProviderFailure as error:
        return summary | {
            "admission_blocker": error.code,
            "cost_basis_reason": "prices_unavailable",
        }
    inputs = sum(len(json.dumps(value, ensure_ascii=False).encode()) + 4096 for value in bodies)
    outputs = sum(llm.output_token_bound(value) for value in bodies)
    input_price, output_price = (
        llm.settings.llm_input_usd_per_mtok,
        llm.settings.llm_output_usd_per_mtok,
    )
    usd = (
        None
        if input_price is None or output_price is None
        else (inputs * input_price + outputs * output_price) / 1_000_000
    )
    first = bounds[0] if bounds else Decimal(0)
    blocker = None
    if first > settings.job_max_charge:
        blocker = "job_charge_limit_exceeded"
    elif body.max_charge is not None and first > body.max_charge:
        blocker = "spend_cap_below_first_call"
    if llm.platform_model_id is not None:
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
        "estimated_charge": sum(bounds, Decimal(0)),
        "cost_basis": "known",
        "cost_basis_reason": "first_pass_token_bound",
        "admission_blocker": blocker,
    }


def reject(row: dict, code: str, *, outcome=None):
    row.update(
        semantic_status="unassessed",
        semantic_outcome=outcome,
        semantic_reason_code=code,
        semantic_citations=[],
        unassessed=True,
    )


def unsafe_reason(reason: str, outbound: dict, library) -> str | None:
    allowed = {hint["placeholder"] for hint in outbound["context"]["confidential_fields"]}
    placeholders = re.findall(r"\{\{[^{}]*\}\}|\[REDACTED_[^\]]*\]", reason)
    if any(value not in allowed for value in placeholders):
        return "unknown_placeholder"
    if redaction.redact(reason, True, library)[0] != reason:
        return "sensitive_model_output"
    normalized = unicodedata.normalize("NFKC", reason)
    # A value echoed without its original identifying label still must be rejected.
    for text in [
        *[ref["original"] for ref in outbound["refs"].values()],
        *outbound.get("hint_originals", []),
    ]:
        original = unicodedata.normalize("NFKC", text)
        for _, pattern in redaction.RULES:
            for match in pattern.finditer(original):
                literal = match.group("value") if "value" in pattern.groupindex else match.group()
                if literal.strip() and literal.strip() in normalized:
                    return "sensitive_model_output"
    return None


def verify_citation(citation, local, sent_refs, outbound, draft_id):
    if citation.ref not in sent_refs:
        return None, "ref_not_sent"
    binding = outbound["refs"][citation.ref]
    if binding["requirement"] != local:
        return None, "cross_requirement_citation"
    if binding["kind"] == "metadata":
        return None, "ref_not_sent"
    if redaction.PLACEHOLDER.search(citation.quote) or redaction.SECRET_PLACEHOLDER.search(
        citation.quote
    ):
        return None, "redacted_input_unassessable"
    sent_text = binding["sent_source"] if binding["kind"] == "tender" else binding["sent"]
    locate_sent = locate_sent_source_quote if binding["kind"] == "tender" else locate_quote
    sent_quote, reason = locate_sent(sent_text, citation.quote)
    if sent_quote is None:
        return None, reason
    original_quote, reason = locate_quote(binding["original"], citation.quote)
    if original_quote is None:
        return None, reason
    span, reason = locate_source_citation_span(
        binding.get("location_original", binding["original"]), binding["original"], original_quote
    )
    if span is None:
        return None, reason
    if redaction.PLACEHOLDER.search(original_quote) or redaction.SECRET_PLACEHOLDER.search(
        original_quote
    ):
        return None, "redacted_input_unassessable"
    # locate_span can prefer a word-boundary occurrence. The database citation
    # gate intentionally requires literal uniqueness even inside a longer token.
    for text, quote in (
        (sent_text, sent_quote),
        (binding["original"], original_quote),
    ):
        first = text.find(quote)
        if first < 0:
            return None, "quote_not_at_position"
        if text.find(quote, first + 1) >= 0:
            return None, "ambiguous_quote"
    if len(original_quote) > 20000:
        return None, "invalid_semantic_answer"
    if binding["kind"] == "tender":
        return {"kind": "tender", "source": {**binding["source"], "quote": original_quote}}, None
    if binding["kind"] == "draft":
        return {
            "kind": "draft",
            "draft_id": draft_id,
            **{key: binding[key] for key in ("response_item_id", "card_revision_id", "field")},
            "quote": original_quote,
        }, None
    return {
        "kind": "evidence",
        "evidence_id": binding["evidence_id"],
        "quote": original_quote,
    }, None


def accept_batch(
    evaluated, outbound, request: CheckProviderRequest, wire: CheckWireOutput, library, draft_id
):
    by_id = {row["item"]["requirement_id"]: row for row in evaluated}
    expected = set(request.requested_requirement_ids)
    counts = Counter(answer.requirement_id for answer in wire.items)
    sent_refs = {row.ref for row in request.context.texts}
    unknown_ids = set(counts) - expected
    if any(
        len(answer.findings) > 20
        or len(answer.citations) > 20
        or any(len(finding.citations) > 20 for finding in answer.findings)
        for answer in wire.items
    ):
        fail("check_output_limit", "A semantic batch exceeds the finding or citation limit", 400, 2)
    for local in request.requested_requirement_ids:
        row = by_id[outbound["requirements"][local]]
        if unknown_ids:
            reject(row, "unknown_requirement_id")
            continue
        if counts[local] != 1:
            reject(
                row, "missing_requirement_id" if counts[local] == 0 else "duplicate_requirement_id"
            )
            continue
        answer = next(value for value in wire.items if value.requirement_id == local)
        if local in outbound["redacted_requirements"]:
            reject(row, "redacted_input_unassessable")
            continue
        if (answer.status != "risk" and answer.findings) or (
            answer.status == "risk" and not answer.findings
        ):
            reject(row, "invalid_semantic_answer")
            continue
        rejected = None
        accepted, findings = [], []
        for candidate in [answer, *answer.findings]:
            citations = []
            for citation in candidate.citations:
                verified, reason = verify_citation(citation, local, sent_refs, outbound, draft_id)
                if reason:
                    rejected = reason
                    break
                citations.append(verified)
            if rejected:
                break
            kinds = {citation["kind"] for citation in citations}
            if candidate is answer:
                accepted = citations
                if answer.status == "no_risk_found" and not (
                    "tender" in kinds and kinds & {"draft", "evidence"}
                ):
                    rejected = "insufficient_citations"
                    break
            else:
                rejected = unsafe_reason(candidate.reason, outbound, library)
                if not rejected and any(
                    other != local
                    and re.search(rf"(?<!\w){re.escape(other)}(?:\.|\b)", candidate.reason)
                    for other in outbound["requirements"]
                ):
                    rejected = "cross_requirement_citation"
                if rejected:
                    break
                if "tender" not in kinds or (
                    candidate.code == "semantic_contradiction" and not kinds & {"draft", "evidence"}
                ):
                    rejected = "insufficient_citations"
                    break
                findings.append(
                    {
                        "method": "semantic",
                        "code": candidate.code,
                        "severity": candidate.severity,
                        "reason": candidate.reason,
                        "citations": citations,
                    }
                )
        if rejected:
            reject(row, rejected)
        elif answer.status == "unknown":
            reject(row, "semantic_unknown", outcome="unknown")
        elif len(row["findings"]) + len(findings) > 20:
            fail("check_finding_limit", "A requirement supports at most 20 findings")
        else:
            row.update(
                semantic_status="assessed",
                semantic_outcome=answer.status,
                semantic_reason_code=None,
                semantic_citations=accepted if answer.status == "no_risk_found" else [],
            )
            row["findings"].extend(findings)


async def usage_integrity(session, job, run_id: UUID, reported_usages) -> None:
    """Provider usage corroborates accounting; it never becomes another settlement."""
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
        fail("check_usage_integrity", "Check usage does not match settled calls", 500, 4)
    for usage in usages:
        if (
            usage.org_id != job.org_id
            or usage.task_id != job.task_id
            or usage.call_id != usage.id
            or usage.charge is None
            or Decimal(str(usage.charge)).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
            != completed[usage.call_id].charge
        ):
            fail("check_usage_integrity", "Check usage settlement identity is invalid", 500, 4)

    # Multiset comparison avoids guessing call IDs from provider return order.
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

    available = Counter(signature(usage) for usage in usages)
    for usage in reported_usages:
        key = signature(usage)
        if available[key] < 1:
            fail(
                "check_usage_integrity",
                "Provider usage does not match the accounting ledger",
                500,
                4,
            )
        available[key] -= 1
    if any(available.values()):
        fail(
            "check_usage_integrity",
            "Settled usage is missing from provider accounting metadata",
            500,
            4,
        )
