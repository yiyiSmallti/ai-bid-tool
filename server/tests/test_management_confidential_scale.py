"""Fixed-scale confidential PostgreSQL/API acceptance.

Failure inventory: N+1 owner reads, all-history scans, unindexed prefix scans,
cross-org/task mixing, missing continuation, ciphertext reads, excessive payload
or SQL work, and warmed p95 above the approved budget. Seed exactly two orgs
with 10,000 fields and 100,000 encrypted values each. Never start a service.
Retain actual read plans and reproducible receipts only under data/work.
Selective prefixes must drive a C-collated token range, rather than scan all
org tokens or roots and filter afterward; remaining tokens use owner lookups.
"""

import json
import math
import os
import platform
from pathlib import Path
from time import perf_counter

import pytest
from app.core.security import Secrets
from sqlalchemy import event, text
from test_management_confidential import FIELDS, VALUES, assert_page
from test_management_features_scale import plan_work
from test_team_workflow_membership import new_task

ROOTS = 10_000
VERSIONS = 10
SAMPLES = 20
TOKEN_TABLE = "confidential_field_search_tokens"
SELECTIVE_PREFIXES = {"key_prefix", "label_prefix", "values_prefix"}
OUTPUT = (
    Path(__file__).resolve().parents[2] / "data/work/management-pages-validation/confidential-scale"
)


def is_select_statement(statement, context):
    # ORM SELECTs with materialized CTEs start with WITH. Use the executed
    # statement's compilation metadata; retain plain driver/text SELECTs too.
    compiled = getattr(context, "compiled", None)
    query = getattr(compiled, "statement", None)
    return bool(getattr(query, "is_select", False)) or statement.lstrip().upper().startswith(
        "SELECT"
    )


def seed_scale(admin_engine, tenants, tasks, settings):
    ciphertext = Secrets.for_data(settings).encrypt("synthetic-scale-private-value-123456789")
    with admin_engine.begin() as connection:
        for org, user, task in zip(tenants["orgs"], tenants["users"], tasks, strict=True):
            values = {
                "org": str(org),
                "actor": user,
                "task": task,
                "roots": ROOTS,
                "versions": VERSIONS,
                "ciphertext": ciphertext,
            }
            connection.execute(text("SELECT set_config('app.current_org',:org,true)"), values)
            connection.execute(
                text(
                    "INSERT INTO confidential_fields(id,org_id,created_by,key,label,kind,scope,archived,revision,created_at)"
                    " SELECT md5(:org||'|field|'||n)::uuid,CAST(:org AS uuid),:actor,"
                    " 'field_'||lpad(n::text,5,'0'),'Scale confidential '||lpad(n::text,5,'0'),'bank_account',"
                    " CASE WHEN n%2=0 THEN 'task' ELSE 'org' END,n%100=0,1,"
                    " TIMESTAMPTZ '2026-01-01T00:00:00Z'+n*interval '1 second'"
                    " FROM generate_series(1,:roots) n"
                ),
                values,
            )
            connection.execute(
                text(
                    "INSERT INTO confidential_values(id,org_id,created_by,field_id,task_id,version,encrypted_value,tail,created_at)"
                    " SELECT md5(:org||'|value|'||n||':'||v)::uuid,CAST(:org AS uuid),:actor,"
                    " md5(:org||'|field|'||n)::uuid,CASE WHEN n%2=0 THEN CAST(:task AS uuid) ELSE NULL END,"
                    " v,:ciphertext,'6789',TIMESTAMPTZ '2026-01-01T00:00:00Z'+n*interval '1 second'+v*interval '1 microsecond'"
                    " FROM generate_series(1,:roots) n CROSS JOIN generate_series(1,:versions) v"
                ),
                values,
            )
            for table, expected in (
                ("confidential_fields", ROOTS),
                ("confidential_values", ROOTS * VERSIONS),
            ):
                assert (
                    connection.scalar(
                        text(f"SELECT count(*) FROM {table} WHERE org_id=CAST(:org AS uuid)"),
                        values,
                    )
                    == expected
                )
        for table in (
            "confidential_fields",
            TOKEN_TABLE,
            "confidential_values",
            "memberships",
            "task_members",
            "task_workflows",
        ):
            connection.execute(text(f"ANALYZE {table}"))


def plan_nodes(node):
    return [node, *(row for child in node.get("Plans", []) for row in plan_nodes(child))]


def selective_token_ranges(node):
    """The rare 099 prefix belongs in Index Cond, with both range bounds."""
    return [
        item
        for item in plan_nodes(node)
        if item.get("Relation Name") == TOKEN_TABLE
        and item.get("Index Name") == "management_confidential_token_prefix"
        and "token" in item.get("Index Cond", "")
        and ">=" in item.get("Index Cond", "")
        and "<" in item.get("Index Cond", "")
        and "099" in item.get("Index Cond", "")
    ]


