"""Fixed-scale real PostgreSQL/API template and binding acceptance.

Failure inventory established before implementation: all-history scans, N+1
revision authors, cross-org matches, response truncation, unbounded audit visits,
missing continuation, read SQL/time/payload budgets, mutation authority bypass.
Seed two orgs with exactly 10,000 roots, 100,000 revisions and 100,000 bindings
per org. Run serial warmed requests through the real restricted-role API; retain
reproducible artifacts in data/work. Never start or reconfigure a database.
"""

import hashlib
import json
import math
import os
import platform
from pathlib import Path
from time import perf_counter
from uuid import UUID

import pytest
from app.services.auth import ROLE_SCOPES
from sqlalchemy import event, text
from test_exports import export_template
from test_management_features_scale import plan_work
from test_management_templates import BASE, BINDINGS, binding_request
from test_templates import metadata

ROOTS = 10_000
REVISIONS = 10
SAMPLES = 20
OUTPUT = (
    Path(__file__).resolve().parents[2] / "data/work/management-pages-validation/templates-scale"
)


def seed_scale(admin_engine, tenants):
    content = export_template()
    descriptor = {
        "name": "synthetic.docx",
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    sections = binding_request({"id": "unused", "file": descriptor})["sections"]
    with admin_engine.begin() as connection:
        for org, user in zip(tenants["orgs"], tenants["users"], strict=True):
            values = {
                "org": str(org),
                "actor": user,
                "roots": ROOTS,
                "revisions": REVISIONS,
                "file": json.dumps(descriptor),
                "sha": descriptor["sha256"],
                "sections": json.dumps(sections),
                "scopes": json.dumps(sorted(ROLE_SCOPES["admin"])),
            }
            connection.execute(
                text(
                    "SELECT set_config('app.current_org',:org,true),set_config('app.actor_kind','session',true),set_config('app.actor_user_id',CAST(:actor AS text),true),set_config('app.actor_token_id','',true),set_config('app.actor_scopes',:scopes,true)"
                ),
                values,
            )
            connection.execute(
                text(
                    "INSERT INTO templates(id,org_id,created_by,current_revision,created_at)"
                    " SELECT md5(:org||'|template|'||n)::uuid,CAST(:org AS uuid),:actor,:revisions,"
                    " TIMESTAMPTZ '2026-01-01T00:00:00Z'+n*interval '1 second' FROM generate_series(1,:roots) n"
                ),
                values,
            )
            connection.execute(
                text(
                    "INSERT INTO template_revisions(id,org_id,template_id,revision,data,file,storage_key,created_at)"
                    " SELECT md5(:org||'|revision|'||n||':'||v)::uuid,CAST(:org AS uuid),md5(:org||'|template|'||n)::uuid,v,"
                    " jsonb_build_object('name','Scale template '||lpad(n::text,5,'0'),'project_types',jsonb_build_array('Scale declared project'),'chapters',NULL),"
                    " CAST(:file AS jsonb),'org/'||:org||'/template/'||md5(:org||'|template|'||n)::uuid||'/'||md5(:org||'|revision|'||n||':'||v)::uuid||'/'||:sha||'.docx',"
                    " TIMESTAMPTZ '2026-01-01T00:00:00Z'+n*interval '1 second'+v*interval '1 microsecond'"
                    " FROM generate_series(1,:roots) n CROSS JOIN generate_series(1,:revisions) v"
                ),
                values,
            )
            connection.execute(
                text(
                    "INSERT INTO audit_logs(id,org_id,actor_user_id,action,object_id,details,created_at)"
                    " SELECT md5(:org||'|audit|'||n||':'||v)::uuid,CAST(:org AS uuid),:actor,"
                    " CASE WHEN v=1 THEN 'resource.template.create' ELSE 'resource.template.update' END,"
                    " md5(:org||'|template|'||n)::uuid,jsonb_build_object('new_revision_id',md5(:org||'|revision|'||n||':'||v)::uuid,'revision',v),"
                    " TIMESTAMPTZ '2026-01-01T00:00:00Z'+n*interval '1 second'"
                    " FROM generate_series(1,:roots) n CROSS JOIN generate_series(1,:revisions) v"
                ),
                values,
            )
            connection.execute(
                text(
                    "INSERT INTO export_template_bindings(id,org_id,template_revision_id,template_sha256,binding_hash,sections,static_content_hash,adapter_version,reviewed_by,reviewed_at)"
                    " SELECT md5(:org||'|binding|'||n||':'||b)::uuid,CAST(:org AS uuid),md5(:org||'|revision|'||n||':'||:revisions)::uuid,:sha,"
                    " md5(:org||'|hash|'||n||':'||b)||md5(:org||'|hash|'||n||':'||b),CAST(:sections AS jsonb),repeat('a',64),'scale-fixed-v1',:actor,"
                    " TIMESTAMPTZ '2026-01-01T00:00:00Z'+n*interval '1 second'+b*interval '1 microsecond'"
                    " FROM generate_series(1,:roots) n CROSS JOIN generate_series(1,:revisions) b"
                ),
                values,
            )
        for table in (
            "templates",
            "template_revisions",
            "audit_logs",
            "export_template_bindings",
            "memberships",
        ):
            connection.execute(text(f"ANALYZE {table}"))
        for org in tenants["orgs"]:
            for table, expected in (
                ("templates", ROOTS),
                ("template_revisions", ROOTS * REVISIONS),
                ("export_template_bindings", ROOTS * REVISIONS),
            ):
                assert (
                    connection.scalar(
                        text(f"SELECT count(*) FROM {table} WHERE org_id=:org"), {"org": org}
                    )
                    == expected
                )


@pytest.mark.latency
async def test_fixed_scale_template_binding_reads_and_writes(
    api, headers, application, tenants, admin_engine
):
    seed_scale(admin_engine, tenants)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    receipt = {
        "mode": "real_api",
        "scenario": "u01-template-binding-fixed-scale-v1",
        "status": "running",
        "schema": "4.0",
        "fixture": {
            "orgs": 2,
            "roots_per_org": ROOTS,
            "revisions_per_org": ROOTS * REVISIONS,
            "bindings_per_org": ROOTS * REVISIONS,
        },
        "hardware": {
            "system": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "logical_cpus": os.cpu_count(),
        },
        "rerun": ".venv/bin/pytest -q server/tests/test_management_templates_scale.py -m latency --basetemp=data/work/management-pages-validation/templates-scale/tmp",
        "measurements": [],
        "write_observations": [],
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
        first = await api.post(BASE + "/query", headers=headers[0], json={"limit": 25})
        assert first.status_code == 200, first.text
        template_id = first.json()["items"][0]["ref"]["resource_id"]
        detail = await api.get(f"{BASE}/{template_id}", headers=headers[0])
        assert detail.status_code == 200, detail.text
        revision_id = detail.json()["data"]["detail"]["revision"]["id"]
        first_bindings = await api.post(
            BINDINGS + "/query",
            headers=headers[0],
            json={"template_revision_id": revision_id, "limit": 5},
        )
        assert first_bindings.status_code == 200, first_bindings.text
        binding_id = first_bindings.json()["items"][0]["id"]
        cases = [
            ("list", "POST", BASE + "/query", {"limit": 25}, 500, 6, 256 * 1024),
            ("prefix", "POST", BASE + "/query", {"limit": 25, "q": "099"}, 500, 6, 256 * 1024),
            (
                "broad_prefix",
                "POST",
                BASE + "/query",
                {"limit": 100, "q": "scale"},
                500,
                6,
                256 * 1024,
            ),
            ("empty_prefix", "POST", BASE + "/query", {"q": "absentprefix"}, 500, 6, 256 * 1024),
            ("inactive_empty", "POST", BASE + "/query", {"state": "inactive"}, 500, 6, 256 * 1024),
            (
                "next_page",
                "POST",
                BASE + "/query",
                {"limit": 25, "cursor": first.json()["data"]["next_cursor"]},
                500,
                6,
                256 * 1024,
            ),
            ("detail", "GET", f"{BASE}/{template_id}", None, 750, 8, 1024 * 1024),
            (
                "historical_detail",
                "GET",
                f"{BASE}/{template_id}?revision=1",
                None,
                750,
                8,
                1024 * 1024,
            ),
            (
                "history",
                "POST",
                f"{BASE}/{template_id}/history/query",
                {"limit": 5},
                750,
                6,
                256 * 1024,
            ),
            (
                "lifecycle_history",
                "POST",
                f"{BASE}/{template_id}/lifecycle/history/query",
                {"limit": 5},
                750,
                6,
                256 * 1024,
            ),
            (
                "binding_list",
                "POST",
                BINDINGS + "/query",
                {"template_revision_id": revision_id, "limit": 5},
                500,
                6,
                256 * 1024,
            ),
            (
                "binding_next_page",
                "POST",
                BINDINGS + "/query",
                {
                    "template_revision_id": revision_id,
                    "limit": 5,
                    "cursor": first_bindings.json()["data"]["next_cursor"],
                },
                500,
                6,
                256 * 1024,
            ),
            (
                "binding_detail",
                "GET",
                f"{BINDINGS}/{binding_id}?template_revision_id={revision_id}",
                None,
                750,
                8,
                1024 * 1024,
            ),
        ]
        for name, method, path, body, latency_limit, sql_limit, byte_limit in cases:
            await api.request(method, path, headers=headers[0], json=body)
            timings, sizes, reads, controls, trips = [], [], [], [], []
            last_queries = []
            for _ in range(SAMPLES):
                captured.clear()
                started = perf_counter()
                response = await api.request(method, path, headers=headers[0], json=body)
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
                    assert "total" not in result["data"] and result["data"]["returned"] == len(
                        result["items"]
                    )
                    assert all(row["org_id"] == headers[0]["X-Org-Id"] for row in result["items"])
                    if name == "prefix":
                        assert all(
                            row["name"].startswith("Scale template 099") for row in result["items"]
                        )
                else:
                    assert result["data"]["org_id"] == headers[0]["X-Org-Id"]
            plans = []
            async with application.state.db.transaction(UUID(headers[0]["X-Org-Id"])) as session:
                from app.services.auth import authenticate, set_actor_context

                actor = await authenticate(
                    session,
                    headers[0]["Authorization"].removeprefix("Bearer "),
                    tenants["orgs"][0],
                    application.state.crypto,
                    joined_membership=True,
                )
                await set_actor_context(session, actor)
                connection = await session.connection()
                for statement, parameters in last_queries:
                    if not statement.lstrip().upper().startswith("SELECT") or not any(
                        table in statement
                        for table in (
                            "templates",
                            "template_revisions",
                            "audit_logs",
                            "export_template_bindings",
                            "resource_lifecycle_events",
                        )
                    ):
                        continue
                    explained = await connection.exec_driver_sql(
                        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, parameters
                    )
                    plan = explained.scalar_one()
                    assert isinstance(plan, list) and len(plan) == 1 and "Execution Time" in plan[0]
                    work = plan_work(plan[0]["Plan"])
                    visits = {}
                    for row in work:
                        relation = row["relation"]
                        if relation in {
                            "template_revisions",
                            "audit_logs",
                            "export_template_bindings",
                            "resource_lifecycle_events",
                        }:
                            visits[relation] = (
                                visits.get(relation, 0)
                                + (row["rows"] + row["rows_removed"]) * row["loops"]
                            )
                    # Current-revision joins under a broad prefix may visit one
                    # row per root, but must never walk the 100,000-row history.
                    for relation, visited in visits.items():
                        bound = (
                            ROOTS * 2
                            if relation == "template_revisions" and name == "broad_prefix"
                            else 200
                        )
                        if visited > bound:
                            failures.append(
                                f"{name}: {relation} visited {visited} rows, bound {bound}"
                            )
                    author_nodes = [row for row in work if row["relation"] == "audit_logs"]
                    if author_nodes and not any(
                        row["index"] and "template_audit_author" in row["index"] for row in work
                    ):
                        failures.append(
                            f"{name}: exact page-author lookup did not use the template author index"
                        )
                    plans.append(
                        {
                            "sql": statement,
                            "plan": plan,
                            "work": work,
                            "history_relation_visits": visits,
                        }
                    )
            assert plans, f"{name}: no actual database plans captured"
            p95 = sorted(timings)[math.ceil(SAMPLES * 0.95) - 1]
            receipt["measurements"].append(
                {
                    "case": name,
                    "samples": SAMPLES,
                    "p95_ms": round(p95, 3),
                    "max_result_bytes": max(sizes),
                    "max_read_statements_including_authority": max(reads),
                    "max_context_statements": max(controls),
                    "max_sql_round_trips": max(trips),
                    "plans": plans,
                }
            )
            if p95 > latency_limit:
                failures.append(f"{name}: p95 {p95:.1f} ms exceeds {latency_limit} ms")
            if max(sizes) > byte_limit:
                failures.append(f"{name}: Result bytes exceed {byte_limit}")
            if max(trips) > sql_limit:
                failures.append(f"{name}: SQL round trips {max(trips)} exceed {sql_limit}")
        other = await api.post(BASE + "/query", headers=headers[1], json={"limit": 25})
        assert other.status_code == 200
        assert all(row["org_id"] == headers[1]["X-Org-Id"] for row in other.json()["items"])
        assert not {row["ref"]["resource_id"] for row in first.json()["items"]} & {
            row["ref"]["resource_id"] for row in other.json()["items"]
        }

        async def observe(name, method, path, **args):
            captured.clear()
            started = perf_counter()
            response = await api.request(method, path, headers=headers[0], **args)
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

        content = export_template()
        maintained = await observe(
            "upload",
            "POST",
            "/resources/templates",
            data={"metadata": json.dumps({"data": metadata(name="Scale maintained template")})},
            files={"file": ("synthetic.docx", content)},
        )
        task_row = await observe(
            "create_task", "POST", "/v4/tasks", json={"name": "Scale template pin acceptance"}
        )
        selected = await observe(
            "pin_scale_existing",
            "POST",
            f"/v4/tasks/{task_row['id']}/templates",
            json={"template_id": template_id, "revision": REVISIONS},
        )
        updated = await observe(
            "revise_docx",
            "POST",
            f"/resources/templates/{maintained['template_id']}/revisions",
            data={
                "metadata": json.dumps(
                    {"expected_revision": 1, "data": metadata(name="Scale maintained revision")}
                )
            },
            files={"file": ("synthetic.docx", content)},
        )
        body = binding_request(updated)
        preview = await observe("binding_preview", "POST", "/export-template-bindings", json=body)
        await observe(
            "binding_create",
            "POST",
            "/export-template-bindings",
            json={
                **body,
                "dry_run": False,
                "expected_static_content_hash": preview["static_content_hash"],
            },
        )
        for version, state, reason in ((0, "inactive", "obsolete"), (1, "active", "restored")):
            await observe(
                state,
                "POST",
                f"{BASE}/{maintained['template_id']}/lifecycle",
                json={
                    "expected_revision": 2,
                    "expected_lifecycle_revision": version,
                    "state": state,
                    "reason_code": reason,
                },
            )
        pins = await api.get(f"/tasks/{task_row['id']}/templates", headers=headers[0])
        assert (
            pins.status_code == 200
            and pins.json()["items"][0]["id"] == selected["id"]
            and pins.json()["items"][0]["revision"] == REVISIONS
        )
        receipt["failures"] = failures
        receipt["status"] = "failed" if failures else "passed"
        assert not failures, "; ".join(failures)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
        if receipt["status"] == "running":
            receipt["status"] = "failed_before_measurements_completed"
        (OUTPUT / "result.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
        )
