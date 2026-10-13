"""PostgreSQL lifecycle and human gates for local service credentials.

Failure inventory: stale revisions, conflicting service bindings, exhausted probe
authorization, tombstone resurrection, nonatomic imports and runtime ciphertext
updates. No vendor I/O.
"""

import json
from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

OWNER = "bid_app"
MANAGER = "bid_app"
READER = "bid_app"
ACTOR = "credential-ops@example.test"


@contextmanager
def role_connection(engine, role):
    assert role == "bid_app"
    with engine.begin() as connection:
        connection.execute(text(f"SET LOCAL SESSION AUTHORIZATION {role}"))
        connection.execute(
            text(
                "SELECT set_config('app.actor_kind','session',true), set_config('app.actor_token_id','',true)"
            )
        )
        yield connection


def manage(connection, action, body=None):
    return connection.scalar(
        text("SELECT public.platform_credential_manage(:action, CAST(:body AS jsonb))"),
        {"action": action, "body": json.dumps(body or {})},
    )


def create_payload(**overrides):
    value = {
        "id": str(uuid4()),
        "name": "test_" + uuid4().hex[:20],
        "purpose": "standalone_llm",
        "provider": "openai",
        "endpoint": "https://api.openai.com/v1",
        "encrypted_key": "gAAAA" + "a" * 155,
        "fingerprint": "sha256:" + "1" * 16,
        "last_four": "test",
        "active": True,
        "reason": "setup",
    }
    value.update(overrides)
    return value


@pytest.fixture
def credential_db(admin_engine, database):
    # Credentials retain tombstone names, so each case starts with an empty store.
    with admin_engine.begin() as connection:
        connection.execute(text("TRUNCATE public.platform_credentials CASCADE"))
    return admin_engine


def test_safe_views_conflict_and_tombstone(credential_db):
    payload = create_payload()
    with role_connection(credential_db, MANAGER) as connection:
        view = manage(connection, "create", payload)["credential"]
        assert view["state"] == "active" and view["revision"] == 1
        assert "encrypted_key" not in json.dumps(view)
        assert payload["encrypted_key"] not in json.dumps(manage(connection, "list"))
    with pytest.raises(DBAPIError, match="revision_conflict"):
        with role_connection(credential_db, MANAGER) as connection:
            manage(
                connection,
                "set_active",
                {
                    "id": payload["id"],
                    "expected_revision": 2,
                    "active": False,
                    "reason": "incident",
                },
            )
    with role_connection(credential_db, MANAGER) as connection:
        changed = manage(
            connection, "remove", {"id": payload["id"], "expected_revision": 1, "reason": "retired"}
        )["credential"]
        assert changed["state"] == "removed" and changed["revision"] == 2
    with credential_db.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT encrypted_key FROM platform_credentials WHERE id=:id"),
                {"id": payload["id"]},
            )
            is None
        )
    with pytest.raises(DBAPIError, match="credential_removed"):
        with role_connection(credential_db, MANAGER) as connection:
            manage(
                connection,
                "set_active",
                {"id": payload["id"], "expected_revision": 2, "active": True, "reason": "setup"},
            )
    with pytest.raises(DBAPIError, match="credential_name_conflict"):
        with role_connection(credential_db, MANAGER) as connection:
            manage(connection, "create", {**payload, "id": str(uuid4())})


def test_probe_authorization_bound_to_revision_and_one_completion(credential_db):
    payload = create_payload(active=False)
    with role_connection(credential_db, MANAGER) as connection:
        manage(connection, "create", payload)
        probe = manage(connection, "probe_begin", {"id": payload["id"], "expected_revision": 1})
    with role_connection(credential_db, READER) as connection:
        resolved = connection.scalar(
            text("SELECT platform_credential_resolve_probe(:id)"), {"id": probe["probe_id"]}
        )
        assert resolved["encrypted_key"] == payload["encrypted_key"]
    with role_connection(credential_db, MANAGER) as connection:
        manage(
            connection,
            "probe_finish",
            {"probe_id": probe["probe_id"], "outcome": "passed", "duration_ms": 1},
        )
    with pytest.raises(DBAPIError, match="credential_probe_interrupted"):
        with role_connection(credential_db, READER) as connection:
            connection.execute(
                text("SELECT platform_credential_resolve_probe(:id)"), {"id": probe["probe_id"]}
            )
    with role_connection(credential_db, MANAGER) as connection:
        for _ in range(4):
            manage(connection, "probe_begin", {"id": payload["id"], "expected_revision": 1})
    with pytest.raises(DBAPIError, match="credential_probe_rate_limited"):
        with role_connection(credential_db, MANAGER) as connection:
            manage(connection, "probe_begin", {"id": payload["id"], "expected_revision": 1})


def test_import_conflict_is_batch_atomic_and_dry_run_writes_nothing(credential_db):
    first = create_payload(reason="migration")
    with role_connection(credential_db, MANAGER) as connection:
        manage(connection, "create", first)
    second = create_payload(
        reason="migration",
        purpose="vendor_search",
        provider="perplexity",
        endpoint="https://api.perplexity.ai",
    )
    with pytest.raises(DBAPIError, match="credential_import_conflict"):
        with role_connection(credential_db, MANAGER) as connection:
            manage(
                connection,
                "import",
                {"entries": [second, {**first, "expected_revision": 9, "same_value": True}]},
            )
    with credential_db.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM platform_credentials WHERE id=:id"), {"id": second["id"]}
            )
            == 0
        )
        count = connection.scalar(text("SELECT count(*) FROM audit_logs"))
    with role_connection(credential_db, MANAGER) as connection:
        result = manage(connection, "import", {"entries": [second], "dry_run": True})
        assert result["would_create"] == 1
    with credential_db.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM audit_logs")) == count


def test_byok_rewrap_is_owner_only(credential_db):
    for role in ("bid_app",):
        with pytest.raises(DBAPIError):
            with role_connection(credential_db, role) as connection:
                connection.execute(
                    text("SELECT provider_credential_rewrap(gen_random_uuid(), 'old', 'new')")
                )
        with pytest.raises(DBAPIError):
            with role_connection(credential_db, role) as connection:
                connection.execute(text("SET LOCAL app.provider_rewrap='on'"))
                connection.execute(text("UPDATE provider_configs SET encrypted_key='new'"))
