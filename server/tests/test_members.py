"""Gateway members, sessions and attribution through the real API and admin command.

Failure matrix before implementation: wrong login and wrong password answer
differently; a must_change_password session reaches other routes; another
session survives a password change or a reset; a disabled member keeps access
through a session, a token or an agent; an idle or expired session still
works; the last active administrator is disabled or demoted; a human write on
a gateway carries no member, or a decision table lacks the column; the sixth
failed sign-in still checks the password. Artifacts are synthetic and
reproducible with --basetemp=data/work/members.
"""

import json
import os
import runpy
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from app.api.main import create_app
from app.core import password_attempts
from app.core.config import Settings
from app.models.agent import AgentPrincipal
from app.services.auth import agent_identity
from conftest import FakeQueue
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

GATEWAY = {
    "BID_DEPLOYMENT": "enterprise",
    "BID_PUBLIC_URL": "https://gateway.example.test",
    "BID_COMPANY_NAME": "Synthetic Company",
}
MIGRATION = Path(__file__).parents[1] / "migrations" / "versions" / "0005_members.py"
DECISION_TABLES = runpy.run_path(str(MIGRATION))["DECISION_TABLES"]


def bootstrap_admin(login):
    done = subprocess.run(
        [sys.executable, "-m", "app.admin", "member", "create", "--role", "admin"]
        + ["--login", login],
        env=os.environ | GATEWAY,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def code(response):
    return [response.status_code, response.json()["data"].get("error", {}).get("code")]


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


async def sign_in(api, login, password, label=None):
    body = {"login": login, "password": password}
    if label is not None:
        body["client_label"] = label
    return await api.post("/auth/session", json=body)


async def session_for(api, login, password):
    response = await sign_in(api, login, password)
    assert response.status_code == 200, response.text
    return bearer(response.json()["data"]["session"])


@pytest.fixture
async def gateway(database, tmp_path, monkeypatch):
    with database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO public.installation (mode, company_name)"
                " VALUES ('enterprise', 'Synthetic Company')"
            )
        )
    for name, value in GATEWAY.items():
        monkeypatch.setenv(name, value)
    application = create_app(Settings(data_dir=tmp_path), queue=FakeQueue())
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client,
    ):
        yield client, application


