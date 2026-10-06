"""DB-free rubric wire-to-review regressions with synthetic scoring clauses.

Failures: semantic errors abort stage 1, fixed section context repairs model values,
stage 2 drops locally cited candidates, malformed wire values evade structural
validation, or invalid candidates cannot be read while confirmation remains gated.
"""

import json
from copy import deepcopy
from uuid import UUID

import pytest
from app.core.errors import ServiceError
from app.schemas.console_assessments import RubricSummaryData
from app.schemas.score_contracts import (
    RubricAnsweredBatch,
    RubricCompletenessView,
    RubricItemRevisionInput,
    RubricItemsWireOutput,
    RubricItemView,
    RubricReviseRequest,
    RubricSectionRevisionInput,
    RubricSectionView,
    RubricSetView,
    RubricStructureOutput,
)
from app.services import assessment_rubrics, score_generation
from pydantic import ValidationError
from test_score_generation import PROVIDER_REQUIREMENT, REAL_REQUIREMENT, candidate, fixed_secret


def accept_wire(wire):
    secret = fixed_secret()
    outbound = score_generation.build_outbound(secret, [], [])
    structure = score_generation.accept_structure(
        secret,
        outbound,
        RubricStructureOutput.model_validate(
            {key: value for key, value in wire.items() if key != "items"}
        ),
    )
    request = score_generation.items_request(outbound, structure)
    fixed_fields = {"key", "aggregation", "aggregation_rule_text", "score_range", "weight", "cap"}
    assert [
        {key: row[key] for key in fixed_fields}
        for row in request.model_dump(mode="json")["sections"]
    ] == [{key: row[key] for key in fixed_fields} for row in wire["sections"]]
    batch = RubricAnsweredBatch(
        requested_requirement_ids=[PROVIDER_REQUIREMENT],
        sent_refs=["r1.tender"],
        structure_hash=request.structure_hash,
        output=RubricItemsWireOutput.model_validate({"items": wire["items"]}),
    )
    accepted = score_generation.accept_batches(secret, outbound, structure, [batch])
    assert accepted["unresolved_requirement_ids"] == []
    assert len(accepted["items"]) == len(wire["items"])
    assert accepted["sections"] == structure["sections"]
    for actual, proposed in zip(accepted["items"], wire["items"], strict=True):
        assert actual["score_range"] == proposed["score_range"]
        assert actual["weight"] == proposed["weight"]
        assert actual["rule_text"] == proposed["rule_text"]
        assert actual["requirement_id"] == str(REAL_REQUIREMENT)
    for key in ("overall_aggregation", "overall_rule_text", "overall_score_range", "overall_cap"):
        assert accepted[key] == wire[key]
    return accepted


def structural_failure_wire():
    """Mirror the reported cap/weight shape without reading real tender content."""
    wire = candidate()
    price = deepcopy(wire["sections"][0])
    price.update(
        key="price",
        title="Synthetic price formula",
        order=1,
        aggregation="formula",
        aggregation_rule_text="Synthetic price ratio rule; do not execute.",
        ambiguity_reason="Synthetic external prices are unavailable.",
        score_range={"minimum": "0", "maximum": "35"},
        weight="0.35",
        cap="35",
    )
    technical = wire["sections"][0]
    technical.update(order=2, aggregation="capped_sum", weight="0.65", cap=None)
    commercial = deepcopy(technical)
    commercial.update(
        key="commercial",
        title="Synthetic commercial score",
        order=3,
        aggregation="weighted_sum",
        weight=None,
    )
    technical_item = wire["items"][0]
    price_item = deepcopy(technical_item)
    price_item.update(
        section_key="price",
        key="price-rule",
        title="Synthetic price item",
        rule_text="Synthetic external price comparison only.",
        assessment_mode="price_comparison",
        score_range={"minimum": "0", "maximum": "35"},
        ambiguity_reason="Synthetic external prices are unavailable.",
    )
    commercial_item = deepcopy(technical_item)
    commercial_item.update(
        section_key="commercial",
        key="commercial-rule",
        title="Synthetic commercial item",
        rule_text="Synthetic commercial evidence earns points.",
        weight="0.25",
    )
    wire["sections"] = [price, technical, commercial]
    wire["items"] = [price_item, technical_item, commercial_item]
    wire["overall_score_range"] = {"minimum": "0", "maximum": "100"}
    return wire


