"""Deterministic observations over an already verified confirmed-draft snapshot."""

from app.services import redaction
from app.services.extraction import locate_quote
from app.services.response_cards import fail

REASONS = {
    "mandatory_response_missing": "A starred or substantive requirement has no confirmed response or comply-only decision.",
    "negative_deviation": "The confirmed response records a negative deviation; its consequence requires human review.",
    "unconfirmed_evidence": "The requirement remains a gap because review or reconfirmation is incomplete.",
    "certificate_expired": "A linked certificate's declared validity ends before the assessment date.",
    "certificate_not_yet_valid": "A linked certificate's declared validity starts after the assessment date.",
    "certificate_date_unknown": "A linked certificate has insufficient declared dates for assessment.",
}
UNCONFIRMED = {
    "unconfirmed",
    "needs_reconfirmation",
    "rejected",
    "needs_material",
    "stale_material",
}


def citation_quote(text: str) -> tuple[str | None, str | None]:
    if redaction.PLACEHOLDER.search(text) or redaction.SECRET_PLACEHOLDER.search(text):
        return None, "redacted_input_unassessable"
    quote, reason = locate_quote(text, text)
    if quote is not None and len(quote) > 20000:
        return None, "citation_quote_limit"
    return quote, reason


def evaluate(secret: dict, draft_id: str) -> list[dict]:
    """Every saved requirement has one coverage row, regardless of finding count."""
    return [evaluate_item(item, secret["certificates"], draft_id) for item in secret["items"]]


def evaluate_item(item: dict, certificates: list[dict], draft_id: str) -> dict:
    observations, findings = [], []
    mandatory = item["starred"] or item["category"] == "substantive"
    tender_quote, tender_rejection = citation_quote(item["source"]["quote"])
    tender = {"kind": "tender", "source": item["source"]}

    def observe(code, outcome, reason, *, certificate=None, extra_citations=()):
        observation = {"code": code, "outcome": outcome, "reason_code": reason}
        if certificate is not None:
            observation |= {
                key: certificate[key] for key in ("task_certificate_id", "certificate_revision_id")
            }
        if outcome in {"risk", "unknown"}:
            if tender_quote is None:
                observation |= {"outcome": "unknown", "reason_code": tender_rejection}
            else:
                citations = [tender, *extra_citations]
                if len(citations) > 20:
                    fail("check_citation_limit", "A finding supports at most 20 citations")
                severity = (
                    ("disqualification_risk" if mandatory else "deduction_risk")
                    if code in {"mandatory_response_missing", "negative_deviation"}
                    else "info"
                )
                findings.append(
                    {
                        "code": code,
                        "severity": severity,
                        "reason": REASONS[code],
                        "citations": citations,
                    }
                )
        observations.append(observation)

    observe(
        "mandatory_response_missing",
        "risk"
        if mandatory and item["partition"] == "gap"
        else "clear"
        if mandatory
        else "not_applicable",
        "mandatory_gap"
        if mandatory and item["partition"] == "gap"
        else "response_partition_present"
        if mandatory
        else "not_mandatory",
    )
    if item["partition"] == "response" and item["deviation"] == "negative":
        quote, rejection = citation_quote(item["deviation_note"])
        if quote is None:
            observations.append(
                {"code": "negative_deviation", "outcome": "unknown", "reason_code": rejection}
            )
        else:
            observe(
                "negative_deviation",
                "risk",
                "confirmed_negative_deviation",
                extra_citations=[
                    {
                        "kind": "draft",
                        "draft_id": draft_id,
                        "response_item_id": item["response_item_id"],
                        "card_revision_id": item["card_revision_id"],
                        "field": "deviation_note",
                        "quote": quote,
                    }
                ],
            )
    else:
        observe(
            "negative_deviation",
            "clear" if item["partition"] == "response" else "not_applicable",
            "no_negative_deviation" if item["partition"] == "response" else "no_confirmed_response",
        )
    unconfirmed = item["partition"] == "gap" and bool(UNCONFIRMED.intersection(item["gap_reasons"]))
    observe(
        "unconfirmed_evidence",
        "risk" if unconfirmed else "not_applicable",
        "review_incomplete" if unconfirmed else "no_unconfirmed_gap",
    )
    for certificate in certificates:
        if item["requirement_id"] not in certificate["requirement_ids"]:
            continue
        state = certificate["date_status"]
        code = {
            "expired": "certificate_expired",
            "not_yet_valid": "certificate_not_yet_valid",
            "unknown": "certificate_date_unknown",
            "valid": "certificate_date_unknown",
        }[state]
        supporting = []
        for evidence in item["evidence"]:
            if (
                evidence["task_certificate_id"] == certificate["task_certificate_id"]
                and evidence["quote"]
            ):
                quote, _ = citation_quote(evidence["quote"])
                if quote is not None:
                    supporting.append(
                        {"kind": "evidence", "evidence_id": evidence["id"], "quote": quote}
                    )
        # Dates are metadata observations. The tender plus fixed certificate
        # binding is sufficient when the only linked evidence is an image;
        # never invent text from a human visual observation.
        observe(
            code,
            "clear" if state == "valid" else "unknown" if state == "unknown" else "risk",
            "declared_dates_valid" if state == "valid" else "declared_dates_" + state,
            certificate=certificate,
            extra_citations=supporting,
        )
    if len(findings) > 20:
        fail("check_finding_limit", "A requirement supports at most 20 findings")
    return {
        "item": item,
        "rules": observations,
        "findings": findings,
        "unassessed": any(row["outcome"] == "unknown" for row in observations),
    }
