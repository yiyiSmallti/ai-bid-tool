"""Organization signup through HTTP and restricted functions against real PostgreSQL.

Failure modes are enumerated before implementation in
``data/work/org-signup/failure-modes.md``. These tests never substitute the database.
"""

import asyncio
import hashlib
import json
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from app.api.main import create_app
from app.core import password_attempts
from app.core.security import verify_password
from app.jobs.queue import Queue
from app.models.entities import User
from app.services.org_signup import OrgSignupService, source_digest
from conftest import OPERATOR, PASSWORD, FakeQueue, cheap_password_hash
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_platform_auth import platform_settings, sign_in

PATH = "/auth/org-applications"
PLATFORM = "/platform/org-applications"
APPLICANT_PASSWORD = "synthetic-applicant-password"
ENVELOPE = {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
SOURCE = "192.0.2.10"


def body(email="applicant@example.test", **changes):
    return {
        "org_name": "Synthetic applicant org",
        "contact_name": "Synthetic contact",
        "email": email,
        "phone": "+86 138 0000 0000",
        "note": "Synthetic application\nNo real applicant data",
        "password": APPLICANT_PASSWORD,
        **changes,
    }


def failure(response, status, code):
    assert response.status_code == status, response.text
    payload = response.json()
    assert set(payload) == ENVELOPE
    assert payload["ok"] is False
    assert payload["data"]["error"]["code"] == code
    if status in (429, 503):
        assert payload["data"]["error"]["exit_code"] == 3
    return payload


def applications(admin_engine):
    with admin_engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                text("SELECT * FROM org_applications ORDER BY created_at, id")
            ).mappings()
        ]


def signup_audit(admin_engine):
    with admin_engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT * FROM platform_audit_logs WHERE action LIKE '%org_application%' ORDER BY created_at, id"
                )
            ).mappings()
        ]


@pytest.fixture
async def signup(operator, tmp_path):
    settings = platform_settings(tmp_path).model_copy(update={"org_signup_enabled": True})
    app = create_app(settings, queue=FakeQueue())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=(SOURCE, 1234)), base_url="http://test"
        ) as api,
    ):
        response = await sign_in(api)
        assert response.status_code == 200, response.text
        headers = {"Authorization": "Bearer " + response.json()["data"]["session"]}
        yield app, api, headers, settings


async def submitted(signup, admin_engine, **changes):
    _, api, _, _ = signup
    response = await api.post(PATH, json=body(**changes))
    assert response.status_code == 200, response.text
    assert set(response.json()) == ENVELOPE
    assert response.json()["data"] == {
        "submitted": True,
        "review": "platform_manual",
        "sign_in_after_approval": True,
    }
    return applications(admin_engine)[-1]


