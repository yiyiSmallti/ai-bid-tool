"""Human review acceptance against the API and immutable PostgreSQL snapshots.

Failure cases: wrong actor/domain, stale revision/hash, pending coverage, invalid
normalization, rewriting old snapshots, inherited classification after revision,
cross-tenant routes, and pagination crossing its task/rubric binding.
"""

from __future__ import annotations

import copy
import json
from uuid import UUID

import pytest
from app.models.entities import AuditLog
from app.schemas.score_contracts import RubricReportData
from sqlalchemy import select
from test_card_generation import token_header
from test_response_cards import set_role
from test_score_api import rubric_case as rubric_case
from test_score_api import rubric_input_case as rubric_input_case


def role(case, value):
    set_role(case["admin_engine"], case["tenants"]["orgs"][0], case["tenants"]["users"][0], value)


def base(case, report=None):
    report = report or case["rubric"]
    return f"/tasks/{case['task']}/score-rubrics/{report['rubric']['id']}"


async def show(case, report=None):
    response = await case["api"].get(base(case, report), headers=case["header"])
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    RubricReportData.model_validate(data)
    return data


def decision(report, subject, action="confirm", **extra):
    return {
        "expected_revision": subject["revision"],
        "expected_input_hash": report["rubric"]["input_hash"],
        "action": action,
        "reason": "Synthetic documented human review",
        **extra,
    }


async def classify_all(case, report=None, domain="commercial"):
    report = await show(case, report)
    role(case, "admin")
    for collection in ("sections", "items"):
        for subject in report[collection]:
            body = decision(report, subject)
            body.pop("action")
            response = await case["api"].post(
                f"{base(case, report)}/{collection}/{subject['id']}/classification",
                headers=case["header"],
                json={**body, "review_domain": domain},
            )
            assert response.status_code == 200, response.text
    role(case, "bidder" if domain == "commercial" else "technical")
    return await show(case, report)


async def confirm_contents(case, report=None):
    report = await classify_all(case, report)
    for collection in ("sections", "items"):
        for subject in report[collection]:
            response = await case["api"].post(
                f"{base(case, report)}/{collection}/{subject['id']}/decisions",
                headers=case["header"],
                json=decision(report, subject),
            )
            assert response.status_code == 200, response.text
    for coverage in report["coverage"]:
        ids = [
            item["id"]
            for item in report["items"]
            if item["requirement_id"] == coverage["requirement_id"]
        ]
        response = await case["api"].post(
            f"{base(case, report)}/coverage/{coverage['requirement_id']}/decisions",
            headers=case["header"],
            json=decision(report, coverage, "mapped", rubric_item_ids=ids),
        )
        assert response.status_code == 200, response.text
    return await show(case, report)


def replacement(report):
    section_keys = {section["id"]: section["key"] for section in report["sections"]}
    section_fields = (
        "key",
        "title",
        "order",
        "aggregation",
        "aggregation_rule_text",
        "score_range",
        "weight",
        "cap",
        "included_in_overall_total",
        "ambiguity_reason",
    )
    item_fields = (
        "requirement_id",
        "key",
        "title",
        "rule_text",
        "order",
        "assessment_mode",
        "score_range",
        "weight",
        "ambiguity_reason",
    )
    return {
        "expected_revision": report["rubric"]["revision"],
        "expected_input_hash": report["rubric"]["input_hash"],
        "reason": "Synthetic corrected complete snapshot",
        "sections": [
            {
                **{key: row[key] for key in section_fields},
                "source_section_id": row["id"],
                "requirement_id": next(
                    entry["requirement_id"]
                    for entry in report["coverage"]
                    if entry["source"] == row["source"]
                ),
            }
            for row in report["sections"]
        ],
        "items": [
            {
                **{key: row[key] for key in item_fields},
                "source_item_id": row["id"],
                "section_key": section_keys[row["section_id"]],
            }
            for row in report["items"]
        ],
        "coverage": [
            {"requirement_id": row["requirement_id"], "disposition": "pending"}
            for row in report["coverage"]
        ],
        **{
            key: report["rubric"][key]
            for key in (
                "overall_aggregation",
                "overall_rule_text",
                "overall_score_range",
                "overall_cap",
            )
        },
    }


