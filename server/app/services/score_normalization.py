"""Deterministic rubric completeness, without executing score assessments or formulas."""

import hashlib
import json
import unicodedata
from collections import Counter, defaultdict
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from uuid import UUID

from app.schemas.score_contracts import RubricCompletenessView

EXECUTABLE = {"sum", "weighted_sum", "capped_sum"}
RECORDED_ONLY = {"formula", "non_additive"}
SCORE_QUANTUM = Decimal("0.00000001")


def content_fingerprint(kind: str, payload: dict) -> str:
    """Identify the rule's content independently of editable names and ordering."""
    fields = (
        ("rule_text", "assessment_mode", "score_range", "weight", "ambiguity_reason", "source")
        if kind == "item"
        else (
            "aggregation",
            "aggregation_rule_text",
            "score_range",
            "weight",
            "cap",
            "included_in_overall_total",
            "ambiguity_reason",
            "source",
        )
    )
    if kind not in {"section", "item"}:
        raise ValueError("Unknown rubric content kind")

    def canonical(value, key=None):
        if value is None:
            return None
        if key in {"minimum", "maximum", "weight", "cap"}:
            # Validated finite decimals become the same fingerprint whether their
            # transport spelling is 1, 1.0 or 1.00000000.
            return format(Decimal(str(value)).normalize(), "f")
        if isinstance(value, dict):
            return {name: canonical(child, name) for name, child in value.items()}
        if isinstance(value, str):
            return " ".join(unicodedata.normalize("NFKC", value).split())
        if isinstance(value, UUID):
            return str(value)
        return value

    value = {key: canonical(payload.get(key), key) for key in fields}
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _bounds(value: object, errors: set[str]) -> tuple[Decimal, Decimal] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        errors.add("invalid_score_bounds")
        return None
    minimum, maximum = _decimal(value.get("minimum")), _decimal(value.get("maximum"))
    if minimum is None or maximum is None or not Decimal(0) <= minimum <= maximum:
        errors.add("invalid_score_bounds")
        return None
    return minimum, maximum


def _duplicates(rows: list[dict], fields: tuple[str, ...], prefix: str, errors: set[str]) -> None:
    for field in fields:
        if any(count > 1 for count in Counter(str(row.get(field)) for row in rows).values()):
            errors.add(f"duplicate_{prefix}_{field}")


def subject_errors(kind: str, node: dict) -> set[str]:
    """Local invariants required before a candidate section or item can be confirmed."""

    errors: set[str] = set()
    bounds = _bounds(node.get("score_range"), errors)
    weight = _decimal(node.get("weight"))
    if node.get("weight") is not None and (weight is None or not 0 < weight <= 1):
        errors.add("invalid_weight")
    if kind == "item":
        if node.get("assessment_mode") == "model_assessable" and bounds is None:
            errors.add("missing_score_bounds")
        if node.get("assessment_mode") != "model_assessable" and not node.get("ambiguity_reason"):
            errors.add("missing_ambiguity_reason")
    elif kind == "section":
        cap = _decimal(node.get("cap"))
        if node.get("aggregation") == "capped_sum" and node.get("cap") is None:
            errors.add("missing_cap")
        if node.get("aggregation") != "capped_sum" and node.get("cap") is not None:
            errors.add("unexpected_cap")
        if node.get("cap") is not None and (cap is None or cap < 0):
            errors.add("invalid_cap")
        if node.get("aggregation") in RECORDED_ONLY:
            if not node.get("aggregation_rule_text"):
                errors.add("missing_aggregation_rule_text")
            if not node.get("ambiguity_reason"):
                errors.add("missing_aggregation_limitation")
    else:
        raise ValueError("Unknown rubric content kind")
    return errors


