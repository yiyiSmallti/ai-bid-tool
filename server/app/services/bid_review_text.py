"""Local quote verification and unresolved signing-location expansion."""

import re
from collections import Counter
from uuid import uuid4

from app.providers.bid_reviewing import BidReviewProviderRequest
from app.services.bid_review import fail
from app.services.extraction import locate_quote, locate_span
from app.services.redaction import PLACEHOLDER, SECRET_PLACEHOLDER


def verified_quote(page, quote):
    if PLACEHOLDER.search(quote) or SECRET_PLACEHOLDER.search(quote):
        return None, "redacted_input_unassessable"
    original = page["original"]
    sent = page["text"]
    for value in (sent, original):
        exact, reason = locate_quote(value, quote)
        if exact is None:
            return None, reason or "quote_not_at_position"
        # Do not reverse a placeholder or repair an approximate quote.
        if exact != quote or value.count(quote) != 1:
            return None, "ambiguous_or_nonverbatim_quote"
    citation = {
        "document_id": page["document_id"],
        "page_id": page["page_id"],
        "page": page["page"],
        "quote": quote,
        "location": None,
        "page_label": "original_pdf",
    }
    if page.get("word_blocks") is not None:
        matches = [b for b in page["word_blocks"] if b["text"].count(quote) == 1]
        if len(matches) != 1:
            return None, "docx_quote_unmapped"
        citation.update(location=matches[0]["location"], page_label="rendered_docx")
    return citation, None


def build_requests(pages, candidates, batch_chars):
    """One whole tender page per call. No bid content or filename is needed by 3a."""
    requests, refs, candidate_refs, excluded = [], {}, {}, []
    for index, page in enumerate(pages, 1):
        ref = f"t{index}"
        refs[ref] = page
        local_candidates = []
        for candidate in candidates:
            if candidate["page_id"] != page["page_id"]:
                continue
            quote = candidate["detail"]["quote"]
            citation, _ = verified_quote(page, quote)
            if citation is None:
                continue
            local = f"c{candidate['ordinal']}"
            candidate_refs[local] = candidate
            local_candidates.append({"candidate_ref": local, "text_ref": ref, "quote": quote})
        request = BidReviewProviderRequest.model_validate(
            {
                "texts": [{"ref": ref, "text": page["text"]}],
                "candidates": local_candidates,
            }
        )
        if len(request.model_dump_json()) > batch_chars:
            excluded.append(page["page_id"])
        else:
            requests.append(request)
    return requests, refs, candidate_refs, excluded


def initial_signing(candidates):
    return {
        c["id"]: {
            "id": str(uuid4()),
            "candidate_id": c["id"],
            "applicability": "unknown",
            "mark_types": c["detail"]["mark_types"],
            "owner_roles": c["detail"]["owner_roles"],
            "date_required": c["detail"]["date_required"] is True,
            "location_rule": "unknown",
            "citation": None,
            "required_locations": [],
            "reason_code": "candidate_not_assessed",
            "source_page_id": c["page_id"],
        }
        for c in candidates
    }


def locations(answer, bid_pages):
    if answer.applicability == "not_applicable":
        return []
    if answer.applicability == "unknown" or answer.location_rule in {"specified", "unknown"}:
        return [{"status": "unresolved", "reason_code": "required_location_unmapped"}]
    return [
        {
            "page_id": p["page_id"],
            "document_id": p["document_id"],
            "page": p["page"],
            "status": "unresolved",
            "reason_code": "presence_not_checked",
            "group_id": p["document_id"] if answer.location_rule == "seam_group" else None,
        }
        for p in bid_pages
    ]


def accept(request, output, refs, candidate_refs, obligations, signing, bid_pages):
    sent_refs = {text.ref for text in request.texts}
    expected_candidates = {c.candidate_ref for c in request.candidates}
    rejected = []
    for answer in output.obligations:
        page = refs.get(answer.ref) if answer.ref in sent_refs else None
        citation, reason = verified_quote(page, answer.quote) if page else (None, "ref_not_sent")
        if citation is None:
            rejected.append(reason)
            continue
        if any(item["citation"] == citation for item in obligations):
            rejected.append("duplicate_obligation")
            continue
        obligations.append(
            {
                "id": str(uuid4()),
                "text": citation["quote"],
                "category": answer.category,
                "starred": answer.starred or "★" in answer.quote or "▲" in answer.quote,
                "rejection_trigger": answer.rejection_trigger
                or "废标" in answer.quote
                or "无效" in answer.quote,
                "citation": citation,
            }
        )
    # Mandatory markers cannot disappear merely because a valid model response
    # omitted the clause. Retain a coverage gap without manufacturing a quote.
    for text in request.texts:
        page = refs[text.ref]
        page_quotes = [
            item["citation"]["quote"]
            for item in obligations
            if item["citation"]["page_id"] == page["page_id"]
        ]
        for clause in re.findall(r"[^\n。；;]+[。；;]?", text.text):
            if re.search(r"[★▲]|废标|无效", clause) and not any(
                quote in clause or clause.strip() in quote for quote in page_quotes
            ):
                rejected.append("mandatory_clause_not_extracted")
    counts = Counter(
        item.candidate_ref for item in output.signing_requirements if item.candidate_ref
    )
    for local in expected_candidates:
        if counts[local] != 1:
            rejected.append(
                "missing_signing_candidate" if counts[local] == 0 else "duplicate_signing_candidate"
            )
    for answer in output.signing_requirements:
        if answer.candidate_ref is not None and (
            answer.candidate_ref not in expected_candidates or counts[answer.candidate_ref] != 1
        ):
            rejected.append("invalid_signing_candidate")
            continue
        page = refs.get(answer.ref) if answer.ref in sent_refs else None
        citation, reason = verified_quote(page, answer.quote) if page else (None, "ref_not_sent")
        if citation is None:
            rejected.append(reason)
            continue
        assert page is not None
        candidate = candidate_refs.get(answer.candidate_ref) if answer.candidate_ref else None
        if candidate is not None:
            span, _ = locate_span(page["original"], answer.quote)
            detail = candidate["detail"]
            if (
                candidate["page_id"] != page["page_id"]
                or span is None
                or not (span[0] < detail["end_offset"] and span[1] > detail["start_offset"])
            ):
                rejected.append("cross_candidate_citation")
                continue
        key = candidate["id"] if candidate else str(uuid4())
        signing[key] = {
            "id": signing[key]["id"] if key in signing else str(uuid4()),
            "candidate_id": candidate["id"] if candidate else None,
            "applicability": answer.applicability,
            "mark_types": answer.mark_types,
            "owner_roles": answer.owner_roles,
            "date_required": answer.date_required,
            "location_rule": answer.location_rule,
            "citation": citation,
            "required_locations": locations(answer, bid_pages),
            "reason_code": "applicability_unknown" if answer.applicability == "unknown" else None,
            "source_page_id": page["page_id"],
        }
    if (
        len(obligations) > 2000
        or len(signing) > 10000
        or sum(len(v["required_locations"]) for v in signing.values()) > 10000
    ):
        fail("bid_review_output_limit", "Review output exceeds the approved bound", 400, 4)
    return sorted(set(rejected))
