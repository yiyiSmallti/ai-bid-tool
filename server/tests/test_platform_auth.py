"""Platform operator sign-in and retained organization login access boundaries."""

import json
import time

import httpx
import pytest
from app.api.main import create_app
from app.core.config import Settings
from app.core.password_attempts import MAX_FAILURES
from app.core.totp import STEP_SECONDS, code_at
from app.models.entities import Org, PlatformAuditLog
from app.services import platform
from conftest import OPERATOR, OPERATOR_PASSWORD, PASSWORD, FakeQueue
from conftest import OPERATOR_SECRET as SECRET
from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy import select
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
    # Pin the code's counter so a 30-second boundary during the request cannot change it.
    counter = now_counter()
    response = await sign_in(client, counter=counter)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["email"] == OPERATOR and data["expires_in"] == 1800
    [row] = await audit_rows(client)
    assert (row.action, row.outcome) == ("platform.login", "success")
    assert row.details == {"totp_counter": counter}


@pytest.mark.parametrize(
    "case",
    ["wrong_password", "wrong_code", "replayed_code", "not_an_operator", "org_member_password"],
)
async def test_failed_sign_ins_are_uniform_and_audited(case, client):
    if case == "replayed_code":
        # Pin the counter: across a 30-second boundary the second code would be new.
        counter = now_counter()
        assert (await sign_in(client, counter=counter)).status_code == 200
        response = await sign_in(client, counter=counter)
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
    for _ in range(MAX_FAILURES):
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


def test_operator_without_environment_factor_can_start_but_malformed_factor_cannot(tmp_path):
    configuration = {
        "database_url": "postgresql+psycopg://bid_app@localhost/bid_test_unused",
        "encryption_key": Fernet.generate_key().decode(),
        "token_key": Fernet.generate_key().decode(),
    }
    settings = Settings(
        **configuration,
        data_dir=tmp_path,
        platform_admin_emails="ops@example.test",
        platform_totp_secrets=None,
    )
    assert settings.platform_admins() == ["ops@example.test"]
    assert settings.platform_totp() == {}
    with pytest.raises(ValidationError):
        Settings(
            **configuration,
            data_dir=tmp_path,
            platform_admin_emails=OPERATOR,
            platform_totp_secrets=f"{OPERATOR}:not base32!",
        )


async def test_disabled_org_rejects_login_sessions_and_tokens(client, tenants, admin_engine):
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
        with Session(admin_engine) as database, database.begin():
            organization = database.get(Org, tenants["orgs"][0])
            assert organization is not None
            organization.active = value

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
