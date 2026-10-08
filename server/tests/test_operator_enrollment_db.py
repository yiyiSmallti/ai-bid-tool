"""Platform operator enrollment through HTTP and restricted PostgreSQL functions.

Failure modes were enumerated in data/work/operator-enrollment/failure-modes.md
before implementation. These tests use real application connections and no vendor I/O.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from app.admin import rotate_provider_secrets
from app.api.main import create_app
from app.core import totp
from app.core.password_attempts import MAX_FAILURES, MAX_SOURCE_FAILURES
from app.core.security import verify_password
from app.models.entities import User
from conftest import OPERATOR, OPERATOR_PASSWORD, OPERATOR_SECRET, PASSWORD, FakeQueue
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_platform_auth import platform_settings

OPERATORS = "/platform/operators"
START = "/platform/enrollment/start"
COMPLETE = "/platform/enrollment/complete"
NEW = "new-operator@example.test"
OTHER = "other-operator@example.test"
EXISTING = "a@example.test"
NEW_PASSWORD = "synthetic-new-operator-password"
SOURCE = "192.0.2.51"
OWNER = "bid_operator_enrollment_fn"
ENVELOPE = {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
SERVER_DIR = Path(__file__).resolve().parents[1]


@asynccontextmanager
async def application_client(settings, *, source=SOURCE):
    app = create_app(settings, queue=FakeQueue())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=(source, 1234)),
            base_url="http://test",
        ) as api,
    ):
        yield app, api


@pytest.fixture
async def enrollment(operator, tmp_path, monkeypatch):
    settings = platform_settings(tmp_path).model_copy(
        update={"platform_admin_emails": ",".join((OPERATOR, NEW, OTHER, EXISTING))}
    )
    counter = int(time.time() // totp.STEP_SECONDS)
    # Pin only the TOTP module clock: TokenSigner expiry continues using real time.
    monkeypatch.setattr(totp, "time", SimpleNamespace(time=lambda: counter * totp.STEP_SECONDS))
    async with application_client(settings) as (app, api):
        response = await login(api, OPERATOR, OPERATOR_PASSWORD, OPERATOR_SECRET, counter)
        assert response.status_code == 200, response.text
        headers = {"Authorization": "Bearer " + response.json()["data"]["session"]}
        yield app, api, headers, settings, counter


def failure(response, status, code):
    assert response.status_code == status, response.text
    payload = response.json()
    assert set(payload) == ENVELOPE and payload["ok"] is False
    assert payload["data"]["error"]["code"] == code
    if status in (429, 503):
        assert payload["data"]["error"]["exit_code"] == 3
    return payload


def factor_rows(admin_engine):
    with admin_engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                text("SELECT * FROM platform_operator_factors ORDER BY email")
            ).mappings()
        ]


def audit_rows(admin_engine):
    with admin_engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                text("SELECT * FROM platform_audit_logs ORDER BY created_at, id")
            ).mappings()
        ]


def account(admin_engine, email):
    with admin_engine.connect() as connection:
        row = (
            connection.execute(
                text("SELECT id, email, password_hash, active FROM users WHERE email=:email"),
                {"email": email},
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None


async def issue(api, headers, email=NEW):
    response = await api.post(f"{OPERATORS}/{email}/enrollment-links", headers=headers)
    assert response.status_code == 200, response.text
    assert set(response.json()) == ENVELOPE
    assert response.json()["command"] == "platform operator enrollment-link"
    data = response.json()["data"]
    assert set(data) == {"email", "url", "expires_at"}
    assert data["email"] == email and data["url"].startswith("/app/platform/enroll#token=")
    expiry = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
    assert 1790 <= (expiry - datetime.now(UTC)).total_seconds() <= 1800
    return data["url"].split("#token=", 1)[1]


async def start(api, token):
    response = await api.post(START, json={"token": token})
    assert response.status_code == 200, response.text
    assert set(response.json()) == ENVELOPE
    assert response.json()["command"] == "platform enrollment start"
    data = response.json()["data"]
    assert set(data) == {"email", "password", "totp_secret", "otpauth_uri", "pending", "expires_at"}
    assert len(data["totp_secret"]) == 32
    assert data["otpauth_uri"] == totp.provisioning_uri(data["email"], data["totp_secret"])
    return data


def complete_body(token, pending, password, counter):
    return {
        "token": token,
        "pending": pending["pending"],
        "password": password,
        "code": totp.code_at(pending["totp_secret"], counter),
    }


async def login(api, email, password, secret, counter):
    return await api.post(
        "/platform/auth/login",
        json={"email": email, "password": password, "totp": totp.code_at(secret, counter)},
    )


def wrong_code(secret, counter):
    valid = {totp.code_at(secret, step) for step in (counter - 1, counter, counter + 1)}
    return next(str(value).zfill(6) for value in range(10) if str(value).zfill(6) not in valid)


async def enrolled(enrollment, email=NEW, password=NEW_PASSWORD, counter=None):
    _, api, headers, _, current = enrollment
    token = await issue(api, headers, email)
    pending = await start(api, token)
    response = await api.post(
        COMPLETE,
        json=complete_body(token, pending, password, current if counter is None else counter),
    )
    assert response.status_code == 200, response.text
    return token, pending, response


async def test_new_operator_start_is_read_only_and_enrollment_logs_in_later(
    enrollment, admin_engine, tenants, caplog
):
    app, api, headers, _, counter = enrollment
    token = await issue(api, headers)
    before = audit_rows(admin_engine)
    pending = await start(api, token)
    assert pending["password"] == "set" and pending["email"] == NEW
    assert account(admin_engine, NEW) is None and factor_rows(admin_engine) == []
    assert audit_rows(admin_engine) == before
    response = await api.post(COMPLETE, json=complete_body(token, pending, NEW_PASSWORD, counter))
    assert response.status_code == 200, response.text
    assert set(response.json()) == ENVELOPE
    assert response.json()["command"] == "platform enrollment complete"
    assert response.json()["data"] == {
        "email": NEW,
        "enrolled": True,
        "account_created": True,
        "password_set": True,
    }
    stored = account(admin_engine, NEW)
    assert stored is not None and verify_password(NEW_PASSWORD, stored["password_hash"])
    [factor] = factor_rows(admin_engine)
    assert factor["email"] == NEW and factor["generation"] == 1
    assert factor["enrolled_by"] == OPERATOR and factor["enrolled_at"] is not None
    assert factor["secret_ciphertext"] != pending["totp_secret"] and factor["key_version"] >= 1
    with admin_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM memberships WHERE user_id=:user"), {"user": stored["id"]}
            )
            == 0
        )
        assert connection.scalar(text("SELECT count(*) FROM orgs")) == len(tenants["orgs"])
    replay = await login(api, NEW, NEW_PASSWORD, pending["totp_secret"], counter)
    failure(replay, 401, "invalid_login")
    later = await login(api, NEW, NEW_PASSWORD, pending["totp_secret"], counter + 1)
    assert later.status_code == 200, later.text
    listed = await api.get(OPERATORS, headers=headers)
    assert listed.status_code == 200 and listed.json()["command"] == "platform operator list"
    assert set(listed.json()) == ENVELOPE
    statuses = {item["email"]: item for item in listed.json()["items"]}
    assert set(statuses) == {OPERATOR, NEW, OTHER, EXISTING}
    assert statuses[NEW] == {
        "email": NEW,
        "has_account": True,
        "factor_source": "database",
        "enrolled_at": factor["enrolled_at"].isoformat().replace("+00:00", "Z"),
        "enrolled_by": OPERATOR,
    }
    assert statuses[OTHER]["factor_source"] == "none" and statuses[OTHER]["has_account"] is False
    assert statuses[OPERATOR]["factor_source"] == "environment"
    success = [
        row for row in audit_rows(admin_engine) if row["action"] == "platform.operator.enroll"
    ]
    assert len(success) == 1 and success[0]["outcome"] == "success"
    assert success[0]["details"]["totp_counter"] == counter
    assert success[0]["actor_email"] == NEW
    exposed = (
        caplog.text
        + json.dumps(audit_rows(admin_engine), default=str)
        + response.text
        + listed.text
        + replay.text
    )
    for value in (
        token,
        pending["pending"],
        pending["totp_secret"],
        pending["otpauth_uri"],
        NEW_PASSWORD,
        factor["secret_ciphertext"],
    ):
        assert value not in exposed
    async with app.state.db.transaction() as session:
        assert await session.scalar(text("SELECT count(*) FROM orgs")) == 0


async def test_existing_password_confirmation_prevents_takeover_and_never_replaces_hash(
    enrollment, admin_engine
):
    _, api, headers, _, counter = enrollment
    initial = account(admin_engine, EXISTING)
    token = await issue(api, headers, EXISTING)
    pending = await start(api, token)
    assert pending["password"] == "confirm"
    failure(
        await api.post(COMPLETE, json=complete_body(token, pending, NEW_PASSWORD, counter)),
        401,
        "invalid_enrollment",
    )
    assert account(admin_engine, EXISTING) == initial and factor_rows(admin_engine) == []
    response = await api.post(COMPLETE, json=complete_body(token, pending, PASSWORD, counter))
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "email": EXISTING,
        "enrolled": True,
        "account_created": False,
        "password_set": False,
    }
    assert account(admin_engine, EXISTING) == initial
    assert (
        await login(api, EXISTING, PASSWORD, pending["totp_secret"], counter + 1)
    ).status_code == 200


@pytest.mark.parametrize(
    "stored_hash,password_step", [("!setup", "set"), ("malformed-hash", "confirm")]
)
async def test_setup_only_and_malformed_password_accounts_fail_closed(
    enrollment, admin_engine, stored_hash, password_step
):
    _, api, headers, _, counter = enrollment
    with Session(admin_engine) as session, session.begin():
        session.add(User(id=uuid4(), email=NEW, password_hash=stored_hash))
    token = await issue(api, headers)
    pending = await start(api, token)
    assert pending["password"] == password_step
    response = await api.post(COMPLETE, json=complete_body(token, pending, NEW_PASSWORD, counter))
    if stored_hash == "!setup":
        assert response.status_code == 200, response.text
        assert response.json()["data"]["password_set"] is True
        assert response.json()["data"]["account_created"] is False
        assert verify_password(NEW_PASSWORD, account(admin_engine, NEW)["password_hash"])
    else:
        failure(response, 401, "invalid_enrollment")
        assert account(admin_engine, NEW)["password_hash"] == stored_hash
        assert factor_rows(admin_engine) == []


async def test_reenrollment_keeps_password_revokes_old_factor_all_links_and_allows_self_rotation(
    enrollment, admin_engine
):
    _, api, headers, _, counter = enrollment
    old_token, old, _ = await enrolled(enrollment, counter=counter - 1)
    previous = account(admin_engine, NEW)
    new_token = await issue(api, headers)
    superseded = await issue(api, headers)
    pending = await start(api, new_token)
    assert pending["password"] == "confirm" and pending["totp_secret"] != old["totp_secret"]
    response = await api.post(
        COMPLETE, json=complete_body(new_token, pending, NEW_PASSWORD, counter)
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["password_set"] is False
    assert (
        account(admin_engine, NEW) == previous and factor_rows(admin_engine)[0]["generation"] == 2
    )
    for token in (old_token, new_token, superseded):
        failure(await api.post(START, json={"token": token}), 400, "invalid_enrollment_link")
    failure(
        await login(api, NEW, NEW_PASSWORD, old["totp_secret"], counter + 1), 401, "invalid_login"
    )
    logged_in = await login(api, NEW, NEW_PASSWORD, pending["totp_secret"], counter + 1)
    assert logged_in.status_code == 200, logged_in.text
    own_headers = {"Authorization": "Bearer " + logged_in.json()["data"]["session"]}
    await issue(api, own_headers, NEW)


async def test_invalid_link_causes_are_identical_and_pending_is_bound_to_link_and_email(
    enrollment, admin_engine
):
    app, api, headers, settings, counter = enrollment
    token = await issue(api, headers)
    pending = await start(api, token)
    other_token = await issue(api, headers, OTHER)
    other_pending = await start(api, other_token)
    same_email_token = await issue(api, headers)
    same_email_pending = await start(api, same_email_token)
    claims = app.state.crypto.open(token)
    expired = app.state.crypto.issue(claims, -1)
    wrong_kind = app.state.crypto.issue({**claims, "kind": "platform"}, 1800)
    bad = ("garbage", token[:-1] + ("A" if token[-1] != "A" else "B"), expired, wrong_kind)
    results = [
        failure(await api.post(START, json={"token": value}), 400, "invalid_enrollment_link")
        for value in bad
    ]
    assert all(result["data"] == results[0]["data"] for result in results)
    complete_results = []
    malformed = app.state.crypto.open(pending["pending"])
    malformed.pop("key_version")
    malformed_signed = app.state.crypto.issue(malformed, 1800)
    for blob in (
        "garbage",
        other_pending["pending"],
        same_email_pending["pending"],
        pending["pending"][:-2] + "AA",
        malformed_signed,
    ):
        response = await api.post(
            COMPLETE, json={**complete_body(token, pending, NEW_PASSWORD, counter), "pending": blob}
        )
        complete_results.append(failure(response, 400, "invalid_enrollment_link"))
    assert all(result["data"] == complete_results[0]["data"] for result in complete_results)
    removed = settings.model_copy(update={"platform_admin_emails": OPERATOR})
    async with application_client(removed) as (_, client):
        response = await client.post(START, json={"token": token})
        assert failure(response, 400, "invalid_enrollment_link")["data"] == results[0]["data"]
        response = await client.post(
            COMPLETE, json=complete_body(token, pending, NEW_PASSWORD, counter)
        )
        assert (
            failure(response, 400, "invalid_enrollment_link")["data"] == complete_results[0]["data"]
        )
    # A password changed outside enrollment supersedes both start and completion.
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users(id,email,password_hash,active) VALUES(:id,:email,'!setup',true)"
            ),
            {"id": uuid4(), "email": NEW},
        )
    assert (
        failure(await api.post(START, json={"token": token}), 400, "invalid_enrollment_link")[
            "data"
        ]
        == results[0]["data"]
    )
    assert (
        failure(
            await api.post(COMPLETE, json=complete_body(token, pending, NEW_PASSWORD, counter)),
            400,
            "invalid_enrollment_link",
        )["data"]
        == complete_results[0]["data"]
    )
    assert factor_rows(admin_engine) == []


async def test_environment_precedence_blocks_enrollment_and_replaces_database_login(enrollment):
    _, api, headers, settings, counter = enrollment
    _, pending, _ = await enrolled(enrollment, counter=counter - 1)
    deployment = settings.model_copy(
        update={
            "platform_totp_secrets": SecretStr(
                f"{OPERATOR}:{OPERATOR_SECRET},{NEW}:{OPERATOR_SECRET}"
            )
        }
    )
    async with application_client(deployment) as (_, client):
        failure(
            await client.post(f"{OPERATORS}/{NEW}/enrollment-links", headers=headers),
            409,
            "factor_managed_by_deployment",
        )
        failure(
            await login(client, NEW, NEW_PASSWORD, pending["totp_secret"], counter + 1),
            401,
            "invalid_login",
        )
        assert (
            await login(client, NEW, NEW_PASSWORD, OPERATOR_SECRET, counter + 1)
        ).status_code == 200
        statuses = (await client.get(OPERATORS, headers=headers)).json()["items"]
        assert (
            next(row for row in statuses if row["email"] == NEW)["factor_source"] == "environment"
        )
    # A previously issued link also becomes unavailable after environment provisioning.
    token = await issue(api, headers, OTHER)
    blocked = settings.model_copy(
        update={
            "platform_totp_secrets": SecretStr(
                f"{OPERATOR}:{OPERATOR_SECRET},{OTHER}:{OPERATOR_SECRET}"
            )
        }
    )
    async with application_client(blocked) as (_, client):
        failure(
            await client.post(START, json={"token": token}), 409, "factor_managed_by_deployment"
        )


async def test_concurrent_completion_of_absent_identity_commits_once(enrollment, admin_engine):
    _, api, headers, _, counter = enrollment
    tokens = [await issue(api, headers), await issue(api, headers)]
    pending = [await start(api, token) for token in tokens]
    responses = await asyncio.gather(
        *[
            api.post(COMPLETE, json=complete_body(token, item, NEW_PASSWORD, counter))
            for token, item in zip(tokens, pending, strict=True)
        ]
    )
    assert sorted(response.status_code for response in responses) == [200, 400]
    failure(
        next(response for response in responses if response.status_code == 400),
        400,
        "invalid_enrollment_link",
    )
    [factor] = factor_rows(admin_engine)
    assert factor["generation"] == 1 and account(admin_engine, NEW) is not None
    assert (
        len(
            [
                row
                for row in audit_rows(admin_engine)
                if row["action"] == "platform.operator.enroll" and row["outcome"] == "success"
            ]
        )
        == 1
    )


@pytest.mark.parametrize("email,password", [(NEW, NEW_PASSWORD), (EXISTING, PASSWORD)])
async def test_wrong_codes_share_account_window_with_login(
    enrollment, admin_engine, email, password
):
    _, api, headers, _, counter = enrollment
    token = await issue(api, headers, email)
    pending = await start(api, token)
    body = {
        **complete_body(token, pending, password, counter),
        "code": wrong_code(pending["totp_secret"], counter),
    }
    for _ in range(MAX_FAILURES):
        failure(await api.post(COMPLETE, json=body), 401, "invalid_enrollment")
    failure(
        await api.post(COMPLETE, json=complete_body(token, pending, password, counter)),
        429,
        "too_many_attempts",
    )
    failure(
        await login(api, email, password, pending["totp_secret"], counter + 1),
        429,
        "too_many_attempts",
    )
    assert factor_rows(admin_engine) == []
    assert all(
        row["outcome"] != "success"
        for row in audit_rows(admin_engine)
        if row["action"] == "platform.operator.enroll"
    )


async def test_wrong_existing_password_shares_failure_window_with_signin(enrollment):
    _, api, headers, _, counter = enrollment
    token = await issue(api, headers, EXISTING)
    pending = await start(api, token)
    for _ in range(MAX_FAILURES):
        failure(
            await api.post(
                COMPLETE, json=complete_body(token, pending, "synthetic-wrong-password", counter)
            ),
            401,
            "invalid_enrollment",
        )
    failure(
        await api.post(COMPLETE, json=complete_body(token, pending, PASSWORD, counter)),
        429,
        "too_many_attempts",
    )
    failure(
        await login(api, EXISTING, PASSWORD, pending["totp_secret"], counter + 1),
        429,
        "too_many_attempts",
    )


async def test_source_failure_window_cannot_be_bypassed_by_changing_email(enrollment, admin_engine):
    app, _, _, settings, counter = enrollment
    emails = [f"limited-{index}@example.test" for index in range(MAX_SOURCE_FAILURES + 1)]
    expanded = settings.model_copy(update={"platform_admin_emails": ",".join((OPERATOR, *emails))})
    headers = {
        "Authorization": "Bearer "
        + app.state.crypto.issue({"kind": "platform", "email": OPERATOR, "totp": True}, 1800)
    }
    async with application_client(expanded) as (_, api):
        for email in emails[:-1]:
            token = await issue(api, headers, email)
            pending = await start(api, token)
            failure(
                await api.post(
                    COMPLETE,
                    json={
                        **complete_body(token, pending, NEW_PASSWORD, counter),
                        "code": wrong_code(pending["totp_secret"], counter),
                    },
                ),
                401,
                "invalid_enrollment",
            )
        token = await issue(api, headers, emails[-1])
        pending = await start(api, token)
        failure(
            await api.post(COMPLETE, json=complete_body(token, pending, NEW_PASSWORD, counter)),
            429,
            "too_many_attempts",
        )
    assert factor_rows(admin_engine) == []


async def test_weak_new_password_and_inactive_identity_cannot_write_factor(
    enrollment, admin_engine
):
    _, api, headers, _, counter = enrollment
    token = await issue(api, headers)
    pending = await start(api, token)
    failure(
        await api.post(COMPLETE, json=complete_body(token, pending, "short", counter)),
        400,
        "weak_password",
    )
    assert factor_rows(admin_engine) == [] and account(admin_engine, NEW) is None
    inactive = await issue(api, headers, EXISTING)
    with admin_engine.begin() as connection:
        connection.execute(
            text("UPDATE users SET active=false WHERE email=:email"), {"email": EXISTING}
        )
    response = await api.post(START, json={"token": inactive})
    if response.status_code == 200:
        item = response.json()["data"]
        failure(
            await api.post(COMPLETE, json=complete_body(inactive, item, PASSWORD, counter)),
            400,
            "invalid_enrollment_link",
        )
    else:
        failure(response, 400, "invalid_enrollment_link")
    assert factor_rows(admin_engine) == []


async def test_operator_session_required_and_allowlist_never_changes(enrollment, tenants):
    _, api, headers, _, _ = enrollment
    org_response = await api.post(
        "/auth/login",
        json={"email": EXISTING, "password": PASSWORD, "org_id": str(tenants["orgs"][0])},
    )
    assert org_response.status_code == 200
    org_headers = {"Authorization": "Bearer " + org_response.json()["data"]["session"]}
    for rejected in ({}, org_headers):
        failure(await api.get(OPERATORS, headers=rejected), 401, "invalid_session")
        failure(
            await api.post(f"{OPERATORS}/{NEW}/enrollment-links", headers=rejected),
            401,
            "invalid_session",
        )
    failure(
        await api.post(f"{OPERATORS}/outside@example.test/enrollment-links", headers=headers),
        404,
        "not_found",
    )
    failure(
        await api.post(f"{OPERATORS}/{OPERATOR}/enrollment-links", headers=headers),
        409,
        "factor_managed_by_deployment",
    )


async def test_missing_secret_root_is_unavailable_without_writes(enrollment, admin_engine):
    _, api, headers, settings, counter = enrollment
    token = await issue(api, headers)
    pending = await start(api, token)
    before = audit_rows(admin_engine)
    missing = settings.model_copy(update={"secrets_key": None, "secrets_key_previous": []})
    async with application_client(missing) as (_, client):
        for response in (
            await client.post(f"{OPERATORS}/{OTHER}/enrollment-links", headers=headers),
            await client.post(START, json={"token": token}),
            await client.post(COMPLETE, json=complete_body(token, pending, NEW_PASSWORD, counter)),
        ):
            failure(response, 503, "enrollment_unavailable")
    assert factor_rows(admin_engine) == [] and account(admin_engine, NEW) is None
    assert audit_rows(admin_engine) == before


async def test_bid_app_denied_direct_access_and_function_owner_has_no_business_privileges(
    enrollment, admin_engine
):
    app, _, _, _, _ = enrollment
    for statement in (
        "SELECT * FROM public.platform_operator_factors",
        "INSERT INTO public.platform_operator_factors(email) VALUES ('forged@example.test')",
        "UPDATE public.platform_operator_factors SET generation=generation+1",
        "DELETE FROM public.platform_operator_factors",
        f"SET ROLE {OWNER}",
    ):
        with pytest.raises(DBAPIError):
            async with app.state.db.transaction() as session:
                await session.execute(text(statement))
    with admin_engine.connect() as connection:
        role = connection.execute(
            text(
                "SELECT rolcanlogin, rolsuper, rolbypassrls, rolinherit, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname=:role"
            ),
            {"role": OWNER},
        ).one()
        assert tuple(role) == (False, False, False, False, False, False)
        assert not connection.scalar(
            text("SELECT pg_has_role('bid_app', :role, 'MEMBER')"), {"role": OWNER}
        )
        assert not connection.scalar(
            text("SELECT has_schema_privilege(:role, 'public', 'CREATE')"), {"role": OWNER}
        )
        for role in ("bid_app", "bid_platform_fn", "bid_platform_credentials_fn"):
            assert not connection.scalar(
                text(
                    "SELECT has_table_privilege(:role, 'public.platform_operator_factors', 'SELECT,INSERT,UPDATE,DELETE')"
                ),
                {"role": role},
            )
        for table in ("orgs", "memberships", "tasks", "documents", "usage_records"):
            assert not connection.scalar(
                text("SELECT has_any_column_privilege(:role, :table, 'SELECT,INSERT,UPDATE')"),
                {"role": OWNER, "table": table},
            )
            assert not connection.scalar(
                text("SELECT has_table_privilege(:role, :table, 'DELETE')"),
                {"role": OWNER, "table": table},
            )
        functions = connection.execute(
            text(
                "SELECT p.proname, pg_get_userbyid(p.proowner), p.prosecdef, p.proconfig, has_function_privilege('bid_app', p.oid, 'EXECUTE'), has_function_privilege('public', p.oid, 'EXECUTE'), pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname LIKE 'platform_operator_%'"
            )
        ).all()
        assert {row[0] for row in functions} == {
            "platform_operator_factor",
            "platform_operator_status",
            "platform_operator_lock",
            "platform_operator_enroll",
        }
        for row in functions:
            assert row[1:3] == (OWNER, True) and row[4:6] == (True, False)
            assert row[3] == ["search_path=pg_catalog, public"]
            assert "EXECUTE " not in row[6].upper()


async def test_host_command_issues_link_that_completes_through_http(enrollment, admin_engine):
    _, api, _, settings, counter = enrollment
    env = {
        **os.environ,
        "BID_DATA_DIR": str(settings.data_dir),
        "BID_PLATFORM_ADMIN_EMAILS": settings.platform_admin_emails,
        "BID_PLATFORM_TOTP_SECRETS": settings.platform_totp_secrets.get_secret_value(),
        "BID_TOKEN_KEY": settings.token_key.get_secret_value(),
        "BID_SECRETS_KEY": settings.secrets_key.get_secret_value(),
    }
    process = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "app.admin", "platform-enroll", NEW],
        cwd=SERVER_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    payload = json.loads(process.stdout)
    assert set(payload) == ENVELOPE and payload["command"] == "platform operator enrollment-link"
    link = payload["data"]
    token = link["url"].split("#token=", 1)[1]
    pending = await start(api, token)
    assert (
        await api.post(COMPLETE, json=complete_body(token, pending, NEW_PASSWORD, counter))
    ).status_code == 200
    assert factor_rows(admin_engine)[0]["enrolled_by"] == "link"
    assert token not in json.dumps(audit_rows(admin_engine), default=str)


async def test_previous_secret_root_and_rotation_keep_factor_generation_and_links(
    enrollment, admin_engine, monkeypatch, capsys, caplog
):
    _, api, headers, settings, counter = enrollment
    _, pending, _ = await enrolled(enrollment, counter=counter - 1)
    [before] = factor_rows(admin_engine)
    link = await issue(api, headers)
    current = Fernet.generate_key().decode()
    old = settings.secrets_key.get_secret_value()
    monkeypatch.setenv("BID_SECRETS_KEY", current)
    monkeypatch.setenv("BID_SECRETS_KEY_PREVIOUS", old)
    monkeypatch.setenv("BID_PLATFORM_ADMIN_EMAILS", settings.platform_admin_emails)
    monkeypatch.setenv(
        "BID_PLATFORM_TOTP_SECRETS", settings.platform_totp_secrets.get_secret_value()
    )
    monkeypatch.setenv("BID_TOKEN_KEY", settings.token_key.get_secret_value())
    monkeypatch.setenv(
        "BID_MIGRATION_DATABASE_URL", admin_engine.url.render_as_string(hide_password=False)
    )
    updated = settings.model_copy(
        update={"secrets_key": SecretStr(current), "secrets_key_previous": [SecretStr(old)]}
    )
    async with application_client(updated) as (_, client):
        assert (
            await login(client, NEW, NEW_PASSWORD, pending["totp_secret"], counter)
        ).status_code == 200
    report = await asyncio.to_thread(rotate_provider_secrets)
    assert report["operator_factors_checked"] == report["operator_factors_rewritten"] == 1
    assert report["failed"] == 0 and report["exit_code"] == 0
    [after] = factor_rows(admin_engine)
    assert after["secret_ciphertext"] != before["secret_ciphertext"]
    assert {
        name: after[name] for name in after if name not in {"secret_ciphertext", "key_version"}
    } == {name: before[name] for name in before if name not in {"secret_ciphertext", "key_version"}}
    monkeypatch.delenv("BID_SECRETS_KEY_PREVIOUS")
    only_current = updated.model_copy(update={"secrets_key_previous": []})
    async with application_client(only_current) as (_, client):
        assert (await start(client, link))["password"] == "confirm"
        assert (
            await login(client, NEW, NEW_PASSWORD, pending["totp_secret"], counter + 1)
        ).status_code == 200
    # A repeated owner operation sees the existing current root and changes nothing.
    monkeypatch.setenv("BID_SECRETS_KEY_PREVIOUS", old)
    repeated = await asyncio.to_thread(rotate_provider_secrets)
    assert repeated["operator_factors_checked"] == 1 and repeated["operator_factors_rewritten"] == 0
    output = capsys.readouterr()
    exposed = (
        output.out + output.err + caplog.text + json.dumps(audit_rows(admin_engine), default=str)
    )
    for value in (
        old,
        current,
        pending["totp_secret"],
        pending["pending"],
        before["secret_ciphertext"],
        after["secret_ciphertext"],
        NEW_PASSWORD,
        link,
    ):
        assert value not in exposed


async def test_shared_password_admission_rejects_enrollment_without_writes(
    enrollment, admin_engine, monkeypatch
):
    from app.core.password_attempts import PASSWORD_QUEUE, PASSWORD_WORKERS
    from test_org_signup_db import blocked_login_workers, eventually

    app, api, headers, _, counter = enrollment
    token = await issue(api, headers)
    pending = await start(api, token)
    before = audit_rows(admin_engine)
    async with (
        blocked_login_workers(monkeypatch) as (entered, release),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=None), base_url="http://test"
        ) as anonymous,
    ):
        running = [
            asyncio.create_task(
                anonymous.post(
                    "/auth/orgs",
                    json={"email": f"busy-{index}@example.test", "password": "synthetic-wrong"},
                )
            )
            for index in range(PASSWORD_WORKERS)
        ]
        queued = []
        try:
            await eventually(lambda: entered() == PASSWORD_WORKERS)
            queued = [
                asyncio.create_task(
                    anonymous.post(
                        "/auth/orgs",
                        json={
                            "email": f"queue-{index}@example.test",
                            "password": "synthetic-wrong",
                        },
                    )
                )
                for index in range(PASSWORD_QUEUE)
            ]
            await eventually(
                lambda: app.state.password_attempts.pending == PASSWORD_WORKERS + PASSWORD_QUEUE
            )
            failure(
                await api.post(COMPLETE, json=complete_body(token, pending, NEW_PASSWORD, counter)),
                503,
                "auth_busy",
            )
            assert factor_rows(admin_engine) == [] and account(admin_engine, NEW) is None
            assert audit_rows(admin_engine) == before
        finally:
            release.set()
            await asyncio.gather(*running, *queued, return_exceptions=True)
    assert (
        await api.post(COMPLETE, json=complete_body(token, pending, NEW_PASSWORD, counter))
    ).status_code == 200


async def test_audit_failure_rolls_back_account_factor_and_hides_database_diagnostics(
    enrollment, admin_engine, caplog
):
    app, api, headers, _, counter = enrollment
    token = await issue(api, headers)
    pending = await start(api, token)
    before = audit_rows(admin_engine)
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE FUNCTION public.enrollment_test_fail_audit() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "DECLARE v_cipher text; BEGIN SELECT secret_ciphertext INTO v_cipher FROM public.platform_operator_factors WHERE email='new-operator@example.test'; "
                "RAISE EXCEPTION 'Synthetic enrollment failure: synthetic-new-operator-password %', v_cipher; END $$"
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER enrollment_test_fail_audit BEFORE INSERT ON public.platform_audit_logs "
                "FOR EACH ROW WHEN (NEW.action='platform.operator.enroll') EXECUTE FUNCTION public.enrollment_test_fail_audit()"
            )
        )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.post(
                COMPLETE, json=complete_body(token, pending, NEW_PASSWORD, counter)
            )
            failure(response, 500, "internal_error")
        assert factor_rows(admin_engine) == [] and account(admin_engine, NEW) is None
        assert audit_rows(admin_engine) == before
        for value in (token, pending["pending"], pending["totp_secret"], NEW_PASSWORD):
            assert value not in response.text + caplog.text
    finally:
        with admin_engine.begin() as connection:
            connection.execute(
                text("DROP TRIGGER enrollment_test_fail_audit ON public.platform_audit_logs")
            )
            connection.execute(text("DROP FUNCTION public.enrollment_test_fail_audit()"))
    assert (
        await api.post(COMPLETE, json=complete_body(token, pending, NEW_PASSWORD, counter))
    ).status_code == 200
