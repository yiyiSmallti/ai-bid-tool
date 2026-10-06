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
from app.schemas.score_contracts import (
    RubricGenerateResult,
    RubricPreview,
    RubricReportData,
    RubricStructureOutput,
)
from app.services import drafts
from sqlalchemy import func, select
from task_fixtures import confirm_requirements_async
from test_check import LiveCheckClient, check_client, invoke_live_cli
from test_check_combined import platform_llm, seed_platform, semantic_llm
from test_response_cards import create_tender, set_role


class RubricVendor:
    def __init__(self):
        self.requests: list[dict] = []
        self.bodies: list[dict] = []
        self.mode = "valid"
        self.stages: list[str] = []
        self.structure_output: dict | None = None
        self.citation_quote: str | None = None
        self.item_failures: dict[int, str] = {}
        self.item_calls = 0
        self.block_stage: str | None = None
        self.expected_inflight = 1
        self.entered: asyncio.Event | None = None
        self.release: asyncio.Event | None = None

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        payload = json.loads(body["messages"][-1]["content"])
        self.requests.append(payload)
        stage = "items" if "structure_hash" in payload else "structure"
        self.stages.append(stage)
        if stage == "items":
            self.item_calls += 1
        if (
            self.entered is not None
            and self.release is not None
            and self.block_stage in {None, stage}
        ):
            if self.block_stage != "items" or self.item_calls >= self.expected_inflight:
                self.entered.set()
            await self.release.wait()
        refs = {
            entry["ref"]: entry["text"].split("\n招标原文：\n", 1)[-1]
            for entry in payload["context"]["texts"]
        }
        rows = payload["requirements"]
        anchor = rows[0]
        source = {
            "ref": anchor["tender_ref"],
            "quote": self.citation_quote or refs[anchor["tender_ref"]],
        }
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
                    "review_domain": None,
                    "citations": [source.copy()],
                }
            ],
            "items": [
                {
                    "requirement_id": row["requirement_id"],
                    "section_key": "technical",
                    "key": f"{row['tender_ref'].removesuffix('.tender')}.item-1",
                    "title": f"Synthetic score item {index}",
                    "rule_text": refs[row["tender_ref"]],
                    "order": int(row["tender_ref"].split(".")[0][1:]),
                    "assessment_mode": "model_assessable",
                    "score_range": {"minimum": "0", "maximum": "5"},
                    "weight": None,
                    "ambiguity_reason": None,
                    "citations": [
                        {
                            "ref": row["tender_ref"],
                            "quote": self.citation_quote or refs[row["tender_ref"]],
                        }
                    ],
                }
                for index, row in enumerate(rows, 1)
            ],
            "overall_aggregation": "sum",
            "overall_rule_text": None,
            "overall_score_range": {"minimum": "0", "maximum": str(5 * len(rows))},
            "overall_cap": None,
            "overall_citations": [source.copy()],
        }
        if self.mode.startswith("whole_table"):
            technical = [row for row in rows if "Technical" in refs[row["tender_ref"]]]
            commercial = [row for row in rows if "Commercial" in refs[row["tender_ref"]]]
            if stage == "structure":
                assert technical and commercial, "Structure call must retain both sections"
            output["sections"] = []
            for order, (key, members) in enumerate(
                (("technical", technical), ("commercial", commercial)), 1
            ):
                if not members:
                    continue
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
                        "review_domain": key,
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
            if self.mode == "whole_table_malformed" and stage == "structure":
                output.pop("overall_aggregation")
        if stage == "structure":
            output.pop("items")
            if self.mode == "structure_bad_ref":
                output["sections"][0]["citations"][0]["ref"] = "untrusted-sensitive-ref"
            elif self.mode == "structure_bad_quote":
                output["sections"][0]["citations"][0]["quote"] = "Absent synthetic quotation"
            elif self.mode == "structure_bad_overall_ref":
                output["overall_citations"][0]["ref"] = "untrusted-sensitive-ref"
            self.structure_output = output
        else:
            output = {"items": output["items"]}
        if self.mode == "unknown_ref" and stage == "items":
            output["items"][0]["citations"][0]["ref"] = "untrusted-sensitive-ref"
        if self.mode == "whole_table_unknown_section" and stage == "items":
            output["items"][0]["section_key"] = "untrusted-sensitive-section"
        if self.mode == "whole_table_crossbatch_requirement" and stage == "items":
            structure = self.requests[0]
            sent_ids = {row["requirement_id"] for row in rows}
            foreign = next(
                row for row in structure["requirements"] if row["requirement_id"] not in sent_ids
            )
            foreign_texts = {
                entry["ref"]: entry["text"].split("\n招标原文：\n", 1)[-1]
                for entry in structure["context"]["texts"]
            }
            output["items"][0]["requirement_id"] = foreign["requirement_id"]
            output["items"][0]["citations"] = [
                {"ref": foreign["tender_ref"], "quote": foreign_texts[foreign["tender_ref"]]}
            ]
        failure = self.item_failures.get(self.item_calls) if stage == "items" else self.mode
        message = {"content": json.dumps(output)}
        if failure == "refused":
            message["refusal"] = "Synthetic refusal"
        return httpx.Response(
            200,
            json={
                "model": "synthetic-check-model",
                "choices": [
                    {
                        "finish_reason": "length" if failure == "truncated" else "stop",
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
            api, app, headers[0], tmp_path, suffix="rubric", confirmed=True
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
    assert terminal["result"]["completion"] == "complete", terminal["result"]
    assert terminal["result"]["candidate_items"] > 0, terminal["result"]
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
    assert preview["prompt_version"] == "score-rubric-v4"
    assert preview["schema_version"] == "score-rubric-wire-v4"
    assert preview["estimated_cost"]["llm_tokens"] >= 2 * case["llm"].settings.llm_max_output_tokens

    stale = await submit_rubric(case, preview, expected_input_hash="0" * 64)
    assert stale.status_code == 409 and not case["vendor"].requests
    submitted = await submit_rubric(case, preview)
    assert submitted.status_code == 200, submitted.text
    terminal = await finish_rubric(case, submitted.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    result = RubricGenerateResult.model_validate(terminal["result"])
    assert result.completion == "complete" and result.candidate_items == 1
    assert result.unresolved_requirements == 0
    assert len(result.usage_record_ids) == len(case["vendor"].requests) == 2
    assert case["vendor"].stages == ["structure", "items"]
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

    cached_preview = await case["api"].post(
        f"/v4/tasks/{case['task']}/score-rubrics/preview",
        headers=case["header"],
        json={"extraction_job_id": case["extraction"], "dry_run": True},
    )
    assert cached_preview.status_code == 200, cached_preview.text
    preflight = cached_preview.json()["data"]["budget_preflight"]
    assert preflight["cached_job_id"] == str(result.job_id)
    assert preflight["planned_calls"] == 0
    assert preflight["estimate"]["basis"] == "cache_hit"
    assert Decimal(preflight["estimate"]["charge"]) == 0
    assert Decimal(preflight["estimate"]["task_amount"]) == 0
    assert preflight["cached_result_cost"] is not None
    assert cached_preview.json()["cost"]["basis"] == "cache_hit"
    assert len(case["vendor"].requests) == 2
    assert await rubric_counts(case) == after

    path = f"/v4/tasks/{case['task']}/score-rubrics/{result.rubric_id}"
    report = await case["api"].get(path, headers=case["header"])
    assert report.status_code == 200, report.text
    RubricReportData.model_validate(report.json()["data"])
    assert report.json()["data"]["rubric"]["state"] == "candidate"
    legacy_report = await case["api"].get(path.removeprefix("/v4"), headers=case["header"])
    assert legacy_report.status_code == 200, legacy_report.text
    assert "requirement_review" not in legacy_report.json()["data"]["rubric"]
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


@pytest.mark.parametrize("repeat_position", ["before", "after"])
async def test_rubric_short_citation_repeated_outside_pinned_source_completes(
    rubric_input_case, repeat_position
):
    """Exercise snapshot, both Provider stages and persisted public Source bindings.

    Failures: an unrelated page repeat rejects stage 1, short citations replace the
    pinned Source, stage 2 loses its item, or the persisted structure loses provenance.
    """
    case = rubric_input_case
    quote = "The offered appliance memory shall be at least 64 GB."
    pinned_quote = f"Synthetic scoring boundary: {quote} Award 5 points for compliance."
    repeated = f"Synthetic unrelated explanation: {quote} This is outside the scoring boundary."
    original = "\n".join(
        (repeated, pinned_quote) if repeat_position == "before" else (pinned_quote, repeated)
    )
    assert original.count(quote) == 2
    assert original.count(pinned_quote) == pinned_quote.count(quote) == 1
    org = case["tenants"]["orgs"][0]
    requirement_id = case["requirements"][0]["id"]
    async with case["app"].state.db.transaction(org) as session:
        requirement = await session.get(Requirement, UUID(requirement_id))
        requirement.text = requirement.quote = pinned_quote
        requirement.fingerprint = hashlib.sha256(pinned_quote.encode()).hexdigest()
        chunk = await session.get(Chunk, requirement.chunk_id)
        chunk.text = original
    source = {**case["requirements"][0]["source"], "quote": pinned_quote}
    case["vendor"].citation_quote = quote

    preview = await preview_rubric(case)
    assert preview["admission_blocker"] is None
    assert preview["scoring_requirement_ids"] == [requirement_id]
    submitted = await submit_rubric(case, preview)
    assert submitted.status_code == 200, submitted.text
    terminal = await finish_rubric(case, submitted.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    result = RubricGenerateResult.model_validate(terminal["result"])
    assert result.completion == "complete"
    assert result.candidate_items == 1 and result.unresolved_requirements == 0
    assert len(result.usage_record_ids) == 2
    vendor = case["vendor"]
    assert vendor.stages == ["structure", "items"]
    assert vendor.structure_output is not None
    assert vendor.structure_output["overall_citations"] == [{"ref": "r1.tender", "quote": quote}]
    for payload in vendor.requests:
        assert len(payload["requirements"]) == 1
        sent = payload["context"]["texts"][0]["text"]
        assert sent.count(quote) == 1
        assert sent.endswith(pinned_quote)
        assert repeated not in sent

    path = f"/tasks/{case['task']}/score-rubrics/{result.rubric_id}"
    shown = await case["api"].get(path, headers=case["header"])
    assert shown.status_code == 200, shown.text
    report = shown.json()["data"]
    RubricReportData.model_validate(report)
    assert report["rubric"]["state"] == "candidate"
    assert report["sections"][0]["sources"] == [
        {"requirement_id": requirement_id, "source": source, "quote": quote}
    ]
    for kind in ("items", "coverage"):
        assert len(report[kind]) == 1
        assert report[kind][0]["source"] == source
    for kind in ("items", "coverage"):
        assert report[kind][0]["requirement_id"] == requirement_id
    # Generation binds every item, but only a human can map its coverage.
    pending_report = report
    coverage = report["coverage"][0]
    assert coverage["disposition"] == "pending" and coverage["rubric_item_ids"] == []
    assert report["rubric"]["completeness"]["normalization_errors"] == ["unmapped_item"]
    mapped = await case["api"].post(
        f"{path}/coverage/{requirement_id}/decisions",
        headers=case["header"],
        json={
            "expected_revision": coverage["revision"],
            "expected_input_hash": report["rubric"]["input_hash"],
            "action": "mapped",
            "rubric_item_ids": [report["items"][0]["id"]],
            "reason": "Synthetic human coverage review of the generated scoring item",
        },
    )
    assert mapped.status_code == 200, mapped.text
    shown = await case["api"].get(path, headers=case["header"])
    assert shown.status_code == 200, shown.text
    report = shown.json()["data"]
    RubricReportData.model_validate(report)
    assert report["rubric"]["state"] == "candidate"
    assert report["sections"] == pending_report["sections"]
    assert report["items"] == pending_report["items"]
    assert report["coverage"][0]["source"] == source
    assert report["coverage"][0]["disposition"] == "mapped"
    assert report["coverage"][0]["rubric_item_ids"] == [report["items"][0]["id"]]
    assert report["rubric"]["completeness"]["pending_requirement_ids"] == []
    assert report["rubric"]["completeness"]["normalization_errors"] == []
    assert (await case["api"].get(path, headers=case["headers"][1])).status_code == 404

    async with case["app"].state.db.transaction(org) as session:
        rubric = await session.get(ScoreRubricSet, result.rubric_id)
        structure = rubric.input_manifest["rubric_structure"]
        assert structure["proposal"] == vendor.structure_output
        assert structure["structure_hash"] == vendor.requests[1]["structure_hash"]
        assert rubric.input_hash == report["rubric"]["input_hash"]
        assert rubric.input_hash == drafts.digest(rubric.input_manifest)
        assert rubric.normalization_errors == []
        assert structure["overall_citations"] == [
            {"requirement_id": requirement_id, "source": source, "quote": quote}
        ]
        assert structure["sections"][0]["source"] == source
    artifact = case["tmp_path"] / f"rubric-pinned-source-{repeat_position}.json"
    artifact.write_text(
        json.dumps(
            {
                "source_original": original,
                "pinned_source": source,
                "provider_quote": quote,
                "preview": preview,
                "job": terminal,
                "pending_report": pending_report,
                "coverage_decision": mapped.json()["data"],
                "report": report,
                "structure": structure,
                "requests": vendor.requests,
            },
            indent=2,
        )
    )
    assert json.loads(artifact.read_text())["report"]["items"][0]["source"] == source


async def add_whole_scoring_table(case):
    """Keep fixed-source integrity while extending the selected extraction's scoring table."""
    org = case["tenants"]["orgs"][0]
    async with case["app"].state.db.transaction(org) as session:
        original = await session.get(Requirement, UUID(case["requirements"][0]["id"]))
        original.category = "technical"
        accepted_ids = [original.id]
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
                citation_verified=True,
            )
            session.add(chunk)
            await session.flush()
            requirement_id = uuid4()
            accepted_ids.append(requirement_id)
            session.add(
                Requirement(
                    id=requirement_id,
                    org_id=org,
                    task_id=chunk.task_id,
                    document_id=chunk.document_id,
                    chunk_id=chunk.id,
                    page=index,
                    quote=text.rstrip(),
                    text=text,
                    category="scoring",
                    starred=False,
                    condition={"must_not_be_sent": True},
                    fingerprint=hashlib.sha256(text.encode()).hexdigest(),
                    job_id=UUID(case["extraction"]),
                )
            )

        await session.flush()
        await confirm_requirements_async(
            session,
            org,
            UUID(case["task"]),
            accepted_ids,
            settings=case["app"].state.processor.settings,
        )


async def test_rubric_whole_table_structure_survives_fixed_section_item_batches(
    rubric_input_case, monkeypatch
):
    case = rubric_input_case
    await add_whole_scoring_table(case)
    case["vendor"].mode = "whole_table"
    llm = semantic_llm(case["tmp_path"], case["vendor"], llm_batch_chars=1000)
    install_rubric_resolver(monkeypatch, llm)
    preview = await preview_rubric(case)
    assert preview["admission_blocker"] is None
    assert len(preview["scoring_requirement_ids"]) == 3
    assert preview["estimated_cost"]["llm_tokens"] >= 4 * llm.settings.llm_max_output_tokens
    submitted = await submit_rubric(case, preview)
    assert submitted.status_code == 200, submitted.text
    terminal = await finish_rubric(case, submitted.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    assert terminal["result"]["completion"] == "complete"
    assert terminal["result"]["candidate_items"] == 3
    assert terminal["result"]["unresolved_requirements"] == 0
    vendor = case["vendor"]
    assert len(vendor.requests) == len(terminal["result"]["usage_record_ids"]) == 4
    assert vendor.stages == ["structure", "items", "items", "items"]
    structure_request, *item_requests = vendor.requests
    assert len(json.dumps(structure_request)) > 8000
    assert len(structure_request["requirements"]) == 3
    assert "sections" not in structure_request and "structure_hash" not in structure_request
    assert all(len(payload["requirements"]) == 1 for payload in item_requests)
    assert {
        row["requirement_id"] for payload in item_requests for row in payload["requirements"]
    } == {row["requirement_id"] for row in structure_request["requirements"]}
    assert len({payload["structure_hash"] for payload in item_requests}) == 1
    assert vendor.structure_output is not None
    fixed_sections = [
        {key: value for key, value in section.items() if key != "citations"}
        for section in vendor.structure_output["sections"]
    ]
    for payload in item_requests:
        assert payload["sections"] == fixed_sections
        assert {row["ref"] for row in payload["context"]["texts"]} == {
            row["tender_ref"] for row in payload["requirements"]
        }
    shown = await case["api"].get(
        f"/tasks/{case['task']}/score-rubrics/{terminal['result']['rubric_id']}",
        headers=case["header"],
    )
    assert shown.status_code == 200, shown.text
    report = shown.json()["data"]
    assert {row["key"] for row in report["sections"]} == {"technical", "commercial"}
    assert all(row["review_domain"] is None for row in report["sections"])
    assert report["rubric"]["overall_aggregation"] == "capped_sum"
    assert Decimal(report["rubric"]["overall_cap"]) == 12
    async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
        rubric = await session.get(ScoreRubricSet, UUID(terminal["result"]["rubric_id"]))
        assert (
            rubric.input_manifest["rubric_structure"]["structure_hash"]
            == item_requests[0]["structure_hash"]
        )
        assert rubric.input_hash == report["rubric"]["input_hash"]
        assert rubric.input_hash != preview["input"]["input_hash"]
        assert rubric.input_hash == drafts.digest(rubric.input_manifest)
        structure_manifest = rubric.input_manifest["rubric_structure"]
        assert {row["review_domain"] for row in structure_manifest["sections"]} == {
            "technical",
            "commercial",
        }
        structure_proposal = RubricStructureOutput.model_validate(
            vendor.structure_output
        ).model_dump(mode="json")
        assert structure_manifest["proposal"] == structure_proposal
        assert structure_manifest["structure_hash"] == drafts.digest(
            {
                "output": structure_proposal,
                "structure_prompt_version": rubric.input_manifest["structure_prompt_version"],
                "items_prompt_version": rubric.input_manifest["items_prompt_version"],
                "schema_version": rubric.input_manifest["schema_version"],
            }
        )
        job = await session.get(Job, UUID(terminal["id"]))
        submission = job.result["submission"]
        assert submission["input_hash"] == rubric.input_hash
        assert submission["input_manifest"] == rubric.input_manifest
        assert submission["preview_input_hash"] == preview["input"]["input_hash"]
    after = await rubric_counts(case)
    cached = await submit_rubric(case, preview)
    assert cached.status_code == 200, cached.text
    assert cached.json()["data"] == {
        "job_id": terminal["id"],
        "status": "succeeded",
        "cached": True,
    }
    assert await rubric_counts(case) == after
    assert len(vendor.requests) == 4
    artifact = case["tmp_path"] / "rubric-whole-table.json"
    artifact.write_text(
        json.dumps(
            {
                "preview": preview,
                "job": terminal,
                "report": report,
                "structure_proposal": structure_proposal,
                "structure_manifest": structure_manifest,
                "requests": vendor.bodies,
            },
            indent=2,
        )
    )
    assert len(json.loads(artifact.read_text())["requests"]) == 4


@pytest.mark.parametrize(
    "mode",
    [
        "whole_table_malformed",
        "truncated",
        "refused",
        "structure_bad_ref",
        "structure_bad_quote",
        "structure_bad_overall_ref",
    ],
)
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
    assert case["vendor"].stages == ["structure"]
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


@pytest.mark.parametrize(
    "mode", ["whole_table_unknown_section", "whole_table_crossbatch_requirement"]
)
async def test_rubric_items_reject_unknown_sections_and_crossbatch_bindings(
    rubric_input_case, monkeypatch, mode
):
    case = rubric_input_case
    await add_whole_scoring_table(case)
    case["vendor"].mode = mode
    install_rubric_resolver(
        monkeypatch,
        semantic_llm(case["tmp_path"], case["vendor"], llm_batch_chars=1000),
    )
    preview = await preview_rubric(case)
    submitted = await submit_rubric(case, preview)
    assert submitted.status_code == 200, submitted.text
    terminal = await finish_rubric(case, submitted.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    result = terminal["result"]
    assert result["completion"] == "partial"
    assert result["candidate_items"] == 0 and result["unresolved_requirements"] == 3
    assert len(result["usage_record_ids"]) == len(case["vendor"].requests) == 4
    shown = await case["api"].get(
        f"/tasks/{case['task']}/score-rubrics/{result['rubric_id']}", headers=case["header"]
    )
    assert shown.status_code == 200, shown.text
    report = shown.json()["data"]
    assert {section["key"] for section in report["sections"]} == {"technical", "commercial"}
    assert report["items"] == []
    assert all(row["disposition"] == "pending" for row in report["coverage"])
    assert "untrusted-sensitive" not in json.dumps({"job": terminal, "report": report})
    artifact = case["tmp_path"] / f"rubric-items-boundary-{mode}.json"
    artifact.write_text(
        json.dumps({"preview": preview, "job": terminal, "report": report}, indent=2)
    )
    assert json.loads(artifact.read_text())["job"]["result"]["unresolved_requirements"] == 3


@pytest.mark.parametrize("failure", ["refused", "task_budget_exceeded"])
async def test_rubric_failed_item_batch_retains_structure_and_valid_items_with_cli_exit5(
    rubric_input_case, monkeypatch, failure
):
    from bid_cli import main as cli

    case = rubric_input_case
    await add_whole_scoring_table(case)
    await seed_platform(
        case["admin_engine"], case["app"].state.processor.settings, case["tenants"]["orgs"][0]
    )
    vendor = case["vendor"]
    vendor.mode = "whole_table"
    if failure == "refused":
        vendor.item_failures = {2: "refused"}
    else:
        # Exercise the shared admission seam and durable partial publication;
        # cumulative task-budget ledger enforcement belongs to its migration suite.
        original_admit = JobExecution.admit

        async def budget_admit(execution, quote):
            if len(vendor.requests) == 2:
                raise execution.stop("task_budget_exceeded", "Synthetic task budget reached")
            return await original_admit(execution, quote)

        monkeypatch.setattr(JobExecution, "admit", budget_admit)
    llm = platform_llm(case["app"].state.processor.settings, vendor)
    llm.settings = llm.settings.model_copy(update={"llm_batch_chars": 1000, "llm_concurrency": 1})
    install_rubric_resolver(monkeypatch, llm)
    before = await rubric_counts(case)
    preview = await preview_rubric(case)
    assert await rubric_counts(case) == before and vendor.requests == []
    submitted = await submit_rubric(case, preview)
    assert submitted.status_code == 200, submitted.text
    terminal = await finish_rubric(case, submitted.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    result = terminal["result"]
    assert result["completion"] == "partial"
    assert result["candidate_items"] == 1 and result["unresolved_requirements"] == 2
    assert result["stop_reason"] is not None
    expected_calls = 3 if failure == "refused" else 2
    assert vendor.stages == ["structure", *(["items"] * (expected_calls - 1))]
    assert len(result["usage_record_ids"]) == expected_calls
    if failure == "task_budget_exceeded":
        assert result["stop_reason"] == failure
    shown = await case["api"].get(
        f"/v4/tasks/{case['task']}/score-rubrics/{result['rubric_id']}", headers=case["header"]
    )
    assert shown.status_code == 200, shown.text
    report = shown.json()["data"]
    assert {section["key"] for section in report["sections"]} == {"technical", "commercial"}
    assert len(report["items"]) == 1
    assert report["rubric"]["overall_aggregation"] == "capped_sum"
    assert Decimal(report["rubric"]["overall_cap"]) == 12
    async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
        job_id = UUID(result["job_id"])
        usages = list(
            (await session.scalars(select(UsageRecord).where(UsageRecord.job_id == job_id))).all()
        )
        calls = list(
            (await session.scalars(select(VendorCall).where(VendorCall.job_id == job_id))).all()
        )
        entries = list(
            (
                await session.scalars(
                    select(BalanceEntry).where(
                        BalanceEntry.usage_record_id.in_([row.id for row in usages])
                    )
                )
            ).all()
        )
        assert len(usages) == len(calls) == len(entries) == expected_calls
        assert {row.call_id for row in usages} == {row.id for row in calls}
        assert all(call.state == "completed" for call in calls)
        assert all(row.charge > 0 for row in usages)
        assert await session.scalar(select(OrgBalance.balance)) == Decimal("10") - sum(
            (row.charge for row in usages), Decimal(0)
        )
    monkeypatch.setenv("BID_SESSION", case["header"]["Authorization"].removeprefix("Bearer "))
    monkeypatch.setenv("BID_ORG", case["header"]["X-Org-Id"])
    monkeypatch.setattr(
        cli,
        "Client",
        lambda mode, server, state: LiveCheckClient(
            mode, server, state, case["app"].state.processor.settings
        ),
    )
    # The terminal job reports the partial generation (exit 5 for `generate --wait`); showing
    # the retained candidate rubric afterwards is a successful read.
    job_exit, job_body = await asyncio.to_thread(invoke_live_cli, ["job", "status", terminal["id"]])
    assert job_exit == 5 and job_body["data"]["result"]["completion"] == "partial"
    exit_code, cli_body = await asyncio.to_thread(
        invoke_live_cli,
        ["score", "rubric", "show", "--task", case["task"], "--rubric", result["rubric_id"]],
    )
    assert exit_code == 0 and cli_body["data"] == report
    artifact = case["tmp_path"] / "rubric-item-batch-partial.json"
    artifact.write_text(
        json.dumps(
            {"preview": preview, "job": terminal, "report": report, "cli": cli_body}, indent=2
        )
    )
    assert (
        len(
            json.loads(artifact.read_text())["cli"]["data"]["rubric"]["completeness"][
                "pending_requirement_ids"
            ]
        )
        == 3
    )


async def test_rubric_item_batches_run_concurrently_after_structure_verification(
    rubric_input_case, monkeypatch
):
    case = rubric_input_case
    await add_whole_scoring_table(case)
    vendor = case["vendor"]
    vendor.mode = "whole_table"
    vendor.block_stage = "items"
    vendor.expected_inflight = 2
    vendor.entered, vendor.release = asyncio.Event(), asyncio.Event()
    install_rubric_resolver(
        monkeypatch,
        semantic_llm(case["tmp_path"], vendor, llm_batch_chars=1000, llm_concurrency=2),
    )
    preview = await preview_rubric(case)
    submitted = await submit_rubric(case, preview)
    assert submitted.status_code == 200, submitted.text
    job_id = submitted.json()["data"]["job_id"]
    worker = asyncio.create_task(case["app"].state.processor(case["header"]["X-Org-Id"], job_id))
    try:
        await asyncio.wait_for(vendor.entered.wait(), 10)
        assert vendor.stages == ["structure", "items", "items"]
        assert vendor.structure_output is not None
        assert vendor.requests[1]["structure_hash"] == vendor.requests[2]["structure_hash"]
    finally:
        vendor.release.set()
        await asyncio.wait_for(worker, 10)
    terminal = (await case["api"].get(f"/jobs/{job_id}", headers=case["header"])).json()["data"]
    assert terminal["status"] == "succeeded" and terminal["result"]["completion"] == "complete"
    assert terminal["result"]["candidate_items"] == 3
    assert len(terminal["result"]["usage_record_ids"]) == len(vendor.requests) == 4
    artifact = case["tmp_path"] / "rubric-concurrent-items.json"
    artifact.write_text(
        json.dumps(
            {"job": terminal, "stages": vendor.stages, "requests": vendor.requests}, indent=2
        )
    )
    assert json.loads(artifact.read_text())["stages"] == ["structure", "items", "items", "items"]


@pytest.mark.parametrize(
    "version_field,old_version",
    [
        ("prompt_version", "score-rubric-v2"),
        ("schema_version", "score-rubric-wire-v1"),
        ("schema_version", "score-rubric-wire-v3"),
        ("structure_prompt_version", "score-rubric-structure-v1"),
        ("items_prompt_version", "score-rubric-items-v1"),
        ("items_prompt_version", "score-rubric-items-v2"),
    ],
)
async def test_rubric_old_queued_versions_fail_before_resolution_or_vendor_calls(
    rubric_input_case, monkeypatch, version_field, old_version
):
    case = rubric_input_case
    preview = await preview_rubric(case)
    submitted = await submit_rubric(case, preview)
    assert submitted.status_code == 200, submitted.text
    job_id = UUID(submitted.json()["data"]["job_id"])
    async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
        job = await session.get(Job, job_id)
        job.result = {
            **job.result,
            "submission": {
                **job.result["submission"],
                "input_manifest": {
                    **job.result["submission"]["input_manifest"],
                    version_field: old_version,
                },
            },
        }

    async def must_not_resolve(*args, **kwargs):
        raise AssertionError("Legacy queued rubric must be rejected before provider resolution")

    monkeypatch.setattr("app.services.score_generation.resolve", must_not_resolve)
    terminal = await finish_rubric(case, submitted.json()["data"])
    assert terminal["status"] == "failed", terminal
    assert terminal["error"]["code"] == "score_rubric_version_unsupported"
    assert case["vendor"].requests == []
    async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(UsageRecord).where(UsageRecord.job_id == job_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(VendorCall).where(VendorCall.job_id == job_id)
            )
            == 0
        )
        assert await session.scalar(select(func.count()).select_from(ScoreRubricSet)) == 0
    artifact = case["tmp_path"] / f"rubric-old-job-{version_field}.json"
    artifact.write_text(json.dumps({"job": terminal, "vendor_calls": 0}, indent=2))
    assert (
        json.loads(artifact.read_text())["job"]["error"]["code"]
        == "score_rubric_version_unsupported"
    )


@pytest.mark.parametrize("mode", ["valid", "refused", "truncated", "unknown_ref"])
async def test_rubric_platform_calls_settle_once_even_when_output_refused(
    rubric_input_case, monkeypatch, mode
):
    case = rubric_input_case
    await seed_platform(
        case["admin_engine"], case["app"].state.processor.settings, case["tenants"]["orgs"][0]
    )
    case["vendor"].mode = mode
    llm = platform_llm(case["app"].state.processor.settings, case["vendor"])
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
        expected_calls = 2 if mode in {"valid", "unknown_ref"} else 1
        assert (
            len(calls)
            == len(usages)
            == len(entries)
            == len(case["vendor"].requests)
            == expected_calls
        )
        assert (
            len({(row.org_id, row.job_id, row.run_id, row.call_id) for row in usages})
            == expected_calls
        )
        assert {row.call_id for row in usages} == {row.id for row in calls}
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
    await seed_platform(
        case["admin_engine"], case["app"].state.processor.settings, case["tenants"]["orgs"][0]
    )
    install_rubric_resolver(
        monkeypatch, platform_llm(case["app"].state.processor.settings, case["vendor"])
    )
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
    assert len(case["vendor"].requests) == 2


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