async def test_rubric_review_confirmation_revision_and_paginated_history(rubric_case):
    case = rubric_case
    pending = await show(case)
    denied = await case["api"].post(
        base(case) + "/decisions", headers=case["header"], json=decision(pending, pending["rubric"])
    )
    assert (
        denied.status_code == 409 and denied.json()["data"]["error"]["code"] == "rubric_incomplete"
    )
    complete = await confirm_contents(case)
    assert complete["rubric"]["completeness"]["complete"]
    response = await case["api"].post(
        base(case) + "/decisions",
        headers=case["header"],
        json=decision(complete, complete["rubric"]),
    )
    assert response.status_code == 200, response.text
    confirmed = await show(case)
    assert confirmed["rubric"]["state"] == "confirmed"
    assert all(row["confirmed_by"] for row in confirmed["sections"] + confirmed["items"])
    stale = await case["api"].post(
        base(case) + "/decisions",
        headers=case["header"],
        json=decision(complete, complete["rubric"], "reopen"),
    )
    assert stale.status_code == 409
    body = replacement(confirmed)
    body["items"][0]["title"] = "Synthetic corrected human title"
    revised = await case["api"].post(base(case) + "/revisions", headers=case["header"], json=body)
    assert revised.status_code == 200, revised.text
    new = revised.json()["data"]
    assert new["rubric"]["prior_rubric_id"] == confirmed["rubric"]["id"]
    assert new["rubric"]["version"] == confirmed["rubric"]["version"] + 1
    assert new["rubric"]["state"] == "candidate"
    assert all(
        row["review_domain"] is None and row["revision"] == 1 and row["state"] == "candidate"
        for row in new["sections"] + new["items"]
    )
    assert all(
        row["disposition"] == "pending" and row["decided_by"] is None for row in new["coverage"]
    )
    assert {row["id"] for row in new["items"]}.isdisjoint(row["id"] for row in confirmed["items"])
    previous = await show(case)
    assert previous["rubric"]["state"] == "superseded"
    assert previous["items"] == confirmed["items"]
    events, cursor = [], None
    while True:
        response = await case["api"].get(
            base(case) + "/history",
            headers=case["header"],
            params={"limit": 2, **({"cursor": cursor} if cursor else {})},
        )
        assert response.status_code == 200, response.text
        events.extend(response.json()["items"])
        cursor = response.json()["data"]["next_cursor"]
        if not cursor:
            break
        wrong = await case["api"].get(
            base(case, new) + "/history", headers=case["header"], params={"cursor": cursor}
        )
        assert wrong.status_code == 400
    assert {row["kind"] for row in events} == {
        "decision",
        "classification",
        "coverage_decision",
        "revision",
    }
    assert len({row["id"] for row in events}) == len(events)
    artifact = case["tmp_path"] / "rubric-human-review.json"
    artifact.write_text(
        json.dumps({"confirmed": confirmed, "replacement": new, "history": events}, indent=2)
    )
    assert json.loads(artifact.read_text())["confirmed"]["rubric"]["completeness"]["complete"]


