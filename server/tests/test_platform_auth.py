"""Platform operator sign-in, org disabling and one-time password setup through the API."""

import json
import time
from uuid import uuid4

import httpx
import pytest
from app.api.main import create_app
from app.core.config import Settings
from app.core.totp import STEP_SECONDS, code_at
from app.models.entities import PlatformAuditLog, User
from app.services import platform
from conftest import OPERATOR, OPERATOR_PASSWORD, PASSWORD, FakeQueue
from conftest import OPERATOR_SECRET as SECRET
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session


def now_counter() -> int:
    return int(time.time() // STEP_SECONDS)


def platform_settings(tmp_path, admins=OPERATOR):
    return Settings(
        data_dir=tmp_path,
        platform_admin_emails=admins,
        platform_totp_secrets=",".join(f"{email}:{SECRET}" for email in admins.split(",")),
    )


@pytest.fixture
async def client(operator, tmp_path):
    app = create_app(platform_settings(tmp_path), queue=FakeQueue())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api,
    ):
        api.app = app  # pyright: ignore[reportAttributeAccessIssue]
        yield api


async def sign_in(api, password=OPERATOR_PASSWORD, counter=None, email=OPERATOR):
    code = code_at(SECRET, now_counter() if counter is None else counter)
    return await api.post(
        "/platform/auth/login", json={"email": email, "password": password, "totp": code}
    )


async def audit_rows(api):
    async with api.app.state.db.transaction() as session:
        return (
            await session.scalars(select(PlatformAuditLog).order_by(PlatformAuditLog.created_at))
        ).all()


async def test_operator_signs_in_with_password_and_totp(client):
    response = await sign_in(client)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["email"] == OPERATOR and data["expires_in"] == 1800
    [row] = await audit_rows(client)
    assert (row.action, row.outcome) == ("platform.login", "success")
    assert row.details == {"totp_counter": now_counter()}


@pytest.mark.parametrize(
    "case",
    ["wrong_password", "wrong_code", "replayed_code", "not_an_operator", "org_member_password"],
)
async def test_failed_sign_ins_are_uniform_and_audited(case, client):
    if case == "replayed_code":
        assert (await sign_in(client)).status_code == 200
        response = await sign_in(client)
    elif case == "wrong_password":
        response = await sign_in(client, password="wrong-password")
    elif case == "wrong_code":
        response = await sign_in(client, counter=now_counter() + 5)
    elif case == "not_an_operator":
        response = await sign_in(client, email="nobody@example.test")
    else:
        # An org admin knows their own password but is not a platform operator.
        response = await sign_in(client, email="a@example.test", password=PASSWORD)
    assert response.status_code == 401
    assert response.json()["data"]["error"]["code"] == "invalid_login"
    failure = (await audit_rows(client))[-1]
    assert failure.outcome == "denied"
    serialized = json.dumps(failure.details)
    assert OPERATOR_PASSWORD not in serialized and PASSWORD not in serialized


async def test_next_time_step_is_accepted_after_a_success(client):
    assert (await sign_in(client)).status_code == 200
    assert (await sign_in(client, counter=now_counter() + 1)).status_code == 200


async def test_repeated_failures_lock_sign_in(client):
    for _ in range(platform.MAX_FAILURES):
        assert (await sign_in(client, password="wrong-password")).status_code == 401
    locked = await sign_in(client)
    assert locked.status_code == 429
    assert locked.json()["data"]["error"]["exit_code"] == 3


async def test_platform_session_is_not_an_org_session_and_follows_config(client, tenants, tmp_path):
    session = (await sign_in(client)).json()["data"]["session"]
    org_route = await client.get(
        "/tasks",
        headers={"Authorization": f"Bearer {session}", "X-Org-Id": str(tenants["orgs"][0])},
    )
    assert org_route.status_code == 401
    crypto = client.app.state.crypto
    assert platform.identify(platform_settings(tmp_path), crypto, session).email == OPERATOR
    with pytest.raises(Exception, match="Invalid credentials"):
        platform.identify(platform_settings(tmp_path, admins="other@example.test"), crypto, session)


def test_operator_without_totp_secret_refuses_to_start(tmp_path):
    with pytest.raises(ValidationError):
        Settings(data_dir=tmp_path, platform_admin_emails="ops@example.test")
    with pytest.raises(ValidationError):
        Settings(
            data_dir=tmp_path,
            platform_admin_emails=OPERATOR,
            platform_totp_secrets=f"{OPERATOR}:not base32!",
        )


async def test_disabled_org_rejects_login_sessions_and_tokens(client, tenants):
    org = str(tenants["orgs"][0])
    body = {"email": "a@example.test", "password": PASSWORD, "org_id": org}
    session = (await client.post("/auth/login", json=body)).json()["data"]["session"]
    header = {"Authorization": f"Bearer {session}", "X-Org-Id": org}
    token = (
        await client.post(
            "/tokens",
            headers=header,
            json={
                "name": "Synthetic",
                "scopes": ["task:read"],
                "expires_at": "2030-01-01T00:00:00+00:00",
            },
        )
    ).json()["data"]["token"]
    token_header = {"Authorization": f"Bearer {token}", "X-Org-Id": org}

    async def set_active(value):
        async with client.app.state.db.transaction() as db_session:
            await db_session.execute(
                text("SELECT platform_set_org_active(:org, :active)"), {"org": org, "active": value}
            )

    await set_active(False)
    for response in (
        await client.post("/auth/login", json=body),
        await client.get("/tasks", headers=header),
        await client.get("/tasks", headers=token_header),
    ):
        assert response.status_code == 403
        assert response.json()["data"]["error"]["code"] == "org_inactive"
    other = str(tenants["orgs"][1])
    other_body = {"email": "b@example.test", "password": PASSWORD, "org_id": other}
    assert (await client.post("/auth/login", json=other_body)).status_code == 200
    await set_active(True)
    assert (await client.get("/tasks", headers=header)).status_code == 200
    assert (await client.get("/tasks", headers=token_header)).status_code == 200


async def test_setup_link_sets_password_once(client, tenants, admin_engine):
    user_id = uuid4()
    with Session(admin_engine) as session, session.begin():
        session.add(
            User(id=user_id, email="new@example.test", password_hash=platform.UNUSABLE_PASSWORD)
        )
    token = platform.setup_token(client.app.state.crypto, user_id, platform.UNUSABLE_PASSWORD)
    weak = await client.post("/auth/setup-password", json={"token": token, "password": "short"})
    assert weak.status_code == 400 and weak.json()["data"]["error"]["code"] == "weak_password"
    new_password = "synthetic-new-password"
    first = await client.post(
        "/auth/setup-password", json={"token": token, "password": new_password}
    )
    assert first.status_code == 200, first.text
    again = await client.post(
        "/auth/setup-password", json={"token": token, "password": "another-password-1"}
    )
    assert (
        again.status_code == 400 and again.json()["data"]["error"]["code"] == "invalid_setup_link"
    )
    garbage = await client.post(
        "/auth/setup-password", json={"token": "garbage", "password": new_password}
    )
    assert garbage.status_code == 400
    with Session(admin_engine) as session:
        stored = session.get(User, user_id)
        assert stored is not None and stored.password_hash.startswith("pbkdf2$")
