"""Retained platform role isolation and audit immutability against real PostgreSQL."""

import pytest
from app.core.config import Settings
from app.core.db import Database
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


@pytest.fixture
async def runtime(tenants, tmp_path):
    database = Database(Settings(data_dir=tmp_path))
    yield database
    await database.engine.dispose()


def test_platform_role_cannot_log_in_or_bypass_rls(admin_engine):
    with admin_engine.connect() as connection:
        role = connection.execute(
            text(
                "SELECT rolcanlogin, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'bid_platform_fn'"
            )
        ).one()
        assert tuple(role) == (False, False, False)
        assert not connection.scalar(
            text("SELECT has_schema_privilege('bid_platform_fn', 'public', 'CREATE')")
        )
        policies = connection.execute(
            text(
                "SELECT tablename, cmd FROM pg_policies WHERE 'bid_platform_fn' = ANY(roles) ORDER BY tablename"
            )
        ).all()
        assert [tuple(p) for p in policies] == [
            ("memberships", "SELECT"),
            ("orgs", "SELECT"),
            ("usage_records", "SELECT"),
        ]
        functions = connection.execute(
            text(
                "SELECT p.proname, pg_get_userbyid(p.proowner), p.prosecdef, "
                "has_function_privilege('bid_app', p.oid, 'EXECUTE'), "
                "has_function_privilege('public', p.oid, 'EXECUTE') "
                "FROM pg_proc p WHERE p.proname = 'user_org_memberships'"
            )
        ).all()
        assert {f[0] for f in functions} == {"user_org_memberships"}
        assert all(f[1:] == ("bid_platform_fn", True, True, False) for f in functions)
        # Clef configuration functions have their own owner role and no PUBLIC execute.
        clef = connection.execute(
            text(
                "SELECT p.proname, pg_get_userbyid(p.proowner), "
                "has_function_privilege('public', p.oid, 'EXECUTE') "
                "FROM pg_proc p WHERE p.proname LIKE 'platform_clef_%'"
            )
        ).all()
        assert {f[0] for f in clef} == {
            "platform_clef_read",
            "platform_clef_manage",
            "platform_clef_row_gate",
        }
        # The row gate is an ordinary trigger function owned by the migration owner.
        callable_fns = [f for f in clef if f[0] != "platform_clef_row_gate"]
        assert all(f[1] == "bid_clef_config_fn" and not f[2] for f in callable_fns)


async def test_runtime_without_org_context_cannot_read_tenant_rows(runtime):
    async with runtime.transaction() as session:
        assert await session.scalar(text("SELECT count(*) FROM usage_records")) == 0
        assert await session.scalar(text("SELECT count(*) FROM orgs")) == 0


async def test_runtime_cannot_rewrite_platform_audit(runtime):
    async with runtime.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO platform_audit_logs (id, actor_email, action, outcome, details) "
                "VALUES (gen_random_uuid(), 'ops@example.test', 'synthetic', 'success', '{}')"
            )
        )
    for statement in (
        "UPDATE platform_audit_logs SET outcome = 'failed'",
        "DELETE FROM platform_audit_logs",
    ):
        with pytest.raises(DBAPIError):
            async with runtime.transaction() as session:
                await session.execute(text(statement))
    with pytest.raises(DBAPIError):
        async with runtime.transaction() as session:
            await session.execute(text("DELETE FROM platform_models"))
