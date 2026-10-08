"""Bounded bid response judgments and deterministic, locally supported findings."""

import re
from collections import Counter
from uuid import uuid4

from app.providers.bid_reviewing import VERSION, BidReviewProviderRequest
from app.services import bid_review as bids
from app.services.bid_review_text import verified_quote
from app.services.redaction import PLACEHOLDER, SECRET_PLACEHOLDER

FINDING_LIMIT = 10000
PER_OBLIGATION_LIMIT = 20
ANCHOR_LIMIT = 20
DOCUMENT_KINDS = {
    "qualification": ("资格文件", "资格证明文件", "资格证明材料", "资格审查文件"),
    "commercial_technical": ("商务技术文件", "商务技术标", "技术标", "商务标"),
    "price": ("报价文件", "报价表", "开标一览表", "分项报价表", "价格文件"),
    "declaration": ("声明文件", "承诺声明", "声明函"),
}


def batches(obligation, pages, batch_chars):
    """Whole pages only; oversized pages are explicitly outside search coverage."""
    requests, refs, excluded = [], {}, []
    tender = obligation["citation"]
    # Only a previously accepted original quotation enters the dependent call.
    tender_text = {"ref": "t1", "text": tender["quote"], "role": "tender"}
    request_base = {
        "operation": "compliance",
        "obligation": {"ref": "o1", "tender_ref": "t1", "quote": tender["quote"]},
    }
    current = []
    for index, page in enumerate(pages, 1):
        ref = f"b{index}"
        value = {"ref": ref, "text": page["text"], "role": "bid"}
        single = BidReviewProviderRequest.model_validate(
            {**request_base, "texts": [tender_text, value]}
        )
        if len(single.model_dump_json()) > batch_chars:
            excluded.append(page["page_id"])
            continue
        candidate = BidReviewProviderRequest.model_validate(
            {**request_base, "texts": [tender_text, *current, value]}
        )
        if current and len(candidate.model_dump_json()) > batch_chars:
            requests.append(
                BidReviewProviderRequest.model_validate(
                    {**request_base, "texts": [tender_text, *current]}
                )
            )
            current = []
        refs[ref] = page
        current.append(value)
    if current:
        requests.append(
            BidReviewProviderRequest.model_validate(
                {**request_base, "texts": [tender_text, *current]}
            )
        )
    return requests, refs, excluded


def accept(request, output, obligation, refs):
    """A forged/unsent/ambiguous quotation never establishes a passing judgment."""
    sent = {text.ref for text in request.texts if text.role == "bid"}
    page_ids = [refs[ref]["page_id"] for ref in sorted(sent)]
    unknown = {
        "outcome": "unknown",
        "confidence": None,
        "bid_support": [],
        "searched_page_ids": [],
        "limitation_codes": ["invalid_compliance_output"],
    }
    if output.obligations or output.signing_requirements or len(output.observations) != 1:
        return unknown
    answer = output.observations[0]
    if answer.obligation_ref != "o1":
        return unknown
    # Tender references must bind this exact obligation rather than a neighboring
    # clause on the same page. Its original citation was verified during extraction.
    if (
        len(answer.tender_references) != 1
        or answer.tender_references[0].ref != "t1"
        or answer.tender_references[0].quote != obligation["citation"]["quote"]
    ):
        return {**unknown, "limitation_codes": ["compliance_tender_citation_invalid"]}
    anchors, rejected = [], []
    for reference in answer.bid_references:
        page = refs.get(reference.ref) if reference.ref in sent else None
        citation, reason = verified_quote(page, reference.quote) if page else (None, "ref_not_sent")
        if citation is None:
            rejected.append(reason)
        elif citation not in anchors:
            anchors.append(citation)
    if rejected:
        return {**unknown, "limitation_codes": sorted(set(rejected))}
    if answer.outcome in {"responded", "deviation"} and not anchors:
        return {**unknown, "limitation_codes": ["compliance_bid_citation_absent"]}
    if answer.outcome == "missing" and anchors:
        return {**unknown, "limitation_codes": ["compliance_absence_has_quote"]}
    if answer.outcome == "missing" and any(
        PLACEHOLDER.search(text.text) or SECRET_PLACEHOLDER.search(text.text)
        for text in request.texts
        if text.role == "bid"
    ):
        return {
            **unknown,
            "confidence": answer.confidence,
            "searched_page_ids": page_ids,
            "limitation_codes": ["redacted_bid_search_incomplete"],
        }
    return {
        "outcome": answer.outcome,
        "confidence": answer.confidence,
        "bid_support": anchors,
        "searched_page_ids": page_ids,
        "limitation_codes": ["provider_compliance_unknown"] if answer.outcome == "unknown" else [],
    }


