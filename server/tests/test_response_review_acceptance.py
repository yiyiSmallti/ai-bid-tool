"""Retained human confirmation material and consumer gate acceptance."""

import json
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.core.errors import ServiceError
from app.schemas.response_card_contracts import CardAction
from app.services import response_cards
from app.services.auth import ROLE_SCOPES
from response_review_fixtures import (
    attach_material,
    complete,
    confirm,
    refresh_card,
    response_scope,
    revoke,
    submit,
)
from task_access_fixtures import add_member, person, workflow
from test_check import publish_draft
from test_exports import setup_template
from test_response_cards import create_card
from test_score_api import rubric_case as rubric_case
from test_score_api import rubric_input_case as rubric_input_case
from test_score_review import base, confirm_contents, decision, role, show
from test_team_workflow_stream_acceptance import authenticated

ARTIFACTS = Path(__file__).resolve().parents[2] / "data/work/response-review-acceptance"


def artifact(name, **values):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / f"{name}.json").write_text(
        json.dumps(
            {
                "command": " .venv/bin/pytest server/tests/test_response_review_acceptance.py -q",
                **values,
            },
            indent=2,
            default=str,
        )
        + "\n"
    )


async def test_single_human_confirm_advances_revision(api, headers, tenants, admin_engine):
    scope = await response_scope(api, headers, tenants, admin_engine, required=False)
    await submit(api, headers[0], scope)
    card = scope["card"]
    response = await api.post(
        f"/cards/{card['id']}/actions",
        headers=scope["members"]["technical"]["headers"],
        json={
            "expected_revision": card["revision"],
            "action": "confirm",
            "reviewed_evidence_ids": [e["id"] for e in card["evidence"]],
            "reviewed_warning_codes": card["warning_codes"],
            "reason": "Synthetic review",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["state"] == "confirmed"
    assert response.json()["data"]["revision"] == card["revision"] + 1
    artifact("single-human", response=response.json())


async def test_human_reviews_all_materials_and_warnings_before_confirmation(
    api, headers, tenants, admin_engine, tmp_path, monkeypatch
):
    # The fake extraction still stores a verifiable quote in its fixed Chunk;
    # use an explicit material obligation so warning acknowledgment is exercised.
    quote = "Synthetic fixture: provide a current certificate with the response."
    monkeypatch.setattr(
        "test_team_workflow_stream_acceptance.synthetic_page_sources",
        lambda count, chunk_count, **kwargs: ([quote], [quote]),
    )
    scope = await response_scope(api, headers, tenants, admin_engine)
    await attach_material(api, headers[0], scope, tmp_path)
    await submit(api, headers[0], scope)
    pending = scope["card"]
    assert pending["evidence"] and pending["warning_codes"]
    evidence_ids = [e["id"] for e in pending["evidence"]]
    for changes, error_code in (
        ({"reviewed_evidence_ids": []}, "review_mismatch"),
        ({"reviewed_evidence_ids": [str(uuid4())]}, "review_mismatch"),
        ({"reviewed_warning_codes": []}, "warning_review_required"),
        (
            {"reviewed_warning_codes": [*pending["warning_codes"], "unrelated-warning"]},
            "warning_review_required",
        ),
    ):
        refused = await confirm(api, scope, **changes)
        assert refused.status_code == 400, refused.text
        assert refused.json()["data"]["error"]["code"] == error_code
        current = await refresh_card(api, headers[0], scope)
        assert current["state"] == "pending_review" and current["revision"] == pending["revision"]
        assert all(e["confirmed_by"] is None for e in current["evidence"])
    confirmed = await confirm(api, scope)
    assert confirmed.status_code == 200, confirmed.text
    current = confirmed.json()["data"]
    assert current["state"] == "confirmed" and current["revision"] == pending["revision"] + 1
    assert {e["id"] for e in current["evidence"]} == set(evidence_ids)
    assert all(
        e["confirmed_by"] == str(scope["members"]["technical"]["user"])
        and e["confirmed_at"] is not None
        for e in current["evidence"]
    )
    artifact("evidence-human-review", response=confirmed.json())


@pytest.mark.parametrize("kind", ["worker", "agent"])
async def test_constructed_nonhuman_service_identity_cannot_confirm(
    api, application, headers, tenants, admin_engine, kind
):
    scope = await response_scope(api, headers, tenants, admin_engine)
    await submit(api, headers[0], scope)
    body = CardAction(
        expected_revision=scope["card"]["revision"],
        action="confirm",
        reviewed_evidence_ids=[],
        reviewed_warning_codes=scope["card"]["warning_codes"],
        reason="Synthetic warning acknowledgment",
    )
    async with authenticated(application, scope["members"]["technical"]["headers"]) as (
        session,
        actor,
    ):
        fabricated = replace(actor, actor_kind=kind, scopes=set(ROLE_SCOPES["technical"]))
        with pytest.raises(ServiceError) as denied:
            await response_cards.card_action(
                session,
                fabricated,
                UUID(scope["card"]["id"]),
                body,
                application.state.storage,
                application.state.processor.settings,
            )
        assert denied.value.status == 403
    current = await refresh_card(api, headers[0], scope)
    assert current["state"] == "pending_review" and current["revision"] == body.expected_revision


@pytest.mark.parametrize("mutation", ["card_reopen", "requirement_reopen"])
async def test_check_export_reject_old_draft_after_review_invalidation(
    api, application, headers, tenants, admin_engine, mutation
):
    scope = await response_scope(api, headers, tenants, admin_engine)
    selected, binding = await setup_template(api, headers[0], str(scope["task_id"]))
    await complete(api, headers[0], scope)
    bidder = scope["members"]["commercial"]["headers"]
    draft = await publish_draft(
        api, application, bidder, str(scope["task_id"]), str(scope["job_id"])
    )
    check_body = {"draft_id": draft["draft_id"], "assessment_date": "2026-10-05", "dry_run": True}
    check = await api.post(f"/tasks/{scope['task_id']}/checks", headers=bidder, json=check_body)
    assert check.status_code == 200, check.text
    export_body = {
        "draft_id": draft["draft_id"],
        "task_template_id": selected["id"],
        "binding_id": binding["id"],
        "mode": "review_copy",
        "dry_run": True,
    }
    export = await api.post(
        f"/tasks/{scope['task_id']}/export-runs", headers=bidder, json=export_body
    )
    assert export.status_code == 200, export.text
    await revoke(api, headers[0], scope, mutation)
    shown = await api.get(f"/drafts/{draft['draft_id']}", headers=bidder)
    assert shown.status_code == 200, shown.text
    assert shown.json()["data"]["validity"] == "stale"
    refused_check = await api.post(
        f"/tasks/{scope['task_id']}/checks", headers=bidder, json=check_body
    )
    assert refused_check.status_code == 409, refused_check.text
    assert refused_check.json()["data"]["error"]["code"] == "check_stale_draft"
    refused_export = await api.post(
        f"/tasks/{scope['task_id']}/export-runs", headers=bidder, json=export_body
    )
    # Export preflight has always returned 400 with its full blocker report.
    # Co-sign invalidation must use that existing refusal contract, not success.
    assert refused_export.status_code == 400, refused_export.text
    refused_preview = refused_export.json()
    assert refused_preview["ok"] is False
    assert refused_preview["data"]["error"]["code"] == "export_blocked"
    assert refused_preview["data"]["ready"] is False
    assert refused_preview["data"]["gap_count"] == 1
    assert sum(refused_preview["data"]["table_rows"].values()) == 0
    blockers = {
        issue["code"] for issue in refused_preview["data"]["issues"] if issue["severity"] == "block"
    }
    assert "export_stale_draft" in blockers
    blocked = await api.post(
        f"/tasks/{scope['task_id']}/export-runs",
        headers=bidder,
        json={
            **export_body,
            "dry_run": False,
            "expected_input_hash": export.json()["data"]["input_hash"],
            "acknowledged_issue_ids": [
                i["issue_id"]
                for i in export.json()["data"]["issues"]
                if i["severity"] == "acknowledge"
            ],
        },
    )
    assert blocked.status_code == 409, blocked.text
    artifact(
        f"check-export-{mutation}",
        draft=shown.json(),
        check=refused_check.json(),
        export=refused_export.json(),
        refused_submission=blocked.json(),
    )


@pytest.mark.parametrize("mutation", ["card_reopen", "requirement_reopen"])
async def test_score_rejects_old_draft_after_review_invalidation(rubric_case, mutation):
    case = rubric_case
    ready = await confirm_contents(case)
    confirmed = await case["api"].post(
        base(case) + "/decisions", headers=case["header"], json=decision(ready, ready["rubric"])
    )
    assert confirmed.status_code == 200, confirmed.text
    rubric = await show(case)
    role(case, "admin")
    task = await workflow(case["api"], case["header"], case["task"])
    members = {}
    for domain, org_role in (("commercial", "bidder"), ("technical", "technical")):
        user, auth = await person(
            case["api"], case["admin_engine"], case["tenants"]["orgs"][0], org_role
        )
        added = await add_member(
            case["api"],
            case["header"],
            case["task"],
            user,
            task["revision"],
            "contributor",
            [domain],
        )
        assert added.active
        task = await workflow(case["api"], case["header"], case["task"])
        members[domain] = {"user": user, "headers": auth}
    requirement = case["requirements"][2]
    card = await create_card(
        case["api"],
        case["header"],
        case["task"],
        case["extraction"],
        requirement,
        {
            "response_kind": "commitment",
            "response_text": "Synthetic 64 GB memory support",
            "deviation": "none",
            "deviation_note": "Synthetic delivery is met",
        },
    )
    scope = {
        "task_id": case["task"],
        "job_id": case["extraction"],
        "requirement_ids": [requirement["id"]],
        "card": card,
        "members": members,
    }
    await complete(case["api"], case["header"], scope)
    draft = await publish_draft(
        case["api"], case["app"], case["header"], case["task"], case["extraction"]
    )
    await revoke(case["api"], case["header"], scope, mutation)
    response = await case["api"].post(
        f"/tasks/{case['task']}/scores/preview",
        headers=case["header"],
        json={
            "draft_id": draft["draft_id"],
            "rubric_id": rubric["rubric"]["id"],
            "assessment_date": "2026-10-05",
            "dry_run": True,
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["data"]["error"]["code"] == "score_stale_draft"
    artifact(f"score-{mutation}", refused_preview=response.json(), draft_id=draft["draft_id"])
