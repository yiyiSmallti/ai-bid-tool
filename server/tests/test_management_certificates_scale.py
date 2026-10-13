"""Fixed-scale API/PostgreSQL acceptance, specified before implementation.

Failure inventory: all-history scans, N+1 metadata lookups, incorrect prefix matches,
unbounded rows/bytes, missing continuation, detail/history scans, query timeout,
and serial warm p95 above the approved budget. Seed one library with 10,000
roots and 100,000 immutable revisions. Capture actual runtime SQL plans,
buffer/row work, SQL counts and complete Result bytes, never credentials.
This test uses the supplied disposable DB; it never starts a service.
"""

import json
import math
import os
import platform
from pathlib import Path
from time import perf_counter

import pytest
from sqlalchemy import event, text

ROOTS = 10000
REVISIONS = 10
SAMPLES = 20
OUTPUT = (
    Path(__file__).resolve().parents[2] / "data/work/management-pages-validation/certificates-scale"
)


def seed_scale(admin_engine, database):
    """Synthetic owner-side bulk setup; runtime measurements still use restricted bid_app."""
    with admin_engine.begin() as connection:
        values = {
            "seed": "test_management_certificates_scale",
            "roots": ROOTS,
            "revisions": REVISIONS,
        }
        values.update(
            {
                "audit_prefix": ":audit:",
                "certificate_prefix": ":certificate:",
                "file_prefix": ":file:",
                "part_prefix": ":part:",
                "revision_prefix": ":revision:",
            }
        )
        connection.execute(
            text(
                "INSERT INTO certificates(id,current_revision,created_at) SELECT md5(:seed||:certificate_prefix||n)::uuid,:revisions,TIMESTAMPTZ '2026-01-01T00:00:00Z'+n*interval '1 second' FROM generate_series(1,:roots) n"
            ),
            values,
        )
        connection.execute(
            text(
                "INSERT INTO certificate_revisions(id,certificate_id,revision,data,created_at) SELECT md5(:seed||:revision_prefix||n||':'||v)::uuid,md5(:seed||:certificate_prefix||n)::uuid,v,jsonb_build_object('name','Scale certificate '||lpad(n::text,5,'0'),'kind','qualification','number','ZX'||lpad(n::text,5,'0'),'valid_from',NULL,'valid_until',NULL),TIMESTAMPTZ '2026-01-01T00:00:00Z'+n*interval '1 second'+v*interval '1 microsecond' FROM generate_series(1,:roots) n CROSS JOIN generate_series(1,:revisions) v"
            ),
            values,
        )
        connection.execute(
            text(
                "INSERT INTO audit_logs(id,action,object_id,details,created_at,actor_kind) SELECT md5(:seed||:audit_prefix||n||':'||v)::uuid,CASE WHEN v=1 THEN 'resource.certificate.create' WHEN v%2=0 THEN 'resource.certificate.file.create' ELSE 'resource.certificate.update' END,md5(:seed||:certificate_prefix||n)::uuid,jsonb_build_object('new_revision_id',md5(:seed||:revision_prefix||n||':'||v)::uuid,'revision',v),TIMESTAMPTZ '2026-01-01T00:00:00Z'+n*interval '1 second','session' FROM generate_series(1,:roots) n CROSS JOIN generate_series(1,:revisions) v"
            ),
            values,
        )
        connection.execute(
            text(
                "INSERT INTO certificate_files(id,certificate_id,certificate_revision_id,file,storage_key) SELECT md5(:seed||:file_prefix||n||':'||v)::uuid,md5(:seed||:certificate_prefix||n)::uuid,md5(:seed||:revision_prefix||n||':'||v)::uuid,jsonb_build_object('name','synthetic.pdf','sha256',repeat('a',64),'size_bytes',1024,'media_type','application/pdf','page_count',1),'certificate/'||md5(:seed||:certificate_prefix||n)::uuid||'/'||md5(:seed||:revision_prefix||n||':'||v)::uuid||'/'||repeat('a',64)||'.pdf' FROM generate_series(1,:roots) n CROSS JOIN generate_series(2,:revisions,2) v"
            ),
            values,
        )
        connection.execute(
            text(
                "INSERT INTO certificate_file_parts(id,certificate_id,certificate_revision_id,certificate_file_id,ordinal,name,media_type,sha256,size_bytes,page_start,page_count,rotation,storage_key) SELECT md5(:seed||:part_prefix||n||':'||v)::uuid,md5(:seed||:certificate_prefix||n)::uuid,md5(:seed||:revision_prefix||n||':'||v)::uuid,md5(:seed||:file_prefix||n||':'||v)::uuid,1,'synthetic.png','image/png',repeat('b',64),512,1,1,0,'certificate/'||md5(:seed||:certificate_prefix||n)::uuid||'/'||md5(:seed||:revision_prefix||n||':'||v)::uuid||'/parts/1-'||repeat('b',64) FROM generate_series(1,:roots) n CROSS JOIN generate_series(2,:revisions,2) v"
            ),
            values,
        )
        for table in (
            "certificates",
            "certificate_revisions",
            "certificate_files",
            "certificate_file_parts",
            "audit_logs",
        ):
            connection.execute(text(f"ANALYZE {table}"))


