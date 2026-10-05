"""PostgreSQL/API failure inventory, recorded before the producer context fix.

* A privileged multi-org statement without app.current_org must commit one durable
  event per affected task, preserving org bindings and restoring the empty context.
* An explicit wrong context must fail, even for an administrator; failed appends
  must roll back both the business mutation and any temporary context.
* bid_app cannot infer a tenant from a row, including when session_user is an
  administrator that used SET ROLE. A controlled transition-table probe exercises
  that boundary without changing RLS on any production table.
* Direct append calls with missing/foreign context stay forbidden, and ordinary
  runtime table writes retain missing-context and cross-org RLS isolation.

No database is started here. Requires the explicitly supplied isolated test DB.
"""

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_team_workflow_membership import new_task

ARTIFACT = (
    Path(__file__).resolve().parents[2] / "data/work/team-workflow-acceptance/event-context.json"
)
INVALIDATION = json.dumps(
    {"type": "board_changed", "invalidate_all": True, "requirement_ids": [], "card_ids": []}
)
APPEND = text(
    "SELECT public.append_task_event(:org,:task,'board_changed',CAST(:payload AS jsonb),NULL)"
)


@pytest.fixture
async def event_tasks(api, headers, tenants):
    return [
        (org, UUID(await new_task(api, header)))
        for org, header in zip(tenants["orgs"], headers, strict=True)
    ]


def heads(connection):
    return dict(connection.execute(text("SELECT task_id,last_seq FROM task_event_heads")).all())


def empty_context(connection):
    connection.execute(text("SELECT set_config('app.current_org','',true)"))


async def test_admin_statement_preserves_two_org_events_and_restores_context(
    admin_engine, event_tasks
):
    with admin_engine.begin() as connection:
        empty_context(connection)
        before = heads(connection)
        connection.execute(
            text("UPDATE tasks SET name='Synthetic administrative rename' WHERE id IN (:a,:b)"),
            {"a": event_tasks[0][1], "b": event_tasks[1][1]},
        )
        assert connection.scalar(text("SELECT current_setting('app.current_org',true)")) == ""
        after = heads(connection)
        for _, task in event_tasks:
            assert after[task] == before[task] + 1
    with admin_engine.connect() as connection:
        retained = []
        for org, task in event_tasks:
            event = (
                connection.execute(
                    text(
                        "SELECT org_id,task_id,seq,event_kind,payload FROM task_events"
                        " WHERE task_id=:task ORDER BY seq DESC LIMIT 1"
                    ),
                    {"task": task},
                )
                .mappings()
                .one()
            )
            assert event["org_id"] == org
            assert event["seq"] == after[task]
            assert event["event_kind"] == "board_changed"
            assert event["payload"] == json.loads(INVALIDATION)
            retained.append(dict(event))
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps({"committed_events": retained}, indent=2, default=str) + "\n")


@pytest.mark.parametrize("as_runtime", [False, True])
@pytest.mark.parametrize("context", ["", "foreign"])
async def test_direct_append_requires_matching_context(
    admin_engine, event_tasks, as_runtime, context
):
    org, task = event_tasks[0]
    with admin_engine.begin() as connection:
        before = heads(connection)
        if as_runtime:
            connection.execute(text("SET LOCAL ROLE bid_app"))
        connection.execute(
            text("SELECT set_config('app.current_org',:org,true)"),
            {"org": str(event_tasks[1][0]) if context else ""},
        )
        with pytest.raises(DBAPIError, match="Task event org mismatch"):
            with connection.begin_nested():
                connection.execute(APPEND, {"org": org, "task": task, "payload": INVALIDATION})
        connection.execute(text("RESET ROLE"))
        assert heads(connection) == before


async def test_explicit_admin_context_cannot_be_replaced(admin_engine, event_tasks):
    with admin_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.current_org',:org,true)"),
            {"org": str(event_tasks[0][0])},
        )
        before = heads(connection)
        old_name = connection.scalar(
            text("SELECT name FROM tasks WHERE id=:task"), {"task": event_tasks[1][1]}
        )
        with pytest.raises(DBAPIError, match="Task event org mismatch"):
            with connection.begin_nested():
                connection.execute(
                    text("UPDATE tasks SET name='Must roll back' WHERE id=:task"),
                    {"task": event_tasks[1][1]},
                )
        assert connection.scalar(text("SELECT current_setting('app.current_org',true)")) == str(
            event_tasks[0][0]
        )
        assert heads(connection) == before
        assert (
            connection.scalar(
                text("SELECT name FROM tasks WHERE id=:task"), {"task": event_tasks[1][1]}
            )
            == old_name
        )


@pytest.mark.parametrize("as_runtime", [False, True])
async def test_producer_checks_effective_role_and_restores_failed_context(
    admin_engine, event_tasks, as_runtime
):
    org, task = event_tasks[0]
    with admin_engine.begin() as connection:
        empty_context(connection)
        before = heads(connection)
        # The admin-created probe permits a transition row to reach the real
        # invoker trigger even without tenant context; production RLS is untouched.
        connection.execute(
            text(
                "CREATE TEMP TABLE event_context_probe "
                "(org_id uuid,task_id uuid,source_id uuid) ON COMMIT DROP"
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER context_probe AFTER INSERT ON event_context_probe "
                "REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT "
                "EXECUTE FUNCTION public.produce_task_events('task_id','source_id')"
            )
        )
        connection.execute(text("GRANT INSERT ON event_context_probe TO bid_app"))
        if as_runtime:
            connection.execute(text("SET LOCAL ROLE bid_app"))
        failure = "Task event org mismatch" if as_runtime else "Invalid task event source"
        with pytest.raises(DBAPIError, match=failure):
            with connection.begin_nested():
                connection.execute(
                    text("INSERT INTO event_context_probe VALUES(:org,:task,:source)"),
                    {"org": org, "task": task, "source": uuid4()},
                )
        assert connection.scalar(text("SELECT current_setting('app.current_org',true)")) == ""
        connection.execute(text("RESET ROLE"))
        assert heads(connection) == before
        assert connection.scalar(text("SELECT count(*) FROM event_context_probe")) == 0


async def test_runtime_rls_missing_and_cross_org_context(admin_engine, event_tasks):
    with admin_engine.begin() as connection:
        before = heads(connection)
        connection.execute(text("SET LOCAL ROLE bid_app"))
        empty_context(connection)
        assert connection.execute(text("UPDATE tasks SET name='Must not write'")).rowcount == 0
        connection.execute(
            text("SELECT set_config('app.current_org',:org,true)"),
            {"org": str(event_tasks[0][0])},
        )
        assert (
            connection.execute(
                text("UPDATE tasks SET name='Must not write' WHERE id=:task"),
                {"task": event_tasks[1][1]},
            ).rowcount
            == 0
        )
        assert (
            connection.execute(
                text("UPDATE tasks SET name='Scoped runtime write' WHERE id=:task"),
                {"task": event_tasks[0][1]},
            ).rowcount
            == 1
        )
        assert connection.scalar(text("SELECT current_setting('app.current_org',true)")) == str(
            event_tasks[0][0]
        )
        connection.execute(text("RESET ROLE"))
        after = heads(connection)
        assert after[event_tasks[0][1]] == before[event_tasks[0][1]] + 1
        assert after[event_tasks[1][1]] == before[event_tasks[1][1]]
