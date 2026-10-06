"""Replacement schema regressions for incremental human normalization.

Failures: unchanged invalid entries deadlock other domains; partial human repairs
are rejected; malformed numbers/enums/keys or caller-supplied Sources are accepted;
structural references become ambiguous. Database/domain gates have API coverage in
test_score_revision_domains_db.py.
"""

from copy import deepcopy

import pytest
from app.schemas.score_contracts import (
    RubricItemRevisionInput,
    RubricReviseRequest,
    RubricSectionRevisionInput,
)
from app.services.score_normalization import rule_errors
from pydantic import ValidationError
from test_rubric_wire_normalization import accept_wire
from test_score_generation import REAL_REQUIREMENT, candidate


def revision_payload():
    accepted = accept_wire(candidate())
    section = {
        key: value
        for key, value in accepted["sections"][0].items()
        if key in RubricSectionRevisionInput.model_fields
    }
    section["sources"] = [
        {key: citation[key] for key in ("requirement_id", "quote")}
        for citation in section["sources"]
    ]
    item = {
        key: value
        for key, value in accepted["items"][0].items()
        if key in RubricItemRevisionInput.model_fields
    }
    return {
        "expected_revision": 1,
        "expected_input_hash": "a" * 64,
        "reason": "Synthetic incremental human repair",
        "sections": [section],
        "items": [item],
        "coverage": [{"requirement_id": str(REAL_REQUIREMENT), "disposition": "pending"}],
        **{
            key: value
            for key, value in accepted.items()
            if key.startswith("overall_") and key in RubricReviseRequest.model_fields
        },
    }


def target(payload, subject):
    return payload if subject == "overall" else payload[subject][0]


@pytest.mark.parametrize(
    "subject,changes,code",
    [
        ("sections", {"weight": "35"}, "invalid_weight"),
        ("items", {"weight": "35"}, "invalid_weight"),
        ("items", {"weight": "0"}, "invalid_weight"),
        ("items", {"weight": "-0.25"}, "invalid_weight"),
        ("items", {"score_range": None}, "missing_score_bounds"),
        ("items", {"assessment_mode": "manual_only"}, "missing_ambiguity_reason"),
        ("sections", {"aggregation": "capped_sum"}, "missing_cap"),
        ("sections", {"cap": "35"}, "unexpected_cap"),
        ("sections", {"cap": "-1"}, "invalid_cap"),
        ("sections", {"aggregation": "formula"}, "missing_aggregation_rule_text"),
        ("sections", {"aggregation": "non_additive"}, "missing_aggregation_limitation"),
        ("overall", {"overall_aggregation": "capped_sum"}, "missing_cap"),
        ("overall", {"overall_cap": "35"}, "unexpected_cap"),
        ("overall", {"overall_cap": "-1"}, "invalid_cap"),
        ("overall", {"overall_aggregation": "formula"}, "missing_aggregation_rule_text"),
        ("items", {"score_range": {"minimum": "6", "maximum": "5"}}, "invalid_score_bounds"),
        ("sections", {"score_range": {"minimum": "-1", "maximum": "5"}}, "invalid_score_bounds"),
        (
            "overall",
            {"overall_score_range": {"minimum": "5", "maximum": "0"}},
            "invalid_score_bounds",
        ),
    ],
)
def test_replacement_preserves_candidate_semantic_errors(subject, changes, code):
    payload = revision_payload()
    target(payload, subject).update(changes)
    saved = RubricReviseRequest.model_validate(payload).model_dump(mode="json")
    assert {key: target(saved, subject)[key] for key in changes} == changes
    errors = rule_errors(saved, saved["sections"], saved["items"], candidate_keys=True)
    assert code in set.union(*errors)


def test_complete_snapshot_can_retain_errors_in_both_domains():
    payload = revision_payload()
    payload["sections"][0]["weight"] = "35"
    payload["items"][0]["score_range"] = None
    payload["overall_cap"] = "-1"
    saved = RubricReviseRequest.model_validate(payload).model_dump(mode="json")
    assert saved["sections"][0]["weight"] == "35"
    assert saved["items"][0]["score_range"] is None
    assert saved["overall_cap"] == "-1"


@pytest.mark.parametrize(
    "subject,field,value",
    [
        ("items", "weight", "35%"),
        ("items", "weight", True),
        ("sections", "cap", "NaN"),
        ("overall", "overall_cap", "Infinity"),
        ("items", "weight", "0.123456789"),
        ("sections", "weight", "10000000000"),
        ("items", "score_range", {"minimum": "unknown", "maximum": "5"}),
        ("items", "score_range", {"maximum": "5"}),
        ("items", "score_range", [0, 5]),
        ("items", "assessment_mode", "guessed"),
        ("sections", "aggregation", "average"),
        ("overall", "overall_aggregation", "average"),
        ("items", "requirement_id", "absent"),
        ("items", "section_key", "absent"),
        ("sections", "sources", []),
        ("sections", "sources", [{"requirement_id": str(REAL_REQUIREMENT)}]),
        ("sections", "sources", [{"requirement_id": str(REAL_REQUIREMENT), "quote": "  "}]),
        ("items", "review_domain", "technical"),
        ("sections", "normalization_errors", []),
    ],
)
def test_replacement_rejects_malformed_values(subject, field, value):
    payload = revision_payload()
    target(payload, subject)[field] = value
    with pytest.raises(ValidationError):
        RubricReviseRequest.model_validate(payload)


@pytest.mark.parametrize(
    "subject,field",
    [
        ("sections", "sources"),
        ("sections", "key"),
        ("items", "requirement_id"),
        ("items", "assessment_mode"),
        ("overall", "expected_input_hash"),
        ("overall", "reason"),
    ],
)
def test_replacement_required_keys_stay_required(subject, field):
    payload = revision_payload()
    del target(payload, subject)[field]
    with pytest.raises(ValidationError):
        RubricReviseRequest.model_validate(payload)


def test_replacement_rejects_supplied_source_and_duplicate_identity():
    payload = revision_payload()
    payload["sections"][0]["sources"][0]["source"] = candidate()["sections"][0]["citations"][0]
    with pytest.raises(ValidationError):
        RubricReviseRequest.model_validate(payload)
    payload = revision_payload()
    payload["items"].append(deepcopy(payload["items"][0]))
    with pytest.raises(ValidationError):
        RubricReviseRequest.model_validate(payload)
