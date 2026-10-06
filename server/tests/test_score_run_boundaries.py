"""Score execution boundaries that complement the main worker acceptance chain.

Failure inventory: a later provider batch can stop after earlier paid results;
billing settlement can fail after a vendor response; a confirmed card can advance
past the revision fixed by a DraftRun; and an otherwise valid score token or foreign
tenant can try to read or cancel another score job. These cases must respectively
publish an explicit partial report, publish no report, reject the stale draft, and
preserve both scope enforcement and tenant-hidden 404 behavior.
"""

import copy
import json
from datetime import date
from uuid import UUID

import httpx
from app.models.entities import UsageRecord, VendorCall
from app.models.score import ScoreReport
from app.providers.base import ProviderFailure
from app.providers.scoring import HTTPScoreProvider, score_provider
from app.services import billing, score_execution, score_run_inputs, score_semantic
from app.services.auth import ROLE_SCOPES, Identity
from sqlalchemy import func, select
from test_card_generation import token_header
from test_check_combined import platform_llm, seed_platform, semantic_llm
from test_response_cards import require_action, set_role
from test_score_api import rubric_case as rubric_case
from test_score_api import rubric_input_case as rubric_input_case
from test_score_review import base, confirm_contents, decision, replacement, show
from test_score_run import (
    ScoreVendor,
    counts,
    finish_score,
    install_score_resolver,
    preview_score,
    submit_score,
)
from test_score_run import score_case as score_case


class SecondBatchRefusalVendor(ScoreVendor):
    """Return one valid score batch, then a settled provider refusal."""

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if len(self.requests) != 1:
            return await super().__call__(request)
        body = json.loads(request.content)
        self.requests.append(json.loads(body["messages"][-1]["content"]))
        return httpx.Response(
            200,
            json={
                "model": "synthetic-check-model",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": "{}",
                            "refusal": "Synthetic second-batch refusal",
                        },
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )


def role(case, value: str) -> None:
    set_role(
        case["admin_engine"],
        case["tenants"]["orgs"][0],
        case["tenants"]["users"][0],
        value,
    )


async def split_and_confirm_rubric(case) -> dict:
    body = replacement(case["rubric"])
    assert len(body["sections"]) == len(body["items"]) == 1
    second = copy.deepcopy(body["items"][0])
    second.update(
        source_item_id=None,
        key="separate-delivery-support",
        title="Separate delivery support",
        rule_text="Award a separate 0 to 5 points for confirmed delivery support.",
        order=2,
    )
    body["items"].append(second)
    body["sections"][0]["score_range"]["maximum"] = "10"
    body["overall_score_range"]["maximum"] = "10"
    response = await case["api"].post(base(case) + "/revisions", headers=case["header"], json=body)
    assert response.status_code == 200, response.text
    revised = await confirm_contents(case, response.json()["data"])
    confirmed = await case["api"].post(
        base(case, revised) + "/decisions",
        headers=case["header"],
        json=decision(revised, revised["rubric"]),
    )
    assert confirmed.status_code == 200, confirmed.text
    return await show(case, revised)


async def one_item_batch_budget(case, rubric: dict) -> int:
    org_id = case["tenants"]["orgs"][0]
    actor = Identity(
        case["tenants"]["users"][0],
        org_id,
        set(ROLE_SCOPES["bidder"]),
        "bidder",
    )
    async with case["app"].state.db.transaction(org_id) as session:
        fixed = await score_run_inputs.snapshot(
            session,
            actor,
            UUID(case["task"]),
            UUID(case["draft_id"]),
            UUID(rubric["rubric"]["id"]),
            date(2026, 10, 5),
        )
        await score_execution.prepare(
            session,
            fixed,
            case["score_llm"],
            None,
            case["app"].state.processor.settings,
        )
    request = score_semantic.provider_request(fixed.secret["outbound"])
    assert request is not None and len(request.items) == 2
    adapter = score_provider(case["score_llm"])
    assert isinstance(adapter, HTTPScoreProvider)
    singles = [
        len(adapter._request_for(request, [index]).model_dump_json())
        for index in range(len(request.items))
    ]
    together = len(adapter._request_for(request, [0, 1]).model_dump_json())
    budget = max(1000, *singles)
    assert budget < together
    return budget


