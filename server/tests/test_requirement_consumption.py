"""B02 consumer and board acceptance through actual API/worker/PostgreSQL paths.

Failure inventory: preparing an unconfirmed requirement implies acceptance;
response confirmation or comply-only bypasses the source gate; an all-gap draft
includes candidate response text; reopening leaves old draft rows usable; legacy
board enums expose new values or count pending requirements as response-complete;
opt-in progress silently chooses an extraction; foreign org projections leak data;
a whole-rubric decision accepts an outstanding fixed scoring requirement.

These tests require an explicitly supplied isolated bid_test PostgreSQL runtime.
They do not start services, bypass triggers or use automatic source confirmation.
Artifacts contain only synthetic public responses, without authentication headers.
"""

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.schemas.requirement_confirmation import (
    RequirementBoardData,
    RequirementBoardItem,
    RequirementProgressData,
    RequirementReviewData,
)
from app.schemas.team_workflow import BoardData, BoardRow
from task_fixtures import reviewer_header
from test_check import publish_draft
from test_response_cards import (
    create_card,
    create_tender,
    phase_one_client,
    require_action,
)
from test_score_api import rubric_case as rubric_case
from test_score_api import rubric_input_case as rubric_input_case
from test_score_review import base, confirm_contents, decision, show

ARTIFACTS = Path(__file__).resolve().parents[2] / "data/work/requirement-confirmation/consumers"
CANDIDATE_RESPONSE = "SYNTHETIC RESPONSE THAT MUST NOT ENTER AN UNCONFIRMED DRAFT"