async def test_submit_approve_login_empty_org_and_atomic_audit(
    signup, admin_engine, tenants, caplog
):
    app, api, headers, _ = signup
    row = await submitted(signup, admin_engine, email=" APPLICANT@EXAMPLE.TEST ")
    assert row["email"] == "applicant@example.test"
    assert verify_password(APPLICANT_PASSWORD, row["password_hash"])
    assert (row["expires_at"] - row["created_at"]) == timedelta(days=30)
    assert len(row["source_digest"]) == 64 and SOURCE not in str(row)
    assert row["source_digest"] == source_digest(signup[3], SOURCE)
    assert row["source_digest"] != hashlib.sha256(SOURCE.encode()).hexdigest()
    listed = await api.get(PLATFORM, headers=headers)
    assert listed.status_code == 200, listed.text
    [view] = listed.json()["items"]
    assert view["id"] == str(row["id"])
    assert view["existing_user"] is False and view["source_submissions_24h"] == 1
    assert "password_hash" not in view and "source_digest" not in view
    orgs = await api.get("/platform/orgs", headers=headers)
    assert orgs.json()["data"]["pending_applications"] == 1
    approved = await api.post(
        f"{PLATFORM}/{row['id']}/approve",
        headers=headers,
        json={"org_name": "Corrected synthetic org"},
    )
    assert approved.status_code == 200, approved.text
    assert set(approved.json()) == ENVELOPE
    decision = approved.json()["data"]
    assert decision["application_id"] == str(row["id"]) and decision["status"] == "approved"
    assert decision["user_created"] is True and decision["attached_existing_user"] is False
    saved = applications(admin_engine)[0]
    assert saved["status"] == "approved" and saved["password_hash"] is None
    assert saved["decided_by"] == OPERATOR and saved["org_id"] == UUID(decision["org_id"])
    with admin_engine.connect() as connection:
        membership = connection.execute(
            text("SELECT role FROM memberships WHERE org_id = :org AND user_id = :user"),
            {"org": saved["org_id"], "user": saved["admin_user_id"]},
        ).scalar_one()
        assert membership == "admin"
        assert (
            connection.scalar(text("SELECT name FROM orgs WHERE id = :id"), {"id": saved["org_id"]})
            == "Corrected synthetic org"
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM tasks WHERE org_id = :id"), {"id": saved["org_id"]}
            )
            == 0
        )
    logged_in = await api.post(
        "/auth/login",
        json={
            "email": row["email"],
            "password": APPLICANT_PASSWORD,
            "org_id": decision["org_id"],
        },
    )
    assert logged_in.status_code == 200, logged_in.text
    session = logged_in.json()["data"]["session"]
    applicant_headers = {"Authorization": "Bearer " + session, "X-Org-Id": decision["org_id"]}
    current = await api.get("/org/current", headers=applicant_headers)
    assert current.status_code == 200, current.text
    assert current.json()["data"] == {
        "org_id": decision["org_id"],
        "role": "admin",
        "user_id": decision["admin_user_id"],
    }
    balance = await api.get("/billing", headers=applicant_headers)
    assert balance.status_code == 200, balance.text
    assert balance.json()["data"]["balance"] == 0 and balance.json()["items"] == []
    tasks = await api.get("/tasks", headers=applicant_headers)
    assert tasks.status_code == 200 and tasks.json()["items"] == []
    async with app.state.db.transaction() as transaction:
        assert await transaction.scalar(text("SELECT count(*) FROM orgs")) == 0
        assert await transaction.scalar(
            text("SELECT current_setting('app.current_org', true)")
        ) in (None, "")
    audit = signup_audit(admin_engine)
    assert [entry["action"] for entry in audit] == [
        "org_application.submit",
        "platform.org_application.approve",
    ]
    assert set(audit[0]["details"]) == {"application_id", "source_digest"}
    assert audit[1]["actor_email"] == OPERATOR
    assert audit[1]["details"]["application_id"] == str(row["id"])
    assert audit[1]["details"]["org_id"] == decision["org_id"]
    assert audit[1]["details"]["attached_existing_user"] is False
    serialized = json.dumps(audit, default=str) + listed.text + approved.text + caplog.text
    assert APPLICANT_PASSWORD not in serialized and row["password_hash"] not in serialized
    assert SOURCE not in json.dumps(audit, default=str)
    assert (await api.get("/platform/orgs", headers=headers)).json()["data"][
        "pending_applications"
    ] == 0


async def test_uniform_receipts_always_hash_and_duplicate_preserves_first_password(
    signup,
    admin_engine,
    monkeypatch,
    caplog,
):
    _, api, _, _ = signup
    original = password_attempts.hash_password
    hashed = []

    def observed(password):
        hashed.append(password)
        return original(password)

    monkeypatch.setattr(password_attempts, "hash_password", observed)
    first = await api.post(PATH, json=body())
    initial = applications(admin_engine)
    initial_audit = signup_audit(admin_engine)
    second_password = "synthetic-duplicate-password"
    duplicate = await api.post(
        PATH, json=body(" APPLICANT@EXAMPLE.TEST ", password=second_password)
    )
    assert applications(admin_engine) == initial and signup_audit(admin_engine) == initial_audit
    registered = await api.post(PATH, json=body("a@example.test"))
    assert first.status_code == duplicate.status_code == registered.status_code == 200
    assert first.json() == duplicate.json() == registered.json()
    assert hashed == [APPLICANT_PASSWORD, second_password, APPLICANT_PASSWORD]
    assert len(applications(admin_engine)) == 2
    assert verify_password(APPLICANT_PASSWORD, initial[0]["password_hash"])
    assert not verify_password(second_password, initial[0]["password_hash"])
    assert second_password not in caplog.text + duplicate.text