async def test_later_refused_batch_publishes_partial_report(score_case, monkeypatch):
    case = score_case
    rubric = await split_and_confirm_rubric(case)
    case["rubric"] = rubric
    budget = await one_item_batch_budget(case, rubric)
    vendor = SecondBatchRefusalVendor()
    llm = semantic_llm(
        case["tmp_path"],
        vendor,
        score_batch_chars=budget,
        llm_concurrency=1,
    )
    install_score_resolver(monkeypatch, llm)

    preview = await preview_score(case)
    accepted = await submit_score(case, preview)
    assert accepted.status_code == 200, accepted.text
    terminal = await finish_score(case, accepted.json()["data"])

    assert terminal["status"] == "succeeded", terminal
    assert len(vendor.requests) == 2
    assert terminal["result"]["completion"] == "partial"
    assert terminal["result"]["assessed_items"] == 1
    assert terminal["result"]["unassessable_items"] == 1
    assert terminal["result"]["estimated_total"] is None
    assert terminal["result"]["stop_reason"] == "provider_refused"
    response = await case["api"].get(
        f"/tasks/{case['task']}/scores/{terminal['result']['report_id']}",
        headers=case["header"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is False
    assert response.json()["data"]["report"]["completion"] == "partial"


async def test_billing_charge_failure_is_visible_and_publishes_no_report(score_case, monkeypatch):
    case = score_case
    await seed_platform(
        case["admin_engine"], case["app"].state.processor.settings, case["tenants"]["orgs"][0]
    )
    install_score_resolver(
        monkeypatch,
        platform_llm(case["app"].state.processor.settings, case["score_vendor"]),
    )

    async def fail_charge(*args, **kwargs):
        raise ProviderFailure(
            "Synthetic billing settlement failure", code="usage_accounting_failed"
        )

    monkeypatch.setattr(billing, "charge_usage", fail_charge)
    preview = await preview_score(case)
    accepted = await submit_score(case, preview)
    assert accepted.status_code == 200, accepted.text
    terminal = await finish_score(case, accepted.json()["data"])
    job_id = UUID(terminal["id"])

    assert terminal["status"] == "failed"
    assert terminal["error"]["code"] == "usage_accounting_failed"
    async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(ScoreReport).where(ScoreReport.job_id == job_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(UsageRecord).where(UsageRecord.job_id == job_id)
            )
            == 0
        )
        calls = list(
            (await session.scalars(select(VendorCall).where(VendorCall.job_id == job_id))).all()
        )
        assert len(calls) == 1 and calls[0].state == "unknown"


async def test_card_revision_makes_fixed_draft_stale_before_score_call(score_case):
    case = score_case
    preview = await preview_score(case)
    role(case, "technical")
    reopened = await require_action(
        case["api"],
        case["header"],
        case["support_card"],
        "reopen",
        reason="Revise the response after the fixed draft was assembled.",
    )
    content = copy.deepcopy(reopened["content"])
    content["response_text"] += " Revised after the draft snapshot."
    revised = await case["api"].put(
        f"/cards/{reopened['id']}",
        headers=case["header"],
        json={"expected_revision": reopened["revision"], "content": content},
    )
    assert revised.status_code == 200, revised.text
    role(case, "bidder")

    refused = await submit_score(case, preview)
    assert refused.status_code == 409, refused.text
    assert refused.json()["data"]["error"]["code"] == "score_stale_draft"
    assert case["score_vendor"].requests == []


async def test_score_token_scopes_and_foreign_job_are_enforced(score_case):
    case = score_case
    role(case, "admin")
    agent = await token_header(
        case["api"],
        case["header"],
        scopes=[
            "task:read",
            "draft:read",
            "card:read",
            "score:read",
            "score:run",
            "job:read",
            "job:cancel",
        ],
    )
    role(case, "bidder")
    preview_response = await case["api"].post(
        f"/tasks/{case['task']}/scores/preview",
        headers=agent,
        json={
            "draft_id": case["draft_id"],
            "rubric_id": case["rubric"]["rubric"]["id"],
            "assessment_date": "2026-10-05",
            "dry_run": True,
        },
    )
    assert preview_response.status_code == 200, preview_response.text
    accepted = await case["api"].post(
        f"/tasks/{case['task']}/scores",
        headers=agent,
        json={
            "draft_id": case["draft_id"],
            "rubric_id": case["rubric"]["rubric"]["id"],
            "assessment_date": "2026-10-05",
            "expected_input_hash": preview_response.json()["data"]["input"]["input_hash"],
        },
    )
    assert accepted.status_code == 200, accepted.text
    job_id = accepted.json()["data"]["job_id"]
    assert (await case["api"].get(f"/jobs/{job_id}", headers=agent)).status_code == 200

    set_role(
        case["admin_engine"],
        case["tenants"]["orgs"][1],
        case["tenants"]["users"][1],
        "bidder",
    )
    foreign = case["headers"][1]
    assert (await case["api"].get(f"/jobs/{job_id}", headers=foreign)).status_code == 404
    assert (await case["api"].post(f"/jobs/{job_id}/cancel", headers=foreign)).status_code == 404

    cancelled = await case["api"].post(f"/jobs/{job_id}/cancel", headers=agent)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["data"]["status"] == "cancelled"


async def test_score_context_limit_is_a_read_only_preview_blocker(score_case, monkeypatch):
    case = score_case
    vendor = ScoreVendor()
    install_score_resolver(
        monkeypatch,
        semantic_llm(
            case["tmp_path"],
            vendor,
            score_batch_chars=1000,
            llm_concurrency=1,
        ),
    )
    before = await counts(case)

    preview = await preview_score(case)

    assert preview["admission_blocker"] == "score_context_limit"
    assert preview["cost_basis_reason"] == "context_limit"
    assert vendor.requests == []
    assert await counts(case) == before

    refused = await submit_score(case, preview)
    assert refused.status_code == 409, refused.text
    assert refused.json()["data"]["error"]["code"] == "score_context_limit"
    after = await counts(case)
    for table in ("jobs", "usage_records", "vendor_calls", "score_reports"):
        assert after[table] == before[table]
    assert vendor.requests == []


async def test_historical_report_remains_readable_after_rubric_revision(score_case):
    case = score_case
    preview = await preview_score(case)
    accepted = await submit_score(case, preview)
    assert accepted.status_code == 200, accepted.text
    terminal = await finish_score(case, accepted.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    report_id = terminal["result"]["report_id"]

    revised = await case["api"].post(
        base(case) + "/revisions",
        headers=case["header"],
        json=replacement(case["rubric"]),
    )
    assert revised.status_code == 200, revised.text

    historical = await case["api"].get(
        f"/tasks/{case['task']}/scores/{report_id}", headers=case["header"]
    )
    assert historical.status_code == 200, historical.text
    report = historical.json()["data"]["report"]
    assert report["validity"] == "stale"
    assert "score_input_changed" in report["invalidation_codes"]
    assert "score_input_changed" in historical.json()["warnings"]


async def test_report_labels_follow_current_confidential_library(score_case):
    from test_confidential_values import add_field, set_value

    case = score_case
    title = case["rubric"]["sections"][0]["title"]
    field = await add_field(
        case["api"], case["header"], "score_label", "Private scoring label", "contact", "task"
    )
    await set_value(case["api"], case["header"], field, title, case["task"])
    accepted = await submit_score(case, await preview_score(case))
    assert accepted.status_code == 200, accepted.text
    terminal = await finish_score(case, accepted.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    shown = await case["api"].get(
        f"/tasks/{case['task']}/scores/{terminal['result']['report_id']}", headers=case["header"]
    )
    assert shown.status_code == 200, shown.text
    assert title not in shown.text
    assert shown.json()["data"]["sections"][0]["title"] == "{{secret.score_label}}"


async def test_confidential_fixed_section_identifier_blocks_without_call(score_case):
    from test_confidential_values import add_field, set_value
    from test_score_run import counts

    case = score_case
    section_key = case["rubric"]["sections"][0]["key"]
    field = await add_field(
        case["api"], case["header"], "score_identifier", "Private identifier", "contact", "task"
    )
    await set_value(case["api"], case["header"], field, section_key, case["task"])
    before = await counts(case)
    preview = await preview_score(case)
    assert preview["admission_blocker"] == "sensitive_report_identifier"
    assert await counts(case) == before
    submitted = await submit_score(case, preview)
    assert submitted.status_code == 409
    assert submitted.json()["data"]["error"]["code"] == "sensitive_report_identifier"
    assert not case["score_vendor"].requests
