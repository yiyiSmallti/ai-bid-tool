"""Fixed-scale confidential PostgreSQL/API acceptance.

Failure inventory: N+1 owner reads, all-history scans, unindexed prefix scans,
cross-org/task mixing, missing continuation, ciphertext reads, excessive payload
or SQL work, and warmed p95 above the approved budget. Each run adds two own orgs
with 10,000 fields and 100,000 encrypted values each, retaining all earlier rows.
Never start a service.
Retain actual read plans and reproducible receipts only under data/work.
Selective prefixes must drive a C-collated token range, rather than scan all
org tokens or roots and filter afterward; remaining tokens use owner lookups.
"""

import json
import math
import os
import platform
import re
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import pytest
from app.core.security import Secrets
from app.models.entities import Membership, Org, User
from conftest import PASSWORD, PASSWORD_HASH
from cryptography.fernet import Fernet
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
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
CARDINALITY_TABLES = (
    "orgs",
    "users",
    "memberships",
    "tasks",
    "confidential_fields",
    "confidential_values",
    TOKEN_TABLE,
)


def global_cardinalities(connection):
    return {
        table: connection.scalar(text(f"SELECT count(*) FROM {table}"))
        for table in CARDINALITY_TABLES
    }


@pytest.fixture
def tenants(admin_engine, monkeypatch):
    # Reproduce the shared Settings setup without invoking its TRUNCATE fixture.
    runtime_url = make_url(os.environ["BID_DATABASE_URL"])
    for variable, role in (
        ("BID_PLATFORM_DATABASE_URL", "bid_platform_app"),
        ("BID_CREDENTIAL_DATABASE_URL", "bid_credential_reader"),
    ):
        if not os.environ.get(variable):
            monkeypatch.setenv(
                variable, runtime_url.set(username=role).render_as_string(hide_password=False)
            )
    if not os.environ.get("BID_SECRETS_KEY"):
        monkeypatch.setenv("BID_SECRETS_KEY", Fernet.generate_key().decode())
    run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}-{uuid4().hex}"
    with admin_engine.connect() as connection:
        before = global_cardinalities(connection)
    orgs, users, emails = [], [], []
    with Session(admin_engine) as session, session.begin():
        for label in ("a", "b"):
            org_id, user_id = uuid4(), uuid4()
            email = f"confidential-scale-{run_id.lower()}-{label}@example.test"
            session.add_all(
                [
                    Org(id=org_id, org_id=org_id, name=f"Synthetic scale {run_id} {label}"),
                    User(id=user_id, email=email, password_hash=PASSWORD_HASH),
                ]
            )
            session.flush()
            session.add(Membership(org_id=org_id, user_id=user_id, role="admin"))
            orgs.append(org_id)
            users.append(user_id)
            emails.append(email)
    # No teardown: repeated runs must query the same growing tables.
    return {
        "orgs": orgs,
        "users": users,
        "emails": emails,
        "run_id": run_id,
        "global_before": before,
    }


@pytest.fixture
async def headers(api, tenants):
    output = []
    for email, org in zip(tenants["emails"], tenants["orgs"], strict=True):
        response = await api.post(
            "/auth/login", json={"email": email, "password": PASSWORD, "org_id": str(org)}
        )
        assert response.status_code == 200
        output.append(
            {"Authorization": "Bearer " + response.json()["data"]["session"], "X-Org-Id": str(org)}
        )
    return output


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
    org_counts = []
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
            counts = {
                table: connection.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE org_id=CAST(:org AS uuid)"),
                    values,
                )
                for table in ("confidential_fields", "confidential_values", TOKEN_TABLE)
            }
            for table, expected in (
                ("confidential_fields", ROOTS),
                ("confidential_values", ROOTS * VERSIONS),
            ):
                assert counts[table] == expected
            org_counts.append({"org_id": str(org), **counts})
        for table in (
            "confidential_fields",
            TOKEN_TABLE,
            "confidential_values",
            "memberships",
            "task_members",
            "task_workflows",
        ):
            connection.execute(text(f"ANALYZE {table}"))
        after = global_cardinalities(connection)
        for table in ("confidential_fields", "confidential_values", TOKEN_TABLE):
            assert after[table] == tenants["global_before"][table] + sum(
                counts[table] for counts in org_counts
            )
    return {"own_org_counts": org_counts, "global_after_seed": after}


def plan_nodes(node):
    return [node, *(row for child in node.get("Plans", []) for row in plan_nodes(child))]


def plan_paths(node, parents=()):
    yield node, parents
    for child in node.get("Plans", []):
        yield from plan_paths(child, (*parents, node))


def directly_limited(parents):
    # A Sort/Aggregate/Join under LIMIT can still consume a whole relation.
    # Only a streaming path from LIMIT to the range scan bounds its input work.
    for parent in reversed(parents):
        if parent["Node Type"] == "Limit":
            return True
        if parent["Node Type"] not in {"Result", "Subquery Scan"}:
            return False
    return False


