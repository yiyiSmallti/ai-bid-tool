"""Database-backed large-draft scoring acceptance through API and worker.

Failure inventory: unrelated gaps can exhaust a score request, scoring can inherit
the extraction batch limit, preview costs can omit repeated batch context, an item's
own gap can lose its metadata, and scoped support can evade local citations or
immutable publication. No external calls are made; HTTP uses MockTransport.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from app.models.entities import Chunk, Requirement, UsageRecord
from app.models.response_cards import DraftRun, ResponseItem
from app.models.score import ScoreReport
from app.providers.scoring import HTTPScoreProvider
from app.schemas.score_contracts import ScorePreview, ScoreProviderRequest, ScoreReportData
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError
from task_fixtures import confirm_requirements_async, reviewer_header
from test_check import publish_draft
from test_check_combined import semantic_llm
from test_response_cards import create_card, require_action
from test_score_api import (
    finish_rubric,
    preview_rubric,
    submit_rubric,
)
from test_score_api import rubric_input_case as rubric_input_case
from test_score_review import base, confirm_contents, decision, role, show
from test_score_run import (
    ScoreVendor,
    counts,
    finish_score,
    install_score_resolver,
    submit_score,
)


@pytest.fixture
async def large_score_case(rubric_input_case, monkeypatch):
    case = rubric_input_case
    org = case["tenants"]["orgs"][0]
    added = []
    async with case["app"].state.db.transaction(org) as session:
        # Seed only extracted source rows. Rubric confirmation, card confirmation,
        # draft construction and score publication still use the actual service flow.
        chunks = []
        for page in range(6, 1547):
            scoring = page < 91
            text = (
                f"Synthetic score row {page}: memory shall be at least 64 GB."
                if scoring
                else f"UNRELATED GAP row {page}: retain the documented service procedure."
            )
            chunk = Chunk(
                id=uuid4(),
                org_id=org,
                task_id=UUID(case["task"]),
                document_id=UUID(case["document"]),
                page=page,
                seq=page,
                text=text,
            )
            chunks.append(chunk)
            added.append(
                Requirement(
                    id=uuid4(),
                    org_id=org,
                    task_id=chunk.task_id,
                    document_id=chunk.document_id,
                    chunk_id=chunk.id,
                    page=page,
                    quote=text,
                    text=text,
                    category="scoring" if scoring else "technical",
                    starred=False,
                    condition={"must_not_be_sent": True},
                    fingerprint=hashlib.sha256(text.encode()).hexdigest(),
                    job_id=UUID(case["extraction"]),
                )
            )
        session.add_all(chunks)
        await session.flush()
        session.add_all(added)
        await session.flush()
        # B02: rubric and score inputs accept only confirmed requirements.
        await confirm_requirements_async(
            session, org, UUID(case["task"]), settings=case["app"].state.processor.settings
        )
    preview = await preview_rubric(case)
    assert len(preview["scoring_requirement_ids"]) == 86
    accepted = await submit_rubric(case, preview)
    assert accepted.status_code == 200, accepted.text
    terminal = await finish_rubric(case, accepted.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    assert terminal["result"]["completion"] == "complete", terminal
    response = await case["api"].get(
        f"/tasks/{case['task']}/score-rubrics/{terminal['result']['rubric_id']}",
        headers=case["header"],
    )
    assert response.status_code == 200, response.text
    case["rubric"] = response.json()["data"]
    complete = await confirm_contents(case)
    assert len(complete["items"]) == 86
    confirmed = await case["api"].post(
        base(case) + "/decisions",
        headers=case["header"],
        json=decision(complete, complete["rubric"]),
    )
    assert confirmed.status_code == 200, confirmed.text
    case["rubric"] = await show(case)
    assert case["rubric"]["rubric"]["state"] == "confirmed"
    _, technical_header = await reviewer_header(
        case["api"], case["admin_engine"], org, UUID(case["task"]), "technical"
    )
    support = [case["requirements"][2], *({"id": str(row.id)} for row in added[85:100])]
    response_texts = []
    for index, requirement in enumerate(support):
        length = 60 if index < 4 else 59
        text = f"Confirmed row {index}: 64 GB memory. ".ljust(length, "x")
        response_texts.append(text)
        card = await create_card(
            case["api"],
            case["header"],
            case["task"],
            case["extraction"],
            requirement,
            {
                "response_kind": "commitment",
                "response_text": text,
                "deviation": "none",
                "deviation_note": "Confirmed.",
                "evidence": [],
            },
        )
        card = await require_action(case["api"], case["header"], card, "submit")
        await require_action(
            case["api"], technical_header, card, "confirm", reviewed_evidence_ids=[]
        )
    assert len(response_texts) == 16
    assert sum(map(len, response_texts)) + 16 * len("Confirmed.") == 1108
    draft = await publish_draft(
        case["api"], case["app"], case["header"], case["task"], case["extraction"]
    )
    case["draft_id"] = draft["draft_id"]
    async with case["app"].state.db.transaction(org) as session:
        stored = await session.get(DraftRun, UUID(case["draft_id"]))
        assert stored is not None and stored.completion == "partial"
        rows = list(
            (
                await session.scalars(
                    select(ResponseItem).where(ResponseItem.draft_id == stored.id)
                )
            ).all()
        )
        assert len(rows) == 1546
        assert sum(row.kind == "row" for row in rows) == 16
        assert sum(row.kind == "gap" for row in rows) == 1530
        assert (
            sum(len(row.response_text or "") + len(row.deviation_note or "") for row in rows)
            == 1108
        )
        case["draft_rows"] = rows
    role(case, "bidder")
    vendor = ScoreVendor()
    # Keep the new scoring limit at its real default, independent of extraction.
    llm = semantic_llm(case["tmp_path"], vendor, llm_batch_chars=8000)
    install_score_resolver(monkeypatch, llm)
    return {**case, "score_vendor": vendor, "score_llm": llm}


async def test_large_draft_score_preview_run_scopes_context_and_preserves_report(large_score_case):
    case = large_score_case
    llm = case["score_llm"]
    assert llm.settings.llm_batch_chars == 8000
    assert llm.settings.score_batch_chars == 64000
    before = await counts(case)
    # Planned-call accounting is part of the Result 4.0 preview, so read it from /v4.
    response = await case["api"].post(
        f"/v4/tasks/{case['task']}/scores/preview",
        headers=case["header"],
        json={
            "draft_id": case["draft_id"],
            "rubric_id": case["rubric"]["rubric"]["id"],
            "assessment_date": "2026-10-05",
            "dry_run": True,
        },
    )
    assert response.status_code == 200, response.text
    preview = response.json()["data"]
    # Result 4.0 adds the shared budget preflight to the score preview fields.
    ScorePreview.model_validate({k: v for k, v in preview.items() if k != "budget_preflight"})
    assert preview["admission_blocker"] is None
    assert preview["cost_basis"] == "known"
    assert len(preview["selected_item_ids"]) == 86
    assert preview["preflight_unassessable_item_ids"] == []
    assert await counts(case) == before and case["score_vendor"].requests == []
    accepted = await submit_score(case, preview)
    assert accepted.status_code == 200, accepted.text
    terminal = await finish_score(case, accepted.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    result = terminal["result"]
    assert result["completion"] == "complete", result
    assert result["assessed_items"] == 86 and result["unassessable_items"] == 0
    assert Decimal(result["estimated_total"]) == 430
    requests = case["score_vendor"].requests
    assert len(requests) > 1
    assert preview["budget_preflight"]["planned_calls"] == len(requests)
    assert len(result["usage_record_ids"]) == len(requests)
    adapter = HTTPScoreProvider(llm)
    bodies = [
        adapter.request_body(ScoreProviderRequest.model_validate(payload)) for payload in requests
    ]
    input_bound = sum(len(json.dumps(body, ensure_ascii=False).encode()) + 4096 for body in bodies)
    output_bound = sum(llm.output_token_bound(body) for body in bodies)
    assert preview["estimated_cost"]["llm_tokens"] == input_bound + output_bound
    expected_usd = (
        input_bound * llm.settings.llm_input_usd_per_mtok
        + output_bound * llm.settings.llm_output_usd_per_mtok
    ) / 1_000_000
    assert Decimal(str(preview["estimated_cost"]["usd"])) == Decimal(str(expected_usd))
    assert any(
        len(ScoreProviderRequest.model_validate(payload).model_dump_json()) > 8000
        for payload in requests
    )
    assert all(
        len(ScoreProviderRequest.model_validate(payload).model_dump_json()) <= 64000
        for payload in requests
    )
    assert {item["rubric_item_id"] for payload in requests for item in payload["items"]} == {
        item["id"] for item in case["rubric"]["items"]
    }
    rows_by_page = {row.source["page"]: row for row in case["draft_rows"]}
    response_ids = {str(row.id) for row in case["draft_rows"] if row.kind == "row"}
    support_texts = {
        text
        for row in case["draft_rows"]
        if row.kind == "row"
        for text in (row.response_text, row.deviation_note)
        if text
    }
    scoped_pages = {}
    for payload in requests:
        contexts = {
            entry["ref"].removesuffix(".context"): rows_by_page[
                json.loads(entry["text"])["source_position"]["page"]
            ]
            for entry in payload["context"]["texts"]
            if entry["ref"].endswith(".context")
        }
        sent = {entry["ref"]: entry["text"] for entry in payload["context"]["texts"]}
        assert support_texts <= set(sent.values())
        union = set()
        for item in payload["items"]:
            allowed_requirements = {item["requirement_id"]} | {
                coverage["requirement_id"]
                for coverage in case["rubric"]["coverage"]
                if item["rubric_item_id"] in coverage["rubric_item_ids"]
            }
            expected = {
                f"{prefix}.{suffix}"
                for prefix, row in contexts.items()
                if row.kind == "row" or str(row.requirement_id) in allowed_requirements
                for suffix in ("context", "tender")
            }
            assert set(item["context_only_refs"]) == expected
            assert len(item["draft_refs"]) == 32
            assert {sent[ref] for ref in item["draft_refs"]} == support_texts
            scoped_pages[item["rubric_item_id"]] = sorted(
                row.source["page"]
                for prefix, row in contexts.items()
                if f"{prefix}.context" in item["context_only_refs"] and row.kind != "row"
            )
            assert len(scoped_pages[item["rubric_item_id"]]) == len(allowed_requirements)
            union.update(expected)
        assert set(payload["context_only_refs"]) == union
        assert set(sent) == union | {
            ref
            for item in payload["items"]
            for ref in (item["tender_ref"], item["rule_ref"], *item["draft_refs"])
        }
        assert all(row.kind == "row" or row.category == "scoring" for row in contexts.values())
    assert all(len(pages) == 1 for pages in scoped_pages.values())
    path = f"/tasks/{case['task']}/scores/{result['report_id']}"
    response = await case["api"].get(path, headers=case["header"])
    assert response.status_code == 200, response.text
    report = response.json()["data"]
    ScoreReportData.model_validate(report)
    assert len(report["items"]) == 86
    for item in report["items"]:
        assert item["anchor_partition"] == "gap"
        assert set(item["response_item_ids"]) <= response_ids
        assert item["response_item_ids"]
        assert {citation["kind"] for citation in item["citations"]} == {"tender", "draft"}
        assert item["anchor_response_item_id"] not in item["response_item_ids"]
        for citation in item["citations"]:
            if citation["kind"] == "draft":
                row = next(
                    row for row in case["draft_rows"] if str(row.id) == citation["response_item_id"]
                )
                assert citation["quote"] in getattr(row, citation["field"])
                assert citation["card_revision_id"] == str(row.card_revision_id)
    org = case["tenants"]["orgs"][0]
    async with case["app"].state.db.transaction(org) as session:
        usage = list(
            (
                await session.scalars(
                    select(UsageRecord).where(UsageRecord.job_id == UUID(result["job_id"]))
                )
            ).all()
        )
        assert len(usage) == len(requests)
        assert all(row.usd is not None for row in usage)
        assert Decimal(str(preview["estimated_cost"]["usd"])) >= sum(
            Decimal(str(row.usd)) for row in usage
        )
    with pytest.raises(DBAPIError):
        async with case["app"].state.db.transaction(org) as session:
            await session.execute(
                update(ScoreReport)
                .where(ScoreReport.id == UUID(result["report_id"]))
                .values(completion="partial")
            )
    retained = await case["api"].get(path, headers=case["header"])
    assert retained.json()["data"] == report
    assert (await case["api"].get(path, headers=case["headers"][1])).status_code == 404
    artifact = case["tmp_path"] / "score-large-request-scope.json"
    artifact.write_text(
        json.dumps(
            {
                "preview": preview,
                "job": terminal,
                "report": report,
                "requests": requests,
                "gap_pages_by_item": scoped_pages,
            },
            indent=2,
        )
    )
    assert len(json.loads(artifact.read_text())["report"]["items"]) == 86
