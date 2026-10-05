"""Real PostgreSQL report boundaries, exercised with API-approved rubric and draft.

Failure inventory recorded before implementation: missing/no tenant context,
foreign tenant/task/draft/rubric parents, stale/expired/cancelled attempts, unconfirmed
or reopened rubric, changed draft, mutable history, late children, forged supporting
rows/quotes, incomplete coverage, and a total hiding unassessable items.
"""

import json
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.models.entities import Job
from app.models.response_cards import DraftRun, ResponseItem
from app.models.score import (
    ScoreItemCitation,
    ScoreReport,
    ScoreReportItem,
    ScoreReportItemResponse,
)
from app.services.auth import ROLE_SCOPES, Identity, set_actor_context
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from test_check import publish_draft
from test_response_cards import create_card, require_action
from test_score_api import rubric_case as rubric_case
from test_score_api import rubric_input_case as rubric_input_case
from test_score_review import base, confirm_contents, decision, role, show

TABLES = (
    "score_reports",
    "score_report_items",
    "score_report_item_responses",
    "score_item_citations",
)


@pytest.fixture
async def score_storage_case(rubric_case):
    case = rubric_case
    report = await confirm_contents(case)
    confirmed = await case["api"].post(
        base(case) + "/decisions",
        headers=case["header"],
        json=decision(report, report["rubric"]),
    )
    assert confirmed.status_code == 200, confirmed.text
    role(case, "technical")
    card = await create_card(
        case["api"],
        case["header"],
        case["task"],
        case["extraction"],
        case["requirements"][2],
        {
            "response_kind": "commitment",
            "response_text": "We commit to delivery within thirty calendar days.",
            "deviation": "none",
            "deviation_note": "Confirmed delivery commitment.",
            "evidence": [],
        },
    )
    card = await require_action(case["api"], case["header"], card, "submit")
    card = await require_action(
        case["api"], case["header"], card, "confirm", reviewed_evidence_ids=[]
    )
    draft = await publish_draft(
        case["api"], case["app"], case["header"], case["task"], case["extraction"]
    )
    return {
        **case,
        "confirmed": await show(case),
        "draft_id": UUID(draft["draft_id"]),
        "support_card": card,
    }


