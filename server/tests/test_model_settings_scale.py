"""Fixed-scale serial PostgreSQL/API acceptance for model metadata reads.

Failure inventory: all-history materialization, per-revision usage or author
queries, ciphertext reads, unbounded response bytes, full audit/history scans,
foreign continuation, catalog fallback and warm p95 beyond the contract. Provider
configuration has one sequence per org; seed 100,000 revisions in each of two
orgs and a separate global 10,000-choice catalog. Runtime measurements use the
real restricted tenant role with guards enabled. No service lifecycle operations.
"""

import json
import math
import os
import platform
from pathlib import Path
from time import perf_counter
from uuid import UUID

import pytest
from conftest import seed_platform_credential
from sqlalchemy import event, text
from test_management_features_scale import plan_work

REVISIONS = 100_000
CATALOG = 10_000
SAMPLES = 20
BASE = "/v4/management/providers"
OUTPUT = Path(__file__).resolve().parents[2] / "data/work/management-pages-validation/models/scale"


def seed_scale(admin_engine, tenants):
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO platform_models(id,capability,provider,model,base_url,credential,vendor_input_usd_per_mtok,vendor_output_usd_per_mtok,sale_input_per_mtok,sale_output_per_mtok,is_default,enabled,reasoning,revision,updated_by) SELECT 'scale-'||lpad(n::text,5,'0'),'llm_extract','openai','Scale model '||n,'https://scale.example.test/v1','model_settings_scale',1,2,3,4,false,true,'[]'::jsonb,1,'fixture@example.test' FROM generate_series(1,:catalog) n"
            ),
            {"catalog": CATALOG},
        )
        # Only owner-side synthetic fixture bulk insertion bypasses sequential
        # guards; the transaction restores them before any runtime measurement.
        connection.execute(
            text("ALTER TABLE provider_configs DISABLE TRIGGER provider_revision_gate")
        )
        for org, user in zip(tenants["orgs"], tenants["users"], strict=True):
            values = {"org": str(org), "actor": user, "revisions": REVISIONS}
            connection.execute(text("SELECT set_config('app.current_org',:org,true)"), values)
            connection.execute(
                text(
                    "INSERT INTO provider_configs(id,org_id,capability,revision,source,platform_model_id,data,updated_by,created_at) SELECT md5(:org||'|provider|'||n)::uuid,CAST(:org AS uuid),'llm_extract',n,'platform','scale-00001',jsonb_build_object('provider','openai','model','Saved scale model','reasoning','[]'::jsonb,'default_reasoning',NULL,'catalog_revision',1,'sale_input_per_mtok',3,'sale_output_per_mtok',4),:actor,TIMESTAMPTZ '2026-01-01T00:00:00Z'+n*interval '1 second' FROM generate_series(1,:revisions) n"
                ),
                values,
            )
            connection.execute(
                text(
                    "INSERT INTO audit_logs(id,org_id,actor_user_id,action,object_id,details,created_at) SELECT md5(:org||'|provider-audit|'||n)::uuid,CAST(:org AS uuid),:actor,'provider.set',md5(:org||'|provider|'||n)::uuid,jsonb_build_object('revision',n,'source','platform','platform_model_id','scale-00001'),TIMESTAMPTZ '2026-01-01T00:00:00Z'+n*interval '1 second' FROM generate_series(1,:revisions) n"
                ),
                values,
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM provider_configs WHERE org_id=CAST(:org AS uuid)"),
                    values,
                )
                == REVISIONS
            )
        connection.execute(
            text("ALTER TABLE provider_configs ENABLE TRIGGER provider_revision_gate")
        )
        for table in ("provider_configs", "platform_models", "audit_logs", "memberships"):
            connection.execute(text(f"ANALYZE {table}"))


