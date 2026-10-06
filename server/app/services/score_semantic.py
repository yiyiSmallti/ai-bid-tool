"""Local score redaction, semantic acceptance, and deterministic aggregation."""

from __future__ import annotations

import json
import re
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from typing import Any, cast
from uuid import UUID

from app.schemas.check_contracts import AssessmentFailure, OutboundContext
from app.schemas.score_contracts import (
    ScoreProviderItem,
    ScoreProviderRequest,
    ScoreProviderResult,
    ScoreRange,
)
from app.services import check_semantic, redaction
from app.services.extraction import (
    locate_quote,
    locate_sent_source_quote,
    locate_source_citation_span,
)

SCORING_RULE_VERSION = "score-rules-v1"
SCORE_QUANTUM = Decimal("0.00000001")
EXECUTABLE = {"sum", "weighted_sum", "capped_sum"}
UNSUPPORTED = {"formula", "non_additive"}

PROOF_RULE = re.compile(
    r"证书|证明|报告|截图|参数|业绩|附件|certificate|proof|report|screenshot|"
    r"specification|parameter|experience|attachment",
    re.IGNORECASE,
)
THIN_PROMISE = re.compile(
    r"满足|完全响应|可提供|将提供|fully\s+comply|\bcomplies?\b|can\s+provide|will\s+provide",
    re.IGNORECASE,
)
SUBSTANTIVE_PARAMETER = re.compile(
    r"\d+(?:\.\d+)?\s*(?:[kmgt]?b|[kmg]?hz|[mun]?m|kg|v|w|a|db|dpi|ppm|fps|bit|%|核|线程)",
    re.IGNORECASE,
)
COMMITMENT_SUFFICIENT_RULE = re.compile(
    r"承诺(?:书|函|文字|本身)?.*(?:得|计)\s*\d*(?:\.\d+)?\s*分|"
    r"(?:commitment|promise).*(?:sufficient|earns?|award).*(?:points?|score)|"
    r"(?:award|score).*(?:commitment|promise)",
    re.IGNORECASE,
)
PRICE_RULE = re.compile(
    r"价格|报价|基准价|评标价|price|quotation|benchmark\s+price",
    re.IGNORECASE,
)
EXTERNAL_RULE = re.compile(
    r"其他投标人|投标人排名|外部名次|评委|现场演示|主观印象|"
    r"other\s+bidders?|rank(?:ing)?|evaluator|live\s+demo|subjective",
    re.IGNORECASE,
)
FORMULA_RULE = re.compile(r"公式|formula", re.IGNORECASE)
UNSAFE_ACTION = re.compile(
    r"伪造|编造|杜撰|假造|虚构|报价策略|压价|抬价|"
    r"fabricat|forg(?:e|ery)|fake\s+(?:a\s+)?(?:certificate|report|screenshot|proof)|"
    r"pricing\s+strategy|lower\s+(?:the\s+)?price|raise\s+(?:the\s+)?price",
    re.IGNORECASE,
)

REASONS = {
    "ambiguous": "The confirmed scoring rule requires manual resolution.",
    "price_comparison": "Price and benchmark comparisons are not assessed automatically.",
    "external_comparison": "The score depends on external comparison or evaluator input.",
    "manual_only": "The confirmed rubric reserves this item for manual assessment.",
    "unsupported_formula": "The confirmed formula is recorded but is not executable.",
    "missing_score_bounds": "Confirmed score bounds are unavailable.",
    "no_confirmed_draft_support": "No confirmed draft text is available for assessment.",
    "thin_promise_unassessable": "A thin promise cannot replace the proof required by the rule.",
    "redacted_input_unassessable": "Required input was redacted and cannot support a score.",
    "missing_rubric_item_id": "The Provider omitted this requested rubric item.",
    "duplicate_rubric_item_id": "The Provider returned this rubric item more than once.",
    "unknown_rubric_item_id": "The Provider returned an item outside the requested batch.",
    "invalid_batch_refs": "The completed batch did not preserve its fixed reference boundary.",
    "provider_incomplete": "The Provider did not complete this rubric item.",
    "invalid_score_output": "The Provider result is not a valid score assessment.",
    "ref_not_sent": "A citation did not use a reference sent in its completed batch.",
    "invalid_citation_kind": "A citation used a reference type that cannot support this score.",
    "quote_not_found": "A citation quote was not found in the fixed text.",
    "quote_not_at_position": "A citation quote does not match the fixed source position.",
    "ambiguous_quote": "A citation quote is not unique in the fixed text.",
    "insufficient_citations": "An assessed item requires verified tender and draft citations.",
    "score_out_of_bounds": "The proposed score is outside the confirmed bounds.",
    "missing_deduction_reason": "A score below the confirmed maximum requires a deduction reason.",
    "negative_deviation_conflict": "A maximum score cannot rely only on confirmed negative deviations.",
    "unknown_placeholder": "Provider prose contains an unknown confidential placeholder.",
    "sensitive_model_output": "Provider prose contains content that cannot be stored safely.",
    "unsafe_strengthening_action": "A strengthening action suggests prohibited fabrication or pricing strategy.",
}


