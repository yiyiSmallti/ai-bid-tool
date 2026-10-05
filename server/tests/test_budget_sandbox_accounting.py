"""Offline sandbox admission boundaries.

Failure inventory before the change: a capture-only token is rejected by a fixed
render scope; a render-only token can capture; missing or mismatched pinned inputs
supply a guessed scope; failed or cancelled sandbox outcomes miss final receipts.
The actual supervisor, PostgreSQL and remote services are outside these tests.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.core.errors import ServiceError
from app.jobs import execution
from app.models.entities import ApiToken, User
from app.providers.base import ProviderFailure


class Session:
    def __init__(self, user_id, token_id, scopes, purpose):
        self.user_id, self.token_id = user_id, token_id
        self.scopes, self.purpose = scopes, purpose

    async def get(self, kind, identifier):
        if kind is User:
            return SimpleNamespace(id=self.user_id, active=True)
        if kind is ApiToken:
            return SimpleNamespace(
                user_id=self.user_id,
                revoked=False,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                scopes=self.scopes,
            )
        raise AssertionError("Unexpected authority lookup")

    async def scalar(self, query):
        return self.purpose


@pytest.mark.parametrize(
    ("purpose", "scope"),
    [
        ("vendor_capture", "sandbox:capture"),
        ("prototype_offline", "sandbox:render"),
    ],
)
async def test_sandbox_admission_uses_pinned_purpose_token_scope(monkeypatch, purpose, scope):
    async def membership(session, user_id, org_id):
        return SimpleNamespace(role="technical")

    monkeypatch.setattr(execution, "membership", membership)
    user, token = uuid4(), uuid4()
    job = SimpleNamespace(
        id=uuid4(),
        task_id=uuid4(),
        org_id=uuid4(),
        kind="sandbox",
        actor_user_id=user,
        actor_token_id=token,
        actor_scopes=["task:read", scope],
        actor_kind="token",
    )
    actor = await execution.authorized_job(Session(user, token, ["task:read", scope], purpose), job)
    assert scope in actor.scopes


@pytest.mark.parametrize("purpose", ["vendor_capture", "prototype_offline"])
async def test_wrong_sandbox_scope_never_admits(monkeypatch, purpose):
    async def membership(session, user_id, org_id):
        return SimpleNamespace(role="technical")

    monkeypatch.setattr(execution, "membership", membership)
    user, token = uuid4(), uuid4()
    wrong = "sandbox:render" if purpose == "vendor_capture" else "sandbox:capture"
    job = SimpleNamespace(
        id=uuid4(),
        task_id=uuid4(),
        org_id=uuid4(),
        kind="sandbox",
        actor_user_id=user,
        actor_token_id=token,
        actor_scopes=["task:read", wrong],
        actor_kind="token",
    )
    with pytest.raises(ServiceError) as caught:
        await execution.authorized_job(Session(user, token, ["task:read", wrong], purpose), job)
    assert caught.value.code == "forbidden"


async def test_missing_pinned_sandbox_input_has_no_default_permission(monkeypatch):
    async def membership(session, user_id, org_id):
        return SimpleNamespace(role="technical")

    monkeypatch.setattr(execution, "membership", membership)
    user, token = uuid4(), uuid4()
    scopes = ["task:read", "sandbox:render", "sandbox:capture"]
    job = SimpleNamespace(
        id=uuid4(),
        task_id=uuid4(),
        org_id=uuid4(),
        kind="sandbox",
        actor_user_id=user,
        actor_token_id=token,
        actor_scopes=scopes,
        actor_kind="token",
    )
    with pytest.raises(ProviderFailure) as caught:
        await execution.authorized_job(Session(user, token, scopes, None), job)
    assert caught.value.code == "sandbox_input_changed"