async def test_rubric_responsible_session_and_hash_gates(rubric_case):
    case = rubric_case
    report = await classify_all(case, domain="technical")
    subject = report["items"][0]
    path = f"{base(case)}/items/{subject['id']}/decisions"
    body = decision(report, subject)
    for wrong_role in ("admin", "bidder"):
        role(case, wrong_role)
        denied = await case["api"].post(path, headers=case["header"], json=body)
        assert denied.status_code == 403, denied.text
    role(case, "admin")
    token = await token_header(case["api"], case["header"], scopes=["task:read", "score:read"])
    denied = await case["api"].post(path, headers=token, json=body)
    assert denied.status_code == 403
    forbidden_token = await case["api"].post(
        "/tokens",
        headers=case["header"],
        json={
            "name": "Synthetic forbidden rubric token",
            "scopes": ["score:rubric:review"],
            "expires_at": "2099-01-01T00:00:00Z",
        },
    )
    assert forbidden_token.status_code in {400, 403, 422}
    role(case, "technical")
    stale = await case["api"].post(
        path, headers=case["header"], json={**body, "expected_input_hash": "0" * 64}
    )
    assert stale.status_code == 409
    valid = await case["api"].post(path, headers=case["header"], json=body)
    assert valid.status_code == 200, valid.text
    duplicate = await case["api"].post(path, headers=case["header"], json=body)
    assert duplicate.status_code == 409
    report = await show(case)
    revised = replacement(report)
    revised["overall_score_range"]["maximum"] = "6"
    denied = await case["api"].post(base(case) + "/revisions", headers=case["header"], json=revised)
    assert denied.status_code == 403
    async with case["app"].state.db.transaction(UUID(case["header"]["X-Org-Id"])) as session:
        audits = list(
            (
                await session.scalars(
                    select(AuditLog).where(AuditLog.action == "score_rubric.decision_denied")
                )
            ).all()
        )
        assert audits
        assert all(
            "reason" not in audit.details and "reason_sha256" in audit.details for audit in audits
        )
        token_denials = [event for event in audits if event.actor_token_id is not None]
        assert len(token_denials) == 1
        assert token_denials[0].actor_kind == "token"
        assert token_denials[0].initiated_by == "external_agent"
        assert token_denials[0].on_behalf_of_user_id == token_denials[0].actor_user_id
        assert token_denials[0].invocation_id is not None


@pytest.mark.parametrize(
    "failure",
    [
        "weights_not_one",
        "missing_weight",
        "unexpected_weight",
        "aggregate_bounds_mismatch",
        "duplicate_item_order",
    ],
)
async def test_complete_replacement_normalization_errors_block_set_confirmation(
    rubric_case, failure
):
    case = rubric_case
    report = await classify_all(case)
    body = replacement(report)
    if failure in {"weights_not_one", "missing_weight"}:
        body["sections"][0]["aggregation"] = "weighted_sum"
        body["items"][0]["weight"] = "0.5" if failure == "weights_not_one" else None
    elif failure == "unexpected_weight":
        body["items"][0]["weight"] = "0.5"
    elif failure == "aggregate_bounds_mismatch":
        body["sections"][0]["score_range"]["maximum"] = "6"
    else:
        duplicate = copy.deepcopy(body["items"][0])
        duplicate.update(source_item_id=None, key="second", rule_text="Synthetic separate boundary")
        body["items"].append(duplicate)
    response = await case["api"].post(base(case) + "/revisions", headers=case["header"], json=body)
    assert response.status_code == 200, response.text
    revised = await confirm_contents(case, response.json()["data"])
    assert failure in revised["rubric"]["completeness"]["normalization_errors"]
    response = await case["api"].post(
        base(case, revised) + "/decisions",
        headers=case["header"],
        json=decision(revised, revised["rubric"]),
    )
    assert (
        response.status_code == 409
        and response.json()["data"]["error"]["code"] == "rubric_incomplete"
    )


@pytest.mark.parametrize(
    "change",
    [
        "missing_cap",
        "unexpected_cap",
        "negative_cap",
        "negative_bounds",
        "inverted_bounds",
        "invalid_weight",
        "duplicate_key",
        "dangling_section",
    ],
)
async def test_invalid_replacement_contract_has_no_new_version(rubric_case, change):
    case = rubric_case
    report = await classify_all(case)
    body = replacement(report)
    if change == "missing_cap":
        body["sections"][0]["aggregation"] = "capped_sum"
    elif change == "unexpected_cap":
        body["sections"][0]["cap"] = "2"
    elif change == "negative_cap":
        body["sections"][0].update(aggregation="capped_sum", cap="-1")
    elif change == "negative_bounds":
        body["items"][0]["score_range"]["minimum"] = "-1"
    elif change == "inverted_bounds":
        body["items"][0]["score_range"]["minimum"] = "6"
    elif change == "invalid_weight":
        body["items"][0]["weight"] = "1.1"
    elif change == "duplicate_key":
        body["items"].append(copy.deepcopy(body["items"][0]))
    elif change == "dangling_section":
        body["items"][0]["section_key"] = "absent"
    response = await case["api"].post(base(case) + "/revisions", headers=case["header"], json=body)
    assert response.status_code == 422, response.text
    listing = await case["api"].get(f"/tasks/{case['task']}/score-rubrics", headers=case["header"])
    assert listing.status_code == 200 and len(listing.json()["items"]) == 1