def artifact(name, **values):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / f"{name}.json").write_text(
        json.dumps(
            {"command": "uv run pytest -q server/tests/test_requirement_consumption.py", **values},
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


def zero_cost(result):
    cost = result["cost"]
    assert cost["basis"] == "zero"
    assert cost["charge"] == cost["task_amount"] == "0"
    assert cost["llm_tokens"] == cost["ocr_pages"] == 0
    assert cost["unpriced_calls"] == cost["unresolved_calls"] == 0
    assert cost["usd"] == 0


async def review(api, headers, requirement_id):
    response = await api.get(f"/v4/requirements/{requirement_id}/review", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    RequirementReviewData.model_validate(data)
    zero_cost(response.json())
    return data


async def source_decision(api, headers, requirement_id, action):
    observed = (await review(api, headers, requirement_id))["requirement"]
    response = await api.post(
        f"/v4/requirements/{requirement_id}/review-decisions",
        headers=headers,
        json={
            "request_id": str(uuid4()),
            "action": action,
            "expected_revision": observed["revision"],
            "expected_review_hash": observed["review_hash"],
            "reason": "Synthetic human inspected the exact tender source before this decision.",
        },
    )
    assert response.status_code == 200, response.text
    RequirementReviewData.model_validate(response.json()["data"])
    zero_cost(response.json())
    return response.json()


async def draft_preview(api, headers, task, extraction):
    response = await api.post(
        f"/v4/tasks/{task}/drafts",
        headers=headers,
        json={"extraction_job_id": extraction, "dry_run": True},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("operation", ["confirm", "comply_only"])
async def test_requirement_confirmation_gates_response_and_disposition(
    tenants, tmp_path, admin_engine, operation
):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        owner = headers[0]
        task, _, extraction, requirements = await create_tender(
            api, app, owner, tmp_path, suffix=operation, confirmed=False
        )
        requirement = requirements[2]
        _, professional = await reviewer_header(
            api, admin_engine, tenants["orgs"][0], UUID(task), "technical"
        )
        initial = await review(api, owner, requirement["id"])
        assert initial["requirement"]["state"] in {"unconfirmed", "legacy_unconfirmed"}
        card = None
        if operation == "confirm":
            card = await create_card(
                api,
                owner,
                task,
                extraction,
                requirement,
                {
                    "response_kind": "commitment",
                    "response_text": CANDIDATE_RESPONSE,
                    "deviation": "none",
                    "deviation_note": "Synthetic source acceptance test",
                },
            )
            card = await require_action(api, professional, card, "submit")
            path = f"/v4/cards/{card['id']}/actions"
            body = {
                "action": "confirm",
                "expected_revision": card["revision"],
                "reviewed_evidence_ids": [],
                "reviewed_warning_codes": card["warning_codes"],
                "reason": "Synthetic professional response review",
            }
        else:
            path = f"/v4/tasks/{task}/cards/dispositions"
            body = {
                "extraction_job_id": extraction,
                "items": [
                    {
                        "requirement_id": requirement["id"],
                        "expected_revision": None,
                        "disposition": "comply_only",
                        "reason": "Synthetic procedural commitment",
                    }
                ],
            }
        denied = await api.post(path, headers=professional, json=body)
        assert denied.status_code == 409, denied.text
        assert denied.json()["data"]["error"]["code"] == "requirement_unconfirmed"
        after_denial = await review(api, owner, requirement["id"])
        assert after_denial["requirement"]["revision"] == initial["requirement"]["revision"]
        before = await draft_preview(api, owner, task, extraction)
        assert before["data"]["response_requirements"] == 0
        assert before["data"]["gap_requirements"] == len(requirements)
        assert before["data"]["gap_reasons"]["requirement_unconfirmed"] == len(requirements)
        confirmed = await source_decision(api, owner, requirement["id"], "confirm")
        assert confirmed["data"]["requirement"]["state"] == "confirmed"
        accepted = await api.post(path, headers=professional, json=body)
        assert accepted.status_code == 200, accepted.text
        artifact(
            operation,
            requirement_confirmation=confirmed,
            blocked=denied.json(),
            accepted=accepted.json(),
            draft_before=before,
        )


async def test_unconfirmed_draft_gap_and_reopen_retire_accepted_rows(
    tenants, tmp_path, admin_engine
):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        owner = headers[0]
        task, _, extraction, requirements = await create_tender(
            api, app, owner, tmp_path, suffix="draft-reopen", confirmed=False
        )
        requirement = requirements[2]
        card = await create_card(
            api,
            owner,
            task,
            extraction,
            requirement,
            {
                "response_kind": "commitment",
                "response_text": CANDIDATE_RESPONSE,
                "deviation": "none",
                "deviation_note": "Synthetic draft test",
            },
        )
        first = await publish_draft(api, app, owner, task, extraction)
        pending = await api.get(f"/v4/drafts/{first['draft_id']}", headers=owner)
        assert pending.status_code == 200, pending.text
        assert pending.json()["data"]["completion"] == "partial"
        assert len(pending.json()["data"]["gaps"]) == len(requirements)
        assert CANDIDATE_RESPONSE not in pending.text
        assert all(
            "requirement_unconfirmed" in row["reasons"] for row in pending.json()["data"]["gaps"]
        )
        await source_decision(api, owner, requirement["id"], "confirm")
        _, professional = await reviewer_header(
            api, admin_engine, tenants["orgs"][0], UUID(task), "technical"
        )
        card = await require_action(api, professional, card, "submit")
        card = await require_action(
            api,
            professional,
            card,
            "confirm",
            reviewed_evidence_ids=[],
            reviewed_warning_codes=card["warning_codes"],
            reason="Synthetic complete response review",
        )
        second = await publish_draft(api, app, owner, task, extraction)
        accepted = await api.get(f"/v4/drafts/{second['draft_id']}", headers=owner)
        assert accepted.status_code == 200, accepted.text
        rows = [row for entries in accepted.json()["data"]["tables"].values() for row in entries]
        assert [row["requirement_id"] for row in rows] == [requirement["id"]]
        assert rows[0]["response_text"] == CANDIDATE_RESPONSE
        reopened = await source_decision(api, owner, requirement["id"], "reopen")
        stale = await api.get(f"/v4/drafts/{second['draft_id']}", headers=owner)
        assert stale.status_code == 200, stale.text
        assert stale.json()["data"]["validity"] == "stale"
        assert requirement["id"] in stale.json()["data"]["invalidated_requirements"]
        fresh = await draft_preview(api, owner, task, extraction)
        assert fresh["data"]["response_requirements"] == 0
        assert fresh["data"]["gap_reasons"]["requirement_unconfirmed"] == len(requirements)
        artifact(
            "draft-reopen",
            candidate=pending.json(),
            accepted=accepted.json(),
            reopened=reopened,
            stale=stale.json(),
            preview=fresh,
        )


async def test_requirement_board_optin_progress_legacy_and_org_isolation(tenants, tmp_path):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task, _, extraction, requirements = await create_tender(
            api, app, headers[0], tmp_path, suffix="board", confirmed=False
        )
        params = {"extraction_job_id": extraction}
        default = await api.get(f"/v4/tasks/{task}/board", headers=headers[0], params=params)
        assert default.status_code == 200, default.text
        BoardData.model_validate(default.json()["data"])
        for row in default.json()["items"]:
            BoardRow.model_validate(row)
            assert row["bucket"] == "gap"
            assert "unconfirmed" in row["blockers"]
            assert row["next_actions"][0]["code"] == "view"
        optin = await api.get(
            f"/v4/tasks/{task}/board",
            headers=headers[0],
            params={**params, "view": "requirement-review"},
        )
        assert optin.status_code == 200, optin.text
        data = RequirementBoardData.model_validate(optin.json()["data"])
        assert data.buckets.total == data.buckets.requirement_review == len(requirements)
        assert data.buckets.responses.total == 0
        for row in optin.json()["items"]:
            item = RequirementBoardItem.model_validate(row)
            assert item.requirement.bucket == "requirement_review"
            assert item.requirement.next_action == "confirm_requirement"
            assert item.requirement.next_actor.user_id == tenants["users"][0]
            assert not item.requirement.counts_as_response_complete
        progress = await api.get(
            f"/v4/tasks/{task}/progress",
            headers=headers[0],
            params={**params, "view": "requirement-review"},
        )
        assert progress.status_code == 200, progress.text
        projected = RequirementProgressData.model_validate(progress.json()["data"])
        assert projected.scope.extraction_job_id == UUID(extraction)
        assert projected.scope.summary.confirmed == 0
        missing = await api.get(
            f"/v4/tasks/{task}/progress", headers=headers[0], params={"view": "requirement-review"}
        )
        assert missing.status_code in {400, 422}, missing.text
        legacy = await api.get(f"/tasks/{task}/board", headers=headers[0], params=params)
        assert legacy.status_code == 200, legacy.text
        assert "requirement_review" not in json.dumps(legacy.json())
        assert "confirm_requirement" not in json.dumps(legacy.json())
        for part in ("board", "progress"):
            foreign = await api.get(
                f"/v4/tasks/{task}/{part}",
                headers=headers[1],
                params={**params, "view": "requirement-review"},
            )
            assert foreign.status_code == 404, foreign.text
            assert task not in foreign.text and extraction not in foreign.text
            forbidden_legacy = await api.get(
                f"/tasks/{task}/{part}",
                headers=headers[0],
                params={**params, "view": "requirement-review"},
            )
            assert forbidden_legacy.status_code == 404, forbidden_legacy.text
        artifact(
            "board",
            default=default.json(),
            optin=optin.json(),
            progress=progress.json(),
            legacy=legacy.json(),
        )


async def test_whole_rubric_confirmation_requires_all_fixed_scoring_requirements(rubric_case):
    case = rubric_case
    report = await confirm_contents(case)
    requirement = next(row for row in case["requirements"] if row["category"] == "scoring")
    await source_decision(case["api"], case["header"], requirement["id"], "reopen")
    denied = await case["api"].post(
        base(case) + "/decisions", headers=case["header"], json=decision(report, report["rubric"])
    )
    assert denied.status_code == 409, denied.text
    assert denied.json()["data"]["error"]["code"] == "requirement_unconfirmed"
    assert (await show(case))["rubric"]["state"] != "confirmed"
    await source_decision(case["api"], case["header"], requirement["id"], "confirm")
    current = await show(case)
    accepted = await case["api"].post(
        base(case) + "/decisions", headers=case["header"], json=decision(current, current["rubric"])
    )
    assert accepted.status_code == 200, accepted.text
    assert (await show(case))["rubric"]["state"] == "confirmed"
    artifact("rubric", rejected=denied.json(), accepted=accepted.json())
