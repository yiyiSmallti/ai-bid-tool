"""Fixed-scale PostgreSQL/API acceptance for local model metadata reads.

Failure inventory: full-history materialization, ciphertext reads, unbounded
response bytes, full history scans, mismatched continuation and excessive warm
latency. Seed 100,000 immutable revisions in one installation; runtime queries
use the restricted role with guards enabled. No service lifecycle operations.
"""

import json
import math
import os
import platform
from pathlib import Path
from time import perf_counter

import pytest
from sqlalchemy import event, text
from test_management_features_scale import plan_work

REVISIONS = 100_000
SAMPLES = 20
BASE = "/v5/management/providers"
OUTPUT = Path(__file__).resolve().parents[2] / "data/work/management-pages-validation/models/scale"


def seed_scale(admin_engine):
    with admin_engine.begin() as connection:
        # Owner fixture seeding restores revision guards before runtime reads.
        connection.execute(
            text("ALTER TABLE provider_configs DISABLE TRIGGER provider_revision_gate")
        )
        connection.execute(
            text(
                "INSERT INTO provider_configs(id,capability,revision,data,encrypted_key,key_last4,created_at) "
                "SELECT md5('provider|'||n)::uuid,'llm_extract',n,"
                "jsonb_build_object('provider','openai','model','Saved scale model','base_url',NULL,'json_mode','json_schema','reasoning','[]'::jsonb,'default_reasoning',NULL),"
                "'synthetic-scale-ciphertext','xxxx',TIMESTAMPTZ '2026-01-01T00:00:00Z'+n*interval '1 second' FROM generate_series(1,:revisions) n"
            ),
            {"revisions": REVISIONS},
        )
        assert connection.scalar(text("SELECT count(*) FROM provider_configs")) == REVISIONS
        connection.execute(
            text("ALTER TABLE provider_configs ENABLE TRIGGER provider_revision_gate")
        )
        connection.execute(text("ANALYZE provider_configs"))


@pytest.mark.latency
async def test_fixed_scale_provider_history_and_exact_reads(
    api, headers, application, database, admin_engine
):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    receipt = {
        "mode": "real_api",
        "scenario": "u01-model-settings-fixed-scale-v1",
        "schema": "5.0",
        "status": "running",
        "fixture": {"config_revisions": REVISIONS},
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
        seed_scale(admin_engine)
        first_history = (await api.post(BASE + "/history/query", headers=headers, json={})).json()
        current = first_history["items"][0]["id"]
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
        ]
        for name, method, path, body, maximum_ms, maximum_reads, maximum_bytes in cases:
            warm = await api.request(method, path, headers=headers, json=body)
            assert warm.status_code == 200, warm.text
            timings, sizes, reads, last_queries = [], [], [], []
            for _ in range(SAMPLES):
                captured.clear()
                started = perf_counter()
                response = await api.request(method, path, headers=headers, json=body)
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
                assert "encrypted_key" not in response.text and "key_last4" not in response.text
            plans = []
            async with application.state.db.transaction() as session:
                connection = await session.connection()
                for statement, parameters in last_queries:
                    if not statement.lstrip().upper().startswith("SELECT") or not any(
                        table in statement for table in ("provider_configs",)
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
                    plans.append(
                        {
                            "sql": statement,
                            "plan": plan,
                            "work": work,
                            "history_rows_visited": history_visits,
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