@pytest.mark.parametrize("created_between", [False, True])
async def test_existing_identity_requires_attach_and_never_changes_password(
    signup,
    admin_engine,
    tenants,
    created_between,
):
    _, api, headers, _ = signup
    email = "late@example.test" if created_between else "a@example.test"
    row = await submitted(signup, admin_engine, email=email)
    if created_between:
        user_id = uuid4()
        with Session(admin_engine) as session, session.begin():
            session.add(User(id=user_id, email=email, password_hash=cheap_password_hash(PASSWORD)))
    else:
        user_id = tenants["users"][0]
    before = applications(admin_engine)
    failure(
        await api.post(f"{PLATFORM}/{row['id']}/approve", headers=headers, json={}),
        409,
        "existing_user_requires_attach",
    )
    assert applications(admin_engine) == before
    assert [a["action"] for a in signup_audit(admin_engine)] == ["org_application.submit"]
    listed = await api.get(PLATFORM, headers=headers)
    assert listed.json()["items"][0]["existing_user"] is True
    response = await api.post(
        f"{PLATFORM}/{row['id']}/approve", headers=headers, json={"attach_existing_user": True}
    )
    assert response.status_code == 200, response.text
    decision = response.json()["data"]
    assert decision["admin_user_id"] == str(user_id)
    assert decision["user_created"] is False and decision["attached_existing_user"] is True
    assert applications(admin_engine)[0]["password_hash"] is None
    login_body = {"email": email, "password": PASSWORD, "org_id": decision["org_id"]}
    assert (await api.post("/auth/login", json=login_body)).status_code == 200
    failure(
        await api.post("/auth/login", json={**login_body, "password": APPLICANT_PASSWORD}),
        401,
        "invalid_login",
    )
    if not created_between:
        assert (
            await api.post("/auth/login", json={**login_body, "org_id": str(tenants["orgs"][0])})
        ).status_code == 200


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"password": "short"}, "weak_password"),
        ({"password": "x" * 1025}, "invalid_input"),
        ({"password": ""}, "invalid_input"),
        ({"org_name": " \n "}, "invalid_input"),
        ({"org_name": "org\x00name"}, "invalid_input"),
        ({"contact_name": "contact\rname"}, "invalid_input"),
        ({"email": "not-an-email"}, "invalid_input"),
        ({"email": "İ" * 126 + "@x.t"}, "invalid_input"),
        ({"phone": "call-me"}, "invalid_input"),
        ({"note": "x" * 501}, "invalid_input"),
        ({"note": "bad\x7fnote"}, "invalid_input"),
        ({"synthetic-applicant-password": "secret-field"}, "invalid_input"),
    ],
)
async def test_invalid_submit_has_no_persistence_or_secret_reflection(
    signup, admin_engine, caplog, changes, code
):
    _, api, _, _ = signup
    response = await api.post(PATH, json=body(**changes))
    failure(response, 400, code)
    assert applications(admin_engine) == [] and signup_audit(admin_engine) == []
    assert APPLICANT_PASSWORD not in response.text + caplog.text


