"""Human scope and durable task event database guards."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.core.config import Settings
from app.core.db import Database
from app.models.entities import ApiToken
from app.services.auth import HUMAN_ONLY_SCOPES, HUMAN_SCOPES
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from task_access_fixtures import new_task

FORBIDDEN_SCOPES = tuple(sorted(HUMAN_ONLY_SCOPES))


@pytest.mark.parametrize("scope", FORBIDDEN_SCOPES)
async def test_token_human_scopes_rejected_api_and_sql(api, headers, database, scope):
    response = await api.post(
        "/tokens",
        headers=headers,
        json={
            "name": "Synthetic forbidden workflow token",
            "scopes": [scope],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert response.status_code == 403
    if scope in {"clef:manage", "credential:manage", "trust-anchor:manage"}:
        return
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError):
            async with db.transaction() as session:
                session.add(
                    ApiToken(
                        name="forbidden",
                        digest="d" * 64,
                        scopes=[scope],
                        expires_at=datetime.now(UTC) + timedelta(hours=1),
                    )
                )
                await session.flush()
    finally:
        await db.engine.dispose()


async def test_direct_sql_owner_and_event_guards(api, headers):
    task = UUID(await new_task(api, headers))
    db = Database(Settings())
    try:
        for statement in ("UPDATE task_events SET payload='{}' WHERE task_id=:task",):
            with pytest.raises(DBAPIError):
                async with db.transaction() as session:
                    await session.execute(
                        text(
                            "SELECT set_config('app.actor_kind','session',true),set_config('app.actor_scopes',:scopes,true)"
                        ),
                        {"scopes": json.dumps(sorted(HUMAN_SCOPES))},
                    )
                    await session.execute(text(statement), {"task": task})
                    await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    finally:
        await db.engine.dispose()
