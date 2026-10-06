"""Score acceptance through API, worker, stored report and CLI.

Failure inventory: dry-run writes/calls, unconfirmed/superseded rubric, stale
DraftRun, cross-org routes, redaction disabled/changed, malformed citations,
bounds, refusal/truncation, cancelled/expired/taken-over attempts and billing
failure must not bypass confirmation, publication or accounting fences.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from app.models.entities import AuditLog, Job, UsageRecord, VendorCall
from app.models.score import ScoreReport
from app.schemas.score_contracts import ScorePreview, ScoreReportData
from sqlalchemy import func, select
from task_fixtures import reviewer_header
from test_check import LiveCheckClient, invoke_live_cli, publish_draft
from test_check_combined import platform_llm, seed_platform, semantic_llm
from test_response_cards import create_card, require_action, set_role
from test_score_api import rubric_case as rubric_case
from test_score_api import rubric_input_case as rubric_input_case
from test_score_api import set_redaction
from test_score_review import base, confirm_contents, decision, replacement, role, show


class ScoreVendor:
    def __init__(self):
        self.requests = []
        self.mode = "valid"
        self.entered = None
        self.release = None

    async def __call__(self, request):
        body = json.loads(request.content)
        payload = json.loads(body["messages"][-1]["content"])
        self.requests.append(payload)
        if self.entered is not None:
            self.entered.set()
            await self.release.wait()
        texts = {row["ref"]: row["text"] for row in payload["context"]["texts"]}
        items = []
        for row in payload["items"]:
            draft_ref = next(ref for ref in row["draft_refs"] if "64 GB" in texts[ref])
            item = {
                "rubric_item_id": row["rubric_item_id"],
                "outcome": "assessed",
                "estimated_score": "5",
                "reason_code": "supported",
                "reason": "Confirmed memory specification matches.",
                "deduction_reasons": [],
                "strengthening_actions": [],
                "tender_citations": [
                    {"ref": row["tender_ref"], "quote": "memory shall be at least 64 GB"}
                ],
                "draft_citations": [{"ref": draft_ref, "quote": "64 GB memory"}],
            }
            if self.mode == "bounds":
                item["estimated_score"] = "6"
            elif self.mode == "unknown_ref":
                item["draft_citations"][0]["ref"] = "private-untrusted-ref"
            elif self.mode == "deduction":
                item["estimated_score"] = "4"
            items.append(item)
        message = {"content": json.dumps({"items": items})}
        if self.mode == "refused":
            message["refusal"] = "Synthetic refusal"
        return httpx.Response(
            200,
            json={
                "model": "synthetic-check-model",
                "choices": [
                    {
                        "finish_reason": "length" if self.mode == "truncated" else "stop",
                        "message": message,
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )


def install_score_resolver(monkeypatch, llm):
    async def resolve(session, settings, job=None):
        return llm

    monkeypatch.setattr("app.services.score_execution.resolve", resolve)


@pytest.fixture
async def score_case(rubric_case, monkeypatch):
    case = rubric_case
    complete = await confirm_contents(case)
    confirmed = await case["api"].post(
        base(case) + "/decisions",
        headers=case["header"],
        json=decision(complete, complete["rubric"]),
    )
    assert confirmed.status_code == 200, confirmed.text
    case["rubric"] = await show(case)
    # A different confirmed requirement supplies the actual bid-side support;
    # the scoring requirement's own anchor remains a gap.
    _, technical_header = await reviewer_header(
        case["api"],
        case["admin_engine"],
        case["tenants"]["orgs"][0],
        UUID(case["task"]),
        "technical",
    )
    card = await create_card(
        case["api"],
        case["header"],
        case["task"],
        case["extraction"],
        case["requirements"][2],
        {
            "response_kind": "commitment",
            "response_text": "The offered appliance includes 64 GB memory and thirty-day delivery.",
            "deviation": "none",
            "deviation_note": "The delivery requirement is met.",
            "evidence": [],
        },
    )
    card = await require_action(case["api"], case["header"], card, "submit")
    card = await require_action(
        case["api"], technical_header, card, "confirm", reviewed_evidence_ids=[]
    )
    await create_card(
        case["api"],
        case["header"],
        case["task"],
        case["extraction"],
        case["requirements"][3],
        {
            "response_kind": "commitment",
            "response_text": "PRIVATE SCORE CANDIDATE MUST NOT LEAK",
            "deviation": "none",
            "deviation_note": "UNCONFIRMED PRIVATE NOTE",
            "evidence": [],
        },
    )
    draft = await publish_draft(
        case["api"], case["app"], case["header"], case["task"], case["extraction"]
    )
    role(case, "bidder")
    vendor = ScoreVendor()
    llm = semantic_llm(case["tmp_path"], vendor)
    install_score_resolver(monkeypatch, llm)
    return {
        **case,
        "draft_id": draft["draft_id"],
        "score_vendor": vendor,
        "score_llm": llm,
        "support_card": card,
    }


async def preview_score(case, **overrides):
    response = await case["api"].post(
        f"/tasks/{case['task']}/scores/preview",
        headers=case["header"],
        json={
            "draft_id": case["draft_id"],
            "rubric_id": case["rubric"]["rubric"]["id"],
            "assessment_date": "2026-10-05",
            "dry_run": True,
            **overrides,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def submit_score(case, preview, **overrides):
    return await case["api"].post(
        f"/tasks/{case['task']}/scores",
        headers=case["header"],
        json={
            "draft_id": case["draft_id"],
            "rubric_id": case["rubric"]["rubric"]["id"],
            "assessment_date": "2026-10-05",
            "expected_input_hash": preview["input"]["input_hash"],
            **overrides,
        },
    )


async def finish_score(case, accepted):
    await case["app"].state.processor(case["header"]["X-Org-Id"], accepted["job_id"])
    response = await case["api"].get(f"/jobs/{accepted['job_id']}", headers=case["header"])
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def counts(case):
    async with case["app"].state.db.transaction(UUID(case["header"]["X-Org-Id"])) as session:
        return {
            model.__tablename__: await session.scalar(select(func.count()).select_from(model))
            for model in (Job, AuditLog, UsageRecord, VendorCall, ScoreReport)
        }


async def test_score_preview_worker_cross_response_total_cache_and_cli(score_case, monkeypatch):
    from bid_cli import main as cli

    case = score_case
    before = await counts(case)
    preview = await preview_score(case)
    ScorePreview.model_validate(preview)
    assert await counts(case) == before and not case["score_vendor"].requests
    refused = await submit_score(case, preview, expected_input_hash="0" * 64)
    assert refused.status_code == 409
    accepted = await submit_score(case, preview)
    assert accepted.status_code == 200, accepted.text
    terminal = await finish_score(case, accepted.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    assert "PRIVATE SCORE CANDIDATE" not in json.dumps(case["score_vendor"].requests)
    assert "UNCONFIRMED PRIVATE NOTE" not in json.dumps(case["score_vendor"].requests)
    result = terminal["result"]
    assert result["completion"] == "complete" and Decimal(result["estimated_total"]) == 5
    path = f"/tasks/{case['task']}/scores/{result['report_id']}"
    response = await case["api"].get(path, headers=case["header"])
    assert response.status_code == 200, response.text
    report = response.json()["data"]
    ScoreReportData.model_validate(report)
    item = report["items"][0]
    assert item["anchor_partition"] == "gap" and item["response_item_ids"]
    assert {citation["kind"] for citation in item["citations"]} == {"tender", "draft"}
    assert item["anchor_response_item_id"] not in item["response_item_ids"]
    after = await counts(case)
    cached = await submit_score(case, preview)
    assert cached.json()["data"]["cached"] and await counts(case) == after
    monkeypatch.setenv("BID_SESSION", case["header"]["Authorization"].removeprefix("Bearer "))
    monkeypatch.setenv("BID_ORG", case["header"]["X-Org-Id"])
    monkeypatch.setattr(
        cli,
        "Client",
        lambda mode, server, state: LiveCheckClient(
            mode, server, state, case["app"].state.processor.settings
        ),
    )
    exit_code, cli_result = await asyncio.to_thread(
        invoke_live_cli, ["score", "show", "--task", case["task"], "--report", result["report_id"]]
    )
    assert exit_code == 0 and cli_result["data"] == report
    artifact = case["tmp_path"] / "score-acceptance.json"
    artifact.write_text(
        json.dumps(
            {"preview": preview, "report": report, "job": terminal, "cli": cli_result}, indent=2
        )
    )
    assert json.loads(artifact.read_text())["report"]["report"]["usage_record_ids"]


@pytest.mark.parametrize("mode", ["bounds", "unknown_ref", "deduction", "refused", "truncated"])
async def test_score_invalid_outputs_bill_once(score_case, monkeypatch, mode):
    case = score_case
    await seed_platform(
        case["admin_engine"], case["app"].state.processor.settings, case["tenants"]["orgs"][0]
    )
    install_score_resolver(
        monkeypatch, platform_llm(case["app"].state.processor.settings, case["score_vendor"])
    )
    case["score_vendor"].mode = mode
    preview = await preview_score(case)
    accepted = await submit_score(case, preview)
    assert accepted.status_code == 200, accepted.text
    terminal = await finish_score(case, accepted.json()["data"])
    async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
        usages = list(
            (
                await session.scalars(
                    select(UsageRecord).where(UsageRecord.job_id == UUID(terminal["id"]))
                )
            ).all()
        )
        assert len(usages) == len(case["score_vendor"].requests) == 1 and usages[0].charge > 0
    if mode not in {"refused", "truncated"}:
        assert terminal["result"]["completion"] == "partial"
        assert terminal["result"]["estimated_total"] is None
    before = await counts(case)
    await case["app"].state.processor(case["header"]["X-Org-Id"], terminal["id"])
    assert await counts(case) == before
    assert "private-untrusted-ref" not in json.dumps(terminal)


@pytest.mark.parametrize("change", ["cancel", "redaction", "lease", "takeover"])
async def test_score_late_usage_never_publishes_after_fence(score_case, change):
    case = score_case
    vendor = case["score_vendor"]
    vendor.entered, vendor.release = asyncio.Event(), asyncio.Event()
    accepted = await submit_score(case, await preview_score(case))
    assert accepted.status_code == 200, accepted.text
    job_id = accepted.json()["data"]["job_id"]
    worker = asyncio.create_task(case["app"].state.processor(case["header"]["X-Org-Id"], job_id))
    try:
        await asyncio.wait_for(vendor.entered.wait(), 10)
        if change == "cancel":
            response = await case["api"].post(f"/jobs/{job_id}/cancel", headers=case["header"])
            assert response.status_code == 200, response.text
        elif change == "redaction":
            await set_redaction(case, False, True)
        else:
            async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
                job = await session.get(Job, UUID(job_id))
                if change == "lease":
                    job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
                else:
                    job.run_id = uuid4()
    finally:
        vendor.release.set()
        await asyncio.wait_for(worker, 10)
    async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
        assert await session.scalar(select(func.count()).select_from(ScoreReport)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(UsageRecord)
                .where(UsageRecord.job_id == UUID(job_id))
            )
            == 1
        )


async def test_score_redaction_off_and_foreign_routes(score_case):
    case = score_case
    foreign = case["headers"][1]
    set_role(
        case["admin_engine"], case["tenants"]["orgs"][1], case["tenants"]["users"][1], "bidder"
    )
    preview = await preview_score(case)
    accepted = await submit_score(case, preview)
    terminal = await finish_score(case, accepted.json()["data"])
    report_id = terminal["result"]["report_id"]
    prefix = f"/tasks/{case['task']}/scores"
    for path in (prefix, prefix + f"/{report_id}"):
        assert (await case["api"].get(path, headers=foreign)).status_code == 404
    for path, extra in (
        (prefix + "/preview", {"dry_run": True}),
        (prefix, {"expected_input_hash": preview["input"]["input_hash"]}),
    ):
        response = await case["api"].post(
            path,
            headers=foreign,
            json={
                "draft_id": case["draft_id"],
                "rubric_id": case["rubric"]["rubric"]["id"],
                "assessment_date": "2026-10-05",
                **extra,
            },
        )
        assert response.status_code == 404, response.text
    await set_redaction(case, False)
    before = await counts(case)
    blocked = await preview_score(case)
    assert blocked["admission_blocker"] == "redaction_required" and await counts(case) == before
    refused = await submit_score(case, blocked)
    assert (
        refused.status_code == 409
        and refused.json()["data"]["error"]["code"] == "redaction_required"
    )


async def test_score_stale_draft_and_revised_rubric_refuse(score_case):
    case = score_case
    preview = await preview_score(case)
    report = case["rubric"]
    revised = await case["api"].post(
        base(case) + "/revisions", headers=case["header"], json=replacement(report)
    )
    assert revised.status_code == 200, revised.text
    refused = await submit_score(case, preview)
    assert refused.status_code == 409 and not case["score_vendor"].requests
    new_rubric = revised.json()["data"]["rubric"]["id"]
    response = await case["api"].post(
        f"/tasks/{case['task']}/scores/preview",
        headers=case["header"],
        json={
            "draft_id": case["draft_id"],
            "rubric_id": new_rubric,
            "assessment_date": "2026-10-05",
            "dry_run": True,
        },
    )
    assert response.status_code == 409