async def test_source_limit_ignores_proxy_headers_survives_new_app_and_expires(
    signup,
    admin_engine,
):
    app, api, _, settings = signup
    for index in range(5):
        response = await api.post(
            PATH,
            json=body(f"source-{index}@example.test"),
            headers={
                "X-Forwarded-For": f"198.51.100.{index}",
                "Forwarded": f"for=198.51.100.{index}",
            },
        )
        assert response.status_code == 200, response.text
    before, audit = applications(admin_engine), signup_audit(admin_engine)
    failure(
        await api.post(
            PATH, json=body("sixth@example.test"), headers={"X-Forwarded-For": "203.0.113.99"}
        ),
        429,
        "too_many_attempts",
    )
    assert applications(admin_engine) == before and signup_audit(admin_engine) == audit
    restarted = create_app(settings, queue=FakeQueue())
    async with (
        restarted.router.lifespan_context(restarted),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted, client=(SOURCE, 5555)),
            base_url="http://test",
        ) as same_source,
    ):
        failure(
            await same_source.post(PATH, json=body("restart@example.test")),
            429,
            "too_many_attempts",
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("192.0.2.20", 5555)),
        base_url="http://test",
    ) as different_source:
        assert (
            await different_source.post(PATH, json=body("other-source@example.test"))
        ).status_code == 200
    with admin_engine.begin() as connection:
        connection.execute(
            text("UPDATE org_applications SET created_at = now() - interval '25 hours'")
        )
    assert (await api.post(PATH, json=body("after-window@example.test"))).status_code == 200
    stored = applications(admin_engine)
    assert len({row["source_digest"] for row in stored}) == 2
    assert stored[-1]["source_digest"] == source_digest(settings, SOURCE)
    assert SOURCE not in json.dumps(stored, default=str) and "192.0.2.20" not in json.dumps(
        stored, default=str
    )


def seed_pending(admin_engine, count, *, overdue=False):
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO org_applications (id, status, org_name, contact_name, email, password_hash, source_digest, created_at, expires_at) "
                "SELECT gen_random_uuid(), 'pending', 'Synthetic cap org', 'Synthetic contact', 'cap-' || i || '@example.test', :hash, "
                "lpad(to_hex(i), 64, '0'), now() - interval '31 days', "
                "CASE WHEN :overdue THEN now() - interval '1 day' ELSE now() + interval '30 days' END "
                "FROM generate_series(1, :count) i"
            ),
            {"hash": cheap_password_hash(APPLICANT_PASSWORD), "count": count, "overdue": overdue},
        )


async def test_pending_cap_and_overdue_read_do_not_count_expired_rows(signup, admin_engine):
    _, api, headers, _ = signup
    seed_pending(admin_engine, 200)
    before, audit = applications(admin_engine), signup_audit(admin_engine)
    failure(await api.post(PATH, json=body()), 503, "signup_busy")
    assert applications(admin_engine) == before and signup_audit(admin_engine) == audit
    with admin_engine.begin() as connection:
        connection.execute(
            text("UPDATE org_applications SET expires_at = now() - interval '1 day'")
        )
    pending = await api.get(PLATFORM, headers=headers)
    assert pending.status_code == 200 and pending.json()["items"] == []
    expired = await api.get(PLATFORM, headers=headers, params={"status": "expired", "limit": 200})
    assert expired.status_code == 200 and len(expired.json()["items"]) == 200
    assert all(row["status"] == "expired" for row in expired.json()["items"])
    assert (await api.get("/platform/orgs", headers=headers)).json()["data"][
        "pending_applications"
    ] == 0
    assert (await api.post(PATH, json=body())).status_code == 200


async def test_concurrent_submissions_keep_one_application_and_one_audit(signup, admin_engine):
    _, api, _, _ = signup
    passwords = ["synthetic-racing-password-1", "synthetic-racing-password-2"]
    responses = await asyncio.gather(
        *[api.post(PATH, json=body(password=password)) for password in passwords]
    )
    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json() == responses[1].json()
    [row] = applications(admin_engine)
    assert sum(verify_password(password, row["password_hash"]) for password in passwords) == 1
    assert len(signup_audit(admin_engine)) == 1


