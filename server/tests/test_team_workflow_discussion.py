"""Slice-two API/DB failure inventory, written before implementation.

Every route must mask other orgs, same-org nonmembers and wrong parent chains.
Assignment CAS must serialize against other assignments, membership removal and
handover; assignment is not card authority. Human observers may discuss, while
tokens, workers, agents and recovery-only admins cannot. Archived writes fail.
Comments are bounded encrypted plain text, append-only and idempotent per author
and task; request reuse across parents or with different content conflicts.
Mentions accept only current members, never deliver externally, and disappear
from current navigation after revocation. Comments leave confirmed cards intact.
Business, audit and durable events roll back together; replay contains only IDs.
Cursor parent/identity binding, pagination, board counts/filtering and lost-reply
recovery are verified through real API paths under bid_app RLS.
"""

import asyncio
import json
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.core.errors import ServiceError
from app.core.security import Secrets
from app.schemas.team_workflow import CommentThreadCreate
from app.services import task_discussion
from app.services.auth import ROLE_SCOPES, Identity
from sqlalchemy import text
from sqlalchemy.orm import Session
from task_fixtures import confirm_requirements
from test_card_generation import token_header
from test_team_workflow_membership import add_member, person, workflow
from test_team_workflow_stream_acceptance import authenticated, seed_scope

ARTIFACT = (
    Path(__file__).resolve().parents[2]
    / "data/work/team-workflow-acceptance/discussion/result.json"
)


async def scope_with_card(api, headers, tenants, admin_engine, *, tenant=0, confirmed=False):
    scope = await seed_scope(api, headers, tenants, admin_engine, count=2, tenant=tenant)
    if confirmed:
        with Session(admin_engine) as session, session.begin():
            confirm_requirements(session, scope["org_id"], scope["task_id"])
    created = await api.post(
        f"/tasks/{scope['task_id']}/cards",
        headers=headers[tenant],
        json={
            "extraction_job_id": str(scope["job_id"]),
            "requirement_id": str(scope["requirement_ids"][0]),
            "content": {"response_kind": "commitment", "response_text": "Synthetic response"},
        },
    )
    assert created.status_code == 200, created.text
    scope["card"] = created.json()["data"]
    return scope


def message(**changes):
    return {
        "body": "Synthetic <script>alert(1)</script> discussion",
        "mentioned_user_ids": [],
        "client_request_id": str(uuid4()),
        **changes,
    }


