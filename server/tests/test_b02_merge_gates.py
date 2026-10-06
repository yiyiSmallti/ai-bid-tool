"""B02 integration with multi-source rubric sections and precise co-sign gaps.

Failure scenarios identified before compatibility edits:
* Adding requirement-review gaps must retain the final co-sign migration's
  specific single-domain response cause, with no multi-domain-only cosign label.
* Requirement causes precede response causes; multi-domain approval loss stays
  the last cosign_required cause and is never hidden by the B02 prefix.
* Reopening only a section's second requirement must block rubric acceptance,
  while unchanged cited content remains available for provisional preparation.
* Changing the non-anchor Source invalidates both the B02 review and rubric
  bindings. All section source IDs must be checked, not only the legacy anchor.
* SQL checks reuse verified requirement pins and the existing batched citation
  verifier; compatibility must not add one locator call per section reference.

These are real API/PostgreSQL acceptance checks, with fake model Providers.
No database/service process is started or reconfigured by this module.
"""

import json
from pathlib import Path
from uuid import UUID

import pytest
from app.models.entities import Requirement
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from task_fixtures import confirm_requirements_async
from test_check import publish_draft
from test_fast_citation import counted_locator
from test_requirement_confirmation import decision as requirement_decision
from test_requirement_confirmation import post_decision, review
from test_score_api import rubric_case as rubric_case  # noqa: F401
from test_score_review import base, confirm_contents, decision
from test_score_section_sources_db import (
    original_rubric_input_case as original_rubric_input_case,  # noqa: F401
)
from test_score_section_sources_db import rubric_input_case as rubric_input_case  # noqa: F401
from test_team_cosign_acceptance import attach_material
from test_team_cosign_consumers import cosign_scope, signature, submit
from test_team_workflow_membership import workflow

ARTIFACTS = Path(__file__).resolve().parents[2] / "data/work/b02-merge/gate-regressions"


def record(name, **data):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / f"{name}.json").write_text(
        json.dumps(
            {
                "command": ".venv/bin/pytest server/tests/test_b02_merge_gates.py -q",
                "scenario": name,
                **data,
            },
            indent=2,
            default=str,
        )
        + "\n"
    )


