"""Membership visibility and token scope rules enforced by PostgreSQL itself."""

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


async def test_memberships_listing_and_token_scope_constraint(runtime, admin_engine, tenants):
    async with runtime.transaction() as session:
        rows = (
            await session.execute(
                text("SELECT * FROM user_org_memberships(:u)"), {"u": tenants["users"][0]}
            )
        ).all()
        assert [(r.org_id, r.role, r.org_active) for r in rows] == [
            (tenants["orgs"][0], "admin", True)
        ]
    with pytest.raises(DBAPIError):
        async with runtime.transaction(tenants["orgs"][0]) as session:
            await session.execute(
                text(
                    "INSERT INTO api_tokens (id, org_id, user_id, name, digest, scopes, expires_at, revoked) "
                    "VALUES (gen_random_uuid(), :o, :u, 'x', :d, '[\"evidence:confirm\"]', now() + interval '1 day', false)"
                ),
                {"o": tenants["orgs"][0], "u": tenants["users"][0], "d": "f" * 64},
            )


async def test_functions_restore_the_callers_org_context(runtime, tenants):
    org, other = tenants["orgs"]
    async with runtime.transaction(org) as session:
        for statement, values in (
            ("SELECT platform_set_org_active(:o, true)", {"o": other}),
            ("SELECT * FROM platform_create_org('Context org', 'ctx@example.test', '!setup')", {}),
        ):
            await session.execute(text(statement), values)
            current = await session.scalar(text("SELECT current_setting('app.current_org', true)"))
            assert current == str(org), statement