async def test_concurrent_approvals_create_exactly_one_org(signup, admin_engine):
    _, api, headers, _ = signup
    row = await submitted(signup, admin_engine)
    responses = await asyncio.gather(
        *[api.post(f"{PLATFORM}/{row['id']}/approve", headers=headers, json={}) for _ in range(2)]
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    failure(
        next(response for response in responses if response.status_code == 409),
        409,
        "application_not_pending",
    )
    with admin_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM orgs")) == 3
        assert connection.scalar(text("SELECT count(*) FROM memberships")) == 3
        assert (
            connection.scalar(
                text("SELECT count(*) FROM users WHERE email='applicant@example.test'")
            )
            == 1
        )
    assert applications(admin_engine)[0]["password_hash"] is None
    assert (
        sum(
            row["action"] == "platform.org_application.approve"
            for row in signup_audit(admin_engine)
        )
        == 1
    )


async def test_reject_and_daily_expiry_clear_hash_and_block_decisions(signup, admin_engine):
    app, api, headers, settings = signup
    row = await submitted(signup, admin_engine)
    failure(
        await api.post(f"{PLATFORM}/{row['id']}/reject", headers=headers, json={"reason": "  "}),
        400,
        "invalid_input",
    )
    assert applications(admin_engine)[0]["password_hash"] is not None
    rejected = await api.post(
        f"{PLATFORM}/{row['id']}/reject",
        headers=headers,
        json={"reason": "Synthetic operator decision"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["data"]["status"] == "rejected"
    saved = applications(admin_engine)[0]
    assert saved["password_hash"] is None and saved["org_id"] is None
    assert saved["decision_reason"] == "Synthetic operator decision"
    assert signup_audit(admin_engine)[-1]["action"] == "platform.org_application.reject"
    failure(
        await api.post(f"{PLATFORM}/{row['id']}/approve", headers=headers, json={}),
        409,
        "application_not_pending",
    )
    overdue = await submitted(signup, admin_engine, email="overdue@example.test")
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE org_applications SET created_at = now() - interval '31 days', expires_at = now() - interval '1 second' WHERE id = :id"
            ),
            {"id": overdue["id"]},
        )
    failure(
        await api.post(f"{PLATFORM}/{overdue['id']}/approve", headers=headers, json={}),
        409,
        "application_not_pending",
    )
    service = OrgSignupService(settings, app.state.db, app.state.password_attempts)
    worker = Queue(settings)
    registrations = [
        entry
        for entry in worker.app.periodic_registry.periodic_tasks.values()
        if entry.task is worker.org_application_expire_task
    ]
    assert len(registrations) == 1 and registrations[0].cron == "0 3 * * *"
    await worker.org_application_expire_task(timestamp=int(datetime.now(UTC).timestamp()))
    assert await service.expire_overdue() == 0
    saved = next(row for row in applications(admin_engine) if row["id"] == overdue["id"])
    assert saved["status"] == "expired" and saved["password_hash"] is None
    failure(
        await api.post(
            f"{PLATFORM}/{overdue['id']}/reject", headers=headers, json={"reason": "Too late"}
        ),
        409,
        "application_not_pending",
    )


async def test_disabled_switch_returns_404_but_keeps_operator_decisions(signup, admin_engine):
    _, api, headers, settings = signup
    row = await submitted(signup, admin_engine)
    disabled = create_app(
        settings.model_copy(update={"org_signup_enabled": False}), queue=FakeQueue()
    )
    async with (
        disabled.router.lifespan_context(disabled),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=disabled),
            base_url="http://test",
        ) as client,
    ):
        assert (await client.get("/health")).json()["data"]["org_signup_enabled"] is False
        before, audit = applications(admin_engine), signup_audit(admin_engine)
        for submitted_body in (body("disabled@example.test"), {"password": APPLICANT_PASSWORD}):
            failure(await client.post(PATH, json=submitted_body), 404, "signup_disabled")
        assert applications(admin_engine) == before and signup_audit(admin_engine) == audit
        assert (await client.get(PLATFORM, headers=headers)).status_code == 200
        approved = await client.post(f"{PLATFORM}/{row['id']}/approve", headers=headers, json={})
        assert approved.status_code == 200, approved.text
    assert (await api.get("/health")).json()["data"]["org_signup_enabled"] is True


