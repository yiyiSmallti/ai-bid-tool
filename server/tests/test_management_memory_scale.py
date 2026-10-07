"""Fixed-scale bounded org-memory read acceptance and real mutation observations.

Failure inventory: all-history/N+1 reads, cross-org identical text, unsafe cursor
continuation, unbounded output, sequential p95 above budget and missing real plans.
Seed two orgs with 10,000 roots and 100,000 revisions each, using the disposable
owner connection for fixture setup only. Every measured operation uses bid_app.
"""

import json
import math
import os
import platform
from pathlib import Path
from time import perf_counter
from uuid import UUID

import pytest
from sqlalchemy import event, text
from test_management_memory import BASE, CONTENT
from test_management_products_scale import plan_work

ROOTS = 10_000
REVISIONS = 10
SAMPLES = 20
OUTPUT = Path(__file__).resolve().parents[2] / "data/work/management-pages-validation/memory-scale"


def seed_scale(admin_engine, tenants):
    """Bulk synthetic setup with FK/CHECK/RLS enabled and gates restored atomically.

    The three memory transition triggers validate each live sequential write and
    cannot accept a bulk ten-revision chain. Only the owner setup transaction pauses
    them; rollback restores them on setup failure. No measured request bypasses gates.
    """
    with admin_engine.begin() as connection:
        for table, trigger in (
            ("memories", "memory_parent_gate"),
            ("memory_revisions", "memory_revision_gate"),
            ("memory_revisions", "memory_revision_complete"),
        ):
            connection.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}"))
        for org, user in zip(tenants["orgs"], tenants["users"], strict=True):
            values = {
                "org": str(org),
                "actor": user,
                "roots": ROOTS,
                "revisions": REVISIONS,
                "root_prefix": ":memory:",
                "revision_prefix": ":revision:",
                "audit_prefix": ":audit:",
            }
            connection.execute(text("SELECT set_config('app.current_org', :org, true)"), values)
            connection.execute(
                text(
                    "INSERT INTO memory_scope_epochs(org_id,scope,owner_id,epoch) "
                    "VALUES(CAST(:org AS uuid),'org',CAST(:org AS uuid),0)"
                ),
                values,
            )
            connection.execute(
                text(
                    "INSERT INTO memories(id,org_id,scope,current_revision_id,revision,created_at) "
                    "SELECT md5(:org||:root_prefix||n)::uuid,CAST(:org AS uuid),'org',"
                    "md5(:org||:revision_prefix||n||':'||:revisions)::uuid,:revisions,"
                    "TIMESTAMPTZ '2026-01-01T00:00:00Z'+n*interval '1 second' "
                    "FROM generate_series(1,:roots) n"
                ),
                values,
            )
            connection.execute(
                text(
                    "INSERT INTO memory_revisions(id,org_id,memory_id,revision,content,normalized_text,"
                    "normalized_tags,status,source,content_sha256,created_by,actor_kind,created_at,"
                    "decision,decision_reason_sha256,confirmed_by,confirmed_at) "
                    "SELECT md5(:org||:revision_prefix||n||':'||v)::uuid,CAST(:org AS uuid),"
                    "md5(:org||:root_prefix||n)::uuid,v,content,"
                    "'scale memory zx'||lpad(n::text,5,'0'),ARRAY['delivery','review'],"
                    "CASE WHEN v=:revisions AND n%2=0 THEN 'active' ELSE 'candidate' END,"
                    "jsonb_build_object('origin','human'),"
                    "encode(sha256(convert_to(public.rubric_canonical_json(content),'UTF8')),'hex'),"
                    ":actor,'session',TIMESTAMPTZ '2026-01-01T00:00:00Z'+n*interval '1 second'+v*interval '1 microsecond',"
                    "CASE WHEN v=:revisions AND n%2=0 THEN 'approve' END,"
                    "CASE WHEN v=:revisions AND n%2=0 THEN encode(sha256(convert_to('Synthetic scale approval','UTF8')),'hex') END,"
                    "CASE WHEN v=:revisions AND n%2=0 THEN :actor END,"
                    "CASE WHEN v=:revisions AND n%2=0 THEN TIMESTAMPTZ '2026-01-01T00:00:00Z'+n*interval '1 second'+v*interval '1 microsecond' END "
                    "FROM generate_series(1,:roots) n CROSS JOIN generate_series(1,:revisions) v "
                    "CROSS JOIN LATERAL (SELECT jsonb_build_object('kind','rule','conflict_key',"
                    "'scale.'||lpad(n::text,5,'0'),'text','Scale memory ZX'||lpad(n::text,5,'0'),"
                    "'tags',jsonb_build_array('delivery','review')) content) body"
                ),
                values,
            )
            connection.execute(
                text(
                    "INSERT INTO audit_logs(id,org_id,actor_user_id,action,object_id,details,created_at) "
                    "SELECT md5(:org||:audit_prefix||n||':'||v)::uuid,CAST(:org AS uuid),:actor,"
                    "CASE WHEN v=1 THEN 'memory.create' WHEN v=:revisions AND n%2=0 THEN 'memory.approve' ELSE 'memory.update' END,"
                    "md5(:org||:root_prefix||n)::uuid,"
                    "jsonb_build_object('revision_id',md5(:org||:revision_prefix||n||':'||v)::uuid,"
                    "'revision',v),TIMESTAMPTZ '2026-01-01T00:00:00Z'+n*interval '1 second' "
                    "FROM generate_series(1,:roots) n CROSS JOIN generate_series(1,:revisions) v"
                ),
                values,
            )
        for table, trigger in (
            ("memories", "memory_parent_gate"),
            ("memory_revisions", "memory_revision_gate"),
            ("memory_revisions", "memory_revision_complete"),
        ):
            connection.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}"))
        for table in (
            "memories",
            "memory_revisions",
            "memory_scope_epochs",
            "audit_logs",
            "memberships",
        ):
            connection.execute(text(f"ANALYZE {table}"))


