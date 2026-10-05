"""Phase A acceptance through the API, durable worker, ledger and CLI.

Failure cases: preview writes/calls; stale hash admission; foreign org reads/writes;
non-scoring input or condition leakage; duplicate cache settlement; late cancellation;
refused/truncated responses losing usage; and attempts publishing after input changes.
All vendor traffic uses MockTransport and artifacts contain synthetic data only.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from app.jobs.execution import JobExecution
from app.models.entities import (
    AuditLog,
    BalanceEntry,
    Chunk,
    Job,
    OrgBalance,
    Requirement,
    UsageRecord,
    VendorCall,
)
from app.models.score import ScoreRubricSet
from app.schemas.contracts import ProviderUsage
from app.schemas.score_contracts import RubricGenerateResult, RubricPreview, RubricReportData
from sqlalchemy import func, select
from test_check import LiveCheckClient, check_client, invoke_live_cli
from test_check_combined import platform_llm, seed_platform, semantic_llm
from test_response_cards import create_tender, set_role


class RubricVendor:
    def __init__(self):
        self.requests: list[dict] = []
        self.bodies: list[dict] = []
        self.mode = "valid"
        self.entered: asyncio.Event | None = None
        self.release: asyncio.Event | None = None

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        payload = json.loads(body["messages"][-1]["content"])
        self.requests.append(payload)
        if self.entered is not None and self.release is not None:
            self.entered.set()
            await self.release.wait()
        refs = {
            entry["ref"]: entry["text"].split("\n招标原文：\n", 1)[-1]
            for entry in payload["context"]["texts"]
        }
        rows = payload["requirements"]
        anchor = rows[0]
        source = {"ref": anchor["tender_ref"], "quote": refs[anchor["tender_ref"]]}
        output = {
            "sections": [
                {
                    "key": "technical",
                    "title": "Synthetic technical score",
                    "order": 1,
                    "aggregation": "sum",
                    "aggregation_rule_text": None,
                    "score_range": {"minimum": "0", "maximum": str(5 * len(rows))},
                    "weight": None,
                    "cap": None,
                    "included_in_overall_total": True,
                    "ambiguity_reason": None,
                    "citations": [source],
                }
            ],
            "items": [
                {
                    "requirement_id": row["requirement_id"],
                    "section_key": "technical",
                    "key": f"item-{index}",
                    "title": f"Synthetic score item {index}",
                    "rule_text": refs[row["tender_ref"]],
                    "order": index,
                    "assessment_mode": "model_assessable",
                    "score_range": {"minimum": "0", "maximum": "5"},
                    "weight": None,
                    "ambiguity_reason": None,
                    "citations": [{"ref": row["tender_ref"], "quote": refs[row["tender_ref"]]}],
                }
                for index, row in enumerate(rows, 1)
            ],
            "overall_aggregation": "sum",
            "overall_rule_text": None,
            "overall_score_range": {"minimum": "0", "maximum": str(5 * len(rows))},
            "overall_cap": None,
        }
        if self.mode in {"whole_table", "whole_table_malformed"}:
            technical = [row for row in rows if "Technical" in refs[row["tender_ref"]]]
            commercial = [row for row in rows if "Commercial" in refs[row["tender_ref"]]]
            assert technical and commercial, "Every call must retain both sections"
            output["sections"] = []
            for order, (key, members) in enumerate(
                (("technical", technical), ("commercial", commercial)), 1
            ):
                output["sections"].append(
                    {
                        "key": key,
                        "title": key,
                        "order": order,
                        "aggregation": "sum",
                        "aggregation_rule_text": None,
                        "score_range": {"minimum": "0", "maximum": str(5 * len(members))},
                        "weight": None,
                        "cap": None,
                        "included_in_overall_total": True,
                        "ambiguity_reason": None,
                        "citations": [
                            {
                                "ref": members[0]["tender_ref"],
                                "quote": refs[members[0]["tender_ref"]],
                            }
                        ],
                    }
                )
            for item, row in zip(output["items"], rows, strict=True):
                item["section_key"] = "technical" if row in technical else "commercial"
            output["overall_aggregation"] = "capped_sum"
            output["overall_cap"] = "12"
            output["overall_score_range"] = {"minimum": "0", "maximum": "12"}
            if self.mode == "whole_table_malformed" and len(self.requests) == 1:
                output.pop("overall_aggregation")
        if self.mode == "unknown_ref":
            output["items"][0]["citations"][0]["ref"] = "untrusted-sensitive-ref"
        message = {"content": json.dumps(output)}
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


def install_rubric_resolver(monkeypatch, llm):
    async def resolve(session, settings, job=None):
        return llm

    monkeypatch.setattr("app.services.score_generation.resolve", resolve)


async def preview_rubric(case, **overrides):
    response = await case["api"].post(
        f"/tasks/{case['task']}/score-rubrics/preview",
        headers=case["header"],
        json={"extraction_job_id": case["extraction"], "dry_run": True, **overrides},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def submit_rubric(case, preview, **overrides):
    return await case["api"].post(
        f"/tasks/{case['task']}/score-rubrics",
        headers=case["header"],
        json={
            "extraction_job_id": case["extraction"],
            "expected_input_hash": preview["input"]["input_hash"],
            **overrides,
        },
    )


async def finish_rubric(case, accepted):
    await case["app"].state.processor(case["header"]["X-Org-Id"], accepted["job_id"])
    response = await case["api"].get(f"/jobs/{accepted['job_id']}", headers=case["header"])
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def rubric_counts(case):
    async with case["app"].state.db.transaction(UUID(case["header"]["X-Org-Id"])) as session:
        counts = {
            model.__tablename__: await session.scalar(select(func.count()).select_from(model))
            for model in (Job, AuditLog, UsageRecord, VendorCall, BalanceEntry, ScoreRubricSet)
        }
        counts["balance"] = await session.scalar(select(OrgBalance.balance))
        return counts


@pytest.fixture
async def rubric_input_case(tenants, tmp_path, admin_engine, monkeypatch):
    async with check_client(tenants, tmp_path) as (api, app, headers, provider):
        task, document, extraction, requirements = await create_tender(
            api, app, headers[0], tmp_path, suffix="rubric"
        )
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "bidder")
        vendor = RubricVendor()
        llm = semantic_llm(tmp_path, vendor)
        install_rubric_resolver(monkeypatch, llm)
        yield {
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
            "llm": llm,
        }


@pytest.fixture
async def rubric_case(rubric_input_case):
    case = rubric_input_case
    preview = await preview_rubric(case)
    submitted = await submit_rubric(case, preview)
    assert submitted.status_code == 200, submitted.text
    terminal = await finish_rubric(case, submitted.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    report = await case["api"].get(
        f"/tasks/{case['task']}/score-rubrics/{terminal['result']['rubric_id']}",
        headers=case["header"],
    )
    assert report.status_code == 200, report.text
    return {**case, "preview": preview, "job": terminal, "rubric": report.json()["data"]}


async def test_rubric_preview_submit_worker_cache_and_cli_artifact(rubric_input_case, monkeypatch):
    from bid_cli import main as cli

    case = rubric_input_case
    before = await rubric_counts(case)
    preview = await preview_rubric(case)
    RubricPreview.model_validate(preview)
    assert await rubric_counts(case) == before
    assert not case["vendor"].requests
    assert preview["scoring_requirement_ids"] == [case["requirements"][0]["id"]]
    assert preview["estimate_kind"] == "first_pass_upper_bound"
    assert preview["estimated_charge"] == "0"

    stale = await submit_rubric(case, preview, expected_input_hash="0" * 64)
    assert stale.status_code == 409 and not case["vendor"].requests
    submitted = await submit_rubric(case, preview)
    assert submitted.status_code == 200, submitted.text
    terminal = await finish_rubric(case, submitted.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    result = RubricGenerateResult.model_validate(terminal["result"])
    assert result.completion == "complete" and result.candidate_items == 1
    assert result.unresolved_requirements == 0
    assert len(result.usage_record_ids) == len(case["vendor"].requests) == 1
    sent = json.dumps(case["vendor"].requests)
    assert "condition" not in sent
    assert case["requirements"][0]["id"] not in sent
    assert all(row["text"] not in sent for row in case["requirements"][1:])
    after = await rubric_counts(case)
    cached = await submit_rubric(case, preview)
    assert cached.status_code == 200, cached.text
    assert cached.json()["data"] == {
        "job_id": str(result.job_id),
        "status": "succeeded",
        "cached": True,
    }
    assert await rubric_counts(case) == after

    path = f"/tasks/{case['task']}/score-rubrics/{result.rubric_id}"
    report = await case["api"].get(path, headers=case["header"])
    assert report.status_code == 200, report.text
    RubricReportData.model_validate(report.json()["data"])
    assert report.json()["data"]["rubric"]["state"] == "candidate"
    assert (await case["api"].get(path, headers=case["headers"][1])).status_code == 404

    monkeypatch.setenv("BID_SESSION", case["header"]["Authorization"].removeprefix("Bearer "))
    monkeypatch.setenv("BID_ORG", case["header"]["X-Org-Id"])
    monkeypatch.setattr(
        cli,
        "Client",
        lambda mode, server, state: LiveCheckClient(
            mode, server, state, case["app"].state.processor.settings
        ),
    )
    exit_code, cli_body = await asyncio.to_thread(
        invoke_live_cli,
        ["score", "rubric", "show", "--task", case["task"], "--rubric", str(result.rubric_id)],
    )
    assert exit_code == 0 and cli_body["data"] == report.json()["data"]
    artifact = case["tmp_path"] / "rubric-acceptance.json"
    artifact.write_text(
        json.dumps(
            {"preview": preview, "job": terminal, "report": report.json(), "cli": cli_body},
            indent=2,
            ensure_ascii=False,
        )
    )
    assert json.loads(artifact.read_text())["job"]["result"]["usage_record_ids"]


async def add_whole_scoring_table(case):
    """Keep fixed-source integrity while extending the selected extraction's scoring table."""
    org = case["tenants"]["orgs"][0]
    async with case["app"].state.db.transaction(org) as session:
        original = await session.get(Requirement, UUID(case["requirements"][0]["id"]))
        original.category = "technical"
        for index, section in enumerate(("Technical", "Technical", "Commercial"), 6):
            text = (
                f"{section} criterion {index}: earn 5 points. " + "Supporting rule wording. " * 140
            )
            chunk = Chunk(
                id=uuid4(),
                org_id=org,
                task_id=UUID(case["task"]),
                document_id=UUID(case["document"]),
                page=index,
                seq=index,
                text=text,
            )
            session.add(chunk)
            await session.flush()
            session.add(
                Requirement(
                    id=uuid4(),
                    org_id=org,
                    task_id=chunk.task_id,
                    document_id=chunk.document_id,
                    chunk_id=chunk.id,
                    page=index,
                    quote=text,
                    text=text,
                    category="scoring",
                    starred=False,
                    condition={"must_not_be_sent": True},
                    fingerprint=hashlib.sha256(text.encode()).hexdigest(),
                    job_id=UUID(case["extraction"]),
                )
            )


