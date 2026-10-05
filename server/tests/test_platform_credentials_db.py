"""PostgreSQL permission and lifecycle coverage for ADR 0006.

Failure cases are exercised through real database roles: forged tenant context, direct
ciphertext access, stale revisions, mismatched consumers, unaudited mutation, exhausted
probe authorization, tombstone resurrection, and ordinary BYOK updates. No vendor I/O.
"""

import json
from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

OWNER = "bid_platform_credentials_fn"
MANAGER = "bid_platform_app"
READER = "bid_credential_reader"
ACTOR = "credential-ops@example.test"


@contextmanager
def role_connection(engine, role):
    assert role in ("bid_app", "bid_platform_fn", OWNER, MANAGER, READER)
    with engine.begin() as connection:
        connection.execute(text(f"SET LOCAL SESSION AUTHORIZATION {role}"))
        yield connection


def manage(connection, action, body=None):
    return connection.scalar(
        text("SELECT public.platform_credential_manage(:action, :actor, CAST(:body AS jsonb))"),
        {"action": action, "actor": ACTOR, "body": json.dumps(body or {})},
    )


def create_payload(**overrides):
    value = {
        "id": str(uuid4()),
        "name": "test_" + uuid4().hex[:20],
        "purpose": "catalog_llm",
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
def credential_db(admin_engine, tenants):
    # Existing generic fixture resets tenants/catalogs; credentials retain names by design.
    with admin_engine.begin() as connection:
        connection.execute(text("TRUNCATE public.platform_credentials CASCADE"))
    return admin_engine


def test_roles_and_function_catalog_are_least_privilege(credential_db):
    with credential_db.connect() as connection:
        for role, login in ((OWNER, False), (MANAGER, True), (READER, True)):
            attrs = connection.execute(
                text(
                    "SELECT rolcanlogin, rolsuper, rolbypassrls, rolinherit, rolcreatedb, "
                    "rolcreaterole FROM pg_roles WHERE rolname=:role"
                ),
                {"role": role},
            ).one()
            assert tuple(attrs) == (login, False, False, False, False, False)
            assert not connection.scalar(
                text("SELECT has_schema_privilege(:role, 'public', 'CREATE')"), {"role": role}
            )
        rows = connection.execute(
            text(
                "SELECT p.proname, pg_get_userbyid(p.proowner), p.prosecdef, p.proconfig, "
                "has_function_privilege('bid_app', p.oid, 'EXECUTE'), "
                "has_function_privilege('public', p.oid, 'EXECUTE') "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='public' AND p.proname LIKE 'platform_credential_%'"
            )
        ).all()
        assert rows
        assert all(not row[4] and not row[5] for row in rows)
        assert all(row[3] == ["search_path=pg_catalog"] for row in rows)
        for role in ("bid_app", "bid_platform_fn", MANAGER, READER):
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert not connection.scalar(
                    text(
                        "SELECT has_table_privilege(:role, 'public.platform_credentials', :privilege)"
                    ),
                    {"role": role, "privilege": privilege},
                )
            assert not connection.scalar(
                text("SELECT pg_has_role(:role, :owner, 'MEMBER')"), {"role": role, "owner": OWNER}
            )
        assert not connection.scalar(
            text(
                "SELECT has_column_privilege('bid_platform_credentials_fn', 'platform_models', 'model', 'SELECT')"
            )
        )
        assert not connection.scalar(
            text(
                "SELECT has_any_column_privilege('bid_platform_credentials_fn', 'platform_models', 'UPDATE, INSERT')"
            )
        )
        assert not connection.scalar(
            text(
                "SELECT has_any_column_privilege('bid_platform_credentials_fn', 'platform_audit_logs', 'UPDATE')"
            )
        )
        for table in ("provider_configs", "tasks", "documents", "memberships", "orgs"):
            assert not connection.scalar(
                text("SELECT has_table_privilege(:role, :table, 'SELECT')"),
                {"role": OWNER, "table": table},
            )


@pytest.mark.parametrize("role", ["bid_app", "bid_platform_fn", MANAGER, READER])
def test_direct_access_and_role_escalation_denied(credential_db, role):
    statements = [
        "SELECT encrypted_key FROM public.platform_credentials",
        "DELETE FROM public.platform_credentials",
        "SET ROLE bid_platform_credentials_fn",
    ]
    if role != MANAGER:
        statements.append(
            "SELECT public.platform_credential_manage('list', 'fake@example.test', '{}')"
        )
    if role != READER:
        statements.append(
            "SELECT public.platform_credential_resolve_service('vendor_search', gen_random_uuid())"
        )
    for statement in statements:
        with pytest.raises(DBAPIError):
            with role_connection(credential_db, role) as connection:
                connection.execute(text("SET LOCAL app.actor_kind='platform'"))
                connection.execute(text("SET LOCAL app.actor_email='fake@example.test'"))
                connection.execute(text(statement))


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


def test_audit_failure_rolls_back_mutation_and_cannot_be_forged(credential_db):
    payload = create_payload()
    # Deny inserts inside a transaction; rollback restores grants and removes the attempted row.
    with credential_db.connect() as connection:
        transaction = connection.begin()
        connection.execute(
            text("REVOKE INSERT ON public.platform_audit_logs FROM bid_platform_credentials_fn")
        )
        connection.execute(text("SET LOCAL ROLE bid_platform_app"))
        with pytest.raises(DBAPIError):
            manage(connection, "create", payload)
        transaction.rollback()
    with credential_db.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM platform_credentials WHERE id=:id"),
                {"id": payload["id"]},
            )
            == 0
        )
    for statement in (
        "INSERT INTO platform_audit_logs(id,actor_email,action,outcome,details) "
        "VALUES(gen_random_uuid(),'fake@example.test','credential.probe_start','success','{}')",
        "UPDATE platform_audit_logs SET outcome='failed'",
        "DELETE FROM platform_audit_logs",
    ):
        with pytest.raises(DBAPIError):
            with role_connection(credential_db, "bid_app") as connection:
                connection.execute(text(statement))


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


