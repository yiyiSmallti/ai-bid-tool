"""Saved response inputs and human reviewers for retained API acceptance."""

from app.core.config import Settings
from app.core.security import Secrets
from app.models.team_workflow import RequirementWorkflow
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from task_access_fixtures import add_member, person
from task_fixtures import actor_context, confirm_requirements
from test_response_cards import CERTIFICATE_PAGE, require_action, select_real_materials
from test_team_workflow_stream_acceptance import seed_scope


def legacy_policy(admin_engine, scope):
    """Persist an old multi-domain policy without restoring its removed API."""
    with Session(admin_engine) as session, session.begin():
        actor = actor_context(session, scope["org_id"], scope["user_id"])
        # Only the owner fixture can materialize a policy from the old installation.
        # The removed public scope is needed by the unchanged database row guard.
        import json

        session.execute(
            text("SELECT set_config('app.actor_scopes',:scopes,true)"),
            {"scopes": json.dumps(sorted(actor.scopes | {"task:review-policy"}))},
        )
        row = session.scalar(
            select(RequirementWorkflow).where(
                RequirementWorkflow.org_id == scope["org_id"],
                RequirementWorkflow.requirement_id == scope["requirement_ids"][0],
            )
        )
        if row is None:
            row = RequirementWorkflow(
                org_id=scope["org_id"],
                task_id=scope["task_id"],
                extraction_job_id=scope["job_id"],
                requirement_id=scope["requirement_ids"][0],
            )
            session.add(row)
        row.co_sign_required = True
        row.policy_revision = (row.policy_revision or 0) + 1
        row.policy_changed_by_user_id = scope["user_id"]
        row.policy_reason_ciphertext = Secrets.for_data(Settings()).encrypt(
            "Synthetic legacy multi-domain policy"
        )


async def response_scope(api, headers, tenants, admin_engine, *, required=False, count=1):
    scope = await seed_scope(api, headers, tenants, admin_engine, count=count)
    with Session(admin_engine) as session, session.begin():
        confirm_requirements(session, scope["org_id"], scope["task_id"])
    members = {}
    for index, (domain, role) in enumerate((("commercial", "bidder"), ("technical", "technical"))):
        user, auth = await person(api, admin_engine, tenants["orgs"][0], role)
        result = await add_member(
            api, headers[0], scope["task_id"], user, index + 1, "contributor", [domain]
        )
        assert result.active
        members[domain] = {"user": user, "headers": auth}
    scope["members"] = members
    if required:
        legacy_policy(admin_engine, scope)
    created = await api.post(
        f"/tasks/{scope['task_id']}/cards",
        headers=headers[0],
        json={
            "extraction_job_id": str(scope["job_id"]),
            "requirement_id": str(scope["requirement_ids"][0]),
            "content": {
                "response_kind": "commitment",
                "response_text": "Synthetic delivery commitment",
                "deviation": "negative",
                "deviation_note": "Synthetic one-day delivery difference",
            },
        },
    )
    assert created.status_code == 200, created.text
    scope["card"] = created.json()["data"]
    return scope


async def submit(api, headers, scope):
    card = scope["card"]
    response = await api.post(
        f"/cards/{card['id']}/actions",
        headers=headers,
        json={"expected_revision": card["revision"], "action": "submit"},
    )
    assert response.status_code == 200, response.text
    scope["card"] = response.json()["data"]
    return scope["card"]


async def refresh_card(api, auth, scope):
    response = await api.get(f"/cards/{scope['card']['id']}", headers=auth)
    assert response.status_code == 200, response.text
    scope["card"] = response.json()["data"]
    return scope["card"]


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


async def confirm(api, scope, domain="technical", **changes):
    card = scope["card"]
    return await api.post(
        f"/cards/{card['id']}/actions",
        headers=scope["members"][domain]["headers"],
        json={
            "expected_revision": card["revision"],
            "action": "confirm",
            "reviewed_evidence_ids": [row["id"] for row in card["evidence"]],
            "reviewed_warning_codes": card["warning_codes"],
            "reason": "Synthetic human inspected all materials and warnings.",
            **changes,
        },
    )


async def complete(api, owner, scope):
    await submit(api, owner, scope)
    result = await confirm(api, scope)
    assert result.status_code == 200, result.text
    scope["card"] = result.json()["data"]


async def revoke(api, owner, scope, mutation):
    if mutation == "card_reopen":
        scope["card"] = await require_action(
            api,
            scope["members"]["technical"]["headers"],
            scope["card"],
            "reopen",
            reason="Synthetic review reopened",
        )
    else:
        from test_requirement_confirmation import decision, post_decision, review

        current = await review(api, owner, scope["card"]["requirement_id"])
        response = await post_decision(
            api, owner, scope["card"]["requirement_id"], decision(current, "reopen")
        )
        assert response.status_code == 200, response.text