def _aggregate(
    node: dict,
    children: list[dict],
    child_ranges: list[tuple[Decimal, Decimal] | None],
    errors: set[str],
    *,
    overall: bool = False,
) -> tuple[Decimal, Decimal] | None:
    prefix = "overall_" if overall else ""
    algorithm = node.get(prefix + "aggregation")
    declared = _bounds(node.get(prefix + "score_range"), errors)
    cap = _decimal(node.get(prefix + "cap"))
    weights = [_decimal(child.get("weight")) for child in children]
    for child, weight in zip(children, weights, strict=True):
        if child.get("weight") is not None and (weight is None or not 0 < weight <= 1):
            errors.add("invalid_weight")
    if algorithm not in EXECUTABLE | RECORDED_ONLY:
        errors.add("invalid_aggregation")
        return None
    if node.get(prefix + "cap") is not None and (cap is None or cap < 0):
        errors.add("invalid_cap")
    if algorithm == "capped_sum":
        if node.get(prefix + "cap") is None:
            errors.add("missing_cap")
    elif node.get(prefix + "cap") is not None:
        errors.add("unexpected_cap")
    if algorithm == "weighted_sum":
        if any(weight is None for weight in weights):
            errors.add("missing_weight")
        elif sum((weight for weight in weights if weight is not None), Decimal(0)) != Decimal(1):
            errors.add("weights_not_one")
    elif any(child.get("weight") is not None for child in children):
        errors.add("unexpected_weight")
    if algorithm in RECORDED_ONLY:
        if not str(
            node.get("overall_rule_text" if overall else "aggregation_rule_text") or ""
        ).strip():
            errors.add("missing_aggregation_rule_text")
        if not overall and not str(node.get("ambiguity_reason") or "").strip():
            errors.add("missing_aggregation_limitation")
        # The fixed wording records a non-executable rule. A declared bound can still
        # constrain its parent; arbitrary formula strings are never interpreted.
        return declared
    if any(value is None for value in child_ranges):
        # Explicit ambiguous/manual rules may have unknown bounds. Completeness is
        # about recording the rule, not pretending it can produce an estimate.
        return None
    minimum, maximum = Decimal(0), Decimal(0)
    for bounds, weight in zip(child_ranges, weights, strict=True):
        assert bounds is not None
        if algorithm == "weighted_sum":
            if weight is None or not 0 < weight <= 1:
                return None
            minimum += bounds[0] * weight
            maximum += bounds[1] * weight
        else:
            minimum += bounds[0]
            maximum += bounds[1]
    if algorithm == "capped_sum":
        if cap is None or cap < 0:
            return None
        minimum, maximum = min(minimum, cap), min(maximum, cap)
    final_bounds = tuple(
        value.quantize(SCORE_QUANTUM, rounding=ROUND_HALF_UP) for value in (minimum, maximum)
    )
    if declared is not None and declared != final_bounds:
        errors.add("aggregate_bounds_mismatch")
    # Parents use the exact intermediate range. Only the published declaration
    # at this node is compared to its final eight-place, half-up result.
    return minimum, maximum


def rule_errors(
    rubric: dict, sections: list[dict], items: list[dict], *, candidate_keys: bool = False
) -> tuple[set[str], set[str], set[str]]:
    """Use the same numeric checks at generation and on every saved rubric read.

    Generation links children by the fixed section key; storage uses section IDs.
    Neither path changes a declared value or infers a missing bound, weight or cap.
    """

    section_field, item_field = ("key", "section_key") if candidate_keys else ("id", "section_id")
    with localcontext() as context:
        context.prec = 80
        item_errors: set[str] = set()
        section_errors: set[str] = set()
        overall_errors: set[str] = set()
        for item in items:
            item_errors |= subject_errors("item", item)
        section_ranges = {}
        for section in sections:
            section_errors |= subject_errors("section", section)
            children = [row for row in items if str(row[item_field]) == str(section[section_field])]
            if not children:
                section_errors.add("empty_section")
            _duplicates(children, ("order",), "item", item_errors)
            section_ranges[str(section[section_field])] = _aggregate(
                section,
                children,
                [_bounds(row.get("score_range"), item_errors) for row in children],
                section_errors,
            )
            if not section.get("included_in_overall_total") and section.get("weight") is not None:
                section_errors.add("unexpected_weight")
        included = [row for row in sections if row.get("included_in_overall_total")]
        _aggregate(
            rubric,
            included,
            [section_ranges[str(row[section_field])] for row in included],
            overall_errors,
            overall=True,
        )
        return item_errors, section_errors, overall_errors


def completeness(report: dict) -> RubricCompletenessView:
    """Check an authorized fixed report graph; parent/source authorization is the service's job.

    Failures include missing/extra coverage, unresolved citations, duplicate identities,
    keys, ordering or content; broken mappings and canonical cycles; unreviewed subjects;
    invalid/missing bounds, caps, weights or rules; and inconsistent declared totals.
    """
    # numeric(18,8) products can have 36 significant digits. The default Decimal
    # context would round those products even before the final public quantization.
    with localcontext() as context:
        context.prec = 80
        return _completeness(report)


