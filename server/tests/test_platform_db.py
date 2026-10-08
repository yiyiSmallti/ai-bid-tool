"""Aggregate-only cross-org access for the platform console, checked against real PostgreSQL."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.core.db import Database
from app.models.entities import Document, Job, Task, UsageRecord, VendorCall
from app.schemas.budget_contracts import BudgetCallQuote
from app.services.auth import ROLE_SCOPES
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from task_fixtures import actor_context, finish_scope, seed_task

FUNCTIONS = {
    "platform_org_summaries",
    "platform_usage_summary",
    "platform_create_org",
    "platform_set_org_active",
    "platform_adjust_balance",
    "redeem_card",
    "user_org_memberships",
}


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
            ("org_balances", "SELECT"),
            ("orgs", "SELECT"),
            ("usage_records", "SELECT"),
        ]
        functions = connection.execute(
            text(
                "SELECT p.proname, pg_get_userbyid(p.proowner), p.prosecdef, "
                "has_function_privilege('bid_app', p.oid, 'EXECUTE'), "
                "has_function_privilege('public', p.oid, 'EXECUTE') "
                "FROM pg_proc p WHERE p.proname LIKE 'platform_%' AND p.proname <> 'platform_cards_final_status' AND p.proname NOT LIKE 'platform_credential_%' AND p.proname NOT LIKE 'platform_operator_%' AND p.proname NOT LIKE 'platform_trust_anchor_%' "
                "OR p.proname IN ('redeem_card', 'user_org_memberships')"
            )
        ).all()
        assert {f[0] for f in functions} == FUNCTIONS
        assert all(f[1:] == ("bid_platform_fn", True, True, False) for f in functions)


def seed_usage(admin_engine, tenants):
    rows = [
        # org A: two platform calls in October, one unpriced env call, one test-only call
        (0, datetime(2026, 10, 1, 0, 0, tzinfo=UTC), "opus", 0.5, 1.0, False),
        (0, datetime(2026, 10, 31, 23, 59, tzinfo=UTC), "opus", 0.25, 0.5, False),
        (0, datetime(2026, 10, 15, tzinfo=UTC), None, None, None, False),
        (0, datetime(2026, 10, 15, tzinfo=UTC), "opus", 9.0, 9.0, True),
        # org B: one call in November, outside an October-only window
        (1, datetime(2026, 11, 1, 0, 0, tzinfo=UTC), "opus", 0.1, 0.2, False),
    ]
    with Session(admin_engine) as session, session.begin():
        tasks = []
        for index, org in enumerate(tenants["orgs"]):
            task = Task(
                id=uuid4(), org_id=org, created_by=tenants["users"][index], name="Synthetic"
            )
            seed_task(session, task)
            finish_scope(session)
            tasks.append(task)
        session.flush()
        documents = []
        for task in tasks:
            actor_context(session, task.org_id, task.created_by)
            document = Document(
                id=uuid4(),
                org_id=task.org_id,
                task_id=task.id,
                name="synthetic.pdf",
                sha256="a" * 64,
                storage_key=f"org/{task.org_id}/synthetic.pdf",
                media_type="application/pdf",
            )
            session.add(document)
            documents.append(document)
            finish_scope(session)
        session.flush()
        for index, created, model_id, usd, charge, test_only in rows:
            org, user, run_id = tenants["orgs"][index], tenants["users"][index], uuid4()
            actor_context(session, org, user)
            quote = BudgetCallQuote(
                capability="llm",
                payer="org_platform" if model_id else "org_direct",
                provider="anthropic",
                model="claude-opus-5-5",
                version="test",
                platform_model_id=model_id,
                price_revision="synthetic-v1",
                request_sha256="a" * 64,
                currency="USD",
                reserved_charge=Decimal(str(charge or 0)),
                reserved_task_amount=Decimal(str(charge)) if model_id else None,
                vendor_usd_upper_bound=Decimal(str(usd)) if usd is not None else None,
                unknown_reason=None if model_id else "missing_price",
            )
            job = Job(
                id=uuid4(),
                org_id=org,
                task_id=tasks[index].id,
                document_id=documents[index].id,
                kind="extract",
                cache_key=uuid4().hex,
                run_id=run_id,
                actor_user_id=user,
                actor_kind="session",
                actor_scopes=sorted(ROLE_SCOPES["admin"]),
            )
            session.add(job)
            session.flush()
            call = VendorCall(
                id=uuid4(),
                org_id=org,
                task_id=tasks[index].id,
                job_id=job.id,
                run_id=run_id,
                budget_revision=1,
                capability=quote.capability,
                payer=quote.payer,
                currency=quote.currency,
                price_revision=quote.price_revision,
                request_sha256=quote.request_sha256,
                reserved_charge=quote.reserved_charge,
                reserved_task_amount=quote.reserved_task_amount,
                quote=quote.model_dump(mode="json"),
            )
            session.add(call)
            session.flush()
            session.add(
                UsageRecord(
                    org_id=org,
                    task_id=tasks[index].id,
                    job_id=job.id,
                    run_id=run_id,
                    call_id=call.id,
                    provider="anthropic",
                    model="claude-opus-5-5",
                    version="test",
                    duration_ms=1,
                    tokens=1000,
                    input_tokens=800,
                    output_tokens=200,
                    usd=usd,
                    charge=charge,
                    platform_model_id=model_id,
                    test_only=test_only,
                    created_at=created,
                    capability=quote.capability,
                    payer=quote.payer,
                    billing_currency=quote.currency,
                    price_revision=quote.price_revision,
                    task_amount=quote.reserved_task_amount,
                )
            )
            session.flush()
            call.state, call.charge = "completed", Decimal(str(charge or 0))
            finish_scope(session)


async def test_usage_summary_aggregates_without_business_columns(runtime, admin_engine, tenants):
    seed_usage(admin_engine, tenants)
    async with runtime.transaction() as session:
        result = await session.execute(
            text("SELECT * FROM platform_usage_summary(:a, :b)"),
            {"a": date(2026, 10, 1), "b": date(2026, 10, 1)},
        )
        assert list(result.keys()) == [
            "org_id", "month", "billing", "provider", "model", "calls", "tokens",
            "input_tokens", "output_tokens", "ocr_pages", "vendor_usd", "unpriced_calls",
            "charge",
        ]  # fmt: skip
        rows = sorted((tuple(r) for r in result.all()), key=lambda r: r[2])
        org_a = tenants["orgs"][0]
        assert [
            (r[0], r[1], r[2], r[5], r[6], float(r[10]), r[11], float(r[12])) for r in rows
        ] == [
            (org_a, date(2026, 10, 1), "platform", 2, 2000, 0.75, 0, 1.5),
            (org_a, date(2026, 10, 1), "unbilled", 1, 1000, 0.0, 1, 0.0),
        ]
        november = await session.execute(
            text("SELECT org_id FROM platform_usage_summary('2026-11-01', '2026-11-30')")
        )
        assert [r[0] for r in november] == [tenants["orgs"][1]]
        # The function's cross-org view never reaches bid_app's own queries.
        assert await session.scalar(text("SELECT count(*) FROM usage_records")) == 0
        assert await session.scalar(text("SELECT count(*) FROM orgs")) == 0


async def test_org_summaries_and_status_changes(runtime, tenants):
    async with runtime.transaction() as session:
        rows = (
            (await session.execute(text("SELECT * FROM platform_org_summaries()"))).mappings().all()
        )
        assert [set(r) for r in rows][0] == {
            "id", "name", "active", "created_at", "member_count", "admin_emails", "currency", "balance"
        }  # fmt: skip
        # Both fixture orgs share a creation timestamp, so compare without order.
        assert {(r["id"], r["member_count"], tuple(r["admin_emails"])) for r in rows} == {
            (tenants["orgs"][0], 1, ("a@example.test",)),
            (tenants["orgs"][1], 1, ("b@example.test",)),
        }
        changed = await session.scalar(
            text("SELECT platform_set_org_active(:org, false)"), {"org": tenants["orgs"][1]}
        )
        assert changed is True
        assert await session.scalar(text("SELECT current_setting('app.current_org', true)")) == ""
        missing = await session.scalar(
            text("SELECT platform_set_org_active(:org, false)"), {"org": uuid4()}
        )
        assert missing is False
    async with runtime.transaction() as session:
        states = await session.execute(text("SELECT id, active FROM platform_org_summaries()"))
        assert dict(states.all())[tenants["orgs"][1]] is False


async def test_create_org_reuses_existing_identity_and_rejects_bad_input(runtime, tenants):
    async with runtime.transaction() as session:
        created = (
            await session.execute(
                text(
                    "SELECT * FROM platform_create_org('Synthetic new org', 'NEW@example.test', '!setup')"
                )
            )
        ).one()
        assert created.user_created is True
        reused = (
            await session.execute(
                text(
                    "SELECT * FROM platform_create_org('Second org', ' a@example.test ', '!unused')"
                )
            )
        ).one()
        assert reused.user_created is False and reused.admin_user_id == tenants["users"][0]
        assert await session.scalar(text("SELECT current_setting('app.current_org', true)")) == ""
        emails = await session.execute(
            text(
                "SELECT name, admin_emails FROM platform_org_summaries() WHERE name <> 'Synthetic tenant A' AND name <> 'Synthetic tenant B' ORDER BY name"
            )
        )
        assert [tuple(r) for r in emails] == [
            ("Second org", ["a@example.test"]),
            ("Synthetic new org", ["new@example.test"]),
        ]
    for name, email in (("   ", "x@example.test"), ("Valid", "not-an-email")):
        with pytest.raises(DBAPIError):
            async with runtime.transaction() as session:
                await session.execute(
                    text("SELECT * FROM platform_create_org(:n, :e, '!setup')"),
                    {"n": name, "e": email},
                )


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
