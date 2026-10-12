"""Account membership discovery requires password authentication."""

import json

import httpx
import pytest
from app.api.main import create_app
from app.core.config import Settings
from conftest import PASSWORD, FakeQueue, seed_platform_credential
from test_llm_providers import Vendor

CODE = "ABCD-EFGH-JKMN-PQRS"


@pytest.fixture
async def account_app(tenants, tmp_path, monkeypatch):
    vendor = Vendor()
    app = create_app(
        Settings(data_dir=tmp_path), queue=FakeQueue(), llm_transport=vendor.transport()
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api,
    ):
        api.app, api.vendor = app, vendor  # pyright: ignore[reportAttributeAccessIssue]
        await seed_platform_credential(app.state.processor.settings)
        yield api


async def org_header(api, org, email="a@example.test"):
    body = {"email": email, "password": PASSWORD, "org_id": str(org)}
    session = (await api.post("/auth/login", json=body)).json()["data"]["session"]
    return {"Authorization": f"Bearer {session}", "X-Org-Id": str(org)}


async def test_auth_orgs_lists_memberships_after_password_check(account_app, tenants):
    found = await account_app.post(
        "/auth/orgs", json={"email": "A@example.test", "password": PASSWORD}
    )
    assert found.status_code == 200
    assert found.json()["items"] == [
        {
            "org_id": str(tenants["orgs"][0]),
            "name": "Synthetic tenant A",
            "role": "admin",
            "active": True,
        }
    ]
    wrong = await account_app.post(
        "/auth/orgs", json={"email": "a@example.test", "password": "wrong"}
    )
    assert wrong.status_code == 401
    unknown = await account_app.post(
        "/auth/orgs", json={"email": "nobody@example.test", "password": PASSWORD}
    )
    assert unknown.status_code == 401
    assert json.dumps(unknown.json()) == json.dumps(wrong.json()).replace(
        str(wrong.json()["duration_ms"]), str(unknown.json()["duration_ms"]), 1
    )
