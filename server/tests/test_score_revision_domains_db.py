"""Repair invalid cross-domain candidates through the provider, worker and review API.

Failures to prevent: one domain's invalid declarations blocking another's repair;
lost local errors; invalid candidate confirmation; cross-domain edits, deletion or
moves; malformed snapshots or forged multi-source selectors becoming accepted;
unreadable replacement recovery; rewritten prior snapshots or inherited approvals.
"""

import copy
import json
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from app.models.entities import Requirement
from app.schemas.score_contracts import RubricReviseRequest
from test_check_combined import semantic_llm
from test_score_api import (
    RubricVendor,
    finish_rubric,
    install_rubric_resolver,
    preview_rubric,
    submit_rubric,
)
from test_score_api import rubric_input_case as rubric_input_case
from test_score_review import base, decision, replacement, role, show


class TwoDomainCandidateVendor(RubricVendor):
    async def __call__(self, request):
        response = await super().__call__(request)
        result = response.json()
        message = result["choices"][0]["message"]
        output = json.loads(message["content"])
        rows = self.requests[-1]["requirements"]
        assert len(rows) == 2
        if self.stages[-1] == "structure":
            sources = {
                entry["ref"]: entry["text"].split("\n招标原文：\n", 1)[-1]
                for entry in self.requests[-1]["context"]["texts"]
            }
            first = output["sections"][0]
            second = copy.deepcopy(first)
            first.update(
                key="commercial",
                title="Synthetic commercial scoring section",
                score_range={"minimum": "0", "maximum": "5"},
                weight="35",
                citations=[
                    {"ref": row["tender_ref"], "quote": sources[row["tender_ref"]]} for row in rows
                ],
            )
            second.update(
                key="technical",
                title="Synthetic technical scoring section",
                order=2,
                score_range={"minimum": "0", "maximum": "5"},
                citations=[{"ref": rows[1]["tender_ref"], "quote": sources[rows[1]["tender_ref"]]}],
            )
            output["sections"] = [first, second]
        else:
            output["items"][0].update(section_key="commercial", weight="35")
            output["items"][1].update(section_key="technical", score_range=None)
        message["content"] = json.dumps(output)
        return httpx.Response(200, json=result)


