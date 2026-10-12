"""Runtime role ownership and human-only token scopes remain database gates."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.core.db import Database
from app.models.entities import ApiToken
from sqlalchemy.exc import DBAPIError


async def test_runtime_refuses_privileged_role(admin_engine):
    settings = Settings(database_url=admin_engine.url.render_as_string(hide_password=False))
    db = Database(settings)
    try:
        with pytest.raises(RuntimeError, match="superuser"):
            await db.verify_role()
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize(
    "scope",
    [
        "export",
        "evidence:confirm",
        "provider:write",
        "screenshot:ingest",
        "token:create",
        "evidence:annotate",
        "req:confirm",
        "score:rubric:review",
        "memory:approve",
    ],
)
async def test_database_rejects_forbidden_token_scope(scope):
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction() as session:
                session.add(
                    ApiToken(
                        name="forbidden",
                        digest=uuid4().hex,
                        scopes=[scope],
                        expires_at=datetime.now(UTC) + timedelta(days=1),
                    )
                )
        assert error.value.orig.sqlstate == "23514"
    finally:
        await db.engine.dispose()