def plan_work(node):
    """Rows visited are executor observations, including rejected candidate rows."""
    own = {
        "node": node["Node Type"],
        "relation": node.get("Relation Name"),
        "index": node.get("Index Name"),
        "rows": node.get("Actual Rows", 0),
        "loops": node.get("Actual Loops", 0),
        "rows_removed": node.get("Rows Removed by Filter", 0)
        + node.get("Rows Removed by Index Recheck", 0),
        "shared_hit_blocks": node.get("Shared Hit Blocks", 0),
        "shared_read_blocks": node.get("Shared Read Blocks", 0),
    }
    return [own, *(row for child in node.get("Plans", []) for row in plan_work(child))]


@pytest.mark.latency
async def test_fixed_scale_certificate_reads(api, headers, application, database, admin_engine):
    seed_scale(admin_engine, database)
    root = OUTPUT.resolve()
    root.mkdir(parents=True, exist_ok=True)
    receipt = {
        "mode": "real_api",
        "scenario": "u01-certificate-fixed-scale-v1",
        "status": "running",
        "schema": "5.0",
        "fixture": {
            "roots": ROOTS,
            "revisions": ROOTS * REVISIONS,
            "originals": ROOTS * (REVISIONS // 2),
            "parts": ROOTS * (REVISIONS // 2),
        },
        "hardware": {
            "system": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "logical_cpus": os.cpu_count(),
        },
        "rerun": ".venv/bin/pytest -q server/tests/test_management_certificates_scale.py -m latency --basetemp=data/work/management-pages-validation/certificates-scale/tmp",
        "measurements": [],
    }
    captured = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        captured.append((statement, parameters))

    engine = application.state.db.engine
    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    failures = []
    try:
        with admin_engine.connect() as connection:
            receipt["postgresql"] = connection.scalar(text("SELECT version()"))
        query_path = "/v4/management/resources/certificates/query"
        first = await api.post(query_path, headers=headers, json={"limit": 25})
        assert first.status_code == 200, first.text
        certificate = first.json()["items"][0]["ref"]["resource_id"]
        cases = [
            ("list", "POST", query_path, {"limit": 25}, 500, 6, 256 * 1024),
            ("prefix", "POST", query_path, {"limit": 25, "q": "ZX099"}, 500, 6, 256 * 1024),
            ("broad_prefix", "POST", query_path, {"limit": 100, "q": "scale"}, 500, 6, 256 * 1024),
            ("empty_prefix", "POST", query_path, {"q": "absentprefix"}, 500, 6, 256 * 1024),
            (
                "next_page",
                "POST",
                query_path,
                {"limit": 25, "cursor": first.json()["data"]["next_cursor"]},
                500,
                6,
                256 * 1024,
            ),
            (
                "detail",
                "GET",
                f"/v4/management/resources/certificates/{certificate}",
                None,
                750,
                8,
                1024 * 1024,
            ),
            (
                "historical_detail",
                "GET",
                f"/v4/management/resources/certificates/{certificate}?revision=1",
                None,
                750,
                8,
                1024 * 1024,
            ),
            (
                "history",
                "POST",
                f"/v4/management/resources/certificates/{certificate}/history/query",
                {"limit": 5},
                750,
                6,
                256 * 1024,
            ),
        ]
        for name, method, path, body, latency_limit, sql_limit, byte_limit in cases:
            await api.request(method, path, headers=headers, json=body)
            timings, sizes, reads, controls, round_trips = ([], [], [], [], [])
            last_queries = []
            for _ in range(SAMPLES):
                captured.clear()
                started = perf_counter()
                response = await api.request(method, path, headers=headers, json=body)
                timings.append((perf_counter() - started) * 1000)
                assert response.status_code == 200, response.text
                sizes.append(len(response.content))
                last_queries = list(captured)
                data_queries = [
                    pair
                    for pair in last_queries
                    if pair[0].lstrip().upper().startswith("SELECT")
                    and "set_config(" not in pair[0].lower()
                ]
                reads.append(len(data_queries))
                controls.append(len(last_queries) - len(data_queries))
                round_trips.append(len(last_queries))
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
                    assert "total" not in result["data"]
                    assert result["data"]["returned"] == len(result["items"])
                    if name == "prefix":
                        assert all(
                            row["name"].startswith("Scale certificate 099")
                            for row in result["items"]
                        )
            plans = []
            async with application.state.db.transaction() as session:
                connection = await session.connection()
                for statement, parameters in last_queries:
                    if not any(
                        table in statement
                        for table in (
                            "certificates",
                            "certificate_revisions",
                            "certificate_files",
                            "certificate_file_parts",
                            "audit_logs",
                        )
                    ):
                        continue
                    explained = await connection.exec_driver_sql(
                        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, parameters
                    )
                    plan = explained.scalar_one()
                    assert isinstance(plan, list) and len(plan) == 1
                    assert isinstance(plan[0], dict) and isinstance(plan[0].get("Plan"), dict)
                    assert "Execution Time" in plan[0] and "Actual Rows" in plan[0]["Plan"]
                    work = plan_work(plan[0]["Plan"])
                    assert work and all(row["loops"] >= 0 and row["rows"] >= 0 for row in work)
                    visits = {}
                    for row in work:
                        relation = row["relation"]
                        if relation in {
                            "certificate_revisions",
                            "certificate_files",
                            "certificate_file_parts",
                            "audit_logs",
                        }:
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
            assert plans, f"{name}: no real database plans captured"
            p95 = sorted(timings)[math.ceil(SAMPLES * 0.95) - 1]
            receipt["measurements"].append(
                {
                    "case": name,
                    "samples": SAMPLES,
                    "p95_ms": round(p95, 3),
                    "max_result_bytes": max(sizes),
                    "max_read_statements_including_authority": max(reads),
                    "max_context_statements": max(controls),
                    "max_sql_round_trips": max(round_trips),
                    "plans": plans,
                }
            )
            if p95 > latency_limit:
                failures.append(f"{name}: p95 {p95:.1f} ms exceeds {latency_limit} ms")
            if max(sizes) > byte_limit:
                failures.append(f"{name}: Result bytes exceed {byte_limit}")
            if max(round_trips) > sql_limit:
                failures.append(f"{name}: {max(round_trips)} SQL round trips exceed {sql_limit}")
        receipt["write_observations"] = []

        async def write(name, path, body):
            captured.clear()
            started = perf_counter()
            response = await api.post(path, headers=headers, json=body)
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
            return response.json()["data"]

        created_task = await write("create_task", "/v4/tasks", {"name": "Scale pin acceptance"})
        pin_path = f"/v4/tasks/{created_task['id']}/certificates"
        selected = await write(
            "pin", pin_path, {"certificate_id": certificate, "revision": REVISIONS}
        )
        data = {
            "kind": "qualification",
            "name": "Scale maintained certificate",
            "number": "CERT-AB-20",
            "valid_from": "2026-01-01",
            "valid_until": "2026-12-31",
        }
        await write(
            "revise",
            f"/v4/resources/certificates/{certificate}/revisions",
            {"expected_revision": REVISIONS, "data": data},
        )
        for lifecycle_revision, state, reason in (
            (0, "inactive", "obsolete"),
            (1, "active", "restored"),
        ):
            await write(
                state,
                f"/v4/management/resources/certificates/{certificate}/lifecycle",
                {
                    "expected_revision": REVISIONS + 1,
                    "expected_lifecycle_revision": lifecycle_revision,
                    "state": state,
                    "reason_code": reason,
                },
            )
        pins = await api.get(pin_path, headers=headers)
        assert pins.status_code == 200
        assert pins.json()["items"][0]["id"] == selected["id"]
        assert pins.json()["items"][0]["revision"] == REVISIONS
        await write("create_certificate", "/v4/resources/certificates", {"data": data})
        receipt["failures"] = failures
        receipt["status"] = "failed" if failures else "passed"
        assert not failures, "; ".join(failures)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
        if receipt["status"] == "running":
            receipt["status"] = "failed_before_measurements_completed"
        (root / "result.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