async def test_operator_auth_required_and_missing_application_is_uniform(signup, tenants):
    _, api, operator_headers, _ = signup
    org_session = await api.post(
        "/auth/login",
        json={"email": "a@example.test", "password": PASSWORD, "org_id": str(tenants["orgs"][0])},
    )
    assert org_session.status_code == 200
    org_headers = {"Authorization": "Bearer " + org_session.json()["data"]["session"]}
    identifier = uuid4()
    for headers in ({}, org_headers):
        failure(await api.get(PLATFORM, headers=headers), 401, "invalid_session")
        failure(
            await api.post(f"{PLATFORM}/{identifier}/approve", headers=headers, json={}),
            401,
            "invalid_session",
        )
        failure(
            await api.post(
                f"{PLATFORM}/{identifier}/reject", headers=headers, json={"reason": "Synthetic"}
            ),
            401,
            "invalid_session",
        )
    failure(
        await api.post(f"{PLATFORM}/{identifier}/approve", headers=operator_headers, json={}),
        404,
        "not_found",
    )
    failure(
        await api.post(
            f"{PLATFORM}/{identifier}/reject",
            headers=operator_headers,
            json={"reason": "Synthetic"},
        ),
        404,
        "not_found",
    )
    for query in (
        {"status": "invalid"},
        {"limit": 201},
        {"before": "not-a-date"},
        {"before": "2026-10-01T00:00:00"},
    ):
        failure(
            await api.get(PLATFORM, headers=operator_headers, params=query), 400, "invalid_input"
        )


async def test_bid_app_has_no_direct_table_access_or_owner_membership(signup, admin_engine):
    app, _, _, _ = signup
    for statement in (
        "SELECT * FROM org_applications",
        "UPDATE org_applications SET status = 'expired'",
        "DELETE FROM org_applications",
    ):
        with pytest.raises(DBAPIError):
            async with app.state.db.transaction() as session:
                await session.execute(text(statement))
    with admin_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT pg_has_role('bid_app', 'bid_platform_fn', 'MEMBER')"))
            is False
        )
        assert (
            connection.scalar(
                text(
                    "SELECT has_table_privilege('bid_app', 'org_applications', 'SELECT,INSERT,UPDATE,DELETE')"
                )
            )
            is False
        )
        functions = connection.execute(
            text(
                "SELECT p.proname, pg_get_userbyid(p.proowner), p.prosecdef, p.proconfig, "
                "has_function_privilege('bid_app', p.oid, 'EXECUTE'), has_function_privilege('public', p.oid, 'EXECUTE') "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname LIKE 'org_application_%'"
            )
        ).all()
        assert {row.proname for row in functions} == {
            "org_application_submit",
            "org_application_list",
            "org_application_approve",
            "org_application_reject",
            "org_application_expire",
        }
        for row in functions:
            assert row[1] == "bid_platform_fn" and row[2] is True
            assert any(config.startswith("search_path=") for config in row[3])
            assert row[4] is True and row[5] is False


@asynccontextmanager
async def blocked_login_workers(monkeypatch):
    original = password_attempts.verify_password
    release = threading.Event()
    entered = 0
    lock = threading.Lock()

    def blocked(password, encoded):
        nonlocal entered
        with lock:
            entered += 1
        if not release.wait(10):
            raise TimeoutError("Test did not release password workers")
        return original(password, encoded)

    monkeypatch.setattr(password_attempts, "verify_password", blocked)
    try:
        yield lambda: entered, release
    finally:
        release.set()


async def eventually(predicate):
    async with asyncio.timeout(3):
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0.01)


