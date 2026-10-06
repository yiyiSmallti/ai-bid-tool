"""Database rubric invariants: isolation, immutable publication, and human gates.

Failures to prevent: cross-org reads/writes, cross-task references, rewriting
history, token review, worker review, admin domain confirmation, stale attempts,
and confirming a set without complete normalized coverage.
"""

import pytest
from sqlalchemy import text
from test_score_api import rubric_case as rubric_case
from test_score_api import rubric_input_case as rubric_input_case

TABLES = (
    "score_rubric_sets",
    "score_rubric_sections",
    "score_rubric_items",
    "score_rubric_coverage",
    "score_rubric_coverage_items",
    "score_rubric_decisions",
    "score_rubric_coverage_decisions",
    "score_rubric_classifications",
    "score_rubric_revision_events",
)


@pytest.mark.parametrize("table", TABLES)
def test_rubric_tables_force_tenant_security(tenants, admin_engine, table):
    """Exercise each actual migrated table as the ordinary application role."""
    with admin_engine.connect() as connection:
        security = connection.execute(
            text("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname=:table"),
            {"table": table},
        ).one()
        assert security == (True, True)
        columns = connection.execute(
            text(
                "SELECT column_name,is_nullable FROM information_schema.columns WHERE table_name=:table AND column_name IN ('org_id','task_id')"
            ),
            {"table": table},
        ).all()
        assert set(columns) == {("org_id", "NO"), ("task_id", "NO")}
        connection.execute(text("SET LOCAL ROLE bid_app"))
        connection.execute(text("SELECT set_config('app.current_org','',true)"))
        assert connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one() == 0


