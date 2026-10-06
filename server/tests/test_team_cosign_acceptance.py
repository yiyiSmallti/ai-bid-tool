"""Independent real-API acceptance for co-sign lifecycle and consumer fences.

Failure inventory: removed/re-added signers resurrect approvals; unrelated task
member/assignment/discussion edits invalidate valid rounds; legacy single-domain
confirm stops working; disposition reviews confirm Evidence; changing back to
respond retains an exempt disposition; old approved draft manifests remain usable
by check, score or export after policy or actual signer-authority loss.
Only an explicitly supplied isolated PostgreSQL runtime may execute these tests.
Artifact files retain synthetic public response data and reproducible commands.
"""

import json
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.core.errors import ServiceError
from app.schemas.team_workflow import CoSignConfirm
from app.services import task_cosign
from app.services.auth import ROLE_SCOPES
from test_check import publish_draft
from test_exports import setup_template
from test_response_cards import CERTIFICATE_PAGE, create_card, select_real_materials
from test_score_api import rubric_case as rubric_case
from test_score_api import rubric_input_case as rubric_input_case
from test_score_review import base, confirm_contents, decision, role, show
from test_team_cosign_consumers import cosign_scope, signature, submit
from test_team_workflow_membership import add_member, person, workflow
from test_team_workflow_stream_acceptance import authenticated

ARTIFACTS = (
    Path(__file__).resolve().parents[2] / "data/work/team-workflow-acceptance/cosign/independent"
)


def artifact(name, **values):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / f"{name}.json").write_text(
        json.dumps(
            {
                "command": "uv run pytest server/tests/test_team_cosign_acceptance.py -q",
                **values,
            },
            indent=2,
            default=str,
        )
        + "\n"
    )