def _plain(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    return cast(dict, value.model_dump(mode="python"))


def _uuid(value: Any) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _rubric(secret: dict) -> tuple[dict, list[dict], list[dict]]:
    report = _plain(secret["rubric"])
    return (
        _plain(report.get("rubric", report.get("report", report))),
        [_plain(row) for row in report["sections"]],
        [_plain(row) for row in report["items"]],
    )


def _anchors(secret: dict) -> dict[str, dict]:
    anchors: dict[str, dict] = {}
    for row in secret["items"]:
        row = _plain(row)
        requirement_id = str(row["requirement_id"])
        if requirement_id in anchors:
            raise ValueError("score snapshot contains duplicate requirement anchors")
        anchors[requirement_id] = row
    return anchors


def _preflight_reason(item: dict, anchor: dict, draft_refs: list[str]) -> str | None:
    mode = item.get("assessment_mode")
    if mode != "model_assessable":
        return str(mode) if mode in REASONS else "manual_only"
    if item.get("score_range") is None:
        return "missing_score_bounds"
    if not draft_refs:
        return "no_confirmed_draft_support"
    rule = str(item.get("rule_text") or "")
    if PRICE_RULE.search(rule):
        return "price_comparison"
    if EXTERNAL_RULE.search(rule):
        return "external_comparison"
    if FORMULA_RULE.search(rule):
        return "unsupported_formula"
    return None


def build_outbound(secret: dict, fields: list[dict], library) -> dict:
    """Redact fixed rubric/draft text and bind it to local, non-repairable refs."""

    _, sections, rubric_items = _rubric(secret)
    anchors = _anchors(secret)
    section_keys = {str(row["id"]): row["key"] for row in sections}
    provider_section_keys = {
        str(row["id"]): f"section{index}" for index, row in enumerate(sections, 1)
    }
    texts: list[dict] = []
    refs: dict[str, dict] = {}
    counts: Counter[str] = Counter({kind: 0 for kind in (*redaction.KINDS, "confidential")})

    def add(ref: str, text: Any, kind: str, *, sent_text=None, **binding) -> None:
        if text is None or not str(text):
            return
        original = str(text)
        if sent_text is None:
            sent, found = redaction.redact(original, True, library)
            counts.update(found)
        else:
            sent = str(sent_text)
        texts.append({"ref": ref, "text": sent})
        refs[ref] = {"kind": kind, "original": original, "sent": sent, **binding}

    item_bindings: dict[str, dict] = {}
    for index, item in enumerate(rubric_items, 1):
        item_id = str(item["id"])
        anchor = anchors.get(str(item["requirement_id"]))
        if anchor is None:
            raise ValueError("score rubric item has no fixed DraftRun anchor")
        tender_ref, rule_ref = f"s{index}.tender", f"s{index}.rule"
        source = _plain(item.get("source") or anchor["source"])
        safe_quote, quote_counts = redaction.redact(str(source["quote"]), True, library)
        safe_location, location_counts = redaction.redact_tree(
            source.get("location"), True, library
        )
        counts.update(quote_counts)
        counts.update(location_counts)
        safe_source = {**source, "quote": safe_quote, "location": safe_location}
        add(
            tender_ref,
            source["quote"],
            "tender",
            sent_text=safe_source["quote"],
            sent_source=safe_source["quote"],
            rubric_item_id=item_id,
            requirement_id=str(item["requirement_id"]),
            source=safe_source,
            source_redacted=safe_source != source,
            location_original=anchor.get("tender_original", source["quote"]),
        )
        add(
            rule_ref,
            item["rule_text"],
            "rule",
            rubric_item_id=item_id,
            requirement_id=str(item["requirement_id"]),
        )
        item_bindings[item_id] = {
            "item": item,
            "anchor": anchor,
            "tender_ref": tender_ref,
            "rule_ref": rule_ref,
            "section_key": section_keys[str(item["section_id"])],
            "provider_section_key": provider_section_keys[str(item["section_id"])],
        }

    draft_refs: list[str] = []
    anchor_draft_refs: dict[str, list[str]] = {}
    anchor_context_refs: dict[str, list[str]] = {}
    context_only_refs: list[str] = []
    response_context_refs: list[str] = []
    for index, row in enumerate((_plain(value) for value in secret["items"]), 1):
        anchor_id = str(row["response_item_id"])
        source = _plain(row["source"])
        metadata_ref = f"d{index}.context"
        metadata = {
            "partition": row["partition"],
            "source_position": {
                "page": source.get("page"),
                "location": source.get("location"),
            },
            "response_kind": row.get("response_kind"),
            "deviation": row.get("deviation"),
            "gap_reason_codes": list(row.get("gap_reasons") or []),
            "disposition": row.get("disposition"),
        }
        add(
            metadata_ref,
            json.dumps(metadata, ensure_ascii=False, default=str),
            "metadata",
        )
        tender_ref = f"d{index}.tender"
        add(
            tender_ref,
            source["quote"],
            "draft_tender",
            location_original=row.get("tender_original", source["quote"]),
        )
        context_only_refs.extend((metadata_ref, tender_ref))
        anchor_context_refs.setdefault(anchor_id, []).extend((metadata_ref, tender_ref))
        if row["partition"] != "response":
            continue
        response_context_refs.extend((metadata_ref, tender_ref))
        for field in ("response_text", "deviation_note"):
            if not row.get(field):
                continue
            ref = f"d{index}.{field}"
            add(
                ref,
                row[field],
                "draft",
                response_item_id=str(row["response_item_id"]),
                card_revision_id=str(row["card_revision_id"]),
                field=field,
                deviation=row.get("deviation"),
            )
            draft_refs.append(ref)
            anchor_draft_refs.setdefault(anchor_id, []).append(ref)

    sent_fields, field_counts = redaction.redact_tree(fields, True, library)
    counts.update(field_counts)
    context = OutboundContext.model_validate({"texts": texts, "confidential_fields": sent_fields})
    if [text.ref for text in context.texts] != list(refs):
        raise ValueError("score outbound redaction changed a fixed ref")

    preflight: dict[str, dict] = {}
    provider_items: list[dict] = []
    for item_id, binding in item_bindings.items():
        item, anchor = binding["item"], binding["anchor"]
        # Confirmed coverage maps only to items with the same requirement_id.
        # Cross-requirement support therefore needs all response candidates, not
        # unrelated text-free partitions or duplicate coverage anchors.
        item_context_refs = set(response_context_refs) | set(
            anchor_context_refs[str(anchor["response_item_id"])]
        )
        reason = _preflight_reason(item, anchor, draft_refs)
        own_refs = [
            binding["tender_ref"],
            binding["rule_ref"],
            *anchor_draft_refs.get(str(anchor["response_item_id"]), []),
            *anchor_context_refs.get(str(anchor["response_item_id"]), []),
        ]
        if reason is None and (
            refs[binding["tender_ref"]].get("source_redacted")
            or any(refs[ref]["sent"] != refs[ref]["original"] for ref in own_refs)
        ):
            reason = "redacted_input_unassessable"
        if reason is not None:
            preflight[item_id] = {"reason_code": reason, "reason": REASONS[reason]}
            continue
        provider_item = ScoreProviderItem(
            rubric_item_id=_uuid(item_id),
            requirement_id=_uuid(item["requirement_id"]),
            section_key=binding["provider_section_key"],
            tender_ref=binding["tender_ref"],
            rule_ref=binding["rule_ref"],
            draft_refs=draft_refs,
            context_only_refs=[ref for ref in context_only_refs if ref in item_context_refs],
            anchor_response_item_id=_uuid(anchor["response_item_id"]),
            anchor_partition=anchor["partition"],
            anchor_gap_reason_codes=list(anchor.get("gap_reasons") or []),
            score_range=item["score_range"],
        )
        provider_items.append(provider_item.model_dump(mode="json"))
    selected_context_refs = {ref for item in provider_items for ref in item["context_only_refs"]}
    context_only_refs = [ref for ref in context_only_refs if ref in selected_context_refs]
    selected_refs = {
        ref
        for item in provider_items
        for ref in (item["tender_ref"], item["rule_ref"], *item["draft_refs"])
    } | selected_context_refs
    context = context.model_copy(
        update={"texts": [text for text in context.texts if text.ref in selected_refs]}
    )
    return {
        "assessment_date": secret["assessment_date"],
        "context": context.model_dump(mode="json"),
        "refs": refs,
        "provider_items": provider_items,
        "context_only_refs": context_only_refs,
        "preflight": preflight,
        "redacted_counts": dict(counts),
        "hint_originals": [field["label"] for field in fields],
    }


def provider_request(outbound: dict, assessment_date=None) -> ScoreProviderRequest | None:
    """Return the fixed Provider request, or None when every item is deterministic."""

    if not outbound["provider_items"]:
        return None
    items = [ScoreProviderItem.model_validate(item) for item in outbound["provider_items"]]
    context_only_refs = list(outbound["context_only_refs"])
    allowed = {
        ref for item in items for ref in (item.tender_ref, item.rule_ref, *item.draft_refs)
    } | set(context_only_refs)
    value = assessment_date or outbound.get("assessment_date")
    if value is None:
        raise ValueError("score outbound is missing its fixed assessment date")
    return ScoreProviderRequest(
        assessment_date=value,
        items=items,
        context_only_refs=context_only_refs,
        context=OutboundContext.model_validate(
            {
                "texts": [row for row in outbound["context"]["texts"] if row["ref"] in allowed],
                "confidential_fields": outbound["context"]["confidential_fields"],
            }
        ),
    )


def empty_result(code: str) -> ScoreProviderResult:
    """Construct an explicit zero-batch Provider stop for deterministic handling."""

    return ScoreProviderResult(
        batches=[],
        usages=[],
        failure=AssessmentFailure(code=code, retryable=False),
    )


def _reason(code: str) -> str:
    return REASONS.get(code, "The score item could not be verified safely.")


def _strengthening_action(code: str) -> str:
    if code == "missing_score_bounds":
        return "Review and confirm explicit score bounds before reassessment."
    if code in {
        "ambiguous",
        "price_comparison",
        "external_comparison",
        "manual_only",
        "unsupported_formula",
    }:
        return (
            "Complete the required human assessment using the confirmed rule and authorized inputs."
        )
    return "Add or confirm verifiable draft support, then rerun the assessment."


def _unassessable(item: dict, anchor: dict, section_key: str, code: str, reason=None) -> dict:
    score_range = item.get("score_range")
    if score_range is not None:
        score_range = ScoreRange.model_validate(score_range).model_dump(mode="python")
    return {
        "rubric_item_id": _uuid(item["id"]),
        "requirement_id": _uuid(item["requirement_id"]),
        "anchor_response_item_id": _uuid(anchor["response_item_id"]),
        "response_item_ids": [],
        "section_key": section_key,
        "anchor_partition": anchor["partition"],
        "outcome": "unassessable",
        "score_range": score_range,
        "estimated_score": None,
        "reason_code": code,
        "reason": reason or _reason(code),
        "deduction_reasons": [],
        "strengthening_actions": [_strengthening_action(code)],
        "citations": [],
        "advisory_only": True,
    }


def _verify_citation(citation, item_id: str, batch, outbound: dict, draft_id: UUID, kind: str):
    if citation.ref not in batch.sent_refs or citation.ref not in outbound["refs"]:
        return None, "ref_not_sent"
    binding = outbound["refs"][citation.ref]
    if kind == "tender":
        if binding["kind"] != "tender" or binding.get("rubric_item_id") != item_id:
            return None, "invalid_citation_kind"
    elif binding["kind"] != "draft":
        return None, "invalid_citation_kind"
    if redaction.PLACEHOLDER.search(citation.quote) or redaction.SECRET_PLACEHOLDER.search(
        citation.quote
    ):
        return None, "redacted_input_unassessable"
    sent_text = binding["sent_source"] if kind == "tender" else binding["sent"]
    locate_sent = locate_sent_source_quote if kind == "tender" else locate_quote
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
    for text, quote in (
        (sent_text, sent_quote),
        (binding["original"], original_quote),
    ):
        first = text.find(quote)
        if first < 0:
            return None, "quote_not_at_position"
        if text.find(quote, first + 1) >= 0:
            return None, "ambiguous_quote"
    if kind == "tender":
        return {
            "kind": "tender",
            "source": {**binding["source"], "quote": original_quote},
        }, None
    return {
        "kind": "draft",
        "draft_id": draft_id,
        "response_item_id": _uuid(binding["response_item_id"]),
        "card_revision_id": _uuid(binding["card_revision_id"]),
        "field": binding["field"],
        "quote": original_quote,
    }, None


def _unsafe_prose(values: list[str], outbound: dict, library) -> str | None:
    for value in values:
        unsafe = check_semantic.unsafe_reason(value, outbound, library)
        if unsafe is not None:
            return unsafe
    return None


def _thin_support(text: str, proof_required: bool) -> bool:
    return bool(THIN_PROMISE.search(text)) and (
        proof_required or not SUBSTANTIVE_PARAMETER.search(text)
    )


def accept_batches(
    secret: dict,
    outbound: dict,
    result: ScoreProviderResult,
    library=(),
) -> list[dict]:
    """Normalize every fixed rubric item without repairing model IDs, refs, or scores."""

    _, sections, rubric_items = _rubric(secret)
    anchors = _anchors(secret)
    section_keys = {str(row["id"]): row["key"] for row in sections}
    eligible = {str(item["rubric_item_id"]): item for item in outbound["provider_items"]}
    draft_id = _uuid(secret["draft_id"])
    completed: dict[str, tuple[Any, Any]] = {}
    rejected: dict[str, str] = {}
    requested_counts: Counter[str] = Counter()

    for batch in result.batches:
        requested = [str(value) for value in batch.requested_rubric_item_ids]
        requested_counts.update(requested)
        requested_known = [item_id for item_id in requested if item_id in eligible]
        if len(set(requested)) != len(requested) or len(requested_known) != len(requested):
            for item_id in requested_known:
                rejected[item_id] = "duplicate_rubric_item_id"
            continue
        expected_refs = {
            ref
            for item_id in requested
            for ref in (
                eligible[item_id]["tender_ref"],
                eligible[item_id]["rule_ref"],
                *eligible[item_id]["draft_refs"],
                *eligible[item_id]["context_only_refs"],
            )
        }
        if (
            len(set(batch.sent_refs)) != len(batch.sent_refs)
            or set(batch.sent_refs) != expected_refs
        ):
            for item_id in requested:
                rejected[item_id] = "invalid_batch_refs"
            continue
        counts = Counter(str(answer.rubric_item_id) for answer in batch.output.items)
        if set(counts) - set(requested):
            for item_id in requested:
                rejected[item_id] = "unknown_rubric_item_id"
            continue
        for item_id in requested:
            if counts[item_id] == 0:
                rejected[item_id] = "missing_rubric_item_id"
            elif counts[item_id] > 1:
                rejected[item_id] = "duplicate_rubric_item_id"
            else:
                answer = next(
                    value for value in batch.output.items if str(value.rubric_item_id) == item_id
                )
                completed[item_id] = (batch, answer)

    for item_id, count in requested_counts.items():
        if count > 1 and item_id in eligible:
            rejected[item_id] = "duplicate_rubric_item_id"
            completed.pop(item_id, None)

    evaluated: list[dict] = []
    for item in rubric_items:
        item_id = str(item["id"])
        anchor = anchors[str(item["requirement_id"])]
        section_key = section_keys[str(item["section_id"])]
        if item_id in outbound["preflight"]:
            preflight = outbound["preflight"][item_id]
            evaluated.append(
                _unassessable(
                    item,
                    anchor,
                    section_key,
                    preflight["reason_code"],
                    preflight["reason"],
                )
            )
            continue
        if item_id in rejected:
            evaluated.append(_unassessable(item, anchor, section_key, rejected[item_id]))
            continue
        if item_id not in completed:
            code = "provider_incomplete" if result.failure is not None else "missing_rubric_item_id"
            evaluated.append(_unassessable(item, anchor, section_key, code))
            continue
        batch, answer = completed[item_id]
        prose = [
            answer.reason_code,
            answer.reason,
            *answer.deduction_reasons,
            *answer.strengthening_actions,
        ]
        unsafe = (
            None
            if re.fullmatch(r"[a-z0-9_-]{1,100}", answer.reason_code)
            else "invalid_score_output"
        )
        if unsafe is None:
            unsafe = _unsafe_prose(prose, outbound, library)
        if unsafe is None and any(
            UNSAFE_ACTION.search(action) for action in answer.strengthening_actions
        ):
            unsafe = "unsafe_strengthening_action"
        if unsafe is not None:
            evaluated.append(_unassessable(item, anchor, section_key, unsafe))
            continue
        if (
            len(answer.deduction_reasons) > 20
            or len(answer.strengthening_actions) > 20
            or len(answer.tender_citations) > 20
            or len(answer.draft_citations) > 20
        ):
            evaluated.append(_unassessable(item, anchor, section_key, "invalid_score_output"))
            continue
        if answer.outcome == "unassessable":
            normalized = _unassessable(item, anchor, section_key, answer.reason_code, answer.reason)
            if answer.strengthening_actions:
                normalized["strengthening_actions"] = list(answer.strengthening_actions)
            evaluated.append(normalized)
            continue
        bounds = ScoreRange.model_validate(item["score_range"])
        if answer.estimated_score is None or not (
            bounds.minimum <= answer.estimated_score <= bounds.maximum
        ):
            evaluated.append(_unassessable(item, anchor, section_key, "score_out_of_bounds"))
            continue
        if answer.estimated_score < bounds.maximum and not answer.deduction_reasons:
            evaluated.append(_unassessable(item, anchor, section_key, "missing_deduction_reason"))
            continue
        citations: list[dict] = []
        citation_error = None
        for citation in answer.tender_citations:
            verified, citation_error = _verify_citation(
                citation, item_id, batch, outbound, draft_id, "tender"
            )
            if citation_error:
                break
            assert verified is not None
            citations.append(verified)
        if citation_error is None:
            for citation in answer.draft_citations:
                verified, citation_error = _verify_citation(
                    citation, item_id, batch, outbound, draft_id, "draft"
                )
                if citation_error:
                    break
                assert verified is not None
                citations.append(verified)
        kinds = {citation["kind"] for citation in citations}
        if citation_error is not None:
            evaluated.append(_unassessable(item, anchor, section_key, citation_error))
            continue
        if not {"tender", "draft"} <= kinds:
            evaluated.append(_unassessable(item, anchor, section_key, "insufficient_citations"))
            continue
        draft_bindings = [outbound["refs"][citation.ref] for citation in answer.draft_citations]
        rule_text = str(item.get("rule_text") or "")
        proof_required = bool(PROOF_RULE.search(rule_text))

        if (
            draft_bindings
            and all(
                _thin_support(binding["original"], proof_required) for binding in draft_bindings
            )
            and not COMMITMENT_SUFFICIENT_RULE.search(rule_text)
        ):
            evaluated.append(_unassessable(item, anchor, section_key, "thin_promise_unassessable"))
            continue
        if answer.estimated_score == bounds.maximum and all(
            binding.get("deviation") == "negative" for binding in draft_bindings
        ):
            evaluated.append(
                _unassessable(item, anchor, section_key, "negative_deviation_conflict")
            )
            continue
        response_ids = list(
            dict.fromkeys(
                citation["response_item_id"]
                for citation in citations
                if citation["kind"] == "draft"
            )
        )
        evaluated.append(
            {
                "rubric_item_id": _uuid(item_id),
                "requirement_id": _uuid(item["requirement_id"]),
                "anchor_response_item_id": _uuid(anchor["response_item_id"]),
                "response_item_ids": response_ids,
                "section_key": section_key,
                "anchor_partition": anchor["partition"],
                "outcome": "assessed",
                "score_range": bounds.model_dump(mode="python"),
                "estimated_score": answer.estimated_score,
                "reason_code": answer.reason_code,
                "reason": answer.reason,
                "deduction_reasons": list(answer.deduction_reasons),
                "strengthening_actions": list(answer.strengthening_actions),
                "citations": citations,
                "advisory_only": True,
            }
        )
    return evaluated


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _range(value: Any) -> tuple[Decimal, Decimal] | None:
    if value is None:
        return None
    value = _plain(value)
    minimum, maximum = _decimal(value.get("minimum")), _decimal(value.get("maximum"))
    if minimum is None or maximum is None or not Decimal(0) <= minimum <= maximum:
        return None
    return minimum, maximum


def _calculate(
    algorithm: str,
    values: list[Decimal],
    children: list[dict],
    cap: Any,
    *,
    partial: bool = False,
) -> Decimal | None:
    if algorithm not in EXECUTABLE or len(values) != len(children):
        return None
    weights = [_decimal(child.get("weight")) for child in children]
    if algorithm == "weighted_sum":
        if any(weight is None or not Decimal(0) < weight <= Decimal(1) for weight in weights):
            return None
        if not partial and sum(cast(list[Decimal], weights), Decimal(0)) != Decimal(1):
            return None
        return sum(
            (value * cast(Decimal, weight) for value, weight in zip(values, weights, strict=True)),
            Decimal(0),
        )
    if any(child.get("weight") is not None for child in children):
        return None
    result = sum(values, Decimal(0))
    if algorithm == "capped_sum":
        limit = _decimal(cap)
        if limit is None or limit < 0:
            return None
        result = min(result, limit)
    elif cap is not None:
        return None
    return result


def _calculate_range(
    algorithm: str,
    ranges: list[tuple[Decimal, Decimal] | None],
    children: list[dict],
    cap: Any,
) -> tuple[Decimal, Decimal] | None:
    if any(bounds is None for bounds in ranges):
        return None
    minimums = [cast(tuple[Decimal, Decimal], bounds)[0] for bounds in ranges]
    maximums = [cast(tuple[Decimal, Decimal], bounds)[1] for bounds in ranges]
    minimum = _calculate(algorithm, minimums, children, cap)
    maximum = _calculate(algorithm, maximums, children, cap)
    return None if minimum is None or maximum is None else (minimum, maximum)


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(SCORE_QUANTUM, rounding=ROUND_HALF_UP)


def _published_range(value: tuple[Decimal, Decimal] | None) -> dict | None:
    if value is None:
        return None
    return {"minimum": _quantize(value[0]), "maximum": _quantize(value[1])}


def aggregate(
    rubric: dict,
    evaluated: list[dict],
    *,
    provider_complete: bool,
) -> tuple[list[dict], dict]:
    """Aggregate exact Decimal intermediates and publish only final 8-place values."""

    report = _plain(rubric)
    rubric_row = _plain(report.get("rubric", report.get("report", report)))
    sections = [_plain(row) for row in report["sections"]]
    items = [_plain(row) for row in report["items"]]
    by_result = {str(row["rubric_item_id"]): row for row in evaluated}
    assessed = sum(row.get("outcome") == "assessed" for row in evaluated)
    unassessable = len(items) - assessed
    section_views: list[dict] = []
    exact_sections: dict[str, dict] = {}
    limitations: set[str] = set()
    if not provider_complete:
        limitations.add("provider_incomplete")
    if unassessable:
        limitations.add("unassessable_items")

    with localcontext() as context:
        context.prec = 80
        for section in sections:
            children = [item for item in items if str(item["section_id"]) == str(section["id"])]
            results = [by_result.get(str(item["id"])) for item in children]
            assessed_results = [row for row in results if row and row.get("outcome") == "assessed"]
            assessed_children = [
                item
                for item, row in zip(children, results, strict=True)
                if row and row.get("outcome") == "assessed"
            ]
            assessed_values = [
                cast(Decimal, _decimal(row["estimated_score"])) for row in assessed_results
            ]
            algorithm = section["aggregation"]
            exact_subtotal = _calculate(
                algorithm,
                assessed_values,
                assessed_children,
                section.get("cap"),
                partial=True,
            )
            if exact_subtotal is None:
                # Unsupported aggregations still expose the literal sum of assessed
                # item scores as a subtotal, never as an estimate or parent input.
                exact_subtotal = sum(assessed_values, Decimal(0))
            possible = _calculate_range(
                algorithm,
                [_range(item.get("score_range")) for item in children],
                children,
                section.get("cap"),
            )
            if possible is None and algorithm in UNSUPPORTED:
                possible = _range(section.get("score_range"))
            all_assessed = len(assessed_results) == len(children)
            exact_estimate = (
                _calculate(
                    algorithm,
                    [
                        cast(Decimal, _decimal(cast(dict, row)["estimated_score"]))
                        for row in results
                    ],
                    children,
                    section.get("cap"),
                )
                if all_assessed and provider_complete
                else None
            )
            status = "estimated" if exact_estimate is not None else "unavailable"
            if algorithm in UNSUPPORTED:
                limitations.add("unsupported_section_aggregation")
            exact_sections[str(section["id"])] = {
                "estimate": exact_estimate,
                "subtotal": exact_subtotal,
                "subtotal_usable": algorithm in EXECUTABLE,
                "range": possible,
                "status": status,
            }
            section_views.append(
                {
                    "section_key": section["key"],
                    "title": section["title"],
                    "aggregation": algorithm,
                    "aggregation_assessable": algorithm in EXECUTABLE,
                    "aggregation_rule_text": section.get("aggregation_rule_text"),
                    "cap": _decimal(section.get("cap")),
                    "configured_range": _published_range(_range(section.get("score_range"))),
                    "assessed_items": len(assessed_results),
                    "unassessable_items": len(children) - len(assessed_results),
                    "assessed_subtotal": _quantize(exact_subtotal),
                    "possible_range": _published_range(possible),
                    "status": status,
                    "estimated_score": _quantize(exact_estimate)
                    if exact_estimate is not None
                    else None,
                }
            )

        included = [section for section in sections if section.get("included_in_overall_total")]
        subtotal_included = [
            section for section in included if exact_sections[str(section["id"])]["subtotal_usable"]
        ]
        subtotal_values = [
            cast(Decimal, exact_sections[str(section["id"])]["subtotal"])
            for section in subtotal_included
        ]
        overall_algorithm = rubric_row["overall_aggregation"]
        exact_subtotal = _calculate(
            overall_algorithm,
            subtotal_values,
            subtotal_included,
            rubric_row.get("overall_cap"),
            partial=True,
        )
        if exact_subtotal is None:
            exact_subtotal = sum(subtotal_values, Decimal(0))
        overall_range = _calculate_range(
            overall_algorithm,
            [exact_sections[str(section["id"])]["range"] for section in included],
            included,
            rubric_row.get("overall_cap"),
        )
        if overall_range is None and overall_algorithm in UNSUPPORTED:
            overall_range = _range(rubric_row.get("overall_score_range"))
        all_available = all(
            exact_sections[str(section["id"])]["status"] == "estimated" for section in included
        )
        exact_total = (
            _calculate(
                overall_algorithm,
                [
                    cast(Decimal, exact_sections[str(section["id"])]["estimate"])
                    for section in included
                ],
                included,
                rubric_row.get("overall_cap"),
            )
            if provider_complete and not unassessable and all_available
            else None
        )
        total_status = "estimated" if exact_total is not None else "unavailable"
        if overall_algorithm in UNSUPPORTED:
            limitations.add("unsupported_overall_aggregation")
        completion = (
            "complete"
            if provider_complete
            and len(by_result) == len(items)
            and not unassessable
            and total_status == "estimated"
            else "partial"
        )
        summary = {
            "completion": completion,
            "assessed_items": assessed,
            "unassessable_items": unassessable,
            "assessed_subtotal": _quantize(exact_subtotal),
            "overall_aggregation": overall_algorithm,
            "overall_aggregation_assessable": overall_algorithm in EXECUTABLE,
            "overall_rule_text": rubric_row.get("overall_rule_text"),
            "overall_cap": _decimal(rubric_row.get("overall_cap")),
            "possible_range": _published_range(overall_range),
            "total_status": total_status,
            "estimated_total": _quantize(exact_total) if exact_total is not None else None,
            "limitations": sorted(limitations),
        }
    return section_views, summary