@pytest.mark.latency
async def test_fixed_scale_confidential_reads(
    api, headers, application, tenants, admin_engine, monkeypatch
):
    tasks = [await new_task(api, auth) for auth in headers]
    seed_scale(admin_engine, tenants, tasks, application.state.processor.settings)

    def fail_decrypt(self, value):
        pytest.fail("A fixed-scale metadata read attempted to decrypt a confidential value")

    monkeypatch.setattr(Secrets, "decrypt", fail_decrypt)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    receipt = {
        "mode": "real_api",
        "scenario": "u01-confidential-fixed-scale-v1",
        "status": "running",
        "schema": "4.0",
        "fixture": {
            "orgs": 2,
            "fields_per_org": ROOTS,
            "values_per_org": ROOTS * VERSIONS,
            "versions_per_field_owner": VERSIONS,
            "org_scope_fields_per_org": ROOTS // 2,
            "task_scope_fields_per_org": ROOTS // 2,
        },
        "hardware": {
            "system": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "logical_cpus": os.cpu_count(),
        },
        "rerun": ".venv/bin/pytest -q server/tests/test_management_confidential_scale.py -m latency --basetemp=data/work/management-pages-validation/confidential-scale/tmp",
        "measurements": [],
        "failures": [],
    }
    captured = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        captured.append((statement, parameters, is_select_statement(statement, context)))

    engine = application.state.db.engine
    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        with admin_engine.connect() as connection:
            receipt["postgresql"] = connection.scalar(text("SELECT version()"))
        first = assert_page(
            await api.post(FIELDS + "/query", headers=headers[0], json={"limit": 25}), headers[0]
        )
        first_values = assert_page(
            await api.post(VALUES + "/query", headers=headers[0], json={"limit": 25}), headers[0]
        )
        org_field = first_values["items"][0]["field_id"]
        task_values = assert_page(
            await api.post(VALUES + "/query", headers=headers[0], json={"task_id": tasks[0]}),
            headers[0],
        )
        task_field = next(row["field_id"] for row in task_values["items"] if row["scope"] == "task")
        scenarios = [
            ("fields_first", FIELDS + "/query", {"limit": 25}, 500, 6),
            (
                "fields_next",
                FIELDS + "/query",
                {"limit": 25, "cursor": first["data"]["next_cursor"]},
                500,
                6,
            ),
            ("key_prefix", FIELDS + "/query", {"q": "field_099", "limit": 25}, 500, 6),
            ("label_prefix", FIELDS + "/query", {"q": "confidential 099", "limit": 25}, 500, 6),
            ("broad_prefix", FIELDS + "/query", {"q": "scale", "limit": 25}, 500, 6),
            (
                "fields_archived_included",
                FIELDS + "/query",
                {"archived": True, "limit": 25},
                500,
                6,
            ),
            ("values_org", VALUES + "/query", {"limit": 25}, 500, 6),
            (
                "values_next",
                VALUES + "/query",
                {"limit": 25, "cursor": first_values["data"]["next_cursor"]},
                500,
                6,
            ),
            ("values_prefix", VALUES + "/query", {"q": "field_099", "limit": 25}, 500, 6),
            ("values_task", VALUES + "/query", {"task_id": tasks[0], "limit": 25}, 500, 6),
            ("org_history", f"{FIELDS}/{org_field}/values/history/query", {"limit": 5}, 750, 8),
            (
                "task_history",
                f"{FIELDS}/{task_field}/values/history/query",
                {"task_id": tasks[0], "limit": 5},
                750,
                8,
            ),
        ]
        for name, path, body, time_limit, sql_limit in scenarios:
            warm = await api.post(path, headers=headers[0], json=body)
            assert_page(warm, headers[0], limit=body["limit"])
            timings, sizes, trips, reads = [], [], [], []
            last_queries = []
            for _ in range(SAMPLES):
                captured.clear()
                started = perf_counter()
                reply = await api.post(path, headers=headers[0], json=body)
                timings.append((perf_counter() - started) * 1000)
                page = assert_page(reply, headers[0], limit=body["limit"])
                sizes.append(len(reply.content))
                last_queries = list(captured)
                trips.append(len(last_queries))
                reads.append(
                    sum(
                        is_read and "set_config(" not in statement
                        for statement, _, is_read in last_queries
                    )
                )
                assert "synthetic-scale-private-value" not in reply.text
                if name.startswith("values"):
                    assert all(
                        row["version"] == VERSIONS and row["status"] == "filled"
                        for row in page["items"]
                    )
                    assert all(
                        row["task_id"] == (tasks[0] if row["scope"] == "task" else None)
                        for row in page["items"]
                    )
                if name == "key_prefix":
                    assert all(row["key"].startswith("field_099") for row in page["items"])
            plans = []
            async with application.state.db.transaction(tenants["orgs"][0]) as session:
                connection = await session.connection()
                for statement, parameters, is_read in last_queries:
                    if not is_read or not any(
                        table in statement
                        for table in ("confidential_fields", "confidential_values", TOKEN_TABLE)
                    ):
                        continue
                    assert "encrypted_value" not in statement
                    plan = (
                        await connection.exec_driver_sql(
                            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, parameters
                        )
                    ).scalar_one()
                    assert isinstance(plan, list) and len(plan) == 1
                    work = plan_work(plan[0]["Plan"])
                    visits = {}
                    for node in work:
                        if node["relation"] in {
                            "confidential_fields",
                            "confidential_values",
                            TOKEN_TABLE,
                        }:
                            visits[node["relation"]] = (
                                visits.get(node["relation"], 0)
                                + (node["rows"] + node["rows_removed"]) * node["loops"]
                            )
                    if name in SELECTIVE_PREFIXES and TOKEN_TABLE in statement:
                        if not selective_token_ranges(plan[0]["Plan"]):
                            receipt["failures"].append(
                                f"{name}: no actual token prefix range Index Cond driving rare 099"
                            )
                        if visits.get(TOKEN_TABLE, 0) > 200:
                            receipt["failures"].append(
                                f"{name}: {TOKEN_TABLE} visits exceed 200: {visits[TOKEN_TABLE]}"
                            )
                        for node in plan_nodes(plan[0]["Plan"]):
                            if (
                                node.get("Relation Name") == TOKEN_TABLE
                                and node.get("Index Name") == "management_confidential_token_owner"
                            ):
                                condition = node.get("Index Cond", "")
                                if not all(
                                    column in condition
                                    for column in ("org_id", "field_id", "token")
                                ):
                                    receipt["failures"].append(
                                        f"{name}: other-token owner index lacks org/field/token bounds"
                                    )
                    if visits.get("confidential_values", 0) > 200:
                        receipt["failures"].append(
                            f"{name}: confidential_values visits exceed 200: {visits['confidential_values']}"
                        )
                    # A broad prefix can inspect one matching row per field, but
                    # owner current/history reads must remain page-bounded.
                    fields_bound = ROOTS * 2 if name == "broad_prefix" else 200
                    if visits.get("confidential_fields", 0) > fields_bound:
                        receipt["failures"].append(
                            f"{name}: field visits exceed {fields_bound}: {visits['confidential_fields']}"
                        )
                    plans.append(
                        {"sql": statement, "plan": plan, "work": work, "relation_visits": visits}
                    )
            assert plans, f"{name}: no actual confidential SQL plan captured"
            if name in SELECTIVE_PREFIXES and not any(
                TOKEN_TABLE in item["relation_visits"] for item in plans
            ):
                receipt["failures"].append(f"{name}: no actual search token projection plan")
            p95 = sorted(timings)[math.ceil(SAMPLES * 0.95) - 1]
            receipt["measurements"].append(
                {
                    "case": name,
                    "samples": SAMPLES,
                    "p95_ms": round(p95, 3),
                    "max_result_bytes": max(sizes),
                    "max_read_statements_including_authority": max(reads),
                    "max_sql_round_trips": max(trips),
                    "plans": plans,
                }
            )
            if p95 > time_limit:
                receipt["failures"].append(f"{name}: p95 {p95:.1f} ms exceeds {time_limit} ms")
            if max(trips) > sql_limit:
                receipt["failures"].append(
                    f"{name}: SQL round trips {max(trips)} exceed {sql_limit}"
                )
        other_fields = assert_page(
            await api.post(FIELDS + "/query", headers=headers[1], json={}), headers[1]
        )
        assert not {row["id"] for row in first["items"]} & {
            row["id"] for row in other_fields["items"]
        }
        other_values = assert_page(
            await api.post(VALUES + "/query", headers=headers[1], json={"task_id": tasks[1]}),
            headers[1],
        )
        assert not {row["field_id"] for row in task_values["items"]} & {
            row["field_id"] for row in other_values["items"]
        }
        receipt["status"] = "failed" if receipt["failures"] else "passed"
        assert not receipt["failures"], "; ".join(receipt["failures"])
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
        if receipt["status"] == "running":
            receipt["status"] = "failed_before_measurements_completed"
        serialized = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
        assert "synthetic-scale-private-value" not in serialized
        assert "encrypted_value" not in serialized
        (OUTPUT / "result.json").write_text(serialized)