@pytest.mark.latency
async def test_fixed_scale_memory_reads_and_write_paths(
    api, headers, application, tenants, admin_engine
):
    seed_scale(admin_engine, tenants)
    output = OUTPUT.resolve()
    output.mkdir(parents=True, exist_ok=True)
    receipt = {
        "mode": "real_api",
        "scenario": "u01-memory-fixed-scale-v1",
        "schema": "4.0",
        "status": "running",
        "fixture": {
            "orgs": 2,
            "roots_per_org": ROOTS,
            "revisions_per_org": ROOTS * REVISIONS,
            "active_current_roots_per_org": ROOTS // 2,
            "candidate_current_roots_per_org": ROOTS // 2,
            "setup": "owner_bulk_seed_transition_triggers_restored_before_commit",
        },
        "hardware": {
            "system": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "logical_cpus": os.cpu_count(),
        },
        "rerun": ".venv/bin/pytest -q server/tests/test_management_memory_scale.py -m latency --basetemp=data/work/management-pages-validation/memory-scale/tmp",
        "measurements": [],
        "write_observations": [],
    }
    captured, failures = [], []

    def capture(connection, cursor, statement, parameters, context, executemany):
        captured.append((statement, parameters))

    engine = application.state.db.engine
    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        with admin_engine.connect() as connection:
            receipt["postgresql"] = connection.scalar(text("SELECT version()"))
            enabled = (
                connection.execute(
                    text(
                        "SELECT tgenabled FROM pg_trigger WHERE tgname IN ('memory_parent_gate','memory_revision_gate','memory_revision_complete')"
                    )
                )
                .scalars()
                .all()
            )
            assert enabled == ["O", "O", "O"]
            for org in tenants["orgs"]:
                states = connection.execute(
                    text(
                        "SELECT search_status,count(*) FROM memories WHERE org_id=:org GROUP BY search_status"
                    ),
                    {"org": org},
                ).all()
                assert dict(states) == {"active": ROOTS // 2, "candidate": ROOTS // 2}
        first = await api.post(BASE + "/query", headers=headers[0], json={"limit": 25})
        assert first.status_code == 200, first.text
        identifier = first.json()["items"][0]["id"]
        cases = [
            ("list", "POST", BASE + "/query", {"limit": 25}, 500, 6, 256 * 1024),
            ("prefix", "POST", BASE + "/query", {"q": "ZX099", "limit": 25}, 500, 6, 256 * 1024),
            (
                "broad_prefix",
                "POST",
                BASE + "/query",
                {"q": "scale", "limit": 100},
                500,
                6,
                256 * 1024,
            ),
            (
                "all_tags",
                "POST",
                BASE + "/query",
                {"tags": ["delivery", "review"], "status": "candidate"},
                500,
                6,
                256 * 1024,
            ),
            ("empty_prefix", "POST", BASE + "/query", {"q": "absentprefix"}, 500, 6, 256 * 1024),
            (
                "next_page",
                "POST",
                BASE + "/query",
                {"limit": 25, "cursor": first.json()["data"]["next_cursor"]},
                500,
                6,
                256 * 1024,
            ),
            ("detail", "GET", f"{BASE}/{identifier}", None, 750, 8, 1024 * 1024),
            (
                "historical_detail",
                "GET",
                f"{BASE}/{identifier}?revision=1",
                None,
                750,
                8,
                1024 * 1024,
            ),
            (
                "history",
                "POST",
                f"{BASE}/{identifier}/history/query",
                {"limit": 5},
                750,
                8,
                256 * 1024,
            ),
        ]
        for name, method, path, body, latency_limit, sql_limit, byte_limit in cases:
            warm = await api.request(method, path, headers=headers[0], json=body)
            assert warm.status_code == 200, warm.text
            timings, sizes, reads, trips, last_queries = [], [], [], [], []
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
                trips.append(len(last_queries))
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
                    assert all(row["org_id"] == headers[0]["X-Org-Id"] for row in result["items"])
                    if name == "prefix":
                        assert all(
                            row["current"]["content"]["text"].startswith("Scale memory ZX099")
                            for row in result["items"]
                        )
                else:
                    assert result["data"]["memory"]["org_id"] == headers[0]["X-Org-Id"]
                    assert result["data"]["revised_by"] == str(tenants["users"][0])
            plans = []
            async with application.state.db.transaction(UUID(headers[0]["X-Org-Id"])) as session:
                connection = await session.connection()
                for statement, parameters in last_queries:
                    if not statement.lstrip().upper().startswith("SELECT") or not any(
                        table in statement
                        for table in ("memories", "memory_revisions", "audit_logs")
                    ):
                        continue
                    explained = await connection.exec_driver_sql(
                        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, parameters
                    )
                    plan = explained.scalar_one()
                    assert isinstance(plan, list) and len(plan) == 1
                    work = plan_work(plan[0]["Plan"])
                    visits = {}
                    for row in work:
                        if row["relation"] in {"memory_revisions", "audit_logs"}:
                            relation = row["relation"]
                            visits[relation] = (
                                visits.get(relation, 0)
                                + (row["rows"] + row["rows_removed"]) * row["loops"]
                            )
                    for relation, visited in visits.items():
                        if visited > 8 * (100 + 1):
                            failures.append(f"{name}: {relation} visited {visited} history rows")
                    plans.append(
                        {
                            "sql": statement,
                            "plan": plan,
                            "work": work,
                            "history_relation_visits": visits,
                        }
                    )
            assert plans, f"{name}: actual SQL plans missing"
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
            if p95 > latency_limit:
                failures.append(f"{name}: p95 {p95:.1f} ms exceeds {latency_limit} ms")
            if max(sizes) > byte_limit:
                failures.append(f"{name}: Result bytes exceed {byte_limit}")
            if max(trips) > sql_limit:
                failures.append(f"{name}: {max(trips)} SQL round trips exceed {sql_limit}")
        other = await api.post(BASE + "/query", headers=headers[1], json={"limit": 25})
        assert other.status_code == 200
        assert all(row["org_id"] == headers[1]["X-Org-Id"] for row in other.json()["items"])
        assert not {row["id"] for row in first.json()["items"]} & {
            row["id"] for row in other.json()["items"]
        }

        async def mutation(name, method, path, body):
            captured.clear()
            started = perf_counter()
            response = await api.request(method, path, headers=headers[0], json=body)
            receipt["write_observations"].append(
                {
                    "case": name,
                    "elapsed_ms": round((perf_counter() - started) * 1000, 3),
                    "status": response.status_code,
                    "result_bytes": len(response.content),
                    "sql_round_trips": len(captured),
                }
            )
            assert response.status_code == 200, response.text
            return response.json()["data"]["memory"]

        written = await mutation(
            "create", "POST", "/v4/memories", {"target": {"scope": "org"}, "content": CONTENT}
        )
        path = f"/v4/memories/{written['id']}"
        await mutation(
            "approve",
            "POST",
            path + "/decisions",
            {"expected_revision": 1, "action": "approve", "reason": "Synthetic reviewed rule"},
        )
        await mutation(
            "withdraw_active_edit",
            "PUT",
            path,
            {"expected_revision": 2, "content": {**CONTENT, "text": "Synthetic updated rule"}},
        )
        await mutation(
            "reapprove",
            "POST",
            path + "/decisions",
            {"expected_revision": 3, "action": "approve", "reason": "Synthetic revised rule"},
        )
        await mutation(
            "disable", "POST", path + "/disable", {"expected_revision": 4, "reason": "Retired"}
        )
        receipt["failures"] = failures
        receipt["status"] = "failed" if failures else "passed"
        assert not failures, "; ".join(failures)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
        if receipt["status"] == "running":
            receipt["status"] = "failed_before_measurements_completed"
        (output / "result.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
        )