def test_semantically_invalid_structure_survives_both_stages_for_human_review(tmp_path):
    wire = structural_failure_wire()
    accepted = accept_wire(wire)
    assert {
        "unexpected_cap",
        "missing_cap",
        "unexpected_weight",
        "weights_not_one",
        "aggregate_bounds_mismatch",
    } <= set(accepted["normalization_errors"])
    artifact = tmp_path / "synthetic-rubric-normalization.json"
    artifact.write_text(json.dumps({"wire": wire, "accepted": accepted}, indent=2))
    saved = json.loads(artifact.read_text())
    assert saved["accepted"]["sections"][0]["cap"] == "35"
    assert saved["accepted"]["sections"][1]["cap"] is None


@pytest.mark.parametrize(
    "subject,changes,expected",
    [
        ("section", {"aggregation": "sum", "cap": "35"}, "unexpected_cap"),
        ("section", {"aggregation": "capped_sum", "cap": None}, "missing_cap"),
        ("section", {"aggregation": "capped_sum", "cap": "-1"}, "invalid_cap"),
        ("overall", {"overall_aggregation": "sum", "overall_cap": "35"}, "unexpected_cap"),
        ("overall", {"overall_aggregation": "capped_sum", "overall_cap": None}, "missing_cap"),
        ("overall", {"overall_aggregation": "capped_sum", "overall_cap": "-1"}, "invalid_cap"),
        ("section", {"weight": "-0.25"}, "invalid_weight"),
        ("section", {"weight": "1.25"}, "invalid_weight"),
        ("item", {"weight": "0"}, "invalid_weight"),
        ("item", {"weight": "1.25"}, "invalid_weight"),
        ("section", {"score_range": {"minimum": "-1", "maximum": "5"}}, "invalid_score_bounds"),
        ("item", {"score_range": {"minimum": "6", "maximum": "5"}}, "invalid_score_bounds"),
        (
            "overall",
            {"overall_score_range": {"minimum": "6", "maximum": "5"}},
            "invalid_score_bounds",
        ),
        ("item", {"score_range": None}, "missing_score_bounds"),
    ],
)
def test_signed_and_incoherent_values_are_preserved_and_reported(subject, changes, expected):
    wire = candidate()
    target = (
        wire if subject == "overall" else wire["sections" if subject == "section" else "items"][0]
    )
    target.update(changes)
    accepted = accept_wire(wire)
    assert expected in accepted["normalization_errors"]


@pytest.mark.parametrize(
    "subject,field,value",
    [
        ("section", "cap", []),
        ("section", "weight", {}),
        ("section", "cap", True),
        ("section", "cap", "NaN"),
        ("section", "weight", "Infinity"),
        ("section", "aggregation", "unknown"),
        ("section", "score_range", {"minimum": "0"}),
        ("section", "score_range", {"minimum": [], "maximum": "5"}),
        ("section", "included_in_overall_total", "yes"),
        ("section", "order", "first"),
        ("section", "order", True),
        ("item", "assessment_mode", "unknown"),
        ("item", "weight", []),
        ("item", "order", "first"),
        ("item", "order", True),
        ("item", "score_range", {"minimum": "0", "maximum": "Infinity"}),
        ("overall", "overall_cap", {}),
        ("overall", "overall_aggregation", "unknown"),
    ],
)
def test_malformed_wire_remains_a_structural_validation_error(subject, field, value):
    wire = candidate()
    target = (
        wire if subject == "overall" else wire["sections" if subject == "section" else "items"][0]
    )
    target[field] = value
    with pytest.raises(ValidationError):
        accept_wire(wire)


@pytest.mark.parametrize("subject", ["section", "item", "overall"])
def test_required_wire_keys_cannot_disappear(subject):
    wire = candidate()
    if subject == "overall":
        del wire["overall_aggregation"]
    else:
        del wire["sections" if subject == "section" else "items"][0]["key"]
    with pytest.raises(ValidationError):
        accept_wire(wire)