async def test_rubric_every_route_hides_other_org(rubric_case):
    case = rubric_case
    report = await classify_all(case)
    prefix = base(case)
    foreign = case["headers"][1]
    for path in (f"/tasks/{case['task']}/score-rubrics", prefix, prefix + "/history"):
        assert (await case["api"].get(path, headers=case["header"])).status_code == 200
        response = await case["api"].get(path, headers=foreign)
        assert response.status_code == 404, response.text
    requests = [
        (prefix + "/decisions", decision(report, report["rubric"])),
        (prefix + "/revisions", replacement(report)),
    ]
    for collection in ("sections", "items"):
        entry = report[collection][0]
        classify = decision(report, entry)
        classify.pop("action")
        requests += [
            (
                f"{prefix}/{collection}/{entry['id']}/classification",
                {**classify, "review_domain": "commercial"},
            ),
            (f"{prefix}/{collection}/{entry['id']}/decisions", decision(report, entry)),
        ]
    entry = report["coverage"][0]
    requests.append(
        (
            f"{prefix}/coverage/{entry['requirement_id']}/decisions",
            decision(report, entry, "mapped", rubric_item_ids=[report["items"][0]["id"]]),
        )
    )
    for path, body in requests:
        response = await case["api"].post(path, headers=foreign, json=body)
        assert response.status_code == 404, response.text


@pytest.mark.parametrize("algorithm", ["weighted_sum", "capped_sum", "formula", "non_additive"])
async def test_confirmed_rules_record_supported_and_unexecuted_aggregations(rubric_case, algorithm):
    case = rubric_case
    report = await classify_all(case)
    body = replacement(report)
    body["sections"][0]["aggregation"] = algorithm
    body["overall_aggregation"] = algorithm
    if algorithm == "weighted_sum":
        body["items"][0]["weight"] = "1.00000000"
        body["sections"][0]["weight"] = "1.00000000"
    elif algorithm == "capped_sum":
        body["sections"][0]["cap"] = "3"
        body["sections"][0]["score_range"]["maximum"] = "3"
        body["overall_cap"] = "2"
        body["overall_score_range"]["maximum"] = "2"
    else:
        body["sections"][0]["aggregation_rule_text"] = "Synthetic original non-executable rule"
        body["sections"][0]["ambiguity_reason"] = (
            "Requires external comparison not available in phase A"
        )
        body["overall_rule_text"] = "Synthetic overall non-executable rule"
        body["items"][0].update(
            assessment_mode="ambiguous",
            score_range=None,
            ambiguity_reason="Original wording does not give numeric bounds",
        )
    response = await case["api"].post(base(case) + "/revisions", headers=case["header"], json=body)
    assert response.status_code == 200, response.text
    revised = await confirm_contents(case, response.json()["data"])
    assert revised["rubric"]["completeness"]["complete"], revised
    response = await case["api"].post(
        base(case, revised) + "/decisions",
        headers=case["header"],
        json=decision(revised, revised["rubric"]),
    )
    assert response.status_code == 200, response.text
    confirmed = response.json()["data"]
    assert confirmed["state"] == "confirmed"
    assert confirmed["overall_aggregation_assessable"] == (
        algorithm in {"weighted_sum", "capped_sum"}
    )
    assert all("estimated_score" not in row for row in revised["items"])


async def test_reclassification_resets_subject_confirmation_and_coverage_reopen(rubric_case):
    case = rubric_case
    complete = await confirm_contents(case)
    role(case, "admin")
    item = complete["items"][0]
    body = decision(complete, item)
    body.pop("action")
    response = await case["api"].post(
        f"{base(case)}/items/{item['id']}/classification",
        headers=case["header"],
        json={**body, "review_domain": "technical"},
    )
    assert response.status_code == 200, response.text
    report = await show(case)
    assert report["items"][0]["state"] == "candidate" and report["items"][0]["confirmed_by"] is None
    assert not report["rubric"]["completeness"]["complete"]
    role(case, "bidder")
    coverage = report["coverage"][0]
    response = await case["api"].post(
        f"{base(case)}/coverage/{coverage['requirement_id']}/decisions",
        headers=case["header"],
        json=decision(report, coverage, "reopen"),
    )
    assert response.status_code == 200, response.text
    pending = await show(case)
    assert pending["coverage"][0]["disposition"] == "pending"
    assert pending["coverage"][0]["rubric_item_ids"] == []