def selective_token_ranges(node):
    """Accept bounded prefix access independently of streaming/bitmap plan shape."""
    matches = []
    for item, parents in plan_paths(node):
        if item.get("Node Type") not in {"Index Scan", "Index Only Scan", "Bitmap Index Scan"}:
            continue
        condition = item.get("Index Cond", "")
        if item.get("Index Name") != "management_confidential_token_prefix" or not all(
            re.search(pattern, condition)
            for pattern in (r"\borg_id\s*=", r"\btoken\s*>=", r"\btoken\s*<(?!=)")
        ):
            continue
        relation = item
        if item["Node Type"] == "Bitmap Index Scan":
            # Bitmap index nodes have no relation/alias. Associate the actual
            # range condition with its nearest heap scan, including BitmapAnd.
            relation = next(
                (
                    parent
                    for parent in reversed(parents)
                    if parent["Node Type"] == "Bitmap Heap Scan"
                ),
                {},
            )
        if relation.get("Relation Name") == TOKEN_TABLE and relation.get("Alias") in {
            "prefix_seed",
            "prefix_seek",
        }:
            matches.append({**item, "Alias": relation["Alias"]})
    return matches


def relation_visits(node):
    """Conservative visits, not rounded per-loop averages mistaken for totals.

    A UUID point lookup visits at most one live root per loop, including a row
    rejected by its filter. A filter-free owner probe streams at most one row
    under its LIMIT 1. Other repeated scans account for EXPLAIN's integer
    rounding separately for returned rows and each reported rejection counter.
    """
    visits = {}
    for item, parents in plan_paths(node):
        relation = item.get("Relation Name")
        if relation not in {"confidential_fields", "confidential_values", TOKEN_TABLE}:
            continue
        loops = item.get("Actual Loops", 0)
        counters = [item.get("Actual Rows", 0)] + [
            item[key]
            for key in ("Rows Removed by Filter", "Rows Removed by Index Recheck")
            if key in item
        ]
        estimate = sum(counters) * loops
        condition = item.get("Index Cond", "")
        root_point = (
            relation == "confidential_fields"
            and item.get("Index Name")
            in {"confidential_fields_pkey", "confidential_fields_org_id_id_key"}
            and "id = matching_fields.field_id" in condition
        )
        owner_probe = (
            relation == TOKEN_TABLE
            and item.get("Index Name") == "management_confidential_token_owner"
            and re.fullmatch(r"prefix_\d+", item.get("Alias", ""))
            and all(column in condition for column in ("org_id", "field_id", "token"))
            and directly_limited(parents)
            and not item.get("Filter")
        )
        if loops <= 1:
            upper = estimate
        elif root_point or owner_probe:
            upper = max(estimate, loops)
        else:
            upper = sum(math.ceil((value + 0.5) * loops) for value in counters)
        visits[relation] = visits.get(relation, 0) + upper
    return visits


@pytest.mark.latency
async def test_fixed_scale_confidential_reads(
    api, headers, application, tenants, admin_engine, monkeypatch
):
    tasks = [await new_task(api, auth) for auth in headers]
    seeded = seed_scale(admin_engine, tenants, tasks, application.state.processor.settings)

    def fail_decrypt(self, value):
        pytest.fail("A fixed-scale metadata read attempted to decrypt a confidential value")

    monkeypatch.setattr(Secrets, "decrypt", fail_decrypt)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    receipt = {
        "mode": "real_api",
        "scenario": "u01-confidential-fixed-scale-v1",
        "status": "running",
        "schema": "4.0",
        "run_id": tenants["run_id"],
        "fixture": {
            "orgs": 2,
            "fields_per_org": ROOTS,
            "values_per_org": ROOTS * VERSIONS,
            "versions_per_field_owner": VERSIONS,
            "org_scope_fields_per_org": ROOTS // 2,
            "task_scope_fields_per_org": ROOTS // 2,
            "org_ids": [str(org) for org in tenants["orgs"]],
            "user_ids": [str(user) for user in tenants["users"]],
            "task_ids": tasks,
            "retained_after_run": True,
            **seeded,
        },
        "global_before": tenants["global_before"],
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
                    visits = relation_visits(plan[0]["Plan"])
                    if name in SELECTIVE_PREFIXES and TOKEN_TABLE in statement:
                        inputs = parameters.values() if isinstance(parameters, dict) else parameters
                        assert "099" in inputs and "09:" in inputs
                        probes = {node["Alias"] for node in selective_token_ranges(plan[0]["Plan"])}
                        if probes != {"prefix_seed", "prefix_seek"}:
                            receipt["failures"].append(
                                f"{name}: missing org/token range on prefix B-tree for seed/seek"
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
                        {
                            "sql": statement,
                            "plan": plan,
                            "work": work,
                            "relation_visits": visits,
                            "visit_measure": "upper bound including per-loop rounding",
                        }
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
        run_output = OUTPUT / "runs" / tenants["run_id"]
        run_output.mkdir(parents=True, exist_ok=True)
        (run_output / "result.json").write_text(serialized)
        (OUTPUT / "result.json").write_text(serialized)