@pytest.mark.asyncio
async def test_every_rubric_table_isolated_and_immutable(rubric_case):
    from uuid import UUID, uuid4

    from sqlalchemy.exc import DBAPIError
    from test_score_review import base, confirm_contents, replacement

    case = rubric_case
    complete = await confirm_contents(case)
    revised = await case["api"].post(
        base(case) + "/revisions", headers=case["header"], json=replacement(complete)
    )
    assert revised.status_code == 200, revised.text
    for table in TABLES:
        with case["admin_engine"].connect() as connection:
            connection.execute(text("SET LOCAL ROLE bid_app"))
            connection.execute(
                text("SELECT set_config('app.current_org',:org,true)"),
                {"org": str(case["tenants"]["orgs"][0])},
            )
            ids = connection.execute(text(f'SELECT id FROM "{table}"')).scalars().all()
            assert ids, table
            connection.execute(
                text("SELECT set_config('app.current_org',:org,true)"),
                {"org": str(case["tenants"]["orgs"][1])},
            )
            assert connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one() == 0
            connection.execute(text("SELECT set_config('app.current_org','',true)"))
            assert connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one() == 0
        for action in ("UPDATE", "DELETE", "INSERT"):
            with pytest.raises(DBAPIError), case["admin_engine"].begin() as connection:
                connection.execute(text("SET LOCAL ROLE bid_app"))
                connection.execute(
                    text("SELECT set_config('app.current_org',:org,true)"),
                    {"org": str(case["tenants"]["orgs"][0])},
                )
                if action == "UPDATE":
                    connection.execute(
                        text(f'UPDATE "{table}" SET id=id WHERE id=:id'), {"id": ids[0]}
                    )
                elif action == "DELETE":
                    connection.execute(text(f'DELETE FROM "{table}" WHERE id=:id'), {"id": ids[0]})
                else:
                    connection.execute(
                        text(
                            f'INSERT INTO "{table}" SELECT (jsonb_populate_record(NULL::"{table}",to_jsonb(existing)||jsonb_build_object(\'id\',:new_id,\'org_id\',:foreign_org))).* FROM "{table}" existing WHERE id=:id'
                        ),
                        {
                            "id": ids[0],
                            "new_id": str(uuid4()),
                            "foreign_org": str(case["tenants"]["orgs"][1]),
                        },
                    )
    assert UUID(revised.json()["data"]["rubric"]["id"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor_kind,role_value",
    [("worker", "bidder"), ("token", "bidder"), ("agent", "bidder"), ("session", "admin")],
)
async def test_database_refuses_nonresponsible_rubric_decisions(
    rubric_case, actor_kind, role_value
):
    from uuid import UUID, uuid4

    from app.models.score import ScoreRubricDecision
    from app.services import response_cards as cards
    from app.services.auth import ROLE_SCOPES, Identity, set_actor_context
    from sqlalchemy.exc import DBAPIError
    from test_score_review import classify_all, role

    case = rubric_case
    report = await classify_all(case)
    role(case, role_value)
    with pytest.raises(DBAPIError):
        async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
            actor = Identity(
                case["tenants"]["users"][0],
                case["tenants"]["orgs"][0],
                ROLE_SCOPES[role_value],
                role_value,
                actor_kind=actor_kind,
            )
            await set_actor_context(session, actor)
            session.add(
                ScoreRubricDecision(
                    id=uuid4(),
                    org_id=actor.org_id,
                    task_id=UUID(case["task"]),
                    rubric_id=UUID(report["rubric"]["id"]),
                    section_id=None,
                    item_id=UUID(report["items"][0]["id"]),
                    revision=report["items"][0]["revision"] + 1,
                    set_revision=report["rubric"]["revision"] + 1,
                    action="confirm",
                    reason="Synthetic direct bypass attempt",
                    reason_sha256=cards.quote_hash("Synthetic direct bypass attempt"),
                    expected_input_hash=report["rubric"]["input_hash"],
                    decided_by=actor.user_id,
                    actor_kind="session",
                )
            )
            await session.flush()


@pytest.mark.asyncio
async def test_database_rejects_incomplete_set_confirmation(rubric_case):
    from uuid import UUID, uuid4

    from app.models.score import ScoreRubricDecision
    from app.services import response_cards as cards
    from app.services.auth import ROLE_SCOPES, Identity, set_actor_context
    from sqlalchemy.exc import DBAPIError
    from test_score_review import classify_all

    case = rubric_case
    report = await classify_all(case)
    with pytest.raises(DBAPIError):
        async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
            actor = Identity(
                case["tenants"]["users"][0],
                case["tenants"]["orgs"][0],
                ROLE_SCOPES["bidder"],
                "bidder",
            )
            await set_actor_context(session, actor)
            session.add(
                ScoreRubricDecision(
                    id=uuid4(),
                    org_id=actor.org_id,
                    task_id=UUID(case["task"]),
                    rubric_id=UUID(report["rubric"]["id"]),
                    section_id=None,
                    item_id=None,
                    revision=report["rubric"]["revision"] + 1,
                    set_revision=report["rubric"]["revision"] + 1,
                    action="confirm",
                    reason="Synthetic incomplete direct confirmation",
                    reason_sha256=cards.quote_hash("Synthetic incomplete direct confirmation"),
                    expected_input_hash=report["rubric"]["input_hash"],
                    decided_by=actor.user_id,
                    actor_kind="session",
                )
            )
            await session.flush()


@pytest.mark.asyncio
async def test_all_rubric_tables_reject_same_org_cross_task_foreign_keys(rubric_case):
    import json
    from uuid import uuid4

    from sqlalchemy.exc import IntegrityError
    from test_response_cards import create_tender
    from test_score_api import finish_rubric, preview_rubric, submit_rubric
    from test_score_review import base, confirm_contents, replacement

    case = rubric_case
    report = await confirm_contents(case)
    revised = await case["api"].post(
        base(case) + "/revisions", headers=case["header"], json=replacement(report)
    )
    assert revised.status_code == 200, revised.text
    task, document, extraction, requirements = await create_tender(
        case["api"],
        case["app"],
        case["header"],
        case["tmp_path"],
        suffix="cross-task-rubric",
        confirmed=True,
    )
    other = {
        **case,
        "task": task,
        "document": document,
        "extraction": extraction,
        "requirements": requirements,
    }
    submitted = await submit_rubric(other, await preview_rubric(other))
    assert submitted.status_code == 200, submitted.text
    terminal = await finish_rubric(other, submitted.json()["data"])
    assert terminal["status"] == "succeeded", terminal
    response = await case["api"].get(
        f"/tasks/{task}/score-rubrics/{terminal['result']['rubric_id']}", headers=case["header"]
    )
    assert response.status_code == 200, response.text
    other_report = response.json()["data"]
    for table in TABLES:
        with case["admin_engine"].connect() as connection:
            row = connection.execute(
                text(
                    f'SELECT to_jsonb(existing) FROM "{table}" existing WHERE task_id=:task ORDER BY created_at LIMIT 1'
                ),
                {"task": case["task"]},
            ).scalar_one()
        row["id"] = str(uuid4())
        if table == "score_rubric_sets":
            row.update(
                task_id=task, prior_rubric_id=revised.json()["data"]["rubric"]["id"], version=999
            )
        else:
            row["rubric_id"] = other_report["rubric"]["id"]
        if table in {"score_rubric_sections", "score_rubric_items"}:
            row["key"] = "cross-task-key"
            row["fingerprint"] = "f" * 64
        if table == "score_rubric_coverage_items":
            row["rubric_item_id"] = other_report["items"][0]["id"]
        if table == "score_rubric_coverage_decisions":
            row["revision"] = 999
        with pytest.raises(IntegrityError) as failure, case["admin_engine"].begin() as connection:
            # Suppress only application triggers in this rolled-back test transaction
            # to prove the composite FOREIGN KEY itself rejects the splice. Internal
            # FK triggers and all production grants/policies remain intact.
            connection.execute(text(f'ALTER TABLE "{table}" DISABLE TRIGGER USER'))
            connection.execute(
                text(
                    f'INSERT INTO "{table}" SELECT (jsonb_populate_record(NULL::"{table}",CAST(:payload AS jsonb))).*'
                ),
                {"payload": json.dumps(row, default=str)},
            )
        assert failure.value.orig.sqlstate == "23503", (table, failure.value.orig.sqlstate)


@pytest.mark.asyncio
async def test_database_rejects_whitespace_only_review_reason(rubric_case):
    from uuid import UUID, uuid4

    from app.models.score import ScoreRubricDecision
    from app.services import response_cards as cards
    from app.services.auth import ROLE_SCOPES, Identity, set_actor_context
    from sqlalchemy.exc import IntegrityError
    from test_score_review import classify_all

    case = rubric_case
    report = await classify_all(case)
    with pytest.raises(IntegrityError) as failure:
        async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
            actor = Identity(
                case["tenants"]["users"][0],
                case["tenants"]["orgs"][0],
                ROLE_SCOPES["bidder"],
                "bidder",
            )
            await set_actor_context(session, actor)
            session.add(
                ScoreRubricDecision(
                    id=uuid4(),
                    org_id=actor.org_id,
                    task_id=UUID(case["task"]),
                    rubric_id=UUID(report["rubric"]["id"]),
                    section_id=None,
                    item_id=UUID(report["items"][0]["id"]),
                    revision=report["items"][0]["revision"] + 1,
                    set_revision=report["rubric"]["revision"] + 1,
                    action="confirm",
                    reason="\n\t  ",
                    reason_sha256=cards.quote_hash("\n\t  "),
                    expected_input_hash=report["rubric"]["input_hash"],
                    decided_by=actor.user_id,
                    actor_kind="session",
                )
            )
            await session.flush()
    assert failure.value.orig.sqlstate == "23514"