@pytest.fixture
async def invalid_domains_case(rubric_input_case, monkeypatch):
    case = rubric_input_case
    async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
        second = await session.get(Requirement, UUID(case["requirements"][1]["id"]))
        assert second is not None
        second.category = "scoring"
    vendor = TwoDomainCandidateVendor()
    install_rubric_resolver(monkeypatch, semantic_llm(case["tmp_path"], vendor))
    submitted = await submit_rubric(case, await preview_rubric(case))
    assert submitted.status_code == 200, submitted.text
    terminal = await finish_rubric(case, submitted.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    assert terminal["result"]["completion"] == "partial", terminal
    assert terminal["result"]["candidate_items"] == 2, terminal
    assert len(terminal["result"]["usage_record_ids"]) == len(vendor.requests) == 2
    response = await case["api"].get(
        f"/tasks/{case['task']}/score-rubrics/{terminal['result']['rubric_id']}",
        headers=case["header"],
    )
    assert response.status_code == 200, response.text
    case["rubric"] = response.json()["data"]
    case["job"] = terminal
    return case


def by_domain(report, collection, domain):
    sections = {row["id"]: row["key"] for row in report["sections"]}
    return next(
        row
        for row in report[collection]
        if (row["key"] if collection == "sections" else sections[row["section_id"]]) == domain
    )


async def classify_domains(case, report=None):
    report = await show(case, report)
    role(case, "admin")
    for collection in ("sections", "items"):
        for domain in ("commercial", "technical"):
            row = by_domain(report, collection, domain)
            body = decision(report, row)
            body.pop("action")
            response = await case["api"].post(
                f"{base(case, report)}/{collection}/{row['id']}/classification",
                headers=case["header"],
                json={**body, "review_domain": domain},
            )
            assert response.status_code == 200, response.text
    role(case, "bidder")
    return await show(case, report)


async def revise(case, report, body):
    response = await case["api"].post(
        base(case, report) + "/revisions", headers=case["header"], json=body
    )
    assert response.status_code == 200, response.text
    revised = response.json()["data"]
    assert revised["rubric"]["prior_rubric_id"] == report["rubric"]["id"]
    assert all(
        row["review_domain"] is None
        and row["revision"] == 1
        and row["state"] == "candidate"
        and row["confirmed_by"] is None
        for row in revised["sections"] + revised["items"]
    )
    for collection in ("sections", "items"):
        assert {row["id"] for row in revised[collection]}.isdisjoint(
            row["id"] for row in report[collection]
        )
    return revised


async def assert_set_blocked(case, report):
    role(case, "bidder")
    response = await case["api"].post(
        base(case, report) + "/decisions",
        headers=case["header"],
        json=decision(report, report["rubric"]),
    )
    assert response.status_code == 409, response.text
    assert response.json()["data"]["error"]["code"] == "rubric_incomplete"


async def test_domains_can_repair_in_sequence_without_confirming_invalid_candidates(
    invalid_domains_case,
):
    case = invalid_domains_case
    original = await classify_domains(case)
    assert by_domain(original, "sections", "commercial")["normalization_errors"] == [
        "invalid_weight"
    ]
    assert by_domain(original, "items", "commercial")["normalization_errors"] == ["invalid_weight"]
    assert by_domain(original, "items", "technical")["normalization_errors"] == [
        "missing_score_bounds"
    ]
    assert by_domain(original, "sections", "technical")["normalization_errors"] == []
    for collection in ("sections", "items"):
        page = await case["api"].get(
            "/v4" + base(case, original) + f"?view=console&part={collection}",
            headers=case["header"],
        )
        assert page.status_code == 200, page.text
        assert {row["id"]: row["normalization_errors"] for row in page.json()["items"]} == {
            row["id"]: row["normalization_errors"] for row in original[collection]
        }
    for collection, domain in (
        ("sections", "commercial"),
        ("items", "commercial"),
        ("items", "technical"),
    ):
        role(case, "bidder" if domain == "commercial" else "technical")
        row = by_domain(original, collection, domain)
        rejected = await case["api"].post(
            f"{base(case, original)}/{collection}/{row['id']}/decisions",
            headers=case["header"],
            json=decision(original, row),
        )
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["data"]["error"]["code"] == "rubric_incomplete"

    # Changed own-domain declarations may remain semantically invalid during repair.
    role(case, "bidder")
    body = replacement(original)
    body["sections"][0]["weight"] = "36"
    body["items"][0]["weight"] = "36"
    intermediate = await revise(case, original, body)
    assert Decimal(by_domain(intermediate, "items", "commercial")["weight"]) == 36
    assert by_domain(intermediate, "items", "technical")["score_range"] is None
    await assert_set_blocked(case, intermediate)
    classified = await classify_domains(case, intermediate)
    body = replacement(classified)
    body["sections"][0]["weight"] = None
    body["items"][0]["weight"] = None
    commercial_fixed = await revise(case, classified, body)
    assert by_domain(commercial_fixed, "sections", "commercial")["normalization_errors"] == []
    assert by_domain(commercial_fixed, "items", "commercial")["normalization_errors"] == []
    assert by_domain(commercial_fixed, "items", "technical")["normalization_errors"] == [
        "missing_score_bounds"
    ]
    assert (
        "invalid_weight" not in commercial_fixed["rubric"]["completeness"]["normalization_errors"]
    )
    await assert_set_blocked(case, commercial_fixed)
    recovery_path = "/v4" + base(case, commercial_fixed) + "?view=console&part=replacement"
    saved = await case["api"].get(recovery_path, headers=case["header"])
    assert saved.status_code == 200, saved.text
    assert RubricReviseRequest.model_validate(saved.json()["data"]["replacement"]) == (
        RubricReviseRequest.model_validate(body)
    )
    assert (await case["api"].get(recovery_path, headers=case["headers"][1])).status_code == 404

    classified = await classify_domains(case, commercial_fixed)
    role(case, "bidder")
    for collection in ("sections", "items"):
        row = by_domain(classified, collection, "commercial")
        response = await case["api"].post(
            f"{base(case, classified)}/{collection}/{row['id']}/decisions",
            headers=case["header"],
            json=decision(classified, row),
        )
        assert response.status_code == 200, response.text
    classified = await show(case, classified)
    assert by_domain(classified, "items", "commercial")["state"] == "confirmed"
    assert by_domain(classified, "items", "technical")["state"] == "candidate"
    role(case, "technical")
    body = replacement(classified)
    body["items"][1]["score_range"] = {"minimum": "0", "maximum": "5"}
    repaired = await revise(case, classified, body)
    assert all(
        row["normalization_errors"] == [] for row in repaired["sections"] + repaired["items"]
    )
    # Only coverage accounting errors remain; every numeric/aggregation error is gone.
    assert repaired["rubric"]["completeness"]["normalization_errors"] == ["unmapped_item"]
    await assert_set_blocked(case, repaired)
    final = await classify_domains(case, repaired)
    for collection in ("sections", "items"):
        for domain in ("commercial", "technical"):
            role(case, "bidder" if domain == "commercial" else "technical")
            row = by_domain(final, collection, domain)
            response = await case["api"].post(
                f"{base(case, final)}/{collection}/{row['id']}/decisions",
                headers=case["header"],
                json=decision(final, row),
            )
            assert response.status_code == 200, response.text
    final = await show(case, final)
    await assert_set_blocked(case, final)
    for coverage in final["coverage"]:
        item_ids = [
            row["id"]
            for row in final["items"]
            if row["requirement_id"] == coverage["requirement_id"]
        ]
        response = await case["api"].post(
            f"{base(case, final)}/coverage/{coverage['requirement_id']}/decisions",
            headers=case["header"],
            json=decision(final, coverage, "mapped", rubric_item_ids=item_ids),
        )
        assert response.status_code == 200, response.text
    final = await show(case, final)
    assert final["rubric"]["completeness"]["normalization_errors"] == []
    assert final["rubric"]["completeness"]["complete"]
    confirmed = await case["api"].post(
        base(case, final) + "/decisions",
        headers=case["header"],
        json=decision(final, final["rubric"]),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["data"]["state"] == "confirmed"
    for prior in (original, classified):
        historical = await show(case, prior)
        assert historical["rubric"]["state"] == "superseded"
        assert historical["sections"] == prior["sections"]
        assert historical["items"] == prior["items"]
        assert historical["coverage"] == prior["coverage"]
    artifact = case["tmp_path"] / "rubric-revision-domains-db.json"
    artifact.write_text(
        json.dumps(
            {
                "job": case["job"],
                "original": original,
                "commercial_fixed": commercial_fixed,
                "replacement": saved.json()["data"],
                "confirmed": confirmed.json()["data"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    assert json.loads(artifact.read_text(encoding="utf-8"))["confirmed"]["state"] == "confirmed"


@pytest.mark.parametrize(
    "change",
    [
        "section_edit",
        "section_range_spelling",
        "item_edit",
        "section_delete",
        "item_delete",
        "item_move",
        "item_rebind",
        "section_sources",
    ],
)
async def test_invalid_copied_candidates_do_not_bypass_domain_authority(
    invalid_domains_case, change
):
    case = invalid_domains_case
    report = await classify_domains(case)
    body = replacement(report)
    if change == "section_edit":
        body["sections"][1]["title"] = "Synthetic unauthorized rewrite"
    elif change == "section_range_spelling":
        # JSON range declarations must match the stored strings, just as the
        # database publication trigger requires, even for equivalent numbers.
        body["sections"][1]["score_range"]["minimum"] = "0.0"
    elif change == "item_edit":
        body["items"][1]["score_range"] = {"minimum": "0", "maximum": "5"}
    elif change == "section_delete":
        body["sections"].pop(1)
        body["items"].pop(1)
    elif change == "item_delete":
        body["items"].pop(1)
    elif change == "item_move":
        body["items"][1]["section_key"] = "commercial"
    elif change == "item_rebind":
        body["items"][1]["requirement_id"] = body["items"][0]["requirement_id"]
    else:
        body["sections"][1]["sources"].append(body["sections"][0]["sources"][0])
    denied = await case["api"].post(base(case) + "/revisions", headers=case["header"], json=body)
    assert denied.status_code == 403, denied.text
    assert denied.json()["data"]["error"]["code"] == "forbidden"
    assert await show(case) == report


@pytest.mark.parametrize(
    "change,status,code",
    [
        ("non_numeric_weight", 422, None),
        ("non_finite_weight", 422, None),
        ("missing_range_member", 422, None),
        ("extra_source_fields", 422, None),
        ("empty_sources", 422, None),
        ("duplicate_key", 422, None),
        ("dangling_section", 422, None),
        ("invented_second_quote", 409, "invalid_revision_source"),
        ("foreign_requirement", 409, "invalid_requirement"),
    ],
)
async def test_candidate_tolerance_preserves_structure_and_verified_sources(
    invalid_domains_case, change, status, code
):
    case = invalid_domains_case
    report = await classify_domains(case)
    body = replacement(report)
    if change == "non_numeric_weight":
        body["items"][0]["weight"] = "35%"
    elif change == "non_finite_weight":
        body["items"][0]["weight"] = "NaN"
    elif change == "missing_range_member":
        body["items"][0]["score_range"].pop("maximum")
    elif change == "extra_source_fields":
        body["sections"][0]["sources"][1]["source"] = report["sections"][0]["sources"][1]["source"]
    elif change == "empty_sources":
        body["sections"][0]["sources"] = []
    elif change == "duplicate_key":
        body["items"][1]["key"] = body["items"][0]["key"]
    elif change == "dangling_section":
        body["items"][0]["section_key"] = "missing"
    elif change == "invented_second_quote":
        body["sections"][0]["sources"][1]["quote"] = "Synthetic invented quotation"
    else:
        body["sections"][0]["sources"][1]["requirement_id"] = str(uuid4())
    denied = await case["api"].post(base(case) + "/revisions", headers=case["header"], json=body)
    assert denied.status_code == status, denied.text
    if code is not None:
        assert denied.json()["data"]["error"]["code"] == code
    assert await show(case) == report
    listing = await case["api"].get(f"/tasks/{case['task']}/score-rubrics", headers=case["header"])
    assert listing.status_code == 200 and len(listing.json()["items"]) == 1
