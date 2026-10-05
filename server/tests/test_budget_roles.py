"""DB-free role contract for session-only budget administration."""

from uuid import uuid4

import pytest
from app.core.errors import ServiceError
from app.services.auth import ROLE_SCOPES, SCOPES, Identity
from app.services.budgets import require_human


def test_budget_write_scopes_are_human_only():
    assert {"task:budget:write", "billing:alert:write"}.isdisjoint(SCOPES)
    assert "task:budget:write" in ROLE_SCOPES["admin"]
    assert "task:budget:write" in ROLE_SCOPES["bidder"]
    assert "billing:alert:write" in ROLE_SCOPES["admin"]
    for role in ("technical", "viewer"):
        assert "task:budget:write" not in ROLE_SCOPES[role]
    for kind in ("token", "agent", "worker"):
        actor = Identity(uuid4(), uuid4(), {"task:budget:write"}, "admin", actor_kind=kind)
        with pytest.raises(ServiceError):
            require_human(actor, "task:budget:write", {"admin", "bidder"})
    actor = Identity(uuid4(), uuid4(), {"task:budget:write"}, "admin", token_id=uuid4())
    with pytest.raises(ServiceError):
        require_human(actor, "task:budget:write", {"admin", "bidder"})
