"""Persist invalid numeric candidates, inspect them, then repair through human review.

Failures to prevent: lost generated candidates/usage, silently corrected numeric
values, unreadable score or console views, cross-org leakage, and confirmation
through either HTTP or direct SQL when normalization error metadata is omitted.
"""

import json
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from app.models.score import ScoreRubricDecision
from app.schemas.score_contracts import RubricReviseRequest
from app.services import response_cards, score_generation
from app.services.auth import ROLE_SCOPES, Identity, set_actor_context
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from test_check_combined import semantic_llm
from test_score_api import (
    RubricVendor,
    finish_rubric,
    install_rubric_resolver,
    preview_rubric,
    submit_rubric,
)
from test_score_api import rubric_input_case as rubric_input_case
from test_score_review import base, classify_all, confirm_contents, decision, replacement, show


class NumericCandidateVendor(RubricVendor):
    def __init__(self, target, changes):
        super().__init__()
        self.target = target
        self.changes = changes

    async def __call__(self, request):
        response = await super().__call__(request)
        body = response.json()
        message = body["choices"][0]["message"]
        output = json.loads(message["content"])
        if self.target == "rubric" and "sections" in output:
            output.update(self.changes)
        elif self.target in output:
            output[self.target][0].update(self.changes)
        message["content"] = json.dumps(output)
        return httpx.Response(200, json=body)