async def history(api, auth, scope):
    response = await api.get(f"/cards/{scope['card']['id']}/signoffs", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


async def refresh_card(api, auth, scope):
    response = await api.get(f"/cards/{scope['card']['id']}", headers=auth)
    assert response.status_code == 200, response.text
    scope["card"] = response.json()["data"]
    return scope["card"]


async def complete(api, owner, scope):
    await submit(api, owner, scope)
    for domain in ("commercial", "technical"):
        response = await signature(api, scope, domain)
        assert response.status_code == 200, response.text
    assert response.json()["data"]["summary"]["status"] == "complete"
    await refresh_card(api, owner, scope)


async def revoke(api, owner, scope, mutation):
    if mutation == "policy":
        response = await api.put(
            f"/tasks/{scope['task_id']}/requirements/{scope['requirement_ids'][0]}/review-policy",
            headers=owner,
            params={"extraction_job_id": str(scope["job_id"])},
            json={
                "expected_policy_revision": 1,
                "co_sign_required": False,
                "reason": "Synthetic revised review policy",
            },
        )
    else:
        current = await workflow(api, owner, scope["task_id"])
        user = scope["members"]["technical"]["user"]
        response = await api.post(
            f"/tasks/{scope['task_id']}/members/{user}/remove",
            headers=owner,
            json={"expected_revision": current["revision"], "reason": "Synthetic signer removal"},
        )
    assert response.status_code == 200, response.text


async def test_removed_readded_signer_does_not_resurrect_round(api, headers, tenants, admin_engine):
    scope = await cosign_scope(api, headers, tenants, admin_engine)
    await complete(api, headers[0], scope)
    before = await history(api, headers[0], scope)
    await revoke(api, headers[0], scope, "member")
    retired = await history(api, headers[0], scope)
    assert retired["data"]["summary"]["status"] == "invalidated"
    current = await workflow(api, headers[0], scope["task_id"])
    restored = await add_member(
        api,
        headers[0],
        scope["task_id"],
        scope["members"]["technical"]["user"],
        current["revision"],
        "contributor",
        ["technical"],
    )
    assert restored.status_code == 200, restored.text
    after = await history(api, headers[0], scope)
    assert after["data"]["summary"]["status"] == "invalidated"
    assert after["data"]["summary"]["signed_domains"] == []
    assert after["items"] == before["items"]
    preview = await api.post(
        f"/tasks/{scope['task_id']}/drafts",
        headers=headers[0],
        json={"extraction_job_id": str(scope["job_id"]), "dry_run": True},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["response_requirements"] == 0
    artifact("removed-readded", before=before, retired=retired, restored=after)


async def test_unrelated_assignment_comment_and_member_preserve_approval(
    api, headers, tenants, admin_engine
):
    scope = await cosign_scope(api, headers, tenants, admin_engine)
    await complete(api, headers[0], scope)
    before = await history(api, headers[0], scope)
    assignment = await api.put(
        f"/tasks/{scope['task_id']}/requirements/{scope['requirement_ids'][0]}/assignment",
        headers=headers[0],
        params={"extraction_job_id": str(scope["job_id"])},
        json={
            "expected_assignment_revision": 0,
            "assignee_user_id": str(scope["members"]["technical"]["user"]),
            "reason": "Synthetic independent assignment",
        },
    )
    assert assignment.status_code == 200, assignment.text
    thread = await api.post(
        f"/cards/{scope['card']['id']}/threads",
        headers=headers[0],
        json={
            "expected_card_revision": scope["card"]["revision"],
            "body": "Synthetic unrelated discussion",
            "client_request_id": str(uuid4()),
        },
    )
    assert thread.status_code == 200, thread.text
    observer, _ = await person(api, admin_engine, tenants["orgs"][0], "viewer")
    current = await workflow(api, headers[0], scope["task_id"])
    added = await add_member(
        api, headers[0], scope["task_id"], observer, current["revision"], "observer"
    )
    assert added.status_code == 200, added.text
    after = await history(api, headers[0], scope)
    assert after["data"]["summary"]["status"] == "complete"
    assert after["data"]["round"]["id"] == before["data"]["round"]["id"]
    assert after["items"] == before["items"]
    preview = await api.post(
        f"/tasks/{scope['task_id']}/drafts",
        headers=headers[0],
        json={"extraction_job_id": str(scope["job_id"]), "dry_run": True},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["response_requirements"] == 1
    artifact("unrelated-edits", before=before, after=after)


async def test_single_domain_legacy_confirm_writes_one_real_signature(
    api, headers, tenants, admin_engine
):
    scope = await cosign_scope(api, headers, tenants, admin_engine, required=False)
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
    saved = await history(api, headers[0], scope)
    assert saved["data"]["summary"]["status"] == "complete"
    assert saved["data"]["summary"]["required_domains"] == ["technical"]
    assert len(saved["items"]) == 1
    assert saved["items"][0]["signer_user_id"] == str(scope["members"]["technical"]["user"])
    artifact("single-domain-legacy", response=response.json(), history=saved)


async def attach_material(api, owner, scope, tmp_path):
    _, _, _, _, source = await select_real_materials(api, owner, str(scope["task_id"]), tmp_path)
    updated = await api.put(
        f"/cards/{scope['card']['id']}",
        headers=owner,
        json={
            "expected_revision": scope["card"]["revision"],
            "content": {
                "response_kind": "evidence",
                "response_text": "Synthetic material is attached",
                "deviation": "negative",
                "deviation_note": "Synthetic reviewed difference",
                "evidence": [
                    {
                        "kind": "certificate_pdf_page",
                        "evidence_source_id": source["id"],
                        "quote": CERTIFICATE_PAGE,
                    }
                ],
            },
        },
    )
    assert updated.status_code == 200, updated.text
    scope["card"] = updated.json()["data"]
    assert scope["card"]["evidence"] and all(
        e["confirmed_by"] is None for e in scope["card"]["evidence"]
    )


@pytest.mark.parametrize("order", [("commercial", "technical"), ("technical", "commercial")])
async def test_every_signature_reviews_all_materials_and_only_final_confirms(
    api, headers, tenants, admin_engine, tmp_path, order, monkeypatch
):
    # The fake extraction still stores a verifiable quote in its fixed Chunk;
    # use an explicit material obligation so warning acknowledgment is exercised.
    quote = "Synthetic fixture: provide a current certificate with the response."
    monkeypatch.setattr(
        "test_team_workflow_stream_acceptance.synthetic_page_sources",
        lambda count, chunk_count, **kwargs: ([quote], [quote]),
    )
    scope = await cosign_scope(api, headers, tenants, admin_engine)
    await attach_material(api, headers[0], scope, tmp_path)
    await submit(api, headers[0], scope)
    pending = scope["card"]
    assert pending["evidence"] and pending["warning_codes"]
    evidence_ids = [e["id"] for e in pending["evidence"]]
    receipts = []
    for index, domain in enumerate(order):
        for changes, error_code in (
            ({"reviewed_evidence_ids": []}, "review_mismatch"),
            ({"reviewed_evidence_ids": [str(uuid4())]}, "review_mismatch"),
            ({"reviewed_warning_codes": []}, "warning_review_required"),
            (
                {"reviewed_warning_codes": [*pending["warning_codes"], "unrelated-warning"]},
                "warning_review_required",
            ),
        ):
            refused = await signature(api, scope, domain, **changes)
            assert refused.status_code == 400, refused.text
            assert refused.json()["data"]["error"]["code"] == error_code
            state = await history(api, headers[0], scope)
            assert len(state["items"]) == index
            assert state["data"]["summary"]["status"] == ("pending" if index == 0 else "partial")
        signed = await signature(api, scope, domain)
        assert signed.status_code == 200, signed.text
        data = signed.json()["data"]
        assert set(data["signature"]["reviewed_evidence_ids"]) == set(evidence_ids)
        assert set(data["signature"]["reviewed_warning_codes"]) == set(pending["warning_codes"])
        current = await refresh_card(api, headers[0], scope)
        if index == 0:
            assert (
                current["state"] == "pending_review" and current["revision"] == pending["revision"]
            )
            assert all(e["confirmed_by"] is None for e in current["evidence"])
        else:
            assert (
                current["state"] == "confirmed" and current["revision"] == pending["revision"] + 1
            )
            assert all(
                e["confirmed_by"] == str(scope["members"][domain]["user"])
                and e["confirmed_at"] is not None
                for e in current["evidence"]
            )
        receipts.append(data)
    artifact("evidence-" + "-".join(order), receipts=receipts, confirmed=scope["card"])


@pytest.mark.parametrize("kind", ["worker", "agent"])
async def test_constructed_nonhuman_service_identity_cannot_sign(
    api, application, headers, tenants, admin_engine, kind
):
    scope = await cosign_scope(api, headers, tenants, admin_engine)
    await submit(api, headers[0], scope)
    listing = await history(api, headers[0], scope)
    body = CoSignConfirm(
        expected_revision=scope["card"]["revision"],
        expected_round=listing["data"]["round"]["round_revision"],
        selected_domain="technical",
        reviewed_evidence_ids=[],
        reviewed_warning_codes=scope["card"]["warning_codes"],
        reason="Synthetic warning acknowledgment",
        client_request_id=uuid4(),
    )
    async with authenticated(application, scope["members"]["technical"]["headers"]) as (
        session,
        actor,
    ):
        fabricated = replace(actor, actor_kind=kind, scopes=set(ROLE_SCOPES["technical"]))
        with pytest.raises(ServiceError) as denied:
            await task_cosign.sign(
                session,
                fabricated,
                UUID(scope["card"]["id"]),
                body,
                application.state.processor.settings,
                storage=application.state.storage,
            )
        assert denied.value.status == 403
    after = await history(api, headers[0], scope)
    assert after["items"] == [] and after["data"]["summary"]["status"] == "pending"


async def test_disposition_rounds_both_directions_do_not_confirm_evidence(
    api, headers, tenants, admin_engine, tmp_path
):
    scope = await cosign_scope(api, headers, tenants, admin_engine)
    await attach_material(api, headers[0], scope, tmp_path)
    receipts = []
    for intended in ("comply_only", "respond"):
        card = scope["card"]
        opened = await api.post(
            f"/cards/{card['id']}/review-rounds",
            headers=scope["members"]["technical"]["headers"],
            json={
                "expected_revision": card["revision"],
                "intended_disposition": intended,
                "reason": f"Synthetic intended {intended}",
                "client_request_id": str(uuid4()),
            },
        )
        assert opened.status_code == 200, opened.text
        review = opened.json()["data"]["round"]
        assert review["purpose"] == "disposition" and review["intended_disposition"] == intended
        assert review["disposition_reason"] == f"Synthetic intended {intended}"
        for index, domain in enumerate(("technical", "commercial")):
            signed = await api.post(
                f"/cards/{card['id']}/signoffs",
                headers=scope["members"][domain]["headers"],
                json={
                    "expected_revision": card["revision"],
                    "expected_round": review["round_revision"],
                    "selected_domain": domain,
                    "purpose": "disposition",
                    "reason": f"Synthetic independent {domain} acknowledgment",
                    "reviewed_warning_codes": card["warning_codes"],
                    "client_request_id": str(uuid4()),
                },
            )
            assert signed.status_code == 200, signed.text
            data = signed.json()["data"]
            assert data["summary"]["status"] == ("partial" if index == 0 else "complete")
            current = await refresh_card(api, headers[0], scope)
            assert current["revision"] == card["revision"] + index
            assert current["disposition"] == (card["disposition"] if index == 0 else intended)
            assert current["state"] == card["state"]
            assert all(e["confirmed_by"] is None for e in current["evidence"])
            assert data["signature"]["reviewed_evidence_ids"] == []
            receipts.append(data)
    artifact("disposition-directions", receipts=receipts, final=scope["card"])


@pytest.mark.parametrize("mutation", ["policy", "member"])
async def test_check_export_reject_old_draft_after_review_invalidation(
    api, application, headers, tenants, admin_engine, mutation
):
    scope = await cosign_scope(api, headers, tenants, admin_engine)
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
    assert refused_export.status_code == 200, refused_export.text
    assert any(
        issue["code"] == "export_stale_draft" and issue["severity"] == "block"
        for issue in refused_export.json()["data"]["issues"]
    )
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


@pytest.mark.parametrize("mutation", ["policy", "member"])
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
        assert added.status_code == 200, added.text
        task = await workflow(case["api"], case["header"], case["task"])
        members[domain] = {"user": user, "headers": auth}
    requirement = case["requirements"][2]
    policy = await case["api"].put(
        f"/tasks/{case['task']}/requirements/{requirement['id']}/review-policy",
        headers=case["header"],
        params={"extraction_job_id": case["extraction"]},
        json={
            "expected_policy_revision": 0,
            "co_sign_required": True,
            "reason": "Synthetic score review",
        },
    )
    assert policy.status_code == 200, policy.text
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