def test_catalog_binding_and_committed_disable_block_reader(credential_db):
    payload = create_payload()
    with role_connection(credential_db, MANAGER) as connection:
        manage(connection, "create", payload)
    model_id = "model_" + uuid4().hex[:20]
    statement = text(
        "INSERT INTO platform_models(id,capability,provider,model,base_url,credential, "
        "vendor_input_usd_per_mtok,vendor_output_usd_per_mtok, "
        "sale_input_per_mtok,sale_output_per_mtok,updated_by) "
        "VALUES(:model,'llm_extract','openai','synthetic',:endpoint,:name,0,0,0,0,:actor)"
    )
    with pytest.raises(DBAPIError, match="credential_reference_mismatch"):
        with credential_db.begin() as connection:
            connection.execute(
                statement,
                {
                    "model": model_id,
                    "endpoint": "https://other.example/v1",
                    "name": payload["name"],
                    "actor": ACTOR,
                },
            )
    with credential_db.begin() as connection:
        connection.execute(
            statement,
            {
                "model": model_id,
                "endpoint": payload["endpoint"],
                "name": payload["name"],
                "actor": ACTOR,
            },
        )
    with role_connection(credential_db, READER) as connection:
        assert (
            connection.scalar(
                text("SELECT platform_credential_resolve_catalog(:model, 1)"), {"model": model_id}
            )["id"]
            == payload["id"]
        )
    with role_connection(credential_db, MANAGER) as connection:
        manage(
            connection,
            "set_active",
            {"id": payload["id"], "expected_revision": 1, "active": False, "reason": "incident"},
        )
    with pytest.raises(DBAPIError, match="credential_disabled"):
        with role_connection(credential_db, READER) as connection:
            connection.execute(
                text("SELECT platform_credential_resolve_catalog(:model, 1)"), {"model": model_id}
            )


def test_import_conflict_is_batch_atomic_and_dry_run_writes_nothing(credential_db):
    first = create_payload(reason="migration")
    with role_connection(credential_db, MANAGER) as connection:
        manage(connection, "create", first)
    second = create_payload(reason="migration")
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
        count = connection.scalar(text("SELECT count(*) FROM platform_audit_logs"))
    with role_connection(credential_db, MANAGER) as connection:
        result = manage(connection, "import", {"entries": [second], "dry_run": True})
        assert result["would_create"] == 1
    with credential_db.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM platform_audit_logs")) == count


def test_byok_rewrap_is_owner_only(credential_db):
    for role in ("bid_app", MANAGER, READER, OWNER):
        with pytest.raises(DBAPIError):
            with role_connection(credential_db, role) as connection:
                connection.execute(
                    text(
                        "SELECT provider_credential_rewrap(gen_random_uuid(), "
                        "gen_random_uuid(), 'old', 'new')"
                    )
                )
        with pytest.raises(DBAPIError):
            with role_connection(credential_db, role) as connection:
                connection.execute(text("SET LOCAL app.provider_rewrap='on'"))
                connection.execute(text("UPDATE provider_configs SET encrypted_key='new'"))


@pytest.mark.parametrize("role", [MANAGER, READER])
async def test_pool_rejects_privileged_session_disguised_as_runtime(credential_db, role):
    from app.core.credential_db import CredentialPool
    from app.core.errors import ServiceError
    from pydantic import SecretStr

    disguised = credential_db.url.update_query_dict({"options": "-c role=" + role})
    pool = CredentialPool(SecretStr(disguised.render_as_string(hide_password=False)), role)
    try:
        with pytest.raises(ServiceError, match="role is invalid"):
            async with pool.transaction():
                pytest.fail("An owner login must never pass as a dedicated runtime role")
    finally:
        await pool.close()