@pytest.fixture
async def many_rubric_case(tenants, tmp_path, admin_engine, monkeypatch):
    from app.schemas.contracts import Category
    from test_check import check_client
    from test_check_combined import semantic_llm
    from test_response_cards import create_tender
    from test_score_api import (
        RubricVendor,
        finish_rubric,
        install_rubric_resolver,
        preview_rubric,
        submit_rubric,
    )

    async with check_client(tenants, tmp_path) as (api, app, headers, provider):
        original = provider._extract

        async def all_scoring(chunks, schema):
            result = await original(chunks, schema)
            for item in result.extraction.items:
                item.category = Category.scoring
            return result

        monkeypatch.setattr(provider, "_extract", all_scoring)
        task, document, extraction, requirements = await create_tender(
            api, app, headers[0], tmp_path, suffix="multi-rubric", confirmed=True
        )
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "bidder")
        vendor = RubricVendor()
        install_rubric_resolver(monkeypatch, semantic_llm(tmp_path, vendor))
        case = {
            "api": api,
            "app": app,
            "headers": headers,
            "header": headers[0],
            "tenants": tenants,
            "tmp_path": tmp_path,
            "admin_engine": admin_engine,
            "task": task,
            "document": document,
            "extraction": extraction,
            "requirements": requirements,
            "vendor": vendor,
        }
        preview = await preview_rubric(case)
        submitted = await submit_rubric(case, preview)
        assert submitted.status_code == 200, submitted.text
        terminal = await finish_rubric(case, submitted.json()["data"])
        assert terminal["status"] == "succeeded", terminal
        response = await api.get(
            f"/tasks/{task}/score-rubrics/{terminal['result']['rubric_id']}", headers=headers[0]
        )
        assert response.status_code == 200, response.text
        yield {**case, "rubric": response.json()["data"]}