@pytest.mark.parametrize(
    "subject,field",
    [
        ("section", "aggregation_rule_text"),
        ("section", "score_range"),
        ("section", "weight"),
        ("section", "cap"),
        ("section", "ambiguity_reason"),
        ("section", "review_domain"),
        ("item", "score_range"),
        ("item", "weight"),
        ("item", "ambiguity_reason"),
        ("overall", "overall_rule_text"),
        ("overall", "overall_score_range"),
        ("overall", "overall_cap"),
    ],
)
def test_nullable_wire_keys_must_be_explicit_even_when_the_value_is_null(subject, field):
    wire = candidate()
    wire["sections"][0]["review_domain"] = None
    target = (
        wire if subject == "overall" else wire["sections" if subject == "section" else "items"][0]
    )
    del target[field]
    with pytest.raises(ValidationError):
        accept_wire(wire)


@pytest.mark.parametrize(
    "subject,changes,expected",
    [
        (
            "section",
            {
                "aggregation": "formula",
                "aggregation_rule_text": None,
                "ambiguity_reason": "Synthetic external inputs are unavailable.",
            },
            "missing_aggregation_rule_text",
        ),
        (
            "section",
            {
                "aggregation": "formula",
                "aggregation_rule_text": "Synthetic formula must not execute.",
                "ambiguity_reason": None,
            },
            "missing_aggregation_limitation",
        ),
        (
            "item",
            {"assessment_mode": "price_comparison", "ambiguity_reason": None},
            "missing_ambiguity_reason",
        ),
        (
            "overall",
            {"overall_aggregation": "formula", "overall_rule_text": None},
            "missing_aggregation_rule_text",
        ),
    ],
)
def test_explicit_null_rule_wording_and_reasons_are_retained_as_normalization_errors(
    subject, changes, expected
):
    wire = candidate()
    target = (
        wire if subject == "overall" else wire["sections" if subject == "section" else "items"][0]
    )
    target.update(changes)
    accepted = accept_wire(wire)
    assert expected in accepted["normalization_errors"]
    actual = (
        accepted
        if subject == "overall"
        else accepted["sections" if subject == "section" else "items"][0]
    )
    assert {field: actual[field] for field in changes} == changes


def test_candidate_rule_text_is_preserved_without_inferred_corrections():
    wire = candidate()
    wire["sections"][0].update(
        aggregation="formula",
        aggregation_rule_text="  Synthetic formula wording.\n",
        ambiguity_reason="Synthetic external inputs are unavailable.",
        cap="35",
    )
    wire["items"][0]["rule_text"] = "  Synthetic criterion keeps its wording.\n"
    wire.update(overall_aggregation="formula", overall_rule_text="  Synthetic overall wording.\n")
    accepted = accept_wire(wire)
    assert "unexpected_cap" in accepted["normalization_errors"]

    # Fix the cap explicitly while leaving all original rule wording untouched.
    section = {
        key: value
        for key, value in accepted["sections"][0].items()
        if key in RubricSectionRevisionInput.model_fields
    }
    item = {
        key: value
        for key, value in accepted["items"][0].items()
        if key in RubricItemRevisionInput.model_fields
    }
    revision = RubricReviseRequest.model_validate(
        {
            "expected_revision": 1,
            "expected_input_hash": "a" * 64,
            "reason": "Synthetic human cap correction",
            "sections": [{**section, "cap": None}],
            "items": [item],
            "coverage": [{"requirement_id": REAL_REQUIREMENT, "disposition": "pending"}],
            **{
                key: accepted[key]
                for key in (
                    "overall_aggregation",
                    "overall_rule_text",
                    "overall_cap",
                    "overall_score_range",
                )
            },
        }
    )
    assert revision.sections[0].aggregation_rule_text == section["aggregation_rule_text"]
    assert revision.items[0].rule_text == item["rule_text"]
    assert revision.overall_rule_text == accepted["overall_rule_text"]


