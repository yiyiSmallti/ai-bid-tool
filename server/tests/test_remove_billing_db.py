"""Migration, no-balance provider execution, durable call admission.

Failure cases are enumerated before implementation in
``data/work/remove-billing/failure-modes.md``. These tests use the normal migrated
PostgreSQL fixtures and a fake vendor; they never call a live external service.
"""

import json

import httpx
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.models.entities import UsageRecord, VendorCall
from app.providers.llm import AnthropicExtractor
from conftest import FakeQueue, credential_app
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from test_api import create_document, run_job
from test_job_boundaries import session_for
from test_llm_providers import anthropic_reply, settings_for

REMOVED_TABLES = (
    "org_balances",
    "balance_entries",
    "platform_cards",
    "platform_models",
    "task_budget_revisions",
    "org_balance_notices",
)
REMOVED_COLUMNS = {
    "tasks": {"budget_usd", "budget_limit", "budget_currency", "budget_revision", "budget_state"},
    "usage_records": {
        "usd",
        "charge",
        "task_amount",
        "payer",
        "billing_currency",
        "price_revision",
        "image_price_revision",
    },
    "vendor_calls": {
        "reserved_charge",
        "charge",
        "payer",
        "budget_revision",
        "reserved_task_amount",
        "currency",
        "price_revision",
        "quote",
    },
    "jobs": {"vendor_cost_history_complete"},
    "agent_pauses": {"budget_ref"},
}


def test_upgrade_head_removes_money_preserves_guards(admin_engine):
    # admin_engine calls prepare_database -> alembic upgrade head.
    with admin_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
        )
        tables = set(
            connection.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
        )
        assert not tables.intersection(REMOVED_TABLES)
        for table, removed in REMOVED_COLUMNS.items():
            columns = set(
                connection.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=:table"
                    ),
                    {"table": table},
                )
            )
            assert not columns.intersection(removed), table
        functions = set(
            connection.scalars(
                text(
                    "SELECT proname FROM pg_proc JOIN pg_namespace ON pg_namespace.oid=pronamespace WHERE nspname='public'"
                )
            )
        )
        assert not any(
            name.startswith(("budget_", "task_budget_", "balance_")) for name in functions
        )
        assert not {
            "redeem_card",
            "platform_adjust_balance",
            "platform_cards_final_status",
        }.intersection(functions)
        for table in ("tasks", "vendor_calls", "usage_records", "agent_sessions", "agent_pauses"):
            assert connection.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid=to_regclass(:table)"
                ),
                {"table": f"public.{table}"},
            ).one() == (False, False)
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger WHERE tgrelid='public.usage_records'::regclass AND tgname='provider_usage_gate'"
                )
            )
            == 1
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger WHERE tgrelid='public.vendor_calls'::regclass AND tgname='task_vendor_lifecycle'"
                )
            )
            == 1
        )


async def test_provider_job_without_balance_retains_usage_and_call_guards(
    database, tmp_path, pdf_bytes
):
    requests = []

    def vendor(request):
        requests.append(request)
        return anthropic_reply([])

    settings = settings_for(tmp_path, "anthropic", llm_concurrency=1)
    llm = AnthropicExtractor(settings, httpx.MockTransport(vendor))
    app = await credential_app(settings, llm=llm, queue=FakeQueue())
    async with app.router.lifespan_context(app), session_for(app, database) as (api, header):
        _, document = await create_document(api, header, pdf_bytes)
        _, parsed = await run_job(api, app, header, document, "parse")
        assert parsed["status"] == "succeeded"
        _, result = await run_job(api, app, header, document, "extract")
        assert result["status"] == "succeeded", result
        assert requests
        async with app.state.db.transaction() as session:
            usages = list((await session.scalars(select(UsageRecord))).all())
            assert usages and sum(u.tokens for u in usages) > 0
            calls = list(
                (
                    await session.scalars(
                        select(VendorCall).where(VendorCall.id.in_([u.call_id for u in usages]))
                    )
                ).all()
            )
            assert len(calls) == len(usages) and all(c.state == "completed" for c in calls)
            call = calls[0]
            call_id, job_id = call.id, call.job_id
        # A completed call cannot be reopened, and a finished job cannot admit a new call.
        for statement in (
            "UPDATE vendor_calls SET state='pending' WHERE id=:id",
            "INSERT INTO vendor_calls(id,job_id,run_id,task_id,capability,request_sha256,provider_identity) SELECT gen_random_uuid(),job_id,run_id,task_id,capability,request_sha256,provider_identity FROM vendor_calls WHERE id=:id",
        ):
            try:
                async with app.state.db.transaction() as session:
                    await session.execute(text(statement), {"id": call_id})
            except DBAPIError:
                pass
            else:
                raise AssertionError("Immutable completion or stale lease admission was accepted")
        # Reusing the normal submit entry point must not repeat vendor side effects.
        before = len(requests)
        _, cached = await run_job(api, app, header, document, "extract")
        assert cached["status"] == "succeeded"
        assert len(requests) == before
        (tmp_path / "remove-billing-db.json").write_text(
            json.dumps(
                {
                    "job_id": str(job_id),
                    "call_ids": [str(c.id) for c in calls],
                    "tokens": sum(u.tokens for u in usages),
                    "vendor_requests": len(requests),
                    "completion_immutable": True,
                    "stale_lease_rejected": True,
                },
                indent=2,
            )
        )
