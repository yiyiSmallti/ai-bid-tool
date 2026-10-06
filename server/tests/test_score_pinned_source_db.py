"""Score citation regression through human confirmation, worker, and PostgreSQL.

Failure inventory recorded before authoring:
* an unrelated short-quote repeat on the cited page can falsely reject scoring;
* a short model citation can overwrite its complete pinned rubric Source;
* API acceptance can hide a failed stored citation binding/uniqueness gate;
* moving the unrelated repeat before or after the Source can change the outcome;
* a stored successful report can expose another tenant's synthetic bid content.

Uses the existing synthetic tender, actual rubric/card confirmation, DraftRun,
HTTP MockTransport scoring Provider, and tenant-scoped persistence fixtures.
This module requires an explicitly authorized isolated PostgreSQL test runtime.
"""

import json
from copy import deepcopy
from decimal import Decimal
from uuid import UUID

import pytest
import test_score_api
from app.models.entities import Chunk, Requirement
from app.models.score import ScoreItemCitation, ScoreReportItem
from app.schemas.score_contracts import ScoreReportData
from app.services import requirement_consumption
from sqlalchemy import select
from task_fixtures import confirm_requirements_async
from test_score_api import rubric_case as rubric_case
from test_score_run import finish_score, preview_score, submit_score
from test_score_run import score_case as score_case

QUOTE = "memory shall be at least 64 GB"
original_rubric_input_case = test_score_api.rubric_input_case


@pytest.fixture
async def rubric_input_case(original_rubric_input_case, repeat_position):
    case = original_rubric_input_case
    source = deepcopy(case["requirements"][0]["source"])
    pinned_quote = source["quote"]
    repeated = f"其他技术条款说明：{QUOTE}；这句话位于固定评分 Source 之外。"
    org = case["tenants"]["orgs"][0]
    async with case["app"].state.db.transaction(org) as session:
        requirement = await session.get(Requirement, UUID(case["requirements"][0]["id"]))
        assert requirement is not None and requirement.quote == pinned_quote
        chunk = await session.get(Chunk, requirement.chunk_id)
        assert chunk is not None and chunk.blocks is None and chunk.page == source["page"]
        original = "\n".join(
            (repeated, chunk.text) if repeat_position == "before" else (chunk.text, repeated)
        )
        assert original.count(pinned_quote) == pinned_quote.count(QUOTE) == 1
        assert original.count(QUOTE) == 2
        chunk.text = original
        await session.flush()
        review = (await requirement_consumption.effective(session, [requirement]))[requirement.id]
        assert review.state == "invalidated"
        await confirm_requirements_async(
            session,
            org,
            UUID(case["task"]),
            [requirement.id],
            settings=case["app"].state.processor.settings,
        )
        assert (await requirement_consumption.effective(session, [requirement]))[
            requirement.id
        ].confirmed
    return {
        **case,
        "pinned_source": source,
        "source_original": original,
        "outside_repeat": repeated,
    }


@pytest.mark.parametrize("repeat_position", ["before", "after"])
async def test_score_short_citation_repeated_outside_source_persists_assessed(
    score_case, repeat_position
):
    case = score_case
    source = case["pinned_source"]
    assert case["rubric"]["rubric"]["state"] == "confirmed"
    assert case["rubric"]["items"][0]["source"] == source
    assert case["rubric"]["items"][0]["confirmed_by"]
    assert not case["score_vendor"].requests

    preview = await preview_score(case)
    assert preview["admission_blocker"] is None
    submitted = await submit_score(case, preview)
    assert submitted.status_code == 200, submitted.text
    terminal = await finish_score(case, submitted.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    assert terminal["result"]["completion"] == "complete", terminal
    assert Decimal(terminal["result"]["estimated_total"]) == 5

    report_id = terminal["result"]["report_id"]
    path = f"/tasks/{case['task']}/scores/{report_id}"
    response = await case["api"].get(path, headers=case["header"])
    assert response.status_code == 200, response.text
    report = response.json()["data"]
    ScoreReportData.model_validate(report)
    assert len(report["items"]) == 1
    item = report["items"][0]
    assert item["outcome"] == "assessed" and item["reason_code"] == "supported"
    assert Decimal(item["estimated_score"]) == 5
    assert {citation["kind"] for citation in item["citations"]} == {"tender", "draft"}
    citation_source = {**source, "quote": QUOTE}
    tender = next(citation for citation in item["citations"] if citation["kind"] == "tender")
    assert tender["source"] == citation_source

    assert len(case["score_vendor"].requests) == 1
    request = case["score_vendor"].requests[0]
    tender_ref = request["items"][0]["tender_ref"]
    sent = next(row["text"] for row in request["context"]["texts"] if row["ref"] == tender_ref)
    assert sent == source["quote"]
    assert QUOTE in sent and case["outside_repeat"] not in json.dumps(request, ensure_ascii=False)
    assert len(report["report"]["usage_record_ids"]) == 1
    assert (await case["api"].get(path, headers=case["headers"][1])).status_code == 404

    org = case["tenants"]["orgs"][0]
    async with case["app"].state.db.transaction(org) as session:
        stored = (
            await session.scalars(
                select(ScoreReportItem).where(ScoreReportItem.report_id == UUID(report_id))
            )
        ).one()
        assert stored.outcome == "assessed" and stored.reason_code == "supported"
        assert stored.estimated_score == Decimal("5")
        citation = (
            await session.scalars(
                select(ScoreItemCitation).where(
                    ScoreItemCitation.report_id == UUID(report_id),
                    ScoreItemCitation.score_item_id == stored.id,
                    ScoreItemCitation.kind == "tender",
                )
            )
        ).one()
        assert citation.quote == QUOTE and citation.source == citation_source
        assert citation.document_id == UUID(source["document_id"])
        assert citation.chunk_id == UUID(source["chunk_id"])
        requirement = await session.get(Requirement, stored.requirement_id)
        assert requirement is not None and requirement.quote == source["quote"]
        chunk = await session.get(Chunk, citation.chunk_id)
        assert chunk is not None and chunk.text == case["source_original"]

    artifact = case["tmp_path"] / f"score-pinned-source-db-{repeat_position}.json"
    artifact.write_text(
        json.dumps(
            {
                "repeat_position": repeat_position,
                "pinned_source": source,
                "source_original": case["source_original"],
                "provider_request": request,
                "preview": preview,
                "job": terminal,
                "report": report,
                "rerun": (
                    ".venv/bin/python -m pytest server/tests/test_score_pinned_source_db.py "
                    f"-k {repeat_position} -q"
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    assert json.loads(artifact.read_text(encoding="utf-8"))["report"] == report