@pytest.mark.latency
async def test_fixed_scale_provider_history_catalog_and_exact_reads(
    api, headers, application, tenants, admin_engine
):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    receipt = {
        "mode": "real_api",
        "scenario": "u01-model-settings-fixed-scale-v1",
        "schema": "4.0",
        "status": "running",
        "fixture": {"orgs": 2, "config_revisions_per_org": REVISIONS, "catalog_choices": CATALOG},
        "hardware": {
            "system": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "logical_cpus": os.cpu_count(),
        },
        "rerun": ".venv/bin/pytest -q server/tests/test_model_settings_scale.py -m latency --basetemp=data/work/management-pages-validation/models/scale/tmp",
        "measurements": [],
    }
    with admin_engine.connect() as connection:
        receipt["postgresql"] = connection.scalar(text("SELECT version()"))
    captured = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        captured.append((statement, parameters))

    engine = application.state.db.engine
    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    failures = []
    try:
        await seed_platform_credential(
            application.state.processor.settings,
            name="model_settings_scale",
            provider="openai",
            endpoint="https://scale.example.test/v1",
        )
        seed_scale(admin_engine, tenants)
        first_history = (
            await api.post(BASE + "/history/query", headers=headers[0], json={})
        ).json()
        first_catalog = (
            await api.post(BASE + "/catalog/query", headers=headers[0], json={})
        ).json()
        current = first_history["items"][0]["id"]
        old = (await api.get(BASE, headers=headers[1])).json()["data"]["current"]["id"]
        assert (await api.get(f"{BASE}/revisions/{old}", headers=headers[0])).status_code == 404
        assert (
            await api.post(
                BASE + "/history/query",
                headers=headers[1],
                json={"cursor": first_history["data"]["next_cursor"]},
            )
        ).status_code == 400
        cases = [
            ("settings", "GET", BASE, None, 750, 8, 1024 * 1024),
            ("revision", "GET", f"{BASE}/revisions/{current}", None, 750, 8, 1024 * 1024),
            ("history", "POST", BASE + "/history/query", {}, 750, 6, 256 * 1024),
            ("history_100", "POST", BASE + "/history/query", {"limit": 100}, 750, 6, 256 * 1024),
            (
                "history_next",
                "POST",
                BASE + "/history/query",
                {"cursor": first_history["data"]["next_cursor"]},
                750,
                6,
                256 * 1024,
            ),
            ("catalog", "POST", BASE + "/catalog/query", {}, 500, 6, 256 * 1024),
            (
                "catalog_next",
                "POST",
                BASE + "/catalog/query",
                {"cursor": first_catalog["data"]["next_cursor"]},
                500,
                6,
                256 * 1024,
            ),
            (
                "catalog_prefix",
                "POST",
                BASE + "/catalog/query",
                {"q": "SCALE-099"},
                500,
                6,
                256 * 1024,
            ),
            ("catalog_empty", "POST", BASE + "/catalog/query", {"q": "absent"}, 500, 6, 256 * 1024),
        ]
        for name, method, path, body, maximum_ms, maximum_reads, maximum_bytes in cases:
            warm = await api.request(method, path, headers=headers[0], json=body)
            assert warm.status_code == 200, warm.text
            timings, sizes, reads, last_queries = [], [], [], []
            for _ in range(SAMPLES):
                captured.clear()
                started = perf_counter()
                response = await api.request(method, path, headers=headers[0], json=body)
                timings.append((perf_counter() - started) * 1000)
                assert response.status_code == 200, response.text
                sizes.append(len(response.content))
                last_queries = list(captured)
                reads.append(
                    sum(
                        statement.lstrip().upper().startswith("SELECT")
                        and "set_config(" not in statement.lower()
                        for statement, _ in last_queries
                    )
                )
                result = response.json()
                assert set(result) == {
                    "ok",
                    "command",
                    "data",
                    "items",
                    "warnings",
                    "cost",
                    "duration_ms",
                }
                if method == "POST":
                    assert len(result["items"]) <= body.get("limit", 25)
                    assert result["data"]["returned"] == len(result["items"])
                    assert "total" not in result["data"]
                    if "history" in name:
                        assert all(
                            item["org_id"] == headers[0]["X-Org-Id"] for item in result["items"]
                        )
                    if name == "catalog_prefix":
                        assert all(item["id"].startswith("scale-099") for item in result["items"])
                assert "encrypted_key" not in response.text and "key_last4" not in response.text
            plans = []
            async with application.state.db.transaction(UUID(headers[0]["X-Org-Id"])) as session:
                connection = await session.connection()
                for statement, parameters in last_queries:
                    if not statement.lstrip().upper().startswith("SELECT") or not any(
                        table in statement
                        for table in ("provider_configs", "platform_models", "audit_logs")
                    ):
                        continue
                    explained = await connection.exec_driver_sql(
                        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, parameters
                    )
                    plan = explained.scalar_one()
                    work = plan_work(plan[0]["Plan"])
                    history_visits = sum(
                        (node["rows"] + node["rows_removed"]) * node["loops"]
                        for node in work
                        if node["relation"] in {"provider_configs", "audit_logs"}
                    )
                    if history_visits > 1000:
                        failures.append(f"{name}: visited {history_visits} history/audit rows")
                    catalog_visits = sum(
                        (node["rows"] + node["rows_removed"]) * node["loops"]
                        for node in work
                        if node["relation"] == "platform_models"
                    )
                    if catalog_visits > 1000:
                        failures.append(f"{name}: visited {catalog_visits} catalog rows")
                    plans.append(
                        {
                            "sql": statement,
                            "plan": plan,
                            "work": work,
                            "history_rows_visited": history_visits,
                            "catalog_rows_visited": catalog_visits,
                        }
                    )
            assert plans
            p95 = sorted(timings)[math.ceil(SAMPLES * 0.95) - 1]
            receipt["measurements"].append(
                {
                    "case": name,
                    "samples": SAMPLES,
                    "p95_ms": round(p95, 3),
                    "max_result_bytes": max(sizes),
                    "max_read_statements_including_authority": max(reads),
                    "plans": plans,
                }
            )
            if p95 > maximum_ms:
                failures.append(f"{name}: p95 {p95:.1f} ms exceeds {maximum_ms} ms")
            if max(sizes) > maximum_bytes:
                failures.append(f"{name}: response exceeds {maximum_bytes} bytes")
            if max(reads) > maximum_reads:
                failures.append(f"{name}: read count exceeds {maximum_reads}")
        receipt["status"] = "failed" if failures else "passed"
        receipt["failures"] = failures
    except BaseException as error:
        receipt["status"] = "failed"
        receipt["failures"] = [*failures, f"Acceptance raised {type(error).__name__}"]
        raise
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
        (OUTPUT / "result.json").write_text(
            json.dumps(receipt, indent=2, ensure_ascii=False) + "\n"
        )
    assert not failures, "\n".join(failures)