async def generate_candidate(case, monkeypatch, target, changes):
    vendor = NumericCandidateVendor(target, changes)
    llm = semantic_llm(case["tmp_path"], vendor)
    install_rubric_resolver(monkeypatch, llm)
    submitted = await submit_rubric(case, await preview_rubric(case))
    assert submitted.status_code == 200, submitted.text
    terminal = await finish_rubric(case, submitted.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    assert terminal["result"]["candidate_items"] == 1, terminal
    assert len(terminal["result"]["usage_record_ids"]) == len(vendor.requests) == 2
    response = await case["api"].get(
        f"/tasks/{case['task']}/score-rubrics/{terminal['result']['rubric_id']}",
        headers=case["header"],
    )
    assert response.status_code == 200, response.text
    case["rubric"] = response.json()["data"]
    return terminal, case["rubric"]


def assert_values_preserved(subject, changes):
    for field, value in changes.items():
        if field.endswith("score_range"):
            assert {key: Decimal(bound) for key, bound in subject[field].items()} == {
                key: Decimal(bound) for key, bound in value.items()
            }
        elif field in {"weight", "cap", "overall_cap"} and value is not None:
            assert Decimal(subject[field]) == Decimal(value)
        else:
            assert subject[field] == value


@pytest.mark.parametrize(
    "target,changes,expected_error",
    [
        ("rubric", {"overall_cap": "5"}, "unexpected_cap"),
        ("rubric", {"overall_aggregation": "capped_sum", "overall_cap": None}, "missing_cap"),
        ("rubric", {"overall_aggregation": "capped_sum", "overall_cap": "-1"}, "invalid_cap"),
        ("sections", {"cap": "5"}, "unexpected_cap"),
        ("sections", {"aggregation": "capped_sum", "cap": None}, "missing_cap"),
        ("sections", {"aggregation": "capped_sum", "cap": "-1"}, "invalid_cap"),
        (
            "sections",
            {
                "aggregation": "formula",
                "aggregation_rule_text": "Synthetic original formula rule",
                "ambiguity_reason": "Synthetic unsupported formula limitation",
                "cap": "35",
            },
            "unexpected_cap",
        ),
        (
            "sections",
            {
                "aggregation": "formula",
                "aggregation_rule_text": "Synthetic original formula rule",
                "ambiguity_reason": None,
            },
            "missing_aggregation_limitation",
        ),
        ("sections", {"weight": "1.25"}, "invalid_weight"),
        ("items", {"weight": "0"}, "invalid_weight"),
        ("items", {"weight": "-0.25"}, "invalid_weight"),
        ("items", {"weight": "1.25"}, "invalid_weight"),
        (
            "rubric",
            {"overall_score_range": {"minimum": "5", "maximum": "0"}},
            "invalid_score_bounds",
        ),
        ("sections", {"score_range": {"minimum": "-1", "maximum": "5"}}, "invalid_score_bounds"),
        ("items", {"score_range": {"minimum": "5", "maximum": "0"}}, "invalid_score_bounds"),
    ],
)
async def test_numeric_candidate_survives_views_and_human_correction(
    rubric_input_case, monkeypatch, target, changes, expected_error
):
    case = rubric_input_case
    terminal, report = await generate_candidate(case, monkeypatch, target, changes)
    assert terminal["result"]["completion"] == "partial"
    assert report["rubric"]["state"] == "candidate"
    assert expected_error in report["rubric"]["completeness"]["normalization_errors"]
    subject = report["rubric"] if target == "rubric" else report[target][0]
    assert_values_preserved(subject, changes)
    views = {}
    for part in ("summary", "sections", "items", "blockers"):
        path = "/v4" + base(case) + f"?view=console&part={part}"
        result = await case["api"].get(path, headers=case["header"])
        assert result.status_code == 200, result.text
        views[part] = result.json()
        assert (await case["api"].get(path, headers=case["headers"][1])).status_code == 404
    assert (await case["api"].get(base(case), headers=case["headers"][1])).status_code == 404
    assert (
        await case["api"].get(
            "/v4" + base(case) + "?view=console&part=replacement", headers=case["header"]
        )
    ).status_code == 404

    report = await classify_all(case)
    subject = report["rubric"] if target == "rubric" else report[target][0]
    suffix = "" if target == "rubric" else f"/{target}/{subject['id']}"
    denied = await case["api"].post(
        base(case) + suffix + "/decisions",
        headers=case["header"],
        json=decision(report, subject),
    )
    assert denied.status_code == 409, denied.text
    assert denied.json()["data"]["error"]["code"] == "rubric_incomplete"

    body = replacement(report)
    body.update(overall_aggregation="sum", overall_cap=None)
    body["overall_score_range"] = {"minimum": "0", "maximum": "5"}
    for section in body["sections"]:
        section.update(aggregation="sum", cap=None, weight=None)
        section["score_range"] = {"minimum": "0", "maximum": "5"}
    for item in body["items"]:
        item.update(weight=None, score_range={"minimum": "0", "maximum": "5"})
    revised_response = await case["api"].post(
        base(case) + "/revisions", headers=case["header"], json=body
    )
    assert revised_response.status_code == 200, revised_response.text
    revised = revised_response.json()["data"]
    replacement_path = "/v4" + base(case, revised) + "?view=console&part=replacement"
    saved_replacement = await case["api"].get(replacement_path, headers=case["header"])
    assert saved_replacement.status_code == 200, saved_replacement.text
    assert saved_replacement.json()["data"]["prior_rubric_id"] == report["rubric"]["id"]
    assert RubricReviseRequest.model_validate(
        saved_replacement.json()["data"]["replacement"]
    ) == RubricReviseRequest.model_validate(body)
    views["replacement"] = saved_replacement.json()
    assert (await case["api"].get(replacement_path, headers=case["headers"][1])).status_code == 404
    revised = await confirm_contents(case, revised)
    assert revised["rubric"]["completeness"]["complete"]
    confirmed = await case["api"].post(
        base(case, revised) + "/decisions",
        headers=case["header"],
        json=decision(revised, revised["rubric"]),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["data"]["state"] == "confirmed"
    historic = await show(case, report)
    assert_values_preserved(
        historic["rubric"] if target == "rubric" else historic[target][0], changes
    )
    artifact = case["tmp_path"] / "numeric-candidate-review.json"
    artifact.write_text(
        json.dumps(
            {"job": terminal, "candidate": report, "console": views, "confirmed": confirmed.json()},
            ensure_ascii=False,
            indent=2,
        )
    )
    assert json.loads(artifact.read_text())["confirmed"]["data"]["state"] == "confirmed"


@pytest.mark.parametrize(
    "target,changes",
    [
        ("rubric", {"overall_cap": "5"}),
        ("sections", {"cap": "5"}),
        ("items", {"weight": "0"}),
        (
            "sections",
            {
                "aggregation": "formula",
                "aggregation_rule_text": "Synthetic original formula rule",
                "ambiguity_reason": None,
            },
        ),
    ],
)
async def test_database_confirmation_checks_numbers_without_error_metadata(
    rubric_input_case, monkeypatch, target, changes
):
    case = rubric_input_case
    accept = score_generation.accept_batches

    def omit_error_metadata(*args, **kwargs):
        accepted = accept(*args, **kwargs)
        accepted["normalization_errors"] = []
        return accepted

    monkeypatch.setattr(score_generation, "accept_batches", omit_error_metadata)
    _, report = await generate_candidate(case, monkeypatch, target, changes)
    report = (
        await confirm_contents(case, report)
        if target == "rubric"
        else await classify_all(case, report)
    )
    async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
        errors = await session.scalar(
            text("SELECT normalization_errors FROM score_rubric_sets WHERE id=:id"),
            {"id": UUID(report["rubric"]["id"])},
        )
        assert errors == []
    subject = report["rubric"] if target == "rubric" else report[target][0]
    with pytest.raises(IntegrityError) as failure:
        async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
            actor = Identity(
                case["tenants"]["users"][0],
                case["tenants"]["orgs"][0],
                ROLE_SCOPES["bidder"],
                "bidder",
            )
            await set_actor_context(session, actor)
            reason = "Synthetic direct numeric confirmation bypass"
            session.add(
                ScoreRubricDecision(
                    id=uuid4(),
                    org_id=actor.org_id,
                    task_id=UUID(case["task"]),
                    rubric_id=UUID(report["rubric"]["id"]),
                    section_id=UUID(subject["id"]) if target == "sections" else None,
                    item_id=UUID(subject["id"]) if target == "items" else None,
                    revision=subject["revision"] + 1,
                    set_revision=report["rubric"]["revision"] + 1,
                    action="confirm",
                    reason=reason,
                    reason_sha256=response_cards.quote_hash(reason),
                    expected_input_hash=report["rubric"]["input_hash"],
                    decided_by=actor.user_id,
                    actor_kind="session",
                )
            )
            await session.flush()
    assert failure.value.orig.sqlstate == "23514"
    assert "Rubric completeness gate failed" in str(
        failure.value
    ) or "Invalid rubric subject" in str(failure.value)