def risk(obligation, outcome):
    if outcome == "responded":
        return "medium", "uncertain"
    # A model flag alone cannot promote an ordinary clause to a rejection trigger.
    if re.search(r"★|废标|无效", obligation["citation"]["quote"]):
        return "fatal", "rejection"
    if obligation["starred"] or obligation["category"] == "substantive":
        return "high", "rejection"
    return "medium", "lost_points" if obligation["category"] == "scoring" else "uncertain"


def basis(binding, confidence=None):
    value = {"kind": "rule", "rule_or_prompt_version": VERSION}
    if confidence is not None:
        identity = binding["model"]
        value.update(
            kind="model",
            confidence=confidence,
            provider_config_id=identity.get("provider_config_id"),
            platform_model_id=identity.get("platform_model_id"),
            provider_revision=identity.get("model_revision"),
            model=identity["model"],
        )
    return value


def finding(
    obligation,
    outcome,
    code,
    title,
    *,
    finding_basis,
    support=None,
    absence=None,
    limits=None,
    explanation=None,
):
    severity, impact = risk(obligation, outcome)
    return {
        "id": str(uuid4()),
        "obligation_id": obligation["id"],
        "outcome": outcome,
        "code": code,
        "title": title,
        "severity": severity,
        "impact": impact,
        "basis": finding_basis,
        "tender_support": [obligation["citation"]],
        "bid_support": support or [],
        "absence_search": absence,
        "limitation_codes": sorted(set(limits or [])),
        "explanation": explanation or title,
        "remediation": "核对已定位的响应原文。"
        if outcome == "responded"
        else "由责任专业复核招标义务和标书，补充或更正响应后重新检验。",
    }


def search_record(manifest, pages, searched, limits, *, method="llm"):
    searched_set = set(searched)
    inventory = [p for p in pages if p["page_id"] in searched_set]
    complete = len(inventory) == manifest["bid_page_count"] and not limits
    return {
        "kind": "locations",
        "searched_pages": [{**p, "role": "bid"} for p in inventory],
        "method": method,
        "coverage": "all_bid_pages" if complete else "partial",
        "limitation_codes": sorted(set(limits or ([] if complete else ["bid_search_partial"]))),
    }


def summarize(obligation, results, manifest, bid_pages, binding, additional_limits):
    limits = list(additional_limits)
    support, searched = [], []
    for result in results:
        limits += result["limitation_codes"]
        searched += result["searched_page_ids"]
        for anchor in result["bid_support"]:
            if anchor not in support:
                support.append(anchor)
    if len(support) > ANCHOR_LIMIT:
        # Reject the conclusion, retaining the full search limits rather than
        # silently truncating evidence to fit the approved schema.
        support = []
        limits.append("bid_review_anchor_limit")
    located = [r for r in results if r["outcome"] in {"responded", "deviation"}]
    if located and support and not limits:
        outcome = "deviation" if any(r["outcome"] == "deviation" for r in located) else "responded"
    elif not limits and results and all(r["outcome"] == "missing" for r in results):
        outcome = "missing"
    else:
        outcome = "unknown"
        if not results:
            limits.append("bid_pages_not_assessed")
    titles = {
        "responded": "已定位义务响应",
        "deviation": "义务响应存在偏离",
        "missing": "未找到义务响应",
        "unknown": "义务响应待确认",
    }
    absence = (
        search_record(manifest, bid_pages, searched, limits)
        if outcome in {"missing", "unknown"} or not support
        else None
    )
    confidences = [r["confidence"] for r in results if r["confidence"] is not None]
    confidence = min(confidences) if confidences else None
    return finding(
        obligation,
        outcome,
        f"compliance_{outcome}",
        titles[outcome],
        finding_basis=basis(binding, confidence),
        support=support,
        absence=absence,
        limits=limits,
    )