async def test_member_sessions_passwords_and_attribution(gateway, database, tmp_path, monkeypatch):
    api, app = gateway
    report = {"rerun": "pytest -q server/tests/test_members.py --basetemp=data/work/members"}

    probe = await api.get("/installation")
    assert probe.status_code == 200
    report["installation"] = {
        key: probe.json()["data"][key] for key in ("mode", "company_name", "contract_version")
    }
    assert report["installation"]["mode"] == "enterprise"
    assert report["installation"]["company_name"] == "Synthetic Company"

    # Bootstrap through the admin command; the password exists only in its output.
    root = bootstrap_admin("root.admin")
    first = root["initial_password"]
    assert root["member"]["role"] == "admin"
    with database.connect() as connection:
        stored = connection.scalar(text("SELECT password_hash FROM members"))
    assert stored.startswith("pbkdf2$600000$") and first not in stored

    # The same refusal for an unknown login and a wrong password; the access key
    # body is refused on a gateway.
    report["sign_in_refusals"] = {
        "unknown_login": code(await sign_in(api, "nobody", first)),
        "wrong_password": code(await sign_in(api, "root.admin", first + "x")),
        "access_key": code(
            await api.post("/auth/session", json={"access_key": os.environ["BID_ACCESS_KEY"]})
        ),
    }
    assert report["sign_in_refusals"] == {
        "unknown_login": [401, "invalid_login"],
        "wrong_password": [401, "invalid_login"],
        "access_key": [401, "access_key_not_accepted"],
    }

    # An initial password allows only the session and password routes.
    signed = await sign_in(api, "Root.Admin", first, label="synthetic laptop")
    assert signed.json()["data"]["must_change_password"] is True
    root_a = bearer(signed.json()["data"]["session"])
    root_b = await session_for(api, "root.admin", first)
    report["must_change_password"] = {
        "tasks": code(await api.get("/tasks", headers=root_a)),
        "session_show": code(await api.get("/auth/session", headers=root_a)),
        "short_password": code(
            await api.post(
                "/account/password", headers=root_a, json={"current": first, "new": "short"}
            )
        ),
    }
    assert report["must_change_password"] == {
        "tasks": [403, "password_change_required"],
        "session_show": [200, None],
        "short_password": [422, "password_too_short"],
    }
    root_password = "synthetic-root-password"
    changed = await api.post(
        "/account/password", headers=root_a, json={"current": first, "new": root_password}
    )
    assert changed.status_code == 200 and changed.json()["data"]["revoked_sessions"] == 1
    # The other session ends; the one that changed the password continues.
    report["after_password_change"] = {
        "other_session": code(await api.get("/tasks", headers=root_b)),
        "this_session": code(await api.get("/tasks", headers=root_a)),
        "old_password": code(await sign_in(api, "root.admin", first)),
    }
    assert report["after_password_change"] == {
        "other_session": [401, "invalid_session"],
        "this_session": [200, None],
        "old_password": [401, "invalid_login"],
    }
    sessions = (await api.get("/account/sessions", headers=root_a)).json()["items"]
    assert [(row["client_label"], row["current"]) for row in sessions] == [
        ("synthetic laptop", True)
    ]

    # A member is created with a one-time password and holds no admin scopes.
    created = await api.post(
        "/members",
        headers=root_a,
        json={"login": "alice", "display_name": "Alice", "grants": ["library:edit"]},
    )
    assert created.status_code == 200, created.text
    alice = created.json()["data"]["member"]
    taken = await api.post(
        "/members", headers=root_a, json={"login": "ALICE".lower(), "display_name": "Again"}
    )
    assert code(taken) == [409, "member_login_taken"]
    alice_session = await session_for(api, "alice", created.json()["data"]["initial_password"])
    alice_password = "synthetic-alice-password"
    await api.post(
        "/account/password",
        headers=alice_session,
        json={"current": created.json()["data"]["initial_password"], "new": alice_password},
    )
    shown = (await api.get("/auth/session", headers=alice_session)).json()["data"]
    assert shown["member"]["grants"] == ["library:edit"] and "member:manage" not in shown["scopes"]
    assert code(await api.get("/members", headers=alice_session)) == [403, "forbidden"]

    # Attribution: a human write records the member; a direct write without one fails.
    task = await api.post("/tasks", headers=alice_session, json={"name": "Synthetic project"})
    assert task.status_code == 200, task.text
    task_id = task.json()["data"]["id"]
    with database.connect() as connection:
        assert connection.scalar(
            text("SELECT actor_member_id FROM tasks WHERE id = :id"), {"id": task_id}
        ) == UUID(alice["id"])
    with pytest.raises(DBAPIError, match="requires the acting member"), database.begin() as raw:
        raw.execute(text("SELECT set_config('app.actor_kind', 'session', true)"))
        raw.execute(
            text(
                "INSERT INTO audit_logs (id, action, object_id, details, actor_kind)"
                " VALUES (gen_random_uuid(), 'synthetic', gen_random_uuid(), '{}', 'session')"
            )
        )
    with database.connect() as connection:
        decision_tables = connection.scalars(text(DECISION_TABLES)).all()
        missing = connection.scalars(
            text(
                "SELECT t FROM unnest(CAST(:tables AS text[])) t WHERE NOT EXISTS ("
                " SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public'"
                " AND c.table_name=t AND c.column_name IN ('actor_member_id', 'member_id'))"
                " OR NOT EXISTS (SELECT 1 FROM pg_trigger g WHERE g.tgrelid=('public.'||t)::regclass"
                " AND g.tgname='actor_member_attribution')"
            ),
            {"tables": list(decision_tables)},
        ).all()
    report["decision_tables"] = len(decision_tables)
    assert missing == [] and {"check_decisions", "export_runs", "audit_logs"} <= set(
        decision_tables
    )

    # A token and an agent work only while their member does.
    token = await api.post(
        "/tokens",
        headers=alice_session,
        json={
            "name": "synthetic",
            "scopes": ["task:read"],
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    alice_token = bearer(token.json()["data"]["token"])
    assert code(await api.get("/tasks", headers=alice_token)) == [200, None]
    principal = AgentPrincipal(
        id=uuid4(),
        member_id=UUID(alice["id"]),
        initial_grants=["task:read"],
        scopes=["task:read"],
        authority_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    async with app.state.db.transaction() as session:
        assert (await agent_identity(session, principal)).member_id == UUID(alice["id"])
    disabled = await api.patch(
        f"/members/{alice['id']}", headers=root_a, json={"status": "disabled"}
    )
    assert disabled.json()["data"]["status"] == "disabled"
    async with app.state.db.transaction() as session:
        try:
            await agent_identity(session, principal)
            agent_after_disable = None
        except Exception as error:
            agent_after_disable = getattr(error, "code", type(error).__name__)
    report["after_disable"] = {
        "session": code(await api.get("/tasks", headers=alice_session)),
        "token": code(await api.get("/tasks", headers=alice_token)),
        "agent": agent_after_disable,
        "sign_in": code(await sign_in(api, "alice", alice_password)),
    }
    assert report["after_disable"] == {
        "session": [401, "invalid_session"],
        "token": [401, "invalid_token"],
        "agent": "agent_authority_expired",
        "sign_in": [401, "invalid_login"],
    }

    # A reset ends the member's sessions and the old password.
    await api.patch(f"/members/{alice['id']}", headers=root_a, json={"status": "active"})
    alice_session = await session_for(api, "alice", alice_password)
    reset = await api.post(f"/members/{alice['id']}/password-reset", headers=root_a)
    assert reset.json()["data"]["member"]["id"] == alice["id"]
    report["after_reset"] = {
        "session": code(await api.get("/tasks", headers=alice_session)),
        "old_password": code(await sign_in(api, "alice", alice_password)),
        "must_change": (
            await sign_in(api, "alice", reset.json()["data"]["initial_password"])
        ).json()["data"]["must_change_password"],
    }
    assert report["after_reset"] == {
        "session": [401, "invalid_session"],
        "old_password": [401, "invalid_login"],
        "must_change": True,
    }

    # Idle and absolute expiry, and sign-out.
    idle = await session_for(api, "root.admin", root_password)
    expired = await session_for(api, "root.admin", root_password)
    signed_out = await session_for(api, "root.admin", root_password)
    with database.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT id FROM member_sessions WHERE revoked_at IS NULL"
                " AND member_id = :id ORDER BY created_at DESC LIMIT 3"
            ),
            {"id": root["member"]["id"]},
        ).scalars()
        latest, middle, oldest = list(rows)
        connection.execute(
            text(
                "UPDATE member_sessions SET last_seen_at = now() - interval '3 hours' WHERE id=:id"
            ),
            {"id": oldest},
        )
        connection.execute(
            text(
                "UPDATE member_sessions SET created_at = now() - interval '2 days',"
                " expires_at = now() - interval '1 second' WHERE id=:id"
            ),
            {"id": middle},
        )
    assert (await api.delete("/auth/session", headers=signed_out)).json()["data"]["revoked"]
    report["expiry"] = {
        "idle": code(await api.get("/tasks", headers=idle)),
        "expired": code(await api.get("/tasks", headers=expired)),
        "signed_out": code(await api.get("/tasks", headers=signed_out)),
    }
    assert report["expiry"] == {
        "idle": [401, "invalid_session"],
        "expired": [401, "invalid_session"],
        "signed_out": [401, "invalid_session"],
    }
    assert latest

    # The last active administrator can be neither demoted nor disabled.
    root_id = root["member"]["id"]
    report["last_admin"] = {
        "demote": code(
            await api.patch(f"/members/{root_id}", headers=root_a, json={"role": "member"})
        ),
        "disable": code(
            await api.patch(f"/members/{root_id}", headers=root_a, json={"status": "disabled"})
        ),
    }
    with pytest.raises(DBAPIError, match="last_admin"), database.begin() as connection:
        connection.execute(text("UPDATE members SET status='disabled', disabled_at=now()"))
    second = await api.post(
        "/members",
        headers=root_a,
        json={"login": "second.admin", "display_name": "B", "role": "admin"},
    )
    report["last_admin"]["demote_with_second"] = code(
        await api.patch(f"/members/{root_id}", headers=root_a, json={"role": "member"})
    )
    assert second.status_code == 200
    assert report["last_admin"] == {
        "demote": [409, "last_admin"],
        "disable": [409, "last_admin"],
        "demote_with_second": [200, None],
    }

    # Five failures lock a login (this one has none yet); the sixth attempt is
    # refused before any password check, even with the right password.
    checks = []
    verify = password_attempts.verify_password

    def counted(password, encoded):
        checks.append(1)
        return verify(password, encoded)

    monkeypatch.setattr(password_attempts, "verify_password", counted)
    failures = [code(await sign_in(api, "second.admin", f"wrong-{n}")) for n in range(5)]
    before = len(checks)
    sixth = code(await sign_in(api, "second.admin", second.json()["data"]["initial_password"]))
    report["limiter"] = {
        "failures": failures,
        "sixth": sixth,
        "checks_before_sixth": before,
        "checks_after_sixth": len(checks),
    }
    assert failures == [[401, "invalid_login"]] * 5
    assert sixth == [429, "too_many_attempts"] and len(checks) == before == 5
    (tmp_path / "members.json").write_text(json.dumps(report, indent=2))
