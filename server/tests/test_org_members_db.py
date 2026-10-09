"""Org-member HTTP acceptance and database isolation against real PostgreSQL.

Failure modes precede implementation in ``data/work/org-members/failure-modes.md``.
Run with ``--basetemp=data/work/org-members/pytest`` to retain the sanitized JSON
receipts. These tests never replace PostgreSQL or persist invitation/session secrets.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.core.security import verify_password
from app.services.auth import HUMAN_ONLY_SCOPES, ROLE_SCOPES, SCOPES
from conftest import PASSWORD
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from task_fixtures import actor_context_async

PATH = "/org/members"
NEW_PASSWORD = "synthetic-member-password"
ENVELOPE = {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
ADMIN_FIELDS = {
    "user_id",
    "email",
    "role",
    "active",
    "password_set",
    "revision",
    "created_by",
    "created_at",
    "updated_at",
}
PRIVATE_FIELDS = {"password_set", "revision", "created_by", "created_at", "updated_at"}


def success(response, command):
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == ENVELOPE
    assert payload["ok"] is True and payload["command"] == command
    return payload


def failure(response, status, code):
    assert response.status_code == status, response.text
    payload = response.json()
    assert set(payload) == ENVELOPE
    assert payload["ok"] is False and payload["data"]["error"]["code"] == code
    return payload["data"]["error"]


async def added(api, headers, email="new-member@example.test", role="bidder"):
    response = await api.post(PATH, headers=headers, json={"email": email, "role": role})
    data = success(response, "org member add")["data"]
    assert set(data) == {"member", "invitation_url", "expires_in"}
    assert set(data["member"]) == ADMIN_FIELDS
    return data


async def signed_in(api, org, email, password=PASSWORD):
    response = await api.post(
        "/auth/login", json={"email": email, "password": password, "org_id": str(org)}
    )
    data = success(response, "login")["data"]
    return {"Authorization": "Bearer " + data["session"], "X-Org-Id": str(org)}


def setup_secret(invitation):
    prefix = "/app/setup-password#token="
    assert invitation["invitation_url"].startswith(prefix)
    assert invitation["expires_in"] == 24 * 60 * 60
    return invitation["invitation_url"][len(prefix) :]


def membership_row(admin_engine, org, user):
    with admin_engine.connect() as connection:
        return dict(
            connection.execute(
                text("SELECT * FROM memberships WHERE org_id=:org AND user_id=:user"),
                {"org": org, "user": UUID(str(user))},
            )
            .mappings()
            .one()
        )


def password_hash(admin_engine, user):
    with admin_engine.connect() as connection:
        return connection.scalar(
            text("SELECT password_hash FROM users WHERE id=:user"), {"user": UUID(str(user))}
        )


def member_audit(admin_engine, org):
    with admin_engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT actor_user_id, actor_token_id, action, object_id, details "
                    "FROM audit_logs WHERE org_id=:org AND action LIKE 'org.member%' "
                    "ORDER BY created_at, id"
                ),
                {"org": org},
            ).mappings()
        ]


def receipt(tmp_path, name, **payload):
    path = tmp_path / f"org-members-{name}.json"
    path.write_text(
        json.dumps({"scenario": name, **payload}, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    assert json.loads(path.read_text(encoding="utf-8"))["scenario"] == name


async def issued_token(api, headers):
    response = await api.post(
        "/tokens",
        headers=headers,
        json={
            "name": "Synthetic org-member acceptance",
            "scopes": ["task:read"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    data = success(response, "token create")["data"]
    return data["id"], {**headers, "Authorization": "Bearer " + data["token"]}


async def test_new_email_invitation_setup_login_and_used_links_die(
    api, headers, tenants, admin_engine, tmp_path, caplog
):
    invitation = await added(api, headers[0], " NEW-MEMBER@EXAMPLE.TEST ", "technical")
    member = invitation["member"]
    assert member["email"] == "new-member@example.test"
    assert member["role"] == "technical" and member["active"] is True
    assert member["password_set"] is False and member["revision"] == 1
    assert member["created_by"] == str(tenants["users"][0])
    assert member["created_at"] and member["updated_at"]
    before = password_hash(admin_engine, member["user_id"])
    assert not verify_password(NEW_PASSWORD, before)
    reissued = await api.post(f"{PATH}/{member['user_id']}/invitation", headers=headers[0])
    fresh = success(reissued, "org member invite")["data"]
    assert fresh["member"] == member
    links = [setup_secret(invitation), setup_secret(fresh)]
    response = await api.post(
        "/auth/setup-password", json={"token": links[-1], "password": NEW_PASSWORD}
    )
    assert success(response, "auth setup-password")["data"] == {"password_set": True}
    stored = password_hash(admin_engine, member["user_id"])
    assert stored != before and verify_password(NEW_PASSWORD, stored)
    colleague = await signed_in(api, tenants["orgs"][0], member["email"], NEW_PASSWORD)
    current = success(await api.get("/org/current", headers=colleague), "org use")["data"]
    assert current == {
        "org_id": str(tenants["orgs"][0]),
        "user_id": member["user_id"],
        "role": "technical",
    }
    for token in links:
        failure(
            await api.post("/auth/setup-password", json={"token": token, "password": NEW_PASSWORD}),
            400,
            "invalid_setup_link",
        )
    failure(
        await api.post(f"{PATH}/{member['user_id']}/invitation", headers=headers[0]),
        409,
        "password_already_set",
    )
    audit = member_audit(admin_engine, tenants["orgs"][0])
    assert len(audit) == 2
    assert [row["action"] for row in audit] == ["org.member.add", "org.member.invite"]
    assert all(row["actor_user_id"] == tenants["users"][0] for row in audit)
    assert all(row["actor_token_id"] is None for row in audit)
    assert all(row["object_id"] == UUID(member["user_id"]) for row in audit)
    state = {"role": "technical", "active": True, "revision": 1}
    assert audit[0]["details"] == {"user_id": member["user_id"], "before": None, "after": state}
    assert audit[1]["details"] == {"user_id": member["user_id"], "before": state, "after": state}
    serialized = json.dumps(audit, default=str) + caplog.text
    for secret in (*links, invitation["invitation_url"], NEW_PASSWORD, stored, before):
        assert secret not in serialized
    assert "invitation_url" not in serialized and "password_hash" not in serialized
    receipt(tmp_path, "new-email", member=member, current=current, audit=audit)


async def test_existing_other_org_identity_keeps_password_and_can_switch(
    api, headers, tenants, admin_engine, tmp_path
):
    user, org_a, org_b = tenants["users"][1], *tenants["orgs"]
    original_hash = password_hash(admin_engine, user)
    original_b = membership_row(admin_engine, org_b, user)
    invitation = await added(api, headers[0], " B@EXAMPLE.TEST ", "viewer")
    assert invitation["member"]["user_id"] == str(user)
    assert invitation["member"]["password_set"] is True
    assert invitation["invitation_url"] == "/app/org/login"
    assert invitation["expires_in"] is None
    assert password_hash(admin_engine, user) == original_hash
    assert membership_row(admin_engine, org_b, user) == original_b
    organizations = success(
        await api.post("/auth/orgs", json={"email": "b@example.test", "password": PASSWORD}),
        "auth orgs",
    )["items"]
    assert {(row["org_id"], row["role"]) for row in organizations} == {
        (str(org_a), "viewer"),
        (str(org_b), "admin"),
    }
    same_session_a = {**headers[1], "X-Org-Id": str(org_a)}
    assert success(await api.get("/org/current", headers=same_session_a), "org use")["data"] == {
        "org_id": str(org_a),
        "user_id": str(user),
        "role": "viewer",
    }
    assert (
        success(await api.get("/org/current", headers=headers[1]), "org use")["data"]["role"]
        == "admin"
    )
    await signed_in(api, org_a, "b@example.test")
    receipt(tmp_path, "existing-email", member=invitation["member"], organizations=organizations)


async def test_duplicate_add_is_conflict_and_has_no_side_effects(
    api, headers, tenants, admin_engine
):
    invitation = await added(api, headers[0])
    user = invitation["member"]["user_id"]
    before = membership_row(admin_engine, tenants["orgs"][0], user)
    original_hash = password_hash(admin_engine, user)
    audit = member_audit(admin_engine, tenants["orgs"][0])
    failure(
        await api.post(
            PATH, headers=headers[0], json={"email": " NEW-MEMBER@EXAMPLE.TEST ", "role": "admin"}
        ),
        409,
        "member_exists",
    )
    assert membership_row(admin_engine, tenants["orgs"][0], user) == before
    assert password_hash(admin_engine, user) == original_hash
    assert member_audit(admin_engine, tenants["orgs"][0]) == audit


async def test_concurrent_duplicate_add_creates_one_identity_and_membership(
    api, headers, tenants, admin_engine
):
    responses = await asyncio.gather(
        *[
            api.post(PATH, headers=headers[0], json={"email": email, "role": "bidder"})
            for email in ("racing-member@example.test", " RACING-MEMBER@EXAMPLE.TEST ")
        ]
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    failure(next(row for row in responses if row.status_code == 409), 409, "member_exists")
    winner = success(next(row for row in responses if row.status_code == 200), "org member add")[
        "data"
    ]
    with admin_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM users WHERE email='racing-member@example.test'")
            )
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM memberships WHERE org_id=:org AND user_id=:user"),
                {"org": tenants["orgs"][0], "user": UUID(winner["member"]["user_id"])},
            )
            == 1
        )
    assert len(member_audit(admin_engine, tenants["orgs"][0])) == 1


async def test_role_cas_stale_and_concurrent_requests_commit_once(
    api, headers, tenants, admin_engine, tmp_path
):
    member = (await added(api, headers[0]))["member"]
    url = f"{PATH}/{member['user_id']}/role"
    changed = success(
        await api.post(url, headers=headers[0], json={"role": "technical", "expected_revision": 1}),
        "org member role",
    )["data"]
    assert changed["revision"] == 2 and changed["role"] == "technical"
    before = membership_row(admin_engine, tenants["orgs"][0], member["user_id"])
    audit = member_audit(admin_engine, tenants["orgs"][0])
    assert audit[-1]["action"] == "org.member.role"
    assert audit[-1]["details"] == {
        "user_id": member["user_id"],
        "before": {"role": "bidder", "active": True, "revision": 1},
        "after": {"role": "technical", "active": True, "revision": 2},
    }
    failure(
        await api.post(url, headers=headers[0], json={"role": "viewer", "expected_revision": 1}),
        409,
        "revision_conflict",
    )
    assert membership_row(admin_engine, tenants["orgs"][0], member["user_id"]) == before
    assert member_audit(admin_engine, tenants["orgs"][0]) == audit
    responses = await asyncio.gather(
        *[
            api.post(url, headers=headers[0], json={"role": role, "expected_revision": 2})
            for role in ("bidder", "viewer")
        ]
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    failure(
        next(response for response in responses if response.status_code == 409),
        409,
        "revision_conflict",
    )
    winner = success(
        next(response for response in responses if response.status_code == 200), "org member role"
    )["data"]
    assert winner["revision"] == 3
    stored = membership_row(admin_engine, tenants["orgs"][0], member["user_id"])
    assert stored["role"] == winner["role"] and stored["revision"] == 3
    assert len(member_audit(admin_engine, tenants["orgs"][0])) == len(audit) + 1
    receipt(
        tmp_path,
        "role-cas",
        winner=winner,
        statuses=[response.status_code for response in responses],
    )


@pytest.mark.parametrize(
    "suffix,body", [("role", {"role": "bidder"}), ("active", {"active": False})]
)
async def test_last_admin_cannot_demote_or_deactivate_self(
    api, headers, tenants, admin_engine, suffix, body
):
    org, user = tenants["orgs"][0], tenants["users"][0]
    before = membership_row(admin_engine, org, user)
    failure(
        await api.post(
            f"{PATH}/{user}/{suffix}", headers=headers[0], json={**body, "expected_revision": 1}
        ),
        409,
        "last_admin_required",
    )
    assert membership_row(admin_engine, org, user) == before
    assert member_audit(admin_engine, org) == []


@pytest.mark.parametrize(
    "suffix,body", [("role", {"role": "bidder"}), ("active", {"active": False})]
)
async def test_concurrent_admin_self_changes_keep_one_active_admin(
    api, headers, tenants, admin_engine, suffix, body
):
    await added(api, headers[0], "b@example.test", "admin")
    actors = [headers[0], {**headers[1], "X-Org-Id": str(tenants["orgs"][0])}]
    responses = await asyncio.gather(
        *[
            api.post(
                f"{PATH}/{user}/{suffix}", headers=actor, json={**body, "expected_revision": 1}
            )
            for user, actor in zip(tenants["users"], actors, strict=True)
        ]
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    failure(
        next(response for response in responses if response.status_code == 409),
        409,
        "last_admin_required",
    )
    with admin_engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM memberships WHERE org_id=:org AND active AND role='admin'"
                ),
                {"org": tenants["orgs"][0]},
            )
            == 1
        )


async def test_deactivate_denies_next_request_revokes_only_org_tokens_preserves_history(
    api, headers, tenants, admin_engine, tmp_path
):
    org_a, org_b = tenants["orgs"]
    user = tenants["users"][1]
    member = (await added(api, headers[0], "b@example.test", "admin"))["member"]
    original_membership = membership_row(admin_engine, org_a, user)
    colleague = {**headers[1], "X-Org-Id": str(org_a)}
    token_a, token_headers_a = await issued_token(api, colleague)
    token_b, token_headers_b = await issued_token(api, headers[1])
    task = success(
        await api.post(
            "/tasks", headers=colleague, json={"name": "Synthetic preserved member history"}
        ),
        "task create",
    )["data"]["id"]
    assert (await api.get(f"/tasks/{task}/workflow", headers=token_headers_a)).status_code == 200
    with admin_engine.connect() as connection:
        history = connection.execute(
            text(
                "SELECT id, user_id, role, active FROM task_members WHERE org_id=:org AND task_id=:task ORDER BY id"
            ),
            {"org": org_a, "task": UUID(task)},
        ).all()
        task_events = connection.execute(
            text(
                "SELECT id, event_kind, payload FROM task_events WHERE org_id=:org AND task_id=:task ORDER BY id"
            ),
            {"org": org_a, "task": UUID(task)},
        ).all()
    disabled = success(
        await api.post(
            f"{PATH}/{user}/active",
            headers=headers[0],
            json={"active": False, "expected_revision": 1},
        ),
        "org member set-active",
    )["data"]
    assert disabled["revision"] == 2 and disabled["active"] is False
    failure(await api.get("/org/current", headers=colleague), 404, "not_found")
    failure(await api.get("/org/current", headers=token_headers_a), 401, "invalid_token")
    assert (await api.get("/org/current", headers=token_headers_b)).status_code == 200
    assert (await api.get("/org/current", headers=headers[1])).status_code == 200
    with admin_engine.connect() as connection:
        revoked = dict(
            connection.execute(
                text("SELECT id, revoked FROM api_tokens WHERE id IN (:a, :b)"),
                {"a": UUID(token_a), "b": UUID(token_b)},
            ).all()
        )
        assert revoked == {UUID(token_a): True, UUID(token_b): False}
        assert (
            connection.execute(
                text(
                    "SELECT id, user_id, role, active FROM task_members WHERE org_id=:org AND task_id=:task ORDER BY id"
                ),
                {"org": org_a, "task": UUID(task)},
            ).all()
            == history
        )
        assert (
            connection.execute(
                text(
                    "SELECT id, event_kind, payload FROM task_events WHERE org_id=:org AND task_id=:task ORDER BY id"
                ),
                {"org": org_a, "task": UUID(task)},
            ).all()
            == task_events
        )
        assert (
            connection.scalar(
                text("SELECT created_by FROM tasks WHERE id=:task"), {"task": UUID(task)}
            )
            == user
        )
    failure(
        await api.post(
            f"{PATH}/{user}/active",
            headers=headers[0],
            json={"active": True, "expected_revision": 1},
        ),
        409,
        "revision_conflict",
    )
    restored = success(
        await api.post(
            f"{PATH}/{user}/active",
            headers=headers[0],
            json={"active": True, "expected_revision": 2},
        ),
        "org member set-active",
    )["data"]
    assert restored["revision"] == 3 and restored["active"] is True
    assert membership_row(admin_engine, org_a, user)["id"] == original_membership["id"]
    assert (await api.get(f"/tasks/{task}/workflow", headers=colleague)).status_code == 200
    failure(await api.get("/org/current", headers=token_headers_a), 401, "invalid_token")
    assert membership_row(admin_engine, org_b, user)["active"] is True
    audit = member_audit(admin_engine, org_a)
    assert [row["action"] for row in audit] == [
        "org.member.add",
        "org.member.active",
        "org.member.active",
    ]
    assert audit[1]["details"] == {
        "user_id": str(user),
        "before": {"role": "admin", "active": True, "revision": 1},
        "after": {"role": "admin", "active": False, "revision": 2},
    }
    assert audit[2]["details"] == {
        "user_id": str(user),
        "before": {"role": "admin", "active": False, "revision": 2},
        "after": {"role": "admin", "active": True, "revision": 3},
    }
    receipt(
        tmp_path,
        "deactivate-reactivate",
        task_id=task,
        member=member,
        disabled=disabled,
        restored=restored,
        history=[dict(row._mapping) for row in history],
        audit=audit,
    )


@pytest.mark.parametrize("role", ["bidder", "technical", "viewer"])
async def test_nonadmins_get_active_public_projection_and_cannot_manage(
    api, headers, tenants, admin_engine, role
):
    member = (await added(api, headers[0], "b@example.test", role))["member"]
    hidden = (await added(api, headers[0], "inactive@example.test"))["member"]
    success(
        await api.post(
            f"{PATH}/{hidden['user_id']}/active",
            headers=headers[0],
            json={"active": False, "expected_revision": 1},
        ),
        "org member set-active",
    )
    colleague = {**headers[1], "X-Org-Id": str(tenants["orgs"][0])}
    administrative = success(await api.get(PATH, headers=headers[0]), "org member list")["items"]
    assert len(administrative) == 3 and all(set(row) == ADMIN_FIELDS for row in administrative)
    assert (
        next(row for row in administrative if row["user_id"] == hidden["user_id"])["active"]
        is False
    )
    rows = success(await api.get(PATH, headers=colleague), "org member list")["items"]
    assert {row["user_id"] for row in rows} == {str(tenants["users"][0]), member["user_id"]}
    assert all(row["active"] is True for row in rows)
    assert all(set(row) == {"user_id", "email", "role", "active"} for row in rows)
    assert all(all(row.get(field) is None for field in PRIVATE_FIELDS) for row in rows)
    before = member_audit(admin_engine, tenants["orgs"][0])
    mutations = [
        (PATH, {"email": "denied@example.test", "role": "admin"}),
        (f"{PATH}/{member['user_id']}/role", {"role": "admin", "expected_revision": 1}),
        (f"{PATH}/{member['user_id']}/active", {"active": False, "expected_revision": 1}),
        (f"{PATH}/{member['user_id']}/invitation", None),
    ]
    for url, body in mutations:
        failure(await api.post(url, headers=colleague, json=body), 403, "forbidden")
    assert member_audit(admin_engine, tenants["orgs"][0]) == before


async def test_tokens_cannot_manage_or_receive_member_manage_scope(
    api, headers, tenants, admin_engine
):
    user = tenants["users"][0]
    _, token_headers = await issued_token(api, headers[0])
    rows = success(await api.get(PATH, headers=token_headers), "org member list")["items"]
    assert all(set(row) == ADMIN_FIELDS for row in rows)
    assert "member:manage" in HUMAN_ONLY_SCOPES and "member:manage" not in SCOPES
    assert "member:manage" in ROLE_SCOPES["admin"]
    assert all(
        "member:manage" not in ROLE_SCOPES[role] for role in ("bidder", "technical", "viewer")
    )
    for url, body in (
        (PATH, {"email": "denied@example.test", "role": "admin"}),
        (f"{PATH}/{user}/role", {"role": "viewer", "expected_revision": 1}),
        (f"{PATH}/{user}/active", {"active": False, "expected_revision": 1}),
        (f"{PATH}/{user}/invitation", None),
    ):
        failure(await api.post(url, headers=token_headers, json=body), 403, "forbidden")
    failure(
        await api.post(
            "/tokens",
            headers=headers[0],
            json={
                "name": "Denied membership management",
                "scopes": ["member:manage"],
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        ),
        403,
        "forbidden_scopes",
    )
    with pytest.raises(DBAPIError) as error, admin_engine.begin() as connection:
        connection.execute(
            text("UPDATE api_tokens SET scopes='[\"member:manage\"]'::jsonb WHERE org_id=:org"),
            {"org": tenants["orgs"][0]},
        )
    assert getattr(error.value.orig, "sqlstate", None) == "23514"


async def test_other_org_member_targets_are_indistinguishable_from_missing(
    api, headers, tenants, admin_engine
):
    own = success(await api.get(PATH, headers=headers[0]), "org member list")["items"]
    assert [row["user_id"] for row in own] == [str(tenants["users"][0])]
    assert own[0]["revision"] == 1 and own[0]["created_by"] is None
    foreign, missing = tenants["users"][1], uuid4()
    for suffix, body in (
        ("role", {"role": "viewer", "expected_revision": 1}),
        ("active", {"active": False, "expected_revision": 1}),
        ("invitation", None),
    ):
        errors = [
            failure(
                await api.post(f"{PATH}/{user}/{suffix}", headers=headers[0], json=body),
                404,
                "not_found",
            )
            for user in (foreign, missing)
        ]
        assert errors[0] == errors[1]
    assert membership_row(admin_engine, tenants["orgs"][1], foreign)["role"] == "admin"
    assert member_audit(admin_engine, tenants["orgs"][0]) == []


@pytest.mark.parametrize(
    "body",
    [
        {"email": "not-an-email", "role": "bidder"},
        {"email": "valid@example.test", "role": "owner"},
        {"email": "valid@example.test", "role": "bidder", "org_id": "unexpected"},
    ],
)
async def test_invalid_add_inputs_do_not_create_users_or_audit(
    api, headers, tenants, admin_engine, body
):
    with admin_engine.connect() as connection:
        before = connection.scalar(text("SELECT count(*) FROM users"))
    failure(await api.post(PATH, headers=headers[0], json=body), 400, "invalid_input")
    with admin_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM users")) == before
    assert member_audit(admin_engine, tenants["orgs"][0]) == []


async def test_membership_rls_hides_foreign_rows_and_rejects_foreign_inserts(
    application, tenants, admin_engine
):
    org_a, org_b = tenants["orgs"]
    async with application.state.db.transaction(org_a) as session:
        assert await session.scalar(text("SELECT count(*) FROM memberships")) == 1
        assert (
            await session.scalar(
                text("SELECT count(*) FROM memberships WHERE org_id=:org"), {"org": org_b}
            )
            == 0
        )
        assert (
            await session.execute(
                text("UPDATE memberships SET active=false WHERE org_id=:org"), {"org": org_b}
            )
        ).rowcount == 0
        assert (
            await session.execute(text("DELETE FROM memberships WHERE org_id=:org"), {"org": org_b})
        ).rowcount == 0
    with pytest.raises(DBAPIError):
        async with application.state.db.transaction(org_a) as session:
            await session.execute(
                text(
                    "INSERT INTO memberships(id, org_id, user_id, role, active) VALUES (:id,:org,:user,'viewer',true)"
                ),
                {"id": uuid4(), "org": org_b, "user": tenants["users"][0]},
            )
    assert membership_row(admin_engine, org_b, tenants["users"][1])["active"] is True
    async with application.state.db.transaction() as session:
        assert await session.scalar(text("SELECT count(*) FROM memberships")) == 0


def test_member_add_function_has_restricted_owner_and_fixed_search_path(admin_engine):
    with admin_engine.connect() as connection:
        function = connection.execute(
            text(
                "SELECT pg_get_userbyid(p.proowner), p.prosecdef, p.proconfig, "
                "has_function_privilege('bid_app', p.oid, 'EXECUTE'), "
                "has_function_privilege('public', p.oid, 'EXECUTE'), "
                "pg_get_functiondef(p.oid) "
                "FROM pg_proc p WHERE p.oid='public.org_add_member(uuid,uuid,text,text)'::regprocedure"
            )
        ).one()
        owner, security_definer, config, app_execute, public_execute, definition = function
        assert owner == "bid_platform_fn" and security_definer is True
        assert config == ["search_path=pg_catalog, public"]
        assert app_execute is True and public_execute is False
        assert "EXECUTE " not in definition.upper()
        assert connection.execute(
            text("SELECT rolcanlogin, rolsuper, rolbypassrls FROM pg_roles WHERE rolname=:role"),
            {"role": owner},
        ).one() == (False, False, False)
        assert not connection.scalar(
            text("SELECT pg_has_role('bid_app', 'bid_platform_fn', 'MEMBER')")
        )
        assert not connection.scalar(
            text("SELECT has_table_privilege('bid_platform_fn', 'users', 'UPDATE')")
        )
        assert not connection.scalar(
            text("SELECT has_table_privilege('bid_app', 'users', 'INSERT')")
        )
        assert connection.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid='memberships'::regclass"
            )
        ).one() == (True, True)


async def test_member_add_function_refuses_missing_context_cross_org_actor_and_tokens(
    application, tenants, admin_engine
):
    org_a, org_b = tenants["orgs"]
    user_a, user_b = tenants["users"]
    statement = text("SELECT * FROM public.org_add_member(:org, :actor, :email, :role)")
    with admin_engine.connect() as connection:
        users_before = connection.scalar(text("SELECT count(*) FROM users"))
    async with application.state.db.transaction() as session:
        denied = (
            await session.execute(
                statement,
                {"org": org_a, "actor": user_a, "email": "denied@example.test", "role": "bidder"},
            )
        ).one()
        assert tuple(denied) == ("forbidden", None)
    async with application.state.db.transaction(org_a) as session:
        await actor_context_async(session, org_a, user_a)
        for org, actor in ((org_b, user_a), (org_a, user_b)):
            denied = (
                await session.execute(
                    statement,
                    {"org": org, "actor": actor, "email": "denied@example.test", "role": "bidder"},
                )
            ).one()
            assert tuple(denied) == ("forbidden", None)
        await actor_context_async(session, org_a, user_a, kind="token", token=uuid4())
        denied = (
            await session.execute(
                statement,
                {"org": org_a, "actor": user_a, "email": "denied@example.test", "role": "bidder"},
            )
        ).one()
        assert tuple(denied) == ("forbidden", None)
        assert await session.scalar(text("SELECT current_setting('app.current_org', true)")) == str(
            org_a
        )
        assert await session.scalar(text("SELECT count(*) FROM memberships")) == 1
    with admin_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM users")) == users_before
        assert connection.scalar(text("SELECT count(*) FROM memberships")) == 2


async def test_member_add_function_requires_actual_active_admin(
    api, headers, application, tenants, admin_engine
):
    org, user = tenants["orgs"][0], tenants["users"][1]
    await added(api, headers[0], "b@example.test", "viewer")
    async with application.state.db.transaction(org) as session:
        await actor_context_async(session, org, user)
        result = (
            await session.execute(
                text(
                    "SELECT * FROM public.org_add_member(:org, :actor, 'denied@example.test', 'bidder')"
                ),
                {"org": org, "actor": user},
            )
        ).one()
        assert tuple(result) == ("forbidden", None)
    with admin_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT count(*) FROM users WHERE email='denied@example.test'"))
            == 0
        )


@pytest.mark.parametrize(
    "bypass",
    ["missing_actor", "missing_cas", "stale_cas", "last_admin", "token", "delete", "insert"],
)
async def test_direct_membership_writes_cannot_bypass_human_cas_or_last_admin(
    application, tenants, admin_engine, bypass
):
    org, user = tenants["orgs"][0], tenants["users"][0]
    original = membership_row(admin_engine, org, user)
    with pytest.raises(DBAPIError) as error:
        async with application.state.db.transaction(org) as session:
            if bypass != "missing_actor":
                await actor_context_async(session, org, user)
            if bypass in {"last_admin", "token", "missing_actor"}:
                await session.execute(
                    text("SELECT set_config('app.member_expected_revision', '1', true)")
                )
            if bypass == "stale_cas":
                await session.execute(
                    text("SELECT set_config('app.member_expected_revision', '2', true)")
                )
            if bypass == "token":
                await actor_context_async(session, org, user, kind="token", token=uuid4())
            if bypass == "delete":
                await session.execute(
                    text("DELETE FROM memberships WHERE user_id=:user"), {"user": user}
                )
            elif bypass == "insert":
                await session.execute(
                    text(
                        "INSERT INTO memberships(id, org_id, user_id, role, active) VALUES (:id,:org,:user,'viewer',true)"
                    ),
                    {"id": uuid4(), "org": org, "user": tenants["users"][1]},
                )
            else:
                assignment = "active=false" if bypass == "last_admin" else "role='bidder'"
                await session.execute(
                    text(
                        f"UPDATE memberships SET {assignment}, revision=revision+1 WHERE user_id=:user"
                    ),
                    {"user": user},
                )
    assert getattr(error.value.orig, "sqlstate", None) == "23514"
    assert membership_row(admin_engine, org, user) == original


@pytest.mark.parametrize("expected_revision", [0, "1", True])
async def test_invalid_revision_is_rejected_before_member_change(
    api, headers, tenants, admin_engine, expected_revision
):
    org, user = tenants["orgs"][0], tenants["users"][0]
    original = membership_row(admin_engine, org, user)
    failure(
        await api.post(
            f"{PATH}/{user}/role",
            headers=headers[0],
            json={"role": "bidder", "expected_revision": expected_revision},
        ),
        400,
        "invalid_input",
    )
    assert membership_row(admin_engine, org, user) == original
