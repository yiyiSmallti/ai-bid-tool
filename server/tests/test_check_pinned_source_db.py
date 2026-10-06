"""Pinned-source semantic citations through the public check API and DB worker.

Failure cases: a repeated short quote elsewhere in the page rejects a valid
semantic answer; page context leaks into the request; the full Requirement Source
changes; report/citation rows lose their original source binding; or another tenant
can read the report. Providers use MockTransport and all material is synthetic.
"""

import json
from copy import deepcopy
from uuid import UUID

import httpx
import pytest
from app.models.check import CheckFindingCitation, CheckItem, CheckRun
from app.models.entities import Chunk, Requirement
from app.services import drafts, requirement_consumption
from sqlalchemy import select
from task_fixtures import confirm_requirements_async
from test_check_combined import (
    SemanticVendor,
    combined_preview,
    install_resolver,
    run_combined,
    scope_draft,
    semantic_llm,
    submit_combined,
)
from test_check_combined import combined_scope_case as combined_scope_case  # noqa: F401


class ShortTenderCitationVendor(SemanticVendor):
    """Keep the existing fake's bid citation, citing only a tender excerpt."""

    def __init__(self, quote: str):
        super().__init__()
        self.quote = quote
        self.answers: list[dict] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        response = await super().__call__(request)
        body = response.json()
        answer = json.loads(body["choices"][0]["message"]["content"])
        for item in answer["items"]:
            for citation in item["citations"]:
                if citation["ref"].endswith(".tender"):
                    assert citation["quote"].count(self.quote) == 1
                    citation["quote"] = self.quote
        self.answers.append(answer)
        body["choices"][0]["message"]["content"] = json.dumps(answer)
        return httpx.Response(response.status_code, json=body)


@pytest.mark.parametrize("repeat_position", ["before", "after"])
async def test_combined_check_short_citation_repeated_outside_pinned_source(
    combined_scope_case, tmp_path, monkeypatch, repeat_position
):
    case = combined_scope_case
    requirement = case["requirements"][2]
    source = deepcopy(requirement["source"])
    quote = "within thirty calendar days"
    assert source["quote"].count(quote) == 1
    repeated = f"Synthetic unrelated paragraph: {quote}. Outside the pinned delivery rule."
    org = UUID(case["header"]["X-Org-Id"])
    async with case["app"].state.db.transaction(org) as session:
        chunk = await session.get(Chunk, UUID(source["chunk_id"]))
        original = "\n".join(
            (repeated, chunk.text) if repeat_position == "before" else (chunk.text, repeated)
        )
        assert original.count(quote) == 2
        assert original.count(source["quote"]) == 1
        chunk.text = original
        await session.flush()
        target = await session.get(Requirement, UUID(requirement["id"]))
        review = (await requirement_consumption.effective(session, [target]))[target.id]
        assert review.state == "invalidated"
        await confirm_requirements_async(
            session,
            org,
            UUID(case["task"]),
            [target.id],
            settings=case["app"].state.processor.settings,
        )
        assert (await requirement_consumption.effective(session, [target]))[target.id].confirmed
    await scope_draft(case, response=True)
    vendor = ShortTenderCitationVendor(quote)
    install_resolver(monkeypatch, semantic_llm(tmp_path, vendor))

    preview_response, preview = await combined_preview(case)
    assert preview_response.status_code == 200, preview_response.text
    assert preview["semantic_items"] == 1
    assert vendor.requests == []
    submitted = await submit_combined(case, preview)
    assert submitted.status_code == 200, submitted.text
    terminal = await run_combined(case, submitted.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    result = terminal["result"]
    assert result["completion"] == "complete"
    assert result["unassessed_requirements"] == 0
    assert len(result["usage_record_ids"]) == len(vendor.requests) == 1
    assert vendor.requests[0]["requested_requirement_ids"] == ["r3"]
    texts = {row["ref"]: row["text"] for row in vendor.requests[0]["context"]["texts"]}
    assert texts["r3.tender"] == source["quote"]
    assert repeated not in json.dumps(vendor.requests)
    assert vendor.answers[0]["items"][0]["citations"][0] == {
        "ref": "r3.tender",
        "quote": quote,
    }

    path = f"/checks/{result['report_id']}"
    shown = await case["api"].get(path, headers=case["header"])
    assert shown.status_code == 200, shown.text
    report = shown.json()
    target = next(
        row for row in report["data"]["coverage"] if row["requirement_id"] == requirement["id"]
    )
    assert target["source"] == source
    assert target["semantic_status"] == "assessed"
    assert target["semantic_outcome"] == "no_risk_found"
    assert target["semantic_reason_code"] is None
    tender_citation = {"kind": "tender", "source": {**source, "quote": quote}}
    assert tender_citation in target["semantic_citations"]
    assert {row["kind"] for row in target["semantic_citations"]} == {"tender", "draft"}
    assert (await case["api"].get(path, headers=case["headers"][1])).status_code == 404

    async with case["app"].state.db.transaction(org) as session:
        stored = await session.get(CheckRun, UUID(result["report_id"]))
        assert stored.completion == "complete"
        assert stored.input_hash == drafts.digest(stored.input_manifest)
        assert stored.input_hash == preview["input"]["input_hash"]
        item = await session.get(CheckItem, UUID(target["id"]))
        assert item.source == source
        citation = await session.scalar(
            select(CheckFindingCitation).where(
                CheckFindingCitation.check_item_id == item.id,
                CheckFindingCitation.kind == "tender",
            )
        )
        assert citation.quote == quote
        assert citation.source == tender_citation["source"]
        assert str(citation.chunk_id) == source["chunk_id"]
        current = await session.get(Requirement, UUID(requirement["id"]))
        assert current.quote == source["quote"]
        manifest = stored.input_manifest
    artifact = tmp_path / f"check-pinned-source-{repeat_position}.json"
    artifact.write_text(
        json.dumps(
            {
                "source_original": original,
                "pinned_source": source,
                "provider_quote": quote,
                "preview": preview,
                "job": terminal,
                "manifest": manifest,
                "report": report,
                "requests": vendor.requests,
                "answers": vendor.answers,
            },
            indent=2,
        )
    )
    assert json.loads(artifact.read_text())["report"] == report
