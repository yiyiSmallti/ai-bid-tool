"""API acceptance specified before co-sign interfaces are implemented.

Failure inventory: any of the seven endpoints leaks a foreign org/nonmember;
removed members retain access; token or observer edits policy/signs; an admin
uses recovery access to sign as a reviewer; archived task writes succeed; dry-run
changes a workflow or policy; mismatched extraction IDs expose a requirement;
body discriminator accepts response fields for disposition; pages lose history.
These tests require the explicitly supplied isolated PostgreSQL runtime. They
never start a database or an external service.
"""

from uuid import uuid4

import pytest
from test_card_generation import token_header
from test_team_workflow_discussion import scope_with_card
from test_team_workflow_membership import add_member, person, workflow

OPERATIONS = ("rule_show", "rule_set", "policy_show", "policy_set", "list", "open", "sign")
WRITES = ("rule_set", "policy_set", "open", "sign")
COMMANDS = {
    "rule_show": "task review-rule show",
    "rule_set": "task review-rule set",
    "policy_show": "card policy show",
    "policy_set": "card policy set",
    "list": "card signoff list",
    "open": "card review-round open",
    "sign": "card signoff add",
}


async def request(api, auth, scope, operation, **changes):
    task, card = scope["task_id"], scope["card"]["id"]
    requirement = scope["requirement_ids"][0]
    params = {"extraction_job_id": str(scope["job_id"])}
    rule = {"expected_revision": 1, "reason": "Synthetic rule edit", "co_sign_starred": True}
    policy = {
        "expected_policy_revision": 0,
        "reason": "Synthetic policy edit",
        "co_sign_required": True,
    }
    opening = {
        "expected_revision": scope["card"]["revision"],
        "reason": "Synthetic disposition review",
        "intended_disposition": "comply_only",
        "purpose": "disposition",
        "client_request_id": str(uuid4()),
    }
    signing = {
        "expected_revision": scope["card"]["revision"],
        "purpose": "disposition",
        "expected_round": 1,
        "selected_domain": "technical",
        "reason": "Synthetic reviewed disposition",
        "client_request_id": str(uuid4()),
    }
    method, path, body, query = {
        "rule_show": ("GET", f"/tasks/{task}/review-rule", None, {}),
        "rule_set": ("PUT", f"/tasks/{task}/review-rule", rule, {}),
        "policy_show": (
            "GET",
            f"/tasks/{task}/requirements/{requirement}/review-policy",
            None,
            params,
        ),
        "policy_set": (
            "PUT",
            f"/tasks/{task}/requirements/{requirement}/review-policy",
            policy,
            params,
        ),
        "list": ("GET", f"/cards/{card}/signoffs", None, {}),
        "open": ("POST", f"/cards/{card}/review-rounds", opening, {}),
        "sign": ("POST", f"/cards/{card}/signoffs", signing, {}),
    }[operation]
    if body is not None:
        body.update(changes)
    return await api.request(
        method, path, headers=auth, params=query, **({"json": body} if body else {})
    )


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_cosign_routes_hide_foreign_org_nonmember_and_removed(
    api, headers, tenants, admin_engine, operation
):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    outsider, removed_header = await person(api, admin_engine, tenants["orgs"][0])
    assert (
        await add_member(api, headers[0], scope["task_id"], outsider, 1, "observer")
    ).status_code == 200
    assert (
        await api.post(
            f"/tasks/{scope['task_id']}/members/{outsider}/remove",
            headers=headers[0],
            json={"expected_revision": 2, "reason": "Synthetic removal"},
        )
    ).status_code == 200
    _, stranger = await person(api, admin_engine, tenants["orgs"][0])
    for auth in (headers[1], removed_header, stranger):
        response = await request(api, auth, scope, operation)
        assert response.status_code == 404, response.text
        assert response.json()["command"] == COMMANDS[operation]
        assert response.json()["data"]["error"]["code"] == "not_found"
        assert str(scope["task_id"]) not in response.text
        assert str(scope["card"]["id"]) not in response.text