async def pending_score(session, case, *, change=None, score_value=5, deduction_reasons=None):
    org, user = case["tenants"]["orgs"][0], case["tenants"]["users"][0]
    await set_actor_context(
        session, Identity(user, org, ROLE_SCOPES["technical"], "technical", actor_kind="worker")
    )
    rubric = case["confirmed"]["rubric"]
    draft = await session.get(DraftRun, case["draft_id"])
    manifest = dict(
        org_id=str(org),
        task_id=case["task"],
        draft_id=str(draft.id),
        extraction_job_id=case["extraction"],
        document_id=case["document"],
        draft_input_hash=draft.input_hash,
        rubric_id=rubric["id"],
        rubric_version=rubric["version"],
        rubric_input_hash=rubric["input_hash"],
        rubric_revision=rubric["revision"],
        assessment_date="2026-10-05",
        prompt_version="score-test",
        schema_version="score-test",
        scoring_rule_version="score-test",
        model_redaction_enabled=True,
    )
    report = ScoreReport(
        id=uuid4(),
        org_id=org,
        task_id=UUID(case["task"]),
        job_id=uuid4(),
        run_id=uuid4(),
        draft_id=draft.id,
        extraction_job_id=UUID(case["extraction"]),
        document_id=UUID(case["document"]),
        rubric_id=UUID(rubric["id"]),
        rubric_version=rubric["version"],
        rubric_revision=rubric["revision"],
        rubric_input_hash=rubric["input_hash"],
        input_hash="a" * 64,
        draft_input_hash=draft.input_hash,
        assessment_date=date(2026, 10, 5),
        prompt_version="score-test",
        schema_version="score-test",
        scoring_rule_version="score-test",
        input_manifest=manifest,
        encrypted_input="synthetic-ciphertext",
        completion="complete",
        limitations=[],
        summary={
            "assessed_items": 1,
            "unassessable_items": 0,
            "assessed_subtotal": "5",
            "total_status": "estimated",
            "estimated_total": "5",
        },
        sections=[
            {
                "section_key": case["confirmed"]["sections"][0]["key"],
                "assessed_items": 1,
                "unassessable_items": 0,
                "status": "estimated",
                "estimated_score": "5",
            }
        ],
        actor_user_id=user,
        actor_token_id=None,
        actor_kind="worker",
    )
    job = Job(
        id=report.job_id,
        org_id=org,
        task_id=report.task_id,
        document_id=report.document_id,
        kind="score",
        status="running",
        run_id=report.run_id,
        cache_key=uuid4().hex * 2,
        lease_until=datetime.now(UTC) + timedelta(minutes=5),
        result={
            "submission": {
                "input_hash": report.input_hash,
                "input_manifest": manifest,
                "encrypted_input": report.encrypted_input,
            }
        },
    )
    if change:
        change(report, job)
    session.add(job)
    await session.flush()
    session.add(report)
    await session.flush()
    item_view = case["confirmed"]["items"][0]
    anchor = await session.scalar(
        select(ResponseItem).where(
            ResponseItem.draft_id == draft.id,
            ResponseItem.requirement_id == UUID(item_view["requirement_id"]),
        )
    )
    support = await session.scalar(
        select(ResponseItem).where(ResponseItem.draft_id == draft.id, ResponseItem.kind == "row")
    )
    item = ScoreReportItem(
        id=uuid4(),
        org_id=org,
        task_id=report.task_id,
        report_id=report.id,
        draft_id=draft.id,
        extraction_job_id=report.extraction_job_id,
        rubric_id=report.rubric_id,
        rubric_item_id=UUID(item_view["id"]),
        section_id=UUID(item_view["section_id"]),
        requirement_id=anchor.requirement_id,
        anchor_response_item_id=anchor.id,
        anchor_partition="gap",
        outcome="assessed",
        score_range=item_view["score_range"],
        estimated_score=score_value,
        reason_code="supported",
        reason="Synthetic score supported by a confirmed response.",
        deduction_reasons=deduction_reasons or [],
        strengthening_actions=[],
    )
    session.add(item)
    await session.flush()
    session.add(
        ScoreReportItemResponse(
            org_id=org,
            task_id=report.task_id,
            report_id=report.id,
            score_item_id=item.id,
            draft_id=draft.id,
            response_item_id=support.id,
            requirement_id=support.requirement_id,
            card_revision_id=support.card_revision_id,
        )
    )
    await session.flush()
    source = item_view["source"]
    session.add_all(
        [
            ScoreItemCitation(
                org_id=org,
                task_id=report.task_id,
                report_id=report.id,
                score_item_id=item.id,
                kind="tender",
                quote=source["quote"],
                document_id=UUID(source["document_id"]),
                chunk_id=UUID(source["chunk_id"]),
                source=source,
            ),
            ScoreItemCitation(
                org_id=org,
                task_id=report.task_id,
                report_id=report.id,
                score_item_id=item.id,
                kind="draft",
                quote=support.response_text,
                draft_id=draft.id,
                response_item_id=support.id,
                card_revision_id=support.card_revision_id,
                field="response_text",
            ),
        ]
    )
    await session.flush()
    return report


@pytest.mark.parametrize("table", TABLES)
def test_score_tables_force_rls_and_required_context(tenants, admin_engine, table):
    with admin_engine.connect() as connection:
        assert connection.execute(
            text("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname=:table"),
            {"table": table},
        ).one() == (True, True)
        assert set(
            connection.execute(
                text(
                    "SELECT column_name,is_nullable FROM information_schema.columns WHERE table_name=:table AND column_name IN ('org_id','task_id')"
                ),
                {"table": table},
            ).all()
        ) == {("org_id", "NO"), ("task_id", "NO")}
        connection.execute(text("SET LOCAL ROLE bid_app"))
        connection.execute(text("SELECT set_config('app.current_org','',true)"))
        assert connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one() == 0