def inventory_search(manifest, description):
    return {
        "kind": "submission_inventory",
        "submission_id": manifest["submission_id"],
        "submission_manifest_sha256": manifest["submission_manifest_sha256"],
        "inspected_bid_document_ids": manifest["bid_document_ids"],
        "required_document_description": description,
        "method": "manifest_rule",
        "coverage": "complete_inventory",
        "limitation_codes": [],
    }


def invalid_signatures(validations):
    """Return paired prepared records with a locally established signature defect."""
    bad_documents, bad_validations = [], []
    for validation in validations:
        signatures = validation["details"].get("signatures", [])
        if signatures and (
            any(
                s.get("crypto_status") in {"invalid", "not_for_signing"}
                or s.get("content_digest_status") == "invalid"
                or s.get("signature_value_status") == "invalid"
                or s.get("certificate_validity_status") == "not_for_signing"
                or s.get("trust_status") == "not_for_signing"
                or s.get("modified_after_signing") is True
                or s.get("post_signing_changes") == "modified"
                for s in signatures
            )
            or validation["details"].get("final_revision", {}).get("modified_after_last_signature")
            is True
        ):
            bad_documents.append(validation["document_id"])
            bad_validations.append(validation["validation_id"])
    return bad_documents, bad_validations


def rules(obligations, findings, manifest, bid_pages, documents, validations):
    """Rules use exact tender clauses plus fixed inventory/local crypto facts."""
    output = []
    kinds = {d["kind"] for d in documents if d["role"] == "bid"}
    by_obligation = {f["obligation_id"]: f for f in findings}
    bad_documents, bad_validations = invalid_signatures(validations)
    for obligation in obligations:
        clause = obligation["citation"]["quote"]
        if re.search(r"须|必须|应当|应提供|需提供|提交|递交|包含|包括", clause) and not re.search(
            r"无需|无须|不需|不须|不必|可不|免于|免予|不得|禁止|不要求", clause
        ):
            for kind, descriptions in DOCUMENT_KINDS.items():
                description = next((s for s in descriptions if s in clause), None)
                if description is not None and kind not in kinds:
                    output.append(
                        finding(
                            obligation,
                            "missing",
                            "required_document_missing",
                            "提交清单缺少要求的文件类型",
                            finding_basis=basis(None),
                            absence=inventory_search(manifest, description),
                        )
                    )
        if bad_documents and re.search(r"签名|签章|数字签|电子签|电子印章", clause):
            output.append(
                finding(
                    obligation,
                    "deviation",
                    "signature_validation_invalid",
                    "已签署文件的本地签名校验失败或签后修改",
                    finding_basis=basis(None),
                    explanation="已签署标书的本地签名校验记录为无效、签后修改或证书不能用于签署；需复核原始签署文件。",
                )
            )
            output[-1]["rule_evidence"] = {
                "document_ids": bad_documents,
                "validation_ids": bad_validations,
                "kind": "pdf_signature_validation",
            }
        existing = by_obligation[obligation["id"]]
        if obligation["starred"] and not existing["bid_support"]:
            output.append(
                finding(
                    obligation,
                    "unknown" if existing["outcome"] == "unknown" else "missing",
                    "starred_response_not_located",
                    "强制条款尚未定位任何标书响应",
                    finding_basis=basis(None),
                    absence=existing["absence_search"],
                    limits=existing["limitation_codes"],
                )
            )
    return output


def enforce_limits(findings):
    counts = Counter(f["obligation_id"] for f in findings)
    if len(findings) > FINDING_LIMIT or any(n > PER_OBLIGATION_LIMIT for n in counts.values()):
        bids.fail("bid_review_finding_limit", "Findings exceed the approved bound", 409, 4)
    if any(
        len(f["tender_support"]) > ANCHOR_LIMIT or len(f["bid_support"]) > ANCHOR_LIMIT
        for f in findings
    ):
        bids.fail("bid_review_anchor_limit", "Finding anchors exceed the approved bound", 409, 4)