def _completeness(report: dict) -> RubricCompletenessView:
    rubric, sections, items, coverage = (
        report["rubric"],
        report["sections"],
        report["items"],
        report["coverage"],
    )
    errors: set[str] = set()
    if not sections or not items:
        errors.add("empty_rubric")
    _duplicates(sections, ("id", "key", "order"), "section", errors)
    if sections and all(row.get("fingerprint") for row in sections):
        _duplicates(sections, ("fingerprint",), "section", errors)
    _duplicates(items, ("id", "key"), "item", errors)
    _duplicates(coverage, ("requirement_id",), "coverage", errors)
    section_by_id = {str(row["id"]): row for row in sections}
    item_by_id = {str(row["id"]): row for row in items}
    coverage_by_req = {str(row["requirement_id"]): row for row in coverage}
    expected = set(map(str, report.get("expected_requirement_ids", coverage_by_req)))
    if expected - coverage_by_req.keys():
        errors.add("missing_coverage")
    if coverage_by_req.keys() - expected:
        errors.add("extra_coverage")
    if report.get("unresolved_requirement_ids") or any(
        row.get("citation_valid") is False for row in [*sections, *items]
    ):
        errors.add("unresolved_citation")
    fingerprints: dict[str, list[UUID]] = defaultdict(list)
    for item in items:
        fingerprints[item["fingerprint"]].append(UUID(str(item["id"])))
        if str(item["section_id"]) not in section_by_id:
            errors.add("dangling_section")
    fingerprint_groups = [ids for ids in fingerprints.values() if len(ids) > 1]
    if fingerprint_groups:
        errors.add("duplicate_item_fingerprint")
    mapped_ids: Counter[str] = Counter()
    pending: set[str] = expected - coverage_by_req.keys()
    covered: set[str] = set()
    for requirement_id, row in coverage_by_req.items():
        disposition = row.get("disposition")
        references = list(map(str, row.get("rubric_item_ids", [])))
        canonical = row.get("canonical_requirement_id")
        if disposition == "pending" or not row.get("decided_by") or not row.get("decided_at"):
            pending.add(requirement_id)
        elif requirement_id in expected:
            covered.add(requirement_id)
        if disposition == "mapped":
            if not references or canonical is not None:
                errors.add("invalid_coverage_mapping")
            for reference in references:
                mapped_ids[reference] += 1
                if (
                    reference not in item_by_id
                    or str(item_by_id[reference]["requirement_id"]) != requirement_id
                ):
                    errors.add("invalid_coverage_mapping")
        elif references:
            errors.add("invalid_coverage_mapping")
        if disposition in {"duplicate", "excluded"} and not str(row.get("reason") or "").strip():
            errors.add("missing_coverage_reason")
        if disposition == "duplicate":
            seen = {requirement_id}
            while canonical is not None:
                canonical = str(canonical)
                if canonical in seen:
                    errors.add("duplicate_coverage_cycle")
                    break
                seen.add(canonical)
                target = coverage_by_req.get(canonical)
                if target is None:
                    errors.add("invalid_canonical_requirement")
                    break
                if target.get("disposition") == "mapped":
                    break
                if target.get("disposition") != "duplicate":
                    errors.add("invalid_canonical_requirement")
                    break
                canonical = target.get("canonical_requirement_id")
            if canonical is None:
                errors.add("invalid_canonical_requirement")
        elif canonical is not None:
            errors.add("invalid_canonical_requirement")
        if disposition not in {"mapped", "duplicate", "excluded", "pending"}:
            errors.add("invalid_coverage_disposition")
    if any(count > 1 for count in mapped_ids.values()):
        errors.add("duplicate_item_inclusion")
    if item_by_id.keys() - mapped_ids.keys():
        errors.add("unmapped_item")
    item_errors, section_errors, overall_errors = rule_errors(rubric, sections, items)
    errors |= item_errors | section_errors | overall_errors
    unconfirmed_sections = [
        UUID(str(row["id"]))
        for row in sections
        if row.get("state") != "confirmed"
        or not row.get("review_domain")
        or not row.get("confirmed_by")
        or not row.get("confirmed_at")
    ]
    unconfirmed_items = [
        UUID(str(row["id"]))
        for row in items
        if row.get("state") != "confirmed"
        or not row.get("review_domain")
        or not row.get("confirmed_by")
        or not row.get("confirmed_at")
    ]
    section_confirmed = not section_errors and not unconfirmed_sections
    overall_confirmed = not overall_errors
    complete = not (
        pending
        or fingerprint_groups
        or unconfirmed_sections
        or unconfirmed_items
        or errors
        or not section_confirmed
        or not overall_confirmed
        or len(covered) != len(expected)
    )
    return RubricCompletenessView(
        scoring_requirement_count=len(expected),
        covered_requirement_count=len(covered),
        pending_requirement_ids=[UUID(value) for value in sorted(pending)],
        unresolved_duplicate_fingerprint_groups=fingerprint_groups,
        unconfirmed_section_ids=unconfirmed_sections,
        unconfirmed_item_ids=unconfirmed_items,
        normalization_errors=sorted(errors),
        section_aggregation_rules_confirmed=section_confirmed,
        overall_aggregation_rule_confirmed=overall_confirmed,
        complete=complete,
    )