async def test_score_report_rows_isolated_immutable_and_closed(score_storage_case):
    case = score_storage_case
    org = case["tenants"]["orgs"][0]
    async with case["app"].state.db.transaction(org) as session:
        report = await pending_score(session, case)
        report_id = report.id
    for table in TABLES:
        with case["admin_engine"].connect() as connection:
            rows = (
                connection.execute(
                    text(
                        f'SELECT to_jsonb(r) FROM "{table}" r WHERE '
                        + ("id" if table == "score_reports" else "report_id")
                        + "=:id"
                    ),
                    {"id": report_id},
                )
                .scalars()
                .all()
            )
            assert rows, table
            connection.execute(text("SET LOCAL ROLE bid_app"))
            for tenant in ("", str(case["tenants"]["orgs"][1])):
                connection.execute(
                    text("SELECT set_config('app.current_org',:org,true)"), {"org": tenant}
                )
                assert connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one() == 0
        for operation in ("UPDATE", "DELETE", "INSERT"):
            with pytest.raises(DBAPIError), case["admin_engine"].begin() as connection:
                connection.execute(text("SET LOCAL ROLE bid_app"))
                connection.execute(
                    text("SELECT set_config('app.current_org',:org,true)"), {"org": str(org)}
                )
                if operation == "UPDATE":
                    connection.execute(text(f'UPDATE "{table}" SET id=id'))
                elif operation == "DELETE":
                    connection.execute(text(f'DELETE FROM "{table}"'))
                else:
                    payload = {**rows[0], "id": str(uuid4())}
                    connection.execute(
                        text(
                            f'INSERT INTO "{table}" SELECT (jsonb_populate_record(NULL::"{table}",CAST(:payload AS jsonb))).*'
                        ),
                        {"payload": json.dumps(payload)},
                    )
        with pytest.raises(DBAPIError), case["admin_engine"].begin() as connection:
            connection.execute(text("SET LOCAL ROLE bid_app"))
            connection.execute(
                text("SELECT set_config('app.current_org',:org,true)"), {"org": str(org)}
            )
            payload = {**rows[0], "id": str(uuid4()), "org_id": str(case["tenants"]["orgs"][1])}
            connection.execute(
                text(
                    f'INSERT INTO "{table}" SELECT (jsonb_populate_record(NULL::"{table}",CAST(:payload AS jsonb))).*'
                ),
                {"payload": json.dumps(payload)},
            )


@pytest.mark.parametrize(
    "mutation",
    ["expired", "run_id", "cancelled", "rubric_revision", "draft_hash", "hidden_partial"],
)
async def test_score_publication_attempt_and_snapshot_fences(score_storage_case, mutation):
    def change(report, job):
        if mutation == "expired":
            job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        elif mutation == "run_id":
            job.run_id = uuid4()
        elif mutation == "cancelled":
            job.status = "cancelled"
        elif mutation == "rubric_revision":
            report.rubric_revision += 1
        elif mutation == "draft_hash":
            report.draft_input_hash = "f" * 64
        elif mutation == "hidden_partial":
            report.summary = {**report.summary, "unassessable_items": 1}

    case = score_storage_case
    with pytest.raises(DBAPIError):
        async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
            await pending_score(session, case, change=change)


@pytest.mark.parametrize("table", TABLES)
async def test_score_composite_fk_rejects_cross_task(score_storage_case, table):
    from task_fixtures import related_task

    case = score_storage_case
    org = case["tenants"]["orgs"][0]
    other_task = uuid4()
    async with case["app"].state.db.transaction(org) as session:
        await related_task(
            session,
            org,
            case["tenants"]["users"][0],
            case["task"],
            name="Other synthetic scoring task",
            task_id=other_task,
        )
        await pending_score(session, case)
    # Both tasks exist in the same tenant. Disable only the append-only user trigger
    # for this rolled-back transaction so the composite parent FK itself is tested.
    with pytest.raises(IntegrityError) as error, case["admin_engine"].begin() as connection:
        connection.execute(text(f'ALTER TABLE "{table}" DISABLE TRIGGER USER'))
        connection.execute(
            text(f'UPDATE "{table}" SET task_id=:task WHERE org_id=:org'),
            {"task": other_task, "org": org},
        )
    assert error.value.orig.sqlstate == "23503"