@pytest.mark.parametrize("operation", WRITES)
async def test_cosign_writes_deny_tokens_and_observers(
    api, headers, tenants, admin_engine, operation
):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    observer, observer_header = await person(api, admin_engine, tenants["orgs"][0], "viewer")
    assert (
        await add_member(api, headers[0], scope["task_id"], observer, 1, "observer")
    ).status_code == 200
    token = await token_header(api, headers[0])
    for auth in (token, observer_header):
        response = await request(api, auth, scope, operation)
        assert response.status_code == 403, response.text
        assert response.json()["data"]["error"]["code"] == "forbidden"


@pytest.mark.parametrize("operation", WRITES)
async def test_cosign_archived_task_blocks_writes(api, headers, tenants, admin_engine, operation):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    uid, reviewer = await person(api, admin_engine, tenants["orgs"][0], "technical")
    assert (
        await add_member(api, headers[0], scope["task_id"], uid, 1, "reviewer", ["technical"])
    ).status_code == 200
    assert (
        await api.post(
            f"/tasks/{scope['task_id']}/archive",
            headers=headers[0],
            json={"expected_revision": 2, "reason": "Synthetic archive"},
        )
    ).status_code == 200
    response = await request(
        api,
        reviewer if operation == "sign" else headers[0],
        scope,
        operation,
        **({"expected_revision": 3} if operation == "rule_set" else {}),
    )
    assert response.status_code == 409, response.text
    assert response.json()["data"]["error"]["code"] == "task_archived"
    for read in ("rule_show", "policy_show", "list"):
        assert (await request(api, headers[0], scope, read)).status_code == 200


async def test_rule_preview_is_read_only_and_policy_keeps_rule_domains(
    api, headers, tenants, admin_engine
):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    before = await workflow(api, headers[0], scope["task_id"])
    before.pop("last_event_cursor")
    policy_before = (await request(api, headers[0], scope, "policy_show")).json()["data"]
    preview = await request(api, headers[0], scope, "rule_set", dry_run=True)
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["dry_run"] is True
    assert preview.json()["data"]["affected_requirements"] == 1
    after = await workflow(api, headers[0], scope["task_id"])
    after.pop("last_event_cursor")
    assert after == before
    assert (await request(api, headers[0], scope, "policy_show")).json()["data"] == policy_before
    saved = await request(api, headers[0], scope, "rule_set")
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["dry_run"] is False
    assert saved.json()["data"]["rule"]["workflow_revision"] == before["revision"] + 1
    policy = await request(api, headers[0], scope, "policy_set", co_sign_required=False)
    assert policy.status_code == 200, policy.text
    effective = policy.json()["data"]["policy"]
    assert effective["co_sign_required"] is False
    assert set(effective["required_domains"]) == {"commercial", "technical"}
    assert (await request(api, headers[0], scope, "rule_set")).status_code == 409


async def test_policy_extraction_binding_and_recovery_admin_cannot_sign(
    api, headers, tenants, admin_engine
):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    foreign = await scope_with_card(api, headers, tenants, admin_engine, tenant=1)
    path = f"/tasks/{scope['task_id']}/requirements/{scope['requirement_ids'][0]}/review-policy"
    for job in (foreign["job_id"], uuid4()):
        for method in ("GET", "PUT"):
            response = await api.request(
                method,
                path,
                headers=headers[0],
                params={"extraction_job_id": str(job)},
                **(
                    {
                        "json": {
                            "expected_policy_revision": 0,
                            "co_sign_required": True,
                            "reason": "Synthetic policy",
                        }
                    }
                    if method == "PUT"
                    else {}
                ),
            )
            assert response.status_code == 404, response.text
    _, admin = await person(api, admin_engine, tenants["orgs"][0], "admin")
    assert (await request(api, admin, scope, "rule_show")).status_code == 200
    assert (await request(api, admin, scope, "sign")).status_code == 404