async def rubric_view(case):
    response = await case["api"].get("/v4" + base(case), headers=case["header"])
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def review_action(api, header, requirement_id, action):
    current = await review(api, header, requirement_id)
    response = await post_decision(
        api, header, requirement_id, requirement_decision(current, action)
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


@pytest.mark.parametrize("multiple_domains", [False, True], ids=["single", "multi"])
@pytest.mark.parametrize("cause", ["material", "quote", "citation", "authority"])
async def test_published_gap_orders_requirement_and_specific_response_causes(
    api, application, headers, tenants, admin_engine, tmp_path, multiple_domains, cause
):
    scope = await cosign_scope(api, headers, tenants, admin_engine, required=multiple_domains)
    if cause == "material":
        await attach_material(api, headers[0], scope, tmp_path)
    await submit(api, headers[0], scope)
    for domain in ("commercial", "technical") if multiple_domains else ("technical",):
        signed = await signature(api, scope, domain)
        assert signed.status_code == 200, signed.text
    current = await api.get(f"/cards/{scope['card']['id']}", headers=headers[0])
    assert current.status_code == 200, current.text
    scope["card"] = current.json()["data"]
    assert scope["card"]["state"] == "confirmed"
    rid = scope["requirement_ids"][0]
    historical = await publish_draft(
        api, application, headers[0], str(scope["task_id"]), str(scope["job_id"])
    )

    if cause == "authority":
        state = await workflow(api, headers[0], scope["task_id"])
        removed = await api.post(
            f"/tasks/{scope['task_id']}/members/{scope['members']['technical']['user']}/remove",
            headers=headers[0],
            json={"expected_revision": state["revision"], "reason": "Synthetic signer removal"},
        )
        assert removed.status_code == 200, removed.text
    else:
        with Session(admin_engine) as session, session.begin():
            session.execute(
                text("SELECT set_config('app.current_org',:org,true)"),
                {"org": str(scope["org_id"])},
            )
            if cause == "material":
                changed = session.execute(
                    text(
                        "UPDATE task_certificates SET active=false WHERE task_id=:task AND active RETURNING id"
                    ),
                    {"task": scope["task_id"]},
                )
                assert changed.scalars().all()
            elif cause == "quote":
                requirement = session.get(Requirement, rid)
                assert requirement is not None and len(requirement.quote) > 1
                # A smaller literal span is still cited but differs from the response's pin.
                requirement.quote = requirement.quote[1:]
            else:
                session.execute(
                    text("UPDATE chunks SET citation_verified=false WHERE id=:chunk"),
                    {"chunk": scope["chunk_id"]},
                )
    if cause in {"material", "authority"}:
        await review_action(api, headers[0], rid, "reopen")
        expected = ["requirement_unconfirmed"]
    else:
        expected = ["requirement_invalidated"]
    if cause == "material":
        expected.append("stale_material")
    elif cause == "quote" or cause == "authority" and not multiple_domains:
        expected.append("needs_reconfirmation")
    elif cause == "citation":
        expected.append("invalid_citation")
    if multiple_domains:
        expected.append("cosign_required")

    historical_read = await api.get(f"/v4/drafts/{historical['draft_id']}", headers=headers[0])
    assert historical_read.status_code == 200, historical_read.text
    old_view = historical_read.json()["data"]
    assert old_view["validity"] == "stale"
    assert len(old_view["gaps"]) == 1 and old_view["gaps"][0]["reasons"] == expected
    assert not any(old_view["tables"].values())

    output = await publish_draft(
        api, application, headers[0], str(scope["task_id"]), str(scope["job_id"])
    )
    shown = await api.get(f"/v4/drafts/{output['draft_id']}", headers=headers[0])
    assert shown.status_code == 200, shown.text
    value = shown.json()["data"]
    assert value["validity"] == "current" and value["completion"] == "partial"
    assert len(value["gaps"]) == 1 and value["gaps"][0]["reasons"] == expected
    with admin_engine.connect() as connection:
        persisted = connection.scalar(
            text(
                "SELECT gap_reasons FROM response_items WHERE draft_id=:draft AND requirement_id=:req"
            ),
            {"draft": UUID(output["draft_id"]), "req": rid},
        )
    assert persisted == expected
    record(
        f"gap-{cause}-{'multi' if multiple_domains else 'single'}",
        requirement_id=rid,
        draft_id=output["draft_id"],
        gap_reasons=expected,
    )


async def confirm_scoring_sources(case):
    org = case["tenants"]["orgs"][0]
    async with case["app"].state.db.transaction(org) as session:
        ids = list(
            await session.scalars(
                select(Requirement.id).where(
                    Requirement.task_id == UUID(case["task"]),
                    Requirement.job_id == UUID(case["extraction"]),
                    Requirement.category == "scoring",
                )
            )
        )
        await confirm_requirements_async(
            session, org, UUID(case["task"]), ids, settings=case["app"].state.processor.settings
        )
    return ids


async def test_second_section_source_reopen_blocks_whole_review_and_score(rubric_case):
    case = rubric_case
    await confirm_scoring_sources(case)
    report = await confirm_contents(case)
    sources = report["sections"][0]["sources"]
    assert len(sources) == 2 and sources[0]["requirement_id"] != sources[1]["requirement_id"]
    second = sources[1]["requirement_id"]
    rubric_id = UUID(report["rubric"]["id"])
    org = case["tenants"]["orgs"][0]
    await review_action(case["api"], case["header"], second, "reopen")
    first = await review(case["api"], case["header"], sources[0]["requirement_id"])
    assert first["requirement"]["state"] == "confirmed"
    async with case["app"].state.db.transaction(org) as session:
        parameters = {"org": org, "rubric": rubric_id}
        assert await session.scalar(
            text("SELECT rubric_section_bindings_current(:org,:rubric)"), parameters
        )
        assert await session.scalar(text("SELECT rubric_current_inputs(:org,:rubric)"), parameters)
        assert not await session.scalar(
            text("SELECT requirement_rubric_confirmed(:org,:rubric)"), parameters
        )
        assert not await session.scalar(text("SELECT rubric_complete(:org,:rubric)"), parameters)
    denied = await case["api"].post(
        "/v4" + base(case) + "/decisions",
        headers=case["header"],
        json=decision(report, report["rubric"]),
    )
    assert denied.status_code == 409, denied.text
    assert denied.json()["data"]["error"]["code"] == "requirement_unconfirmed"

    await review_action(case["api"], case["header"], second, "confirm")
    current = await rubric_view(case)
    accepted = await case["api"].post(
        "/v4" + base(case) + "/decisions",
        headers=case["header"],
        json=decision(current, current["rubric"]),
    )
    assert accepted.status_code == 200, accepted.text
    current = await rubric_view(case)
    await review_action(case["api"], case["header"], second, "reopen")
    # Reassembly eliminates an unrelated stale-draft refusal: only the second
    # scoring interpretation is unconfirmed, and every response gap is current.
    output = await publish_draft(
        case["api"], case["app"], case["header"], case["task"], case["extraction"]
    )
    score_preview = await case["api"].post(
        f"/v4/tasks/{case['task']}/scores/preview",
        headers=case["header"],
        json={
            "draft_id": output["draft_id"],
            "rubric_id": str(rubric_id),
            "assessment_date": "2026-10-05",
            "dry_run": True,
        },
    )
    assert score_preview.status_code == 409, score_preview.text
    assert score_preview.json()["data"]["error"]["code"] == "requirement_unconfirmed"
    async with case["app"].state.db.transaction(org) as session:
        assert not await session.scalar(
            text("SELECT score_inputs_current(:org,:draft,:rubric,:revision)"),
            {
                "org": org,
                "draft": UUID(output["draft_id"]),
                "rubric": rubric_id,
                "revision": current["rubric"]["revision"],
            },
        )
    record(
        "second-section-source",
        rubric_id=rubric_id,
        source_ids=[entry["requirement_id"] for entry in sources],
        whole_review_error=denied.json()["data"]["error"]["code"],
        score_error=score_preview.json()["data"]["error"]["code"],
    )


async def test_section_review_gate_preserves_shared_locator_count(rubric_case):
    case = rubric_case
    await confirm_scoring_sources(case)
    report = await rubric_view(case)
    assert len(report["sections"][0]["sources"]) == 2
    rubric_id = UUID(report["rubric"]["id"])
    parameters = {"org": case["tenants"]["orgs"][0], "rubric": rubric_id}
    with counted_locator(case["admin_engine"], case["tenants"]) as connection:
        assert connection.scalar(
            text("SELECT requirement_rubric_confirmed(:org,:rubric)"), parameters
        )
        assert connection.scalar(text("SELECT count(*) FROM citation_locator_calls")) == 0
        assert connection.scalar(text("SELECT rubric_current_inputs(:org,:rubric)"), parameters)
        calls = connection.execute(
            text("SELECT source_text,quote FROM citation_locator_calls")
        ).all()
        expected = set(
            connection.execute(
                text(
                    "SELECT DISTINCT c.text,r.quote FROM requirements r JOIN chunks c ON c.org_id=r.org_id AND c.id=r.chunk_id "
                    "WHERE r.org_id=:org AND r.job_id=:job AND r.category='scoring'"
                ),
                {"org": parameters["org"], "job": UUID(case["extraction"])},
            ).all()
        )
        assert set(calls) == expected and len(calls) == len(expected)
    record(
        "section-source-locator-count",
        rubric_id=rubric_id,
        review_gate_calls=0,
        shared_batch_calls=len(calls),
    )


@pytest.mark.parametrize("mutation", ["category", "verification"])
async def test_non_anchor_identity_preserving_drift_is_readable_stale(rubric_case, mutation):
    case = rubric_case
    await confirm_scoring_sources(case)
    report = await confirm_contents(case)
    sources = report["sections"][0]["sources"]
    assert len(sources) == 2
    second = sources[1]
    org = case["tenants"]["orgs"][0]
    async with case["app"].state.db.transaction(org) as session:
        if mutation == "category":
            await session.execute(
                text("UPDATE requirements SET category='technical' WHERE id=:id"),
                {"id": UUID(second["requirement_id"])},
            )
        else:
            await session.execute(
                text("UPDATE chunks SET citation_verified=false WHERE id=:id"),
                {"id": UUID(second["source"]["chunk_id"])},
            )
    shown = await case["api"].get("/v4" + base(case), headers=case["header"])
    assert shown.status_code == 200, shown.text
    current = shown.json()["data"]
    assert current["sections"][0]["sources"] == sources
    assert current["rubric"]["requirement_review"]["state"] == "stale"
    denied = await case["api"].post(
        "/v4" + base(case) + "/decisions",
        headers=case["header"],
        json=decision(report, report["rubric"]),
    )
    assert denied.status_code == 409, denied.text
    # An unverified citation is rejected by the fixed scoring-input snapshot;
    # classification drift instead changes the preparation manifest.
    assert denied.json()["data"]["error"]["code"] == (
        "score_rubric_input_integrity" if mutation == "verification" else "rubric_input_changed"
    )
    async with case["app"].state.db.transaction(org) as session:
        assert not await session.scalar(
            text("SELECT requirement_rubric_confirmed(:org,:rubric)"),
            {"org": org, "rubric": UUID(report["rubric"]["id"])},
        )
    record(
        f"non-anchor-stale-{mutation}",
        rubric_id=report["rubric"]["id"],
        source_ids=[entry["requirement_id"] for entry in sources],
        readiness=current["rubric"]["requirement_review"]["state"],
        decision_error=denied.json()["data"]["error"]["code"],
    )