@pytest.mark.parametrize("changed_input", ["rubric_reopened", "card_reopened"])
async def test_score_rejects_changed_confirmations(score_storage_case, changed_input):
    case = score_storage_case
    if changed_input == "rubric_reopened":
        role(case, "bidder")
        response = await case["api"].post(
            base(case) + "/decisions",
            headers=case["header"],
            json=decision(case["confirmed"], case["confirmed"]["rubric"], "reopen"),
        )
        assert response.status_code == 200, response.text
        role(case, "technical")
    else:
        await require_action(
            case["api"],
            case["header"],
            case["support_card"],
            "reopen",
            reason="Synthetic current-draft invalidation.",
        )
    with pytest.raises(DBAPIError):
        async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
            await pending_score(session, case)


@pytest.mark.parametrize(
    "mutation", ["invented_quote", "foreign_response", "wrong_revision", "wrong_task"]
)
async def test_score_rejects_forged_draft_citation(score_storage_case, mutation):
    case = score_storage_case
    with pytest.raises(DBAPIError):
        async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
            report = await pending_score(session, case)
            original = await session.scalar(
                select(ScoreItemCitation).where(
                    ScoreItemCitation.report_id == report.id, ScoreItemCitation.kind == "draft"
                )
            )
            values = {
                column.name: getattr(original, column.name)
                for column in ScoreItemCitation.__table__.columns
            }
            values["id"] = uuid4()
            if mutation == "invented_quote":
                values["quote"] = "This quote does not exist in the confirmed response."
            elif mutation == "foreign_response":
                values["response_item_id"] = uuid4()
            elif mutation == "wrong_revision":
                values["card_revision_id"] = uuid4()
            else:
                values["task_id"] = uuid4()
            session.add(ScoreItemCitation(**values))
            await session.flush()


async def test_score_deferred_publish_rechecks_worker_attempt(score_storage_case):
    case = score_storage_case
    with pytest.raises(DBAPIError):
        async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
            report = await pending_score(session, case)
            job = await session.get(Job, report.job_id)
            job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
            await session.flush()


@pytest.mark.parametrize(
    "score_value,deductions", [(-1, ["Deduction"]), (6, []), ("NaN", []), (4, [])]
)
async def test_score_database_rejects_invalid_numeric_score(
    score_storage_case, score_value, deductions
):
    case = score_storage_case
    with pytest.raises(IntegrityError) as error:
        async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
            await pending_score(
                session, case, score_value=score_value, deduction_reasons=deductions
            )
    assert error.value.orig.sqlstate == "23514"


@pytest.mark.parametrize("score_value", [0, 5])
async def test_score_database_accepts_inclusive_bounds(score_storage_case, score_value):
    case = score_storage_case

    def summary(report, job):
        report.summary = {
            **report.summary,
            "assessed_subtotal": str(score_value),
            "estimated_total": str(score_value),
        }
        report.sections = [{**row, "estimated_score": str(score_value)} for row in report.sections]

    async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
        await pending_score(
            session,
            case,
            score_value=score_value,
            deduction_reasons=["Confirmed support earns only the lower bound."]
            if score_value == 0
            else [],
            change=summary,
        )


@pytest.mark.parametrize("completion", ["partial", "complete"])
async def test_score_no_total_requires_partial_even_when_items_assessed(
    score_storage_case, completion
):
    case = score_storage_case

    def incomplete_provider(report, job):
        report.completion = completion
        report.summary = {**report.summary, "total_status": "range_only", "estimated_total": None}
        report.sections = [
            {**row, "status": "range_only", "estimated_score": None} for row in report.sections
        ]

    async def publish():
        async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
            await pending_score(session, case, change=incomplete_provider)

    if completion == "partial":
        await publish()
    else:
        with pytest.raises(IntegrityError) as error:
            await publish()
        assert error.value.orig.sqlstate == "23514"
