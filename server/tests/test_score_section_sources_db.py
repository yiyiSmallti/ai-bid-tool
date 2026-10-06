"""End-to-end multi-citation section persistence and review regression.

Failure inventory before implementation: lose a non-anchor citation; allow a
foreign org/task/extraction binding; change a source or invent a quotation during
revision; allow another domain to remove sources; mutate published history;
accept confirmation after the second source drifts; lose legacy NULL reads.
Requires an explicitly authorized isolated PostgreSQL runtime. Artifacts contain
only the synthetic tender and can be reproduced with this module's pytest command.
"""

import json
from copy import deepcopy
from uuid import UUID, uuid4

import httpx
import pytest
import test_score_api
from app.models.entities import Requirement
from app.models.score import ScoreRubricSection, ScoreRubricSet
from app.services.auth import ROLE_SCOPES, Identity, set_actor_context
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from test_score_api import RubricVendor
from test_score_api import rubric_case as rubric_case
from test_score_review import base, classify_all, decision, replacement, role, show

original_rubric_input_case = test_score_api.rubric_input_case


@pytest.fixture
async def rubric_input_case(original_rubric_input_case, monkeypatch):
    case = original_rubric_input_case
    async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
        extra = await session.get(Requirement, UUID(case["requirements"][1]["id"]))
        assert extra is not None
        extra.category = "scoring"
    original_call = RubricVendor.__call__

    async def multi_source(self, request):
        response = await original_call(self, request)
        if self is not case["vendor"] or self.stages[-1] != "structure":
            return response
        payload = self.requests[-1]
        refs = {
            entry["ref"]: entry["text"].split("\n招标原文：\n", 1)[-1]
            for entry in payload["context"]["texts"]
        }
        result = response.json()
        output = json.loads(result["choices"][0]["message"]["content"])
        output["sections"][0]["citations"] = [
            {"ref": entry["tender_ref"], "quote": refs[entry["tender_ref"]]}
            for entry in payload["requirements"]
        ]
        assert len(output["sections"][0]["citations"]) == 2
        result["choices"][0]["message"]["content"] = json.dumps(output)
        return httpx.Response(200, json=result)

    monkeypatch.setattr(RubricVendor, "__call__", multi_source)
    return case


async def test_multi_sources_persist_read_isolate_and_revise(rubric_case):
    case = rubric_case
    report = await classify_all(case)
    section = report["sections"][0]
    assert len(section["sources"]) == 2 and "source" not in section
    by_requirement = {entry["id"]: entry["source"] for entry in case["requirements"]}
    for citation in section["sources"]:
        assert citation["source"] == by_requirement[citation["requirement_id"]]
        assert citation["quote"] == citation["source"]["quote"]
    async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
        stored = await session.get(ScoreRubricSection, UUID(section["id"]))
        assert stored is not None and stored.sources == section["sources"]
        assert str(stored.requirement_id) == section["sources"][0]["requirement_id"]
        assert stored.source == section["sources"][0]["source"]
    async with case["app"].state.db.transaction(case["tenants"]["orgs"][1]) as session:
        assert await session.get(ScoreRubricSection, UUID(section["id"])) is None
        assert not list(await session.scalars(select(ScoreRubricSection.sources)))
    assert (await case["api"].get(base(case), headers=case["headers"][1])).status_code == 404

    body = replacement(report)
    # Even a real substring is not a previously selected verified quotation.
    body["sections"][0]["sources"][1]["quote"] = section["sources"][1]["quote"][:-1]
    denied = await case["api"].post(base(case) + "/revisions", headers=case["header"], json=body)
    assert (
        denied.status_code == 409
        and denied.json()["data"]["error"]["code"] == "invalid_revision_source"
    )
    body = replacement(report)
    body["sections"][0]["sources"] = body["sections"][0]["sources"][1:]
    role(case, "technical")
    denied = await case["api"].post(base(case) + "/revisions", headers=case["header"], json=body)
    assert denied.status_code == 403
    role(case, "bidder")
    revised = await case["api"].post(base(case) + "/revisions", headers=case["header"], json=body)
    assert revised.status_code == 200, revised.text
    current = revised.json()["data"]
    assert current["sections"][0]["sources"] == section["sources"][1:]
    assert (await show(case))["sections"][0]["sources"] == section["sources"]
    assert current["sections"][0]["confirmed_by"] is None
    async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
        stored = await session.get(ScoreRubricSection, UUID(current["sections"][0]["id"]))
        assert (
            stored is not None
            and str(stored.requirement_id) == section["sources"][1]["requirement_id"]
        )
    artifact = case["tmp_path"] / "rubric-section-sources-db.json"
    artifact.write_text(
        json.dumps({"prior": report, "revision": current}, indent=2), encoding="utf-8"
    )
    assert json.loads(artifact.read_text(encoding="utf-8"))["revision"] == current