async def test_signup_uses_shared_password_admission_and_busy_stores_nothing(
    signup,
    admin_engine,
    monkeypatch,
):
    app, api, _, _ = signup
    attempts = app.state.password_attempts
    async with (
        blocked_login_workers(monkeypatch) as (entered, release),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=None),
            base_url="http://test",
        ) as anonymous,
    ):
        running = [
            asyncio.create_task(
                anonymous.post(
                    "/auth/orgs",
                    json={
                        "email": f"busy-{index}@example.test",
                        "password": "synthetic-wrong-password",
                    },
                )
            )
            for index in range(password_attempts.PASSWORD_WORKERS)
        ]
        queued = []
        try:
            await eventually(lambda: entered() == password_attempts.PASSWORD_WORKERS)
            queued = [
                asyncio.create_task(
                    anonymous.post(
                        "/auth/orgs",
                        json={
                            "email": f"queued-{index}@example.test",
                            "password": "synthetic-wrong-password",
                        },
                    )
                )
                for index in range(password_attempts.PASSWORD_QUEUE)
            ]
            await eventually(
                lambda: (
                    attempts.pending
                    == password_attempts.PASSWORD_WORKERS + password_attempts.PASSWORD_QUEUE
                )
            )
            failure(await api.post(PATH, json=body()), 503, "auth_busy")
            assert applications(admin_engine) == [] and signup_audit(admin_engine) == []
        finally:
            release.set()
            await asyncio.gather(*running, *queued, return_exceptions=True)
    assert (await api.post(PATH, json=body())).status_code == 200


async def test_org_creation_failure_rolls_back_user_org_decision_and_audit(
    signup, admin_engine, caplog
):
    app, _, headers, _ = signup
    row = await submitted(signup, admin_engine)
    before, audit = applications(admin_engine), signup_audit(admin_engine)
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE FUNCTION public.signup_test_fail_org() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "DECLARE v_hash text; BEGIN SELECT password_hash INTO v_hash FROM public.org_applications WHERE email = 'applicant@example.test'; "
                "RAISE EXCEPTION 'Synthetic org failure: synthetic-applicant-password %', v_hash; END $$"
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER signup_test_fail_org BEFORE INSERT ON public.orgs "
                "FOR EACH ROW WHEN (NEW.name = 'Synthetic applicant org') EXECUTE FUNCTION public.signup_test_fail_org()"
            )
        )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as api:
            response = await api.post(f"{PLATFORM}/{row['id']}/approve", headers=headers, json={})
            failure(response, 500, "internal_error")
            assert APPLICANT_PASSWORD not in response.text + caplog.text
            assert row["password_hash"] not in response.text + caplog.text
            assert "Organization application database operation" in caplog.text
        assert applications(admin_engine) == before and signup_audit(admin_engine) == audit
        with admin_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM orgs")) == 2
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM users WHERE email='applicant@example.test'")
                )
                == 0
            )
    finally:
        with admin_engine.begin() as connection:
            connection.execute(text("DROP TRIGGER signup_test_fail_org ON public.orgs"))
            connection.execute(text("DROP FUNCTION public.signup_test_fail_org()"))
    assert (
        await signup[1].post(f"{PLATFORM}/{row['id']}/approve", headers=headers, json={})
    ).status_code == 200


async def test_concurrent_source_limit_cannot_overshoot(signup, admin_engine):
    _, api, _, _ = signup
    responses = await asyncio.gather(
        *[api.post(PATH, json=body(f"racing-source-{index}@example.test")) for index in range(6)]
    )
    assert sorted(response.status_code for response in responses) == [200, 200, 200, 200, 200, 429]
    failure(
        next(response for response in responses if response.status_code == 429),
        429,
        "too_many_attempts",
    )
    assert len(applications(admin_engine)) == len(signup_audit(admin_engine)) == 5


async def test_concurrent_pending_cap_cannot_overshoot(signup, admin_engine):
    _, api, _, _ = signup
    seed_pending(admin_engine, 199)
    responses = await asyncio.gather(
        *[api.post(PATH, json=body(f"racing-cap-{index}@example.test")) for index in range(2)]
    )
    assert sorted(response.status_code for response in responses) == [200, 503]
    failure(
        next(response for response in responses if response.status_code == 503), 503, "signup_busy"
    )
    assert len(applications(admin_engine)) == 200 and len(signup_audit(admin_engine)) == 1