async def test_coverage_duplicates_cycles_exclusions_and_foreign_mapping(many_rubric_case):
    case = many_rubric_case
    original = await classify_all(case)
    assert len(original["coverage"]) >= 4
    body = replacement(original)
    first = body["items"][0]
    body["items"] = [first]
    body["sections"][0]["score_range"]["maximum"] = "5"
    body["overall_score_range"]["maximum"] = "5"
    response = await case["api"].post(base(case) + "/revisions", headers=case["header"], json=body)
    assert response.status_code == 200, response.text
    report = await classify_all(case, response.json()["data"])
    for collection in ("sections", "items"):
        for entry in report[collection]:
            response = await case["api"].post(
                f"{base(case, report)}/{collection}/{entry['id']}/decisions",
                headers=case["header"],
                json=decision(report, entry),
            )
            assert response.status_code == 200, response.text
    canonical = first["requirement_id"]
    extras = [
        entry["requirement_id"]
        for entry in report["coverage"]
        if entry["requirement_id"] != canonical
    ]

    async def cover(requirement_id, action, **values):
        current = await show(case, report)
        entry = next(
            entry for entry in current["coverage"] if entry["requirement_id"] == requirement_id
        )
        return await case["api"].post(
            f"{base(case, report)}/coverage/{requirement_id}/decisions",
            headers=case["header"],
            json=decision(current, entry, action, **values),
        )

    mapped = await cover(canonical, "mapped", rubric_item_ids=[report["items"][0]["id"]])
    assert mapped.status_code == 200, mapped.text
    wrong = await cover(extras[0], "mapped", rubric_item_ids=[report["items"][0]["id"]])
    assert (
        wrong.status_code == 409
        and wrong.json()["data"]["error"]["code"] == "invalid_coverage_mapping"
    )
    for index, requirement_id in enumerate(extras):
        response = await cover(
            requirement_id,
            "duplicate",
            canonical_requirement_id=extras[1 - index] if index < 2 else canonical,
        )
        assert response.status_code == 200, response.text
    cyclic = await show(case, report)
    assert "duplicate_coverage_cycle" in cyclic["rubric"]["completeness"]["normalization_errors"]
    response = await case["api"].post(
        base(case, cyclic) + "/decisions",
        headers=case["header"],
        json=decision(cyclic, cyclic["rubric"]),
    )
    assert response.status_code == 409
    assert (
        await cover(extras[1], "duplicate", canonical_requirement_id=canonical)
    ).status_code == 200
    assert (await cover(extras[-1], "excluded")).status_code == 200
    fixed = await show(case, report)
    assert fixed["rubric"]["completeness"]["complete"], fixed
    response = await case["api"].post(
        base(case, fixed) + "/decisions",
        headers=case["header"],
        json=decision(fixed, fixed["rubric"]),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["state"] == "confirmed"


async def test_mixed_responsibility_revision_preserves_other_domain(rubric_case):
    case = rubric_case
    report = await classify_all(case)
    role(case, "admin")
    item = report["items"][0]
    body = decision(report, item)
    body.pop("action")
    response = await case["api"].post(
        f"{base(case)}/items/{item['id']}/classification",
        headers=case["header"],
        json={**body, "review_domain": "technical"},
    )
    assert response.status_code == 200, response.text
    report = await show(case)
    role(case, "bidder")
    body = replacement(report)
    body["items"][0]["title"] = "Unauthorized cross-domain rewrite"
    denied = await case["api"].post(base(case) + "/revisions", headers=case["header"], json=body)
    assert denied.status_code == 403
    body = replacement(report)
    body["sections"][0]["title"] = "Allowed commercial section correction"
    revised = await case["api"].post(base(case) + "/revisions", headers=case["header"], json=body)
    assert revised.status_code == 200, revised.text
    new = revised.json()["data"]
    assert new["items"][0]["title"] == report["items"][0]["title"]
    assert new["items"][0]["review_domain"] is None and new["sections"][0]["review_domain"] is None


@pytest.mark.parametrize(
    "failure",
    [
        "duplicate_section_order",
        "duplicate_item_fingerprint",
        "missing_aggregation_limitation",
        "missing_aggregation_rule_text",
    ],
)
async def test_additional_normalization_failures_are_reviewable(rubric_case, failure):
    case = rubric_case
    report = await classify_all(case)
    body = replacement(report)
    if failure == "duplicate_section_order":
        duplicate = copy.deepcopy(body["sections"][0])
        duplicate.update(
            source_section_id=None,
            key="second",
            aggregation="capped_sum",
            cap="3",
            score_range={"minimum": "0", "maximum": "3"},
        )
        body["sections"].append(duplicate)
    elif failure == "duplicate_item_fingerprint":
        duplicate = copy.deepcopy(body["items"][0])
        duplicate.update(source_item_id=None, key="renamed", title="Renamed duplicate", order=2)
        duplicate["score_range"] = {"minimum": "0.00000000", "maximum": "5.00000000"}
        body["items"].append(duplicate)
    else:
        body["sections"][0]["aggregation"] = "formula"
        body["sections"][0]["aggregation_rule_text"] = (
            None if failure == "missing_aggregation_rule_text" else "Original unsupported formula"
        )
    response = await case["api"].post(base(case) + "/revisions", headers=case["header"], json=body)
    if failure == "duplicate_item_fingerprint":
        assert response.status_code == 409 and response.json()["data"]["error"]["code"] == failure
    elif failure == "missing_aggregation_rule_text":
        assert response.status_code == 422
    else:
        assert response.status_code == 200, response.text
        if failure == "missing_aggregation_limitation":
            revised = await classify_all(case, response.json()["data"])
            section = revised["sections"][0]
            denied = await case["api"].post(
                f"{base(case, revised)}/sections/{section['id']}/decisions",
                headers=case["header"],
                json=decision(revised, section),
            )
            assert denied.status_code == 409
            assert denied.json()["data"]["error"]["code"] == "rubric_incomplete"
        else:
            revised = await confirm_contents(case, response.json()["data"])
        assert failure in revised["rubric"]["completeness"]["normalization_errors"]
        response = await case["api"].post(
            base(case, revised) + "/decisions",
            headers=case["header"],
            json=decision(revised, revised["rubric"]),
        )
        assert response.status_code == 409


async def test_human_review_rejects_sensitive_prose_and_preserves_coverage_proposal(rubric_case):
    from app.models.score import ScoreRubricRevisionEvent

    case = rubric_case
    report = await classify_all(case)
    sensitive = "联系电话：13800138000"
    item = report["items"][0]
    response = await case["api"].post(
        f"{base(case)}/items/{item['id']}/decisions",
        headers=case["header"],
        json=decision(report, item, reason=sensitive),
    )
    assert (
        response.status_code == 409
        and response.json()["data"]["error"]["code"] == "sensitive_review_text"
    )
    assert sensitive not in response.text
    body = replacement(report)
    body["coverage"][0].update(
        disposition="mapped",
        rubric_item_keys=[body["items"][0]["key"]],
        reason="Synthetic new-version coverage proposal",
    )
    response = await case["api"].post(base(case) + "/revisions", headers=case["header"], json=body)
    assert response.status_code == 200, response.text
    revised = response.json()["data"]
    assert revised["coverage"][0]["disposition"] == "pending"
    async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
        event = await session.scalar(
            select(ScoreRubricRevisionEvent).where(
                ScoreRubricRevisionEvent.rubric_id == UUID(revised["rubric"]["id"])
            )
        )
        assert (
            event is not None
            and event.replacement_snapshot["coverage"][0]["disposition"] == "mapped"
        )
        assert len(event.snapshot_sha256) == 64
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "score_rubric.revised")
        )
        assert audit is not None and audit.details["snapshot_sha256"] == event.snapshot_sha256
        assert "replacement_snapshot" not in audit.details