@pytest.mark.parametrize("source_index", [0, 1], ids=["anchor-reference", "second-source"])
async def test_second_source_drift_refuses_read_and_confirmation(rubric_case, source_index):
    case = rubric_case
    report = await classify_all(case)
    section = report["sections"][0]
    async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
        requirement = await session.get(
            Requirement, UUID(section["sources"][source_index]["requirement_id"])
        )
        assert requirement is not None
        requirement.quote = "Synthetic changed second pinned source"
    # A changed pinned Source no longer resolves the immutable input identity.
    # score_inputs.require_dependencies masks that with 404 for both the legacy
    # anchor and additional sources, before the review-conflict gates run.
    response = await case["api"].get(base(case), headers=case["header"])
    assert response.status_code == 404
    assert response.json()["data"]["error"]["code"] == "not_found"
    denied = await case["api"].post(
        f"{base(case)}/sections/{section['id']}/decisions",
        headers=case["header"],
        json=decision(report, section),
    )
    assert denied.status_code == 404
    assert denied.json()["data"]["error"] == response.json()["data"]["error"]
    async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
        assert (
            await session.scalar(
                text("SELECT rubric_current_inputs(:org,:rubric)"),
                {
                    "org": case["tenants"]["orgs"][0],
                    "rubric": UUID(report["rubric"]["id"]),
                },
            )
            is False
        )


@pytest.mark.parametrize("corruption", ["requirement", "chunk", "quote", "empty"])
async def test_database_validates_every_section_source(rubric_case, corruption):
    case = rubric_case
    report = case["rubric"]
    sources = deepcopy(report["sections"][0]["sources"])
    if corruption == "requirement":
        sources[1]["requirement_id"] = case["requirements"][2]["id"]
    elif corruption == "chunk":
        sources[1]["source"]["chunk_id"] = sources[0]["source"]["chunk_id"]
        sources[1]["source"]["quote"] = "Synthetic wrong source binding"
    elif corruption == "quote":
        sources[1]["quote"] = "Synthetic invented quotation"
    elif corruption == "empty":
        sources = []
    async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
        assert (
            await session.scalar(
                text("SELECT rubric_section_sources_valid(:org,:rubric,CAST(:sources AS jsonb))"),
                {
                    "org": case["tenants"]["orgs"][0],
                    "rubric": UUID(report["rubric"]["id"]),
                    "sources": json.dumps(sources),
                },
            )
            is False
        )
    with pytest.raises(DBAPIError) as rejected:
        async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
            actor = Identity(
                case["tenants"]["users"][0],
                case["tenants"]["orgs"][0],
                ROLE_SCOPES["bidder"],
                "bidder",
            )
            await set_actor_context(session, actor)
            prior = await session.get(ScoreRubricSet, UUID(report["rubric"]["id"]))
            original = await session.get(ScoreRubricSection, UUID(report["sections"][0]["id"]))
            assert prior is not None and original is not None
            values = {
                column.key: getattr(prior, column.key)
                for column in ScoreRubricSet.__table__.columns
            }
            values.update(
                id=uuid4(),
                prior_rubric_id=prior.id,
                version=prior.version + 1,
                actor_kind="session",
                actor_token_id=None,
            )
            candidate = ScoreRubricSet(**values)
            session.add(candidate)
            await session.flush()
            values = {
                column.key: getattr(original, column.key)
                for column in ScoreRubricSection.__table__.columns
            }
            values.update(id=uuid4(), rubric_id=candidate.id, sources=sources)
            session.add(ScoreRubricSection(**values))
            await session.flush()
    assert rejected.value.orig.sqlstate == "23514"
    with pytest.raises(DBAPIError), case["admin_engine"].begin() as connection:
        connection.execute(text("SET LOCAL ROLE bid_app"))
        connection.execute(
            text("SELECT set_config('app.current_org',:org,true)"),
            {"org": str(case["tenants"]["orgs"][0])},
        )
        connection.execute(
            text("UPDATE score_rubric_sections SET sources=CAST(:sources AS jsonb) WHERE id=:id"),
            {
                "sources": json.dumps(sources),
                "id": report["sections"][0]["id"],
            },
        )


async def test_legacy_null_source_expands_only_on_read(rubric_case):
    case = rubric_case
    section = case["rubric"]["sections"][0]
    # Simulate an already-existing pre-0045 snapshot. Runtime writes cannot do this.
    with case["admin_engine"].begin() as connection:
        connection.execute(
            text("ALTER TABLE score_rubric_sections DISABLE TRIGGER rubric_immutable")
        )
        connection.execute(
            text("UPDATE score_rubric_sections SET sources=NULL WHERE id=:id"),
            {"id": section["id"]},
        )
        connection.execute(
            text("ALTER TABLE score_rubric_sections ENABLE TRIGGER rubric_immutable")
        )
    report = await show(case)
    assert report["sections"][0]["sources"] == section["sources"][:1]
    async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
        stored = await session.get(ScoreRubricSection, UUID(section["id"]))
        assert stored is not None and stored.sources is None