async def test_list_keyset_pagination_and_terminal_filter(signup, admin_engine):
    _, api, headers, _ = signup
    for index in range(3):
        assert (await api.post(PATH, json=body(f"page-{index}@example.test"))).status_code == 200
    # Distinct timestamps exercise the contract's created_at-only cursor.
    with admin_engine.begin() as connection:
        for index, row in enumerate(applications(admin_engine)):
            connection.execute(
                text(
                    "UPDATE org_applications SET created_at = now() - make_interval(hours => :hours) WHERE id = :id"
                ),
                {"hours": index + 1, "id": row["id"]},
            )
    first = await api.get(PLATFORM, headers=headers, params={"limit": 2})
    assert first.status_code == 200 and len(first.json()["items"]) == 2
    second = await api.get(
        PLATFORM,
        headers=headers,
        params={"limit": 2, "before": first.json()["items"][-1]["created_at"]},
    )
    assert second.status_code == 200 and len(second.json()["items"]) == 1
    ids = [row["id"] for row in first.json()["items"] + second.json()["items"]]
    assert len(set(ids)) == 3
    rejected_id = ids[0]
    response = await api.post(
        f"{PLATFORM}/{rejected_id}/reject", headers=headers, json={"reason": "Synthetic review"}
    )
    assert response.status_code == 200
    rejected = await api.get(PLATFORM, headers=headers, params={"status": "rejected"})
    assert [row["id"] for row in rejected.json()["items"]] == [rejected_id]


async def test_database_checks_reject_invalid_pending_and_terminal_shapes(
    signup,
    admin_engine,
    tenants,
):
    _, api, headers, _ = signup
    row = await submitted(signup, admin_engine)
    before = applications(admin_engine)
    invalid_pending = (
        "status = 'expired'",
        "password_hash = NULL",
        "org_id = :org",
        "status = 'approved', password_hash = NULL, org_id = :org",
        "status = 'rejected', password_hash = NULL, decided_at = now(), decided_by = 'ops@example.test'",
        "status = 'approved', password_hash = NULL, org_id = :org, admin_user_id = :user, decided_at = now(), decided_by = 'ops@example.test', user_created = true, attached_existing_user = true",
    )
    for assignment in invalid_pending:
        with pytest.raises(DBAPIError) as error, admin_engine.begin() as connection:
            connection.execute(
                text(f"UPDATE org_applications SET {assignment} WHERE id = :id"),
                {
                    "id": row["id"],
                    "org": tenants["orgs"][0],
                    "user": tenants["users"][0],
                },
            )
        assert getattr(error.value.orig, "sqlstate", None) == "23514"
        assert applications(admin_engine) == before
    assert (
        await api.post(
            f"{PLATFORM}/{row['id']}/reject", headers=headers, json={"reason": "Synthetic review"}
        )
    ).status_code == 200
    terminal = applications(admin_engine)
    for assignment in ("password_hash = :hash", "org_id = :org", "decision_reason = NULL"):
        with pytest.raises(DBAPIError) as error, admin_engine.begin() as connection:
            connection.execute(
                text(f"UPDATE org_applications SET {assignment} WHERE id = :id"),
                {
                    "id": row["id"],
                    "hash": row["password_hash"],
                    "org": tenants["orgs"][0],
                },
            )
        assert getattr(error.value.orig, "sqlstate", None) == "23514"
        assert applications(admin_engine) == terminal


async def test_expired_same_email_can_reapply_and_old_hash_is_cleared(signup, admin_engine):
    _, api, _, _ = signup
    old = await submitted(signup, admin_engine)
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE org_applications SET created_at = now() - interval '31 days', expires_at = now() - interval '1 second' WHERE id = :id"
            ),
            {"id": old["id"]},
        )
    second_password = "synthetic-new-application-password"
    response = await api.post(PATH, json=body(password=second_password))
    assert response.status_code == 200, response.text
    rows = applications(admin_engine)
    assert len(rows) == 2
    assert rows[0]["id"] == old["id"] and rows[0]["status"] == "expired"
    assert rows[0]["password_hash"] is None
    assert rows[1]["status"] == "pending" and rows[1]["email"] == old["email"]
    assert verify_password(second_password, rows[1]["password_hash"])
    assert not verify_password(APPLICANT_PASSWORD, rows[1]["password_hash"])
    assert len(signup_audit(admin_engine)) == 2