async def test_rubric_whole_table_cross_section_and_overall_survive_one_request(
    rubric_input_case, monkeypatch
):
    case = rubric_input_case
    await add_whole_scoring_table(case)
    case["vendor"].mode = "whole_table"
    llm = semantic_llm(case["tmp_path"], case["vendor"], llm_batch_chars=8000)
    install_rubric_resolver(monkeypatch, llm)
    preview = await preview_rubric(case)
    assert preview["admission_blocker"] is None
    assert len(preview["scoring_requirement_ids"]) == 3
    submitted = await submit_rubric(case, preview)
    assert submitted.status_code == 200, submitted.text
    terminal = await finish_rubric(case, submitted.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    assert terminal["result"]["completion"] == "complete"
    assert terminal["result"]["candidate_items"] == 3
    assert terminal["result"]["unresolved_requirements"] == 0
    assert len(case["vendor"].requests) == len(terminal["result"]["usage_record_ids"]) == 1
    assert all(len(json.dumps(payload)) > 8000 for payload in case["vendor"].requests)
    assert all(len(payload["requirements"]) == 3 for payload in case["vendor"].requests)
    assert all(payload == case["vendor"].requests[0] for payload in case["vendor"].requests)
    shown = await case["api"].get(
        f"/tasks/{case['task']}/score-rubrics/{terminal['result']['rubric_id']}",
        headers=case["header"],
    )
    assert shown.status_code == 200, shown.text
    report = shown.json()["data"]
    assert {row["key"] for row in report["sections"]} == {"technical", "commercial"}
    assert report["rubric"]["overall_aggregation"] == "capped_sum"
    assert Decimal(report["rubric"]["overall_cap"]) == 12
    artifact = case["tmp_path"] / "rubric-whole-table.json"
    artifact.write_text(
        json.dumps(
            {
                "preview": preview,
                "job": terminal,
                "report": report,
                "requests": case["vendor"].bodies,
            },
            indent=2,
        )
    )
    assert len(json.loads(artifact.read_text())["requests"]) == 1


@pytest.mark.parametrize("mode", ["whole_table_malformed", "truncated"])
async def test_rubric_structure_failure_keeps_whole_table_and_accounts_one_call(
    rubric_input_case, monkeypatch, mode
):
    case = rubric_input_case
    await add_whole_scoring_table(case)
    case["vendor"].mode = mode
    llm = semantic_llm(case["tmp_path"], case["vendor"], llm_batch_chars=8000)
    install_rubric_resolver(monkeypatch, llm)
    preview = await preview_rubric(case)
    submitted = await submit_rubric(case, preview)
    assert submitted.status_code == 200, submitted.text
    terminal = await finish_rubric(case, submitted.json()["data"])
    assert terminal["status"] == "failed", terminal
    assert len(case["vendor"].requests) == 1
    assert len(case["vendor"].requests[0]["requirements"]) == 3
    job_id = UUID(terminal["id"])
    async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
        assert await session.scalar(select(func.count()).select_from(ScoreRubricSet)) == 0
        assert (
            await session.scalar(
                select(func.count()).select_from(UsageRecord).where(UsageRecord.job_id == job_id)
            )
            == 1
        )
    artifact = case["tmp_path"] / f"rubric-structure-failure-{mode}.json"
    artifact.write_text(json.dumps({"job": terminal, "requests": case["vendor"].bodies}, indent=2))
    assert len(json.loads(artifact.read_text())["requests"]) == 1


@pytest.mark.parametrize("overflow", ["table", "schema", "options"])
async def test_rubric_full_request_limit_rejects_preview_and_submit_without_calls(
    rubric_input_case, monkeypatch, overflow
):
    case = rubric_input_case
    if overflow == "table":
        await add_whole_scoring_table(case)
    values = {"rubric_max_request_bytes": 8000 if overflow == "table" else 2000}
    if overflow == "options":
        values = {
            "rubric_max_request_bytes": 20000,
            "llm_request_options": json.dumps({"synthetic_padding": "x" * 20000}),
        }
    llm = semantic_llm(case["tmp_path"], case["vendor"], **values)
    install_rubric_resolver(monkeypatch, llm)
    org = case["tenants"]["orgs"][0]
    async with case["app"].state.db.transaction(org) as session:
        previous_audit_ids = list((await session.scalars(select(AuditLog.id))).all())
    before = await rubric_counts(case)
    preview = await preview_rubric(case)
    assert preview["admission_blocker"] == "rubric_context_limit"
    assert await rubric_counts(case) == before
    submitted = await submit_rubric(case, preview)
    assert submitted.status_code == 409, submitted.text
    assert submitted.json()["data"]["error"]["code"] == "rubric_context_limit"
    assert not case["vendor"].requests
    after = await rubric_counts(case)
    assert after == {**before, "audit_logs": before["audit_logs"] + 1}
    # Authenticated business rejection retains one safe audit after rolling back admission.
    async with case["app"].state.db.transaction(org) as session:
        audits = list(
            (
                await session.scalars(
                    select(AuditLog).where(AuditLog.id.not_in(previous_audit_ids))
                )
            ).all()
        )
        assert len(audits) == 1
        rejected = audits[0]
        assert rejected.action == "score_rubric.failed"
        assert rejected.org_id == org
        assert rejected.object_id == UUID(case["task"])
        assert rejected.actor_user_id == case["tenants"]["users"][0]
        assert rejected.actor_token_id is None
        assert rejected.details == {
            "task_id": case["task"],
            "extraction_job_id": case["extraction"],
            "input_hash": preview["input"]["input_hash"],
            "error_code": "rubric_context_limit",
            "actor_kind": "session",
        }
        rejection_audit = {
            "action": rejected.action,
            "org_id": str(rejected.org_id),
            "object_id": str(rejected.object_id),
            "actor_user_id": str(rejected.actor_user_id),
            "actor_token_id": rejected.actor_token_id,
            "details": rejected.details,
        }
    artifact = case["tmp_path"] / f"rubric-limit-{overflow}.json"
    artifact.write_text(
        json.dumps(
            {
                "preview": preview,
                "submit": submitted.json(),
                "vendor_calls": len(case["vendor"].requests),
                "counts_before": before,
                "counts_after": after,
                "rejection_audit": rejection_audit,
            },
            indent=2,
        )
    )
    assert json.loads(artifact.read_text())["vendor_calls"] == 0


@pytest.mark.parametrize("mode", ["valid", "refused", "truncated", "unknown_ref"])
async def test_rubric_platform_calls_settle_once_even_when_output_refused(
    rubric_input_case, monkeypatch, mode
):
    case = rubric_input_case
    seed_platform(case["admin_engine"], case["tenants"]["orgs"][0])
    case["vendor"].mode = mode
    llm = platform_llm(case["tmp_path"], case["vendor"])
    install_rubric_resolver(monkeypatch, llm)
    preview = await preview_rubric(case)
    assert Decimal(preview["estimated_charge"]) > 0
    submitted = await submit_rubric(case, preview)
    assert submitted.status_code == 200, submitted.text
    terminal = await finish_rubric(case, submitted.json()["data"])
    job_id = UUID(terminal["id"])
    async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
        calls = list(
            (await session.scalars(select(VendorCall).where(VendorCall.job_id == job_id))).all()
        )
        usages = list(
            (await session.scalars(select(UsageRecord).where(UsageRecord.job_id == job_id))).all()
        )
        balance = await session.scalar(select(OrgBalance.balance))
        entries = list(
            (
                await session.scalars(
                    select(BalanceEntry).where(
                        BalanceEntry.usage_record_id.in_([row.id for row in usages])
                    )
                )
            ).all()
        )
        assert len(calls) == len(usages) == len(entries) == len(case["vendor"].requests) == 1
        assert len({(row.org_id, row.job_id, row.run_id, row.call_id) for row in usages}) == 1
        assert balance == Decimal("10") - sum((row.charge for row in usages), Decimal(0))
        assert all(row.charge > 0 for row in usages)
        settled = usages[0]
        replay = ProviderUsage(**{key: getattr(settled, key) for key in ProviderUsage.model_fields})
        run_id, call_id = settled.run_id, settled.call_id
    previous = await rubric_counts(case)
    execution = JobExecution(
        case["app"].state.processor.settings,
        case["app"].state.db,
        case["tenants"]["orgs"][0],
        job_id,
        run_id,
    )
    await execution.complete(call_id, replay)
    assert await rubric_counts(case) == previous
    await case["app"].state.processor(case["header"]["X-Org-Id"], str(job_id))
    assert await rubric_counts(case) == previous
    if mode == "valid":
        assert terminal["status"] == "succeeded" and terminal["result"]["completion"] == "complete"
    elif mode == "unknown_ref":
        assert terminal["result"]["completion"] == "partial"
        assert "untrusted-sensitive-ref" not in json.dumps(terminal)


async def test_rubric_cancel_drains_usage_and_fences_publication(rubric_input_case, monkeypatch):
    case = rubric_input_case
    seed_platform(case["admin_engine"], case["tenants"]["orgs"][0])
    install_rubric_resolver(monkeypatch, platform_llm(case["tmp_path"], case["vendor"]))
    case["vendor"].entered, case["vendor"].release = asyncio.Event(), asyncio.Event()
    preview = await preview_rubric(case)
    submitted = await submit_rubric(case, preview)
    assert submitted.status_code == 200, submitted.text
    job_id = submitted.json()["data"]["job_id"]
    worker = asyncio.create_task(case["app"].state.processor(case["header"]["X-Org-Id"], job_id))
    try:
        await asyncio.wait_for(case["vendor"].entered.wait(), 10)
        cancelled = await case["api"].post(f"/jobs/{job_id}/cancel", headers=case["header"])
        assert cancelled.status_code == 200, cancelled.text
    finally:
        case["vendor"].release.set()
        await asyncio.wait_for(worker, 10)
    terminal = await case["api"].get(f"/jobs/{job_id}", headers=case["header"])
    assert terminal.json()["data"]["status"] == "cancelled"
    counts = await rubric_counts(case)
    assert counts["score_rubric_sets"] == 0
    async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
        usages = list(
            (
                await session.scalars(select(UsageRecord).where(UsageRecord.job_id == UUID(job_id)))
            ).all()
        )
        assert len(usages) == 1 and usages[0].charge > 0
        audits = list(
            (
                await session.scalars(
                    select(AuditLog).where(AuditLog.action == "score_rubric.cancelled")
                )
            ).all()
        )
        assert len(audits) == 1


async def set_redaction(case, *enabled: bool):
    """Change the task redaction setting through the admin route, as a human would."""
    from app.models.entities import Task

    org, user = case["tenants"]["orgs"][0], case["tenants"]["users"][0]
    set_role(case["admin_engine"], org, user, "admin")
    try:
        for value in enabled:
            with case["admin_engine"].connect() as connection:
                revision = connection.execute(
                    select(Task.model_redaction_revision).where(Task.id == UUID(case["task"]))
                ).scalar_one()
            changed = await case["api"].put(
                f"/tasks/{case['task']}/model-redaction",
                headers=case["header"],
                json={"expected_revision": revision, "model_redaction_enabled": value},
            )
            assert changed.status_code == 200, changed.text
    finally:
        set_role(case["admin_engine"], org, user, "bidder")


async def test_rubric_redaction_off_is_zero_write_admission_blocker(rubric_input_case):
    case = rubric_input_case
    await set_redaction(case, False)
    before = await rubric_counts(case)
    preview = await preview_rubric(case)
    assert preview["admission_blocker"] == "redaction_required"
    assert await rubric_counts(case) == before
    submission = await submit_rubric(case, preview)
    assert (
        submission.status_code == 409
        and submission.json()["data"]["error"]["code"] == "redaction_required"
    )
    assert not case["vendor"].requests
    after = await rubric_counts(case)
    assert after["jobs"] == before["jobs"] and after["score_rubric_sets"] == 0


async def test_rubric_queue_outage_keeps_one_durable_job_and_redispatches(
    rubric_input_case, monkeypatch
):
    case = rubric_input_case
    queue = case["app"].state.queue
    normal_enqueue = queue.enqueue

    async def unavailable(org_id, job_id):
        raise OSError("Synthetic unavailable queue")

    preview = await preview_rubric(case)
    monkeypatch.setattr(queue, "enqueue", unavailable)
    refused = await submit_rubric(case, preview)
    assert refused.status_code == 503
    body = refused.json()
    assert body["data"]["error"]["exit_code"] == 3
    job_id = body["data"]["job_id"]
    assert not case["vendor"].requests
    before = await rubric_counts(case)
    monkeypatch.setattr(queue, "enqueue", normal_enqueue)
    accepted = await submit_rubric(case, preview)
    assert accepted.status_code == 200 and accepted.json()["data"]["job_id"] == job_id
    assert accepted.json()["data"]["cached"]
    assert await rubric_counts(case) == before
    terminal = await finish_rubric(case, accepted.json()["data"])
    assert terminal["status"] == "succeeded", terminal


async def test_rubric_cancel_retry_and_live_expired_lease(rubric_input_case):
    case = rubric_input_case
    preview = await preview_rubric(case)
    accepted = await submit_rubric(case, preview)
    assert accepted.status_code == 200, accepted.text
    job_id = accepted.json()["data"]["job_id"]
    cancelled = await case["api"].post(f"/jobs/{job_id}/cancel", headers=case["header"])
    assert cancelled.status_code == 200
    cached = await submit_rubric(case, preview)
    assert cached.json()["data"]["status"] == "cancelled"
    retried = await submit_rubric(case, preview, retry=True)
    assert retried.status_code == 200 and retried.json()["data"]["status"] == "queued"
    assert retried.json()["data"]["job_id"] == job_id
    async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
        job = await session.get(Job, UUID(job_id))
        job.status, job.run_id = "running", uuid4()
        job.lease_until = datetime.now(UTC) + timedelta(minutes=5)
    queue_count = len(case["app"].state.queue.calls)
    live = await submit_rubric(case, preview, retry=True)
    assert live.json()["data"]["status"] == "running"
    assert len(case["app"].state.queue.calls) == queue_count
    await case["app"].state.processor(case["header"]["X-Org-Id"], job_id)
    assert not case["vendor"].requests
    async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
        job = await session.get(Job, UUID(job_id))
        job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    takeover = await submit_rubric(case, preview, retry=True)
    assert takeover.status_code == 200 and takeover.json()["data"]["status"] == "queued"
    terminal = await finish_rubric(case, takeover.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    assert len(case["vendor"].requests) == 1


@pytest.mark.parametrize("change", ["redaction_revision", "expired_lease", "run_takeover"])
async def test_rubric_late_calls_bill_but_cannot_publish_changed_attempt(rubric_input_case, change):

    case = rubric_input_case
    case["vendor"].entered, case["vendor"].release = asyncio.Event(), asyncio.Event()
    preview = await preview_rubric(case)
    submitted = await submit_rubric(case, preview)
    assert submitted.status_code == 200, submitted.text
    job_id = submitted.json()["data"]["job_id"]
    worker = asyncio.create_task(case["app"].state.processor(case["header"]["X-Org-Id"], job_id))
    try:
        await asyncio.wait_for(case["vendor"].entered.wait(), 10)
        if change == "redaction_revision":
            # Off and back on: redaction stays enabled but its revision moves on.
            await set_redaction(case, False, True)
        else:
            async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
                job = await session.get(Job, UUID(job_id))
                if change == "expired_lease":
                    job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
                else:
                    job.run_id = uuid4()
    finally:
        case["vendor"].release.set()
        await asyncio.wait_for(worker, 10)
    async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
        assert await session.scalar(select(func.count()).select_from(ScoreRubricSet)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(UsageRecord)
                .where(UsageRecord.job_id == UUID(job_id))
            )
            == 1
        )


async def test_rubric_generation_and_job_routes_hide_other_org(rubric_input_case):
    case = rubric_input_case
    foreign = case["headers"][1]
    # The other tenant has a valid generation role so RLS, rather than a scope
    # refusal, determines the resource-not-found result.
    set_role(
        case["admin_engine"], case["tenants"]["orgs"][1], case["tenants"]["users"][1], "bidder"
    )
    prefix = f"/tasks/{case['task']}/score-rubrics"
    for suffix, body in (("/preview", {"dry_run": True}), ("", {"expected_input_hash": "0" * 64})):
        response = await case["api"].post(
            prefix + suffix, headers=foreign, json={"extraction_job_id": case["extraction"], **body}
        )
        assert response.status_code == 404, response.text
    preview = await preview_rubric(case)
    submitted = await submit_rubric(case, preview)
    assert submitted.status_code == 200, submitted.text
    job_id = submitted.json()["data"]["job_id"]
    assert (await case["api"].get(f"/jobs/{job_id}", headers=foreign)).status_code == 404
    assert (await case["api"].post(f"/jobs/{job_id}/cancel", headers=foreign)).status_code == 404


async def test_rubric_sensitive_fixed_source_blocks_without_rewriting_source(rubric_input_case):
    from test_confidential_values import add_field, set_value

    case = rubric_input_case
    value = "offered appliance"
    field = await add_field(
        case["api"], case["header"], "source_phrase", "Synthetic private phrase", "contact", "task"
    )
    await set_value(case["api"], case["header"], field, value, case["task"])
    before = await rubric_counts(case)
    preview = await preview_rubric(case)
    assert preview["admission_blocker"] == "sensitive_scoring_source"
    assert value not in json.dumps(preview)
    assert await rubric_counts(case) == before
    submitted = await submit_rubric(case, preview)
    assert submitted.status_code == 409
    assert submitted.json()["data"]["error"]["code"] == "sensitive_scoring_source"
    assert value not in submitted.text and not case["vendor"].requests
    after = await rubric_counts(case)
    assert after["jobs"] == before["jobs"] and after["score_rubric_sets"] == 0