async def test_same_org_wrong_task_routes_are_not_found(rubric_case):
    from test_response_cards import create_tender

    case = rubric_case
    report = await classify_all(case)
    other_task, _, _, _ = await create_tender(
        case["api"],
        case["app"],
        case["header"],
        case["tmp_path"],
        suffix="wrong-task-rubric",
        confirmed=True,
    )
    prefix = f"/tasks/{other_task}/score-rubrics/{report['rubric']['id']}"
    for path in (prefix, prefix + "/history"):
        assert (await case["api"].get(path, headers=case["header"])).status_code == 404
    calls = [("decisions", decision(report, report["rubric"])), ("revisions", replacement(report))]
    for collection in ("sections", "items"):
        row = report[collection][0]
        classify = decision(report, row)
        classify.pop("action")
        calls.extend(
            [
                (
                    f"{collection}/{row['id']}/classification",
                    {**classify, "review_domain": "commercial"},
                ),
                (f"{collection}/{row['id']}/decisions", decision(report, row)),
            ]
        )
    row = report["coverage"][0]
    calls.append(
        (
            f"coverage/{row['requirement_id']}/decisions",
            decision(report, row, "mapped", rubric_item_ids=[report["items"][0]["id"]]),
        )
    )
    for suffix, body in calls:
        response = await case["api"].post(prefix + "/" + suffix, headers=case["header"], json=body)
        assert response.status_code == 404, response.text


async def test_weighted_bounds_round_half_up_only_at_declared_node_boundary(rubric_case):
    case = rubric_case
    report = await classify_all(case)
    body = replacement(report)
    body["sections"][0].update(
        aggregation="weighted_sum", score_range={"minimum": "0", "maximum": "0.16666667"}
    )
    body["overall_score_range"] = {"minimum": "0", "maximum": "0.16666667"}
    body["items"][0].update(weight="0.5", score_range={"minimum": "0", "maximum": "0.33333333"})
    second = copy.deepcopy(body["items"][0])
    second.update(
        source_item_id=None,
        key="separate-weighted-boundary",
        order=2,
        rule_text="Synthetic separately reviewed zero-score boundary",
        score_range={"minimum": "0", "maximum": "0"},
    )
    body["items"].append(second)
    response = await case["api"].post(base(case) + "/revisions", headers=case["header"], json=body)
    assert response.status_code == 200, response.text
    revised = await confirm_contents(case, response.json()["data"])
    assert revised["rubric"]["completeness"]["complete"], revised
    response = await case["api"].post(
        base(case, revised) + "/decisions",
        headers=case["header"],
        json=decision(revised, revised["rubric"]),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["state"] == "confirmed"