async def thread(api, header, card, **changes):
    response = await api.post(
        f"/cards/{card['id']}/threads",
        headers=header,
        json=message(expected_card_revision=card["revision"], **changes),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def assign(api, header, scope, user, revision=0, requirement=None):
    return await api.put(
        f"/tasks/{scope['task_id']}/requirements/{requirement or scope['requirement_ids'][0]}/assignment",
        headers=header,
        params={"extraction_job_id": str(scope["job_id"])},
        json={
            "expected_assignment_revision": revision,
            "assignee_user_id": str(user) if user else None,
            "reason": "Synthetic assignment reason",
        },
    )


async def board(api, header, scope, **filters):
    response = await api.get(
        f"/tasks/{scope['task_id']}/board",
        headers=header,
        params={"extraction_job_id": str(scope["job_id"]), **filters},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("operation", ["assign", "threads", "create", "comments", "reply"])
async def test_all_discussion_routes_hide_foreign_org_nonmember_and_removed(
    api,
    headers,
    tenants,
    admin_engine,
    operation,
):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    receipt = await thread(api, headers[0], scope["card"])
    card, tid = scope["card"]["id"], receipt["thread"]["id"]
    outsider, outside = await person(api, admin_engine, tenants["orgs"][0])
    assert (
        await add_member(api, headers[0], scope["task_id"], outsider, 1, "observer")
    ).status_code == 200
    removed = await api.post(
        f"/tasks/{scope['task_id']}/members/{outsider}/remove",
        headers=headers[0],
        json={"expected_revision": 2, "reason": "Synthetic removal"},
    )
    assert removed.status_code == 200
    _, stranger = await person(api, admin_engine, tenants["orgs"][0])
    for auth in (headers[1], outside, stranger):
        if operation == "assign":
            response = await assign(api, auth, scope, None)
        else:
            path = f"/cards/{card}/threads"
            if operation in {"comments", "reply"}:
                path += f"/{tid}/comments"
            body = message(**({"expected_card_revision": 1} if operation == "create" else {}))
            response = await api.request(
                "POST" if operation in {"create", "reply"} else "GET",
                path,
                headers=auth,
                **({"json": body} if operation in {"create", "reply"} else {}),
            )
        assert response.status_code == 404, response.text
        assert response.json()["data"]["error"]["code"] == "not_found"
        assert card not in response.text and str(scope["task_id"]) not in response.text


async def test_assignment_cas_gap_filters_and_member_removal(api, headers, tenants, admin_engine):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    user, member_header = await person(api, admin_engine, tenants["orgs"][0])
    assert (await add_member(api, headers[0], scope["task_id"], user, 1)).status_code == 200
    gap = scope["requirement_ids"][1]
    results = await asyncio.gather(
        assign(api, headers[0], scope, user, requirement=gap),
        assign(api, headers[0], scope, None, requirement=gap),
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    result = next(r for r in results if r.status_code == 200).json()["data"]["assignment"]
    if result["assignee_user_id"] is None:
        result = (await assign(api, headers[0], scope, user, 1, gap)).json()["data"]["assignment"]
    mine = await board(api, member_header, scope, mine="true")
    assert [r["requirement_id"] for r in mine["items"]] == [str(gap)]
    row = mine["items"][0]
    assert row["card_id"] is None and row["owner_user_id"] == str(user)
    assert row["assignment_revision"] == result["revision"] and "unassigned" not in row["blockers"]
    assert (await board(api, headers[0], scope, owner_user_id=str(user)))["data"]["counts"][
        "total"
    ] == 1
    assert (await board(api, headers[0], scope, unassigned="true"))["data"]["counts"]["total"] == 1
    for response in (
        await api.post(
            f"/tasks/{scope['task_id']}/members/{user}/remove",
            headers=headers[0],
            json={"expected_revision": 2, "reason": "Cannot remove assigned member"},
        ),
        await add_member(api, headers[0], scope["task_id"], user, 2, "observer"),
    ):
        assert response.status_code == 409, response.text
        assert response.json()["data"]["error"]["code"] == "member_has_assignments"
    assert (await assign(api, member_header, scope, user)).status_code == 403
    assert (await assign(api, headers[0], scope, None, result["revision"], gap)).status_code == 200
    assert (
        await add_member(api, headers[0], scope["task_id"], user, 2, "observer")
    ).status_code == 200
    assert (await assign(api, headers[0], scope, user)).status_code == 403


async def test_assignment_vs_removal_and_disabled_assignee(api, headers, tenants, admin_engine):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    user, _ = await person(api, admin_engine, tenants["orgs"][0])
    assert (await add_member(api, headers[0], scope["task_id"], user, 1)).status_code == 200
    assigned, removed = await asyncio.gather(
        assign(api, headers[0], scope, user),
        api.post(
            f"/tasks/{scope['task_id']}/members/{user}/remove",
            headers=headers[0],
            json={"expected_revision": 2, "reason": "Concurrent removal"},
        ),
    )
    assert (assigned.status_code, removed.status_code) in {(200, 409), (404, 200)}
    if removed.status_code == 200:
        assert (await add_member(api, headers[0], scope["task_id"], user, 3)).status_code == 200
        assert (await assign(api, headers[0], scope, user)).status_code == 200
    with admin_engine.begin() as connection:
        connection.execute(
            text("UPDATE memberships SET active=false WHERE org_id=:org AND user_id=:user"),
            {"org": tenants["orgs"][0], "user": user},
        )
    rows = (await board(api, headers[0], scope))["items"]
    assert "assignee_unavailable" in next(r for r in rows if r["card_id"])["blockers"]
    assert (await assign(api, headers[0], scope, None, 1)).status_code == 200


@pytest.mark.parametrize(
    "org_role,task_role,domains",
    [
        ("viewer", "observer", []),
        ("technical", "reviewer", ["technical"]),
        ("bidder", "contributor", ["commercial"]),
        ("admin", "observer", []),
    ],
)
async def test_all_human_task_roles_can_comment_without_changing_card(
    api,
    headers,
    tenants,
    admin_engine,
    org_role,
    task_role,
    domains,
):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    user, auth = await person(api, admin_engine, tenants["orgs"][0], org_role)
    assert (
        await add_member(api, headers[0], scope["task_id"], user, 1, task_role, domains)
    ).status_code == 200
    before = (await api.get(f"/cards/{scope['card']['id']}", headers=auth)).json()["data"]
    receipt = await thread(api, auth, scope["card"], mentioned_user_ids=[str(tenants["users"][0])])
    response = await api.post(
        f"/cards/{scope['card']['id']}/threads/{receipt['thread']['id']}/comments",
        headers=auth,
        json=message(),
    )
    assert response.status_code == 200, response.text
    after = (await api.get(f"/cards/{scope['card']['id']}", headers=auth)).json()["data"]
    assert before == after
    assert (await workflow(api, headers[0], scope["task_id"]))["revision"] == 2


async def test_encrypted_idempotent_discussion_mentions_board_and_replay(
    api,
    application,
    headers,
    tenants,
    admin_engine,
):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    viewer, viewer_header = await person(api, admin_engine, tenants["orgs"][0], "viewer")
    assert (
        await add_member(api, headers[0], scope["task_id"], viewer, 1, "observer")
    ).status_code == 200
    snapshot = await board(api, headers[0], scope)
    payload = message(
        expected_card_revision=scope["card"]["revision"], mentioned_user_ids=[str(viewer)]
    )
    path = f"/cards/{scope['card']['id']}/threads"
    a, b = await asyncio.gather(
        api.post(path, headers=headers[0], json=payload),
        api.post(path, headers=headers[0], json=payload),
    )
    assert a.status_code == b.status_code == 200, (a.text, b.text)
    assert a.json()["data"] == b.json()["data"]
    receipt = a.json()["data"]
    conflict = await api.post(path, headers=headers[0], json={**payload, "body": "Different text"})
    assert (
        conflict.status_code == 409
        and conflict.json()["data"]["error"]["code"] == "idempotency_conflict"
    )
    reply_path = path + f"/{receipt['thread']['id']}/comments"
    reply = message()
    first = await api.post(reply_path, headers=viewer_header, json=reply)
    again = await api.post(reply_path, headers=viewer_header, json=reply)
    assert first.status_code == again.status_code == 200
    assert first.json()["data"] == again.json()["data"]
    listed = await api.get(reply_path, headers=viewer_header, params={"limit": 1})
    assert listed.status_code == 200 and listed.json()["data"]["has_more"]
    cursor = listed.json()["data"]["next_cursor"]
    second = await api.get(reply_path, headers=viewer_header, params={"cursor": cursor, "limit": 1})
    assert second.status_code == 200 and len(second.json()["items"]) == 1
    assert second.json()["items"][0]["id"] != listed.json()["items"][0]["id"]
    mine = await board(api, viewer_header, scope, mine="true")
    assert len(mine["items"]) == 1 and mine["items"][0]["comment_thread_count"] == 1
    replay = await api.get(
        f"/tasks/{scope['task_id']}/events/poll",
        headers=headers[0],
        params={"cursor": snapshot["data"]["event_cursor"]},
    )
    assert replay.status_code == 200 and replay.json()["items"]
    assert any(
        e["payload"].get("comment_id") == receipt["first_comment"]["id"]
        for e in replay.json()["items"]
    )
    assert payload["body"] not in replay.text and "example.test" not in replay.text
    with admin_engine.connect() as connection:
        saved = connection.execute(
            text("SELECT body_ciphertext,body_sha256 FROM card_comments WHERE id=:id"),
            {"id": UUID(receipt["first_comment"]["id"])},
        ).one()
        audit = connection.scalars(
            text("SELECT details FROM audit_logs WHERE object_id=:task"), {"task": scope["task_id"]}
        ).all()
    assert (
        Secrets.for_data(application.state.processor.settings).decrypt(saved[0]) == payload["body"]
    )
    assert saved[1] == sha256(payload["body"].encode()).hexdigest()
    assert payload["body"] not in json.dumps(audit)
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(
        json.dumps(
            {
                "scenario": "assignment-and-discussion",
                "task_id": str(scope["task_id"]),
                "thread_id": receipt["thread"]["id"],
                "event_ids": [e["event_id"] for e in replay.json()["items"]],
                "encrypted": True,
                "idempotent": True,
            },
            indent=2,
        )
        + "\n"
    )


async def test_wrong_parents_mentions_and_cursor_are_masked(api, headers, tenants, admin_engine):
    one = await scope_with_card(api, headers, tenants, admin_engine)
    two = await scope_with_card(api, headers, tenants, admin_engine)
    receipt = await thread(api, headers[0], one["card"])
    for method in ("GET", "POST"):
        response = await api.request(
            method,
            f"/cards/{two['card']['id']}/threads/{receipt['thread']['id']}/comments",
            headers=headers[0],
            **({"json": message()} if method == "POST" else {}),
        )
        assert response.status_code == 404
    _, unrelated = await person(api, admin_engine, tenants["orgs"][0])
    # A valid foreign-org user ID and a random ID must have the same failure shape.
    for user in (tenants["users"][1], uuid4()):
        response = await api.post(
            f"/cards/{one['card']['id']}/threads",
            headers=headers[0],
            json=message(expected_card_revision=1, mentioned_user_ids=[str(user)]),
        )
        assert response.status_code == 404
    wrong_assignment = await assign(
        api, headers[0], one, None, requirement=two["requirement_ids"][0]
    )
    assert wrong_assignment.status_code == 404
    await thread(api, headers[0], one["card"])
    page = await api.get(
        f"/cards/{one['card']['id']}/threads", headers=headers[0], params={"limit": 1}
    )
    assert page.json()["data"]["has_more"]
    cross = await api.get(
        f"/cards/{two['card']['id']}/threads",
        headers=headers[0],
        params={"cursor": page.json()["data"]["next_cursor"]},
    )
    assert cross.status_code == 404


@pytest.mark.parametrize(
    "changes",
    [
        {"body": " "},
        {"body": "x" * 4001},
        {"mentioned_user_ids": [str(uuid4()) for _ in range(21)]},
        {"mentioned_user_ids": ["00000000-0000-4000-8000-000000000001"] * 2},
    ],
)
async def test_comment_input_bounds_never_echo_content(
    api, headers, tenants, admin_engine, changes
):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    response = await api.post(
        f"/cards/{scope['card']['id']}/threads",
        headers=headers[0],
        json=message(expected_card_revision=1, **changes),
    )
    assert response.status_code == 422 and "Synthetic <script>" not in response.text


async def test_archived_and_token_comment_assignment_denied(api, headers, tenants, admin_engine):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    receipt = await thread(api, headers[0], scope["card"])
    token = await token_header(api, headers[0])
    _, recovery = await person(api, admin_engine, tenants["orgs"][0], "admin")
    for auth, status in ((token, 403), (recovery, 404)):
        response = await api.post(
            f"/cards/{scope['card']['id']}/threads",
            headers=auth,
            json=message(expected_card_revision=1),
        )
        assert response.status_code == status
    assert (await assign(api, token, scope, None)).status_code == 403
    archived = await api.post(
        f"/tasks/{scope['task_id']}/archive",
        headers=headers[0],
        json={"expected_revision": 1, "reason": "Synthetic archive"},
    )
    assert archived.status_code == 200
    for path, payload in (
        (f"/cards/{scope['card']['id']}/threads", message(expected_card_revision=1)),
        (f"/cards/{scope['card']['id']}/threads/{receipt['thread']['id']}/comments", message()),
    ):
        response = await api.post(path, headers=headers[0], json=payload)
        assert (
            response.status_code == 409
            and response.json()["data"]["error"]["code"] == "task_archived"
        )
        assert (await api.get(path, headers=headers[0])).status_code == 200
    assert (await assign(api, headers[0], scope, None)).json()["data"]["error"][
        "code"
    ] == "task_archived"


@pytest.mark.parametrize("kind", ["worker", "agent"])
async def test_nonhuman_service_identity_cannot_discuss(
    api, application, headers, tenants, admin_engine, kind
):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    async with authenticated(application, headers[0]) as (session, actor):
        actor = Identity(
            actor.user_id, actor.org_id, set(ROLE_SCOPES["admin"]), "admin", actor_kind=kind
        )
        with pytest.raises(ServiceError) as denied:
            await task_discussion.create_thread(
                session,
                actor,
                UUID(scope["card"]["id"]),
                CommentThreadCreate(**message(expected_card_revision=1)),
                application.state.processor.settings,
            )
        assert denied.value.status == 403


async def test_comment_and_event_audit_rollback(
    api, application, headers, tenants, admin_engine, monkeypatch
):
    scope = await scope_with_card(api, headers, tenants, admin_engine)

    def counts():
        with admin_engine.connect() as connection:
            return [
                connection.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE task_id=:task"),
                    {"task": scope["task_id"]},
                )
                for table in (
                    "card_comment_threads",
                    "card_comments",
                    "card_comment_mentions",
                    "task_events",
                )
            ]

    before = counts()

    def broken_audit(*args, **kwargs):
        raise ServiceError("audit_unavailable", "Synthetic audit failure", 503, 3)

    monkeypatch.setattr(task_discussion, "audit", broken_audit)
    response = await api.post(
        f"/cards/{scope['card']['id']}/threads",
        headers=headers[0],
        json=message(expected_card_revision=1),
    )
    assert response.status_code == 503 and counts() == before


async def test_reply_request_key_cannot_cross_thread(api, headers, tenants, admin_engine):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    a = await thread(api, headers[0], scope["card"])
    b = await thread(api, headers[0], scope["card"])
    payload = message()
    for receipt, status in ((a, 200), (b, 409)):
        response = await api.post(
            f"/cards/{scope['card']['id']}/threads/{receipt['thread']['id']}/comments",
            headers=headers[0],
            json=payload,
        )
        assert response.status_code == status