def public_views(accepted):
    common = {
        "org_id": UUID(int=401),
        "task_id": UUID(int=402),
        "rubric_id": UUID(int=403),
        "state": "candidate",
        "revision": 1,
    }
    section = {
        key: value
        for key, value in accepted["sections"][0].items()
        if key in RubricSectionView.model_fields
    }
    section.update(common, id=UUID(int=404), aggregation_assessable=True)
    item = {
        key: value
        for key, value in accepted["items"][0].items()
        if key in RubricItemView.model_fields
    }
    item.update(common, id=UUID(int=405), section_id=section["id"])
    completeness = RubricCompletenessView(
        scoring_requirement_count=1,
        covered_requirement_count=0,
        pending_requirement_ids=[REAL_REQUIREMENT],
        unresolved_duplicate_fingerprint_groups=[],
        unconfirmed_section_ids=[section["id"]],
        unconfirmed_item_ids=[item["id"]],
        normalization_errors=accepted["normalization_errors"],
        section_aggregation_rules_confirmed=False,
        overall_aggregation_rule_confirmed=False,
        complete=False,
    )
    rubric = {
        **{key: value for key, value in accepted.items() if key in RubricSetView.model_fields},
        **{key: value for key, value in common.items() if key != "rubric_id"},
        "id": common["rubric_id"],
        "extraction_job_id": UUID(int=406),
        "document_id": UUID(int=201),
        "version": 1,
        "input_hash": "a" * 64,
        "normalization_rule_version": "synthetic",
        "prompt_version": "synthetic",
        "schema_version": "synthetic",
        "overall_aggregation_assessable": True,
        "completeness": completeness,
        "created_at": "2026-10-05T00:00:00Z",
    }
    return [(RubricSectionView, section), (RubricItemView, item), (RubricSetView, rubric)]


def test_invalid_candidates_are_readable_but_cannot_become_confirmed():
    wire = candidate()
    wire["sections"][0].update(cap="-1", weight="1.25")
    wire["items"][0].update(score_range=None, weight="-0.25")
    wire.update(overall_cap="-1", overall_score_range={"minimum": "6", "maximum": "5"})
    accepted = accept_wire(wire)
    for model, payload in public_views(accepted):
        assert model.model_validate(payload).state == "candidate"
        confirmed = {
            **payload,
            "state": "confirmed",
            "review_domain": "technical",
            "confirmed_by": UUID(int=407),
            "confirmed_at": "2026-10-05T00:00:00Z",
        }
        if model is RubricSetView:
            confirmed.pop("review_domain")
        with pytest.raises(ValidationError):
            model.model_validate(confirmed)


def test_console_summary_and_blockers_expose_the_candidate_errors(tmp_path):
    wire = candidate()
    wire.update(overall_cap="-1", overall_score_range={"minimum": "6", "maximum": "5"})
    accepted = accept_wire(wire)
    rubric = public_views(accepted)[-1][1]
    complete = rubric["completeness"]
    summary = RubricSummaryData.model_validate(
        {
            **{
                key: value for key, value in rubric.items() if key in RubricSummaryData.model_fields
            },
            "prior_rubric_id": None,
            "validity": "current",
            "section_count": 1,
            "item_count": 1,
            "actions": [],
            "completeness": assessment_rubrics.compact_completeness(complete),
        }
    )
    notices = assessment_rubrics.blockers(complete)
    assert summary.completeness.normalization_errors == len(accepted["normalization_errors"])
    assert set(accepted["normalization_errors"]) <= {notice.code for notice in notices}
    assert summary.overall_cap == -1
    assert not summary.completeness.complete
    artifact = tmp_path / "synthetic-console-rubric-errors.json"
    artifact.write_text(
        json.dumps(
            {
                "summary": summary.model_dump(mode="json"),
                "blockers": [notice.model_dump(mode="json") for notice in notices],
            },
            indent=2,
        )
    )
    assert (
        json.loads(artifact.read_text())["summary"]["overall_score_range"]
        == wire["overall_score_range"]
    )


def test_semantic_tolerance_does_not_relax_structure_citation_verification():
    wire = candidate(quote="Synthetic quote not present in the pinned scoring clause")
    wire["sections"][0]["cap"] = "35"
    with pytest.raises(ServiceError) as failure:
        accept_wire(wire)
    assert failure.value.code == "invalid_overall_citation"
