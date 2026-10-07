"""Fixed-scale DB-backed attachment metadata acceptance before implementation.

Failure inventory: scanning immutable history for current browse/detail; one review
or author query per row; offset/COUNT pagination; ciphertext or storage reads for
safe metadata; cross-org continuations; unrestricted revision-list nesting; one
indivisible response exceeding the 1 MiB output cap. The seed preserves all real
migration gates and uses two orgs, 10,000 roots and 100,000 revisions/files per org.
"""

import hashlib
import json
import math
from pathlib import Path
from time import perf_counter
from uuid import UUID

import pytest
from app.core.security import Secrets
from sqlalchemy import event, text
from test_attachment_archive import BASE
from test_attachment_archive import (
    api as api,  # noqa: F401 -- register the local Result 4.0 fixture
)
from test_management_profiles_scale import plan_work

ROOTS = 10_000
REVISIONS = 10
REVIEW_HISTORY = 1001
SAMPLES = 20
OUTPUT = Path(__file__).resolve().parents[2] / "data/work/attachment-archive-acceptance/scale"


def stable_id(org, kind, n, revision=None):
    return str(UUID(hashlib.md5(f"{org}/{kind}/{n}/{revision}".encode()).hexdigest()))


def seed(admin_engine, tenants, settings, pdf_bytes):
    """Bulk owner fixture; real AFTER/FK/CHECK gates stay enabled during every insert."""
    crypto = Secrets.for_data(settings)
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    metadata_hash = hashlib.sha256(
        json.dumps(
            {"kind": "contract", "label": "Synthetic scale archive"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    with admin_engine.begin() as connection:
        for org, user in zip(tenants["orgs"], tenants["users"], strict=True):
            connection.execute(
                text(
                    "SELECT set_config('app.current_org',:org,true),set_config('app.actor_user_id',:actor,true),set_config('app.actor_kind','session',true),set_config('app.actor_token_id','',true)"
                ),
                {"org": str(org), "actor": str(user)},
            )
            root_rows = [{"id": stable_id(org, "root", n), "n": n} for n in range(1, ROOTS + 1)]
            connection.execute(
                text(
                    "INSERT INTO attachment_archives(id,org_id,current_revision,state_version,active,custodian_user_id,reviewer_user_id,created_by,created_at) "
                    "SELECT id::uuid,:org,1,1,true,:actor,:actor,:actor,TIMESTAMPTZ '2026-01-01T00:00:00Z'+n*interval '1 second' "
                    "FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS x(id text,n int)"
                ),
                {"org": org, "actor": user, "rows": json.dumps(root_rows)},
            )
            for version in range(1, REVISIONS + 1):
                if version > 1:
                    connection.execute(
                        text(
                            "UPDATE attachment_archives SET current_revision=current_revision+1,state_version=state_version+1 WHERE org_id=:org"
                        ),
                        {"org": org},
                    )
                rows = []
                for n in range(1, ROOTS + 1):
                    revision_id, file_id = (
                        stable_id(org, "revision", n, version),
                        stable_id(org, "file", n, version),
                    )
                    rows.append(
                        {
                            "revision_id": revision_id,
                            "file_id": file_id,
                            "root": stable_id(org, "root", n),
                            "n": n,
                            "label": crypto.encrypt(
                                json.dumps(
                                    {
                                        "org_id": str(org),
                                        "id": revision_id,
                                        "value": "Synthetic scale archive",
                                    }
                                )
                            ),
                            "upload_name": crypto.encrypt(
                                json.dumps(
                                    {"org_id": str(org), "id": file_id, "value": "synthetic.pdf"}
                                )
                            ),
                            "request": stable_id(org, "request", n, version),
                        }
                    )
                parameters = {
                    "org": org,
                    "actor": user,
                    "version": version,
                    "rows": json.dumps(rows),
                    "sha": sha,
                    "meta": metadata_hash,
                    "size": len(pdf_bytes),
                }
                connection.execute(
                    text(
                        "INSERT INTO attachment_revisions(id,org_id,attachment_id,revision,kind,label_encrypted,metadata_sha256,created_by,request_id,payload_hash,created_at) "
                        "SELECT revision_id::uuid,:org,root::uuid,:version,'contract',label,:meta,:actor,request::uuid,:sha,"
                        "TIMESTAMPTZ '2026-01-01T00:00:00Z'+n*interval '1 second'+:version*interval '1 microsecond' "
                        "FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS x(revision_id text,root text,label text,request text,n int)"
                    ),
                    parameters,
                )
                connection.execute(
                    text(
                        "INSERT INTO attachment_files(id,org_id,attachment_id,attachment_revision_id,created_by,file,storage_key,upload_name_encrypted) "
                        "SELECT file_id::uuid,:org,root::uuid,revision_id::uuid,:actor,"
                        "jsonb_build_object('name','attachment-'||revision_id||'.pdf','media_type','application/pdf','sha256',CAST(:sha AS text),'size_bytes',CAST(:size AS bigint),'page_count',2),"
                        "'org/'||CAST(:org AS text)||'/attachment/'||root||'/'||revision_id||'/'||:sha||'.pdf',upload_name "
                        "FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS x(file_id text,revision_id text,root text,upload_name text)"
                    ),
                    parameters,
                )
                connection.execute(
                    text(
                        "INSERT INTO audit_logs(id,org_id,actor_user_id,action,object_id,details,created_at) "
                        "SELECT request::uuid,:org,:actor,CASE WHEN :version=1 THEN 'attachment.create' ELSE 'attachment.revise' END,root::uuid,"
                        "jsonb_build_object('new_revision_id',revision_id,'revision',CAST(:version AS integer),'request_id',request,'payload_hash',CAST(:sha AS text)),"
                        "TIMESTAMPTZ '2026-01-01T00:00:00Z'+n*interval '1 second'+:version*interval '1 microsecond' "
                        "FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS x(request text,revision_id text,root text,n int)"
                    ),
                    parameters,
                )
            prior = None
            for decision_number in range(1, REVIEW_HISTORY + 1):
                review_id = stable_id(org, "review", 1, decision_number)
                approve = decision_number % 2 == 1
                connection.execute(
                    text(
                        "UPDATE attachment_archives SET state_version=state_version+1 WHERE org_id=:org AND id=:root"
                    ),
                    {"org": org, "root": stable_id(org, "root", 1)},
                )
                connection.execute(
                    text(
                        "INSERT INTO attachment_reviews(id,org_id,attachment_id,attachment_revision_id,file_id,"
                        "original_sha256,metadata_sha256,prior_review_id,decision,reason,reviewed_by,reviewed_at,request_id,payload_hash,created_at) "
                        "VALUES(:id,:org,:root,:revision,:file,:sha,:meta,:prior,:decision,:reason,:actor,"
                        "TIMESTAMPTZ '2026-01-02T00:00:00Z'+:n*interval '1 microsecond',:request,:sha,"
                        "TIMESTAMPTZ '2026-01-02T00:00:00Z'+:n*interval '1 microsecond')"
                    ),
                    {
                        "id": review_id,
                        "org": org,
                        "root": stable_id(org, "root", 1),
                        "revision": stable_id(org, "revision", 1, REVISIONS),
                        "file": stable_id(org, "file", 1, REVISIONS),
                        "sha": sha,
                        "meta": metadata_hash,
                        "prior": prior,
                        "actor": user,
                        "n": decision_number,
                        "decision": "approve" if approve else "revoke",
                        "reason": "accepted_for_internal_use" if approve else "withdrawn_by_org",
                        "request": stable_id(org, "review-request", 1, decision_number),
                    },
                )
                prior = review_id
        for table in (
            "attachment_archives",
            "attachment_revisions",
            "attachment_files",
            "attachment_reviews",
            "audit_logs",
            "memberships",
        ):
            connection.execute(text(f"ANALYZE {table}"))


@pytest.mark.latency
async def test_fixed_scale_bounded_metadata_and_exact_history(
    api, headers, tenants, application, admin_engine, pdf_bytes, monkeypatch
):
    seed(admin_engine, tenants, application.state.processor.settings, pdf_bytes)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    receipt = {
        "scenario": "attachment-fixed-scale-v1",
        "mode": "real_api",
        "status": "running",
        "fixture": {
            "orgs": 2,
            "roots_per_org": ROOTS,
            "revisions_per_org": ROOTS * REVISIONS,
            "files_per_org": ROOTS * REVISIONS,
            "reviews_on_first_current_revision": REVIEW_HISTORY,
        },
        "fixture_pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "rerun": ".venv/bin/pytest -q server/tests/test_attachment_scale.py -m latency --basetemp=data/work/attachment-archive-acceptance/scale/tmp --junitxml=data/work/attachment-archive-acceptance/scale/junit.xml",
        "measurements": [],
    }
    captured = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        captured.append((statement, parameters))

    async def no_storage(*args, **kwargs):
        raise AssertionError("metadata read fetched attachment bytes")

    monkeypatch.setattr(application.state.storage, "read", no_storage)
    monkeypatch.setattr(application.state.storage, "read_bounded", no_storage)
    engine = application.state.db.engine
    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        first = await api.get(BASE, headers=headers[0], params={"limit": 50})
        assert first.status_code == 200, first.text
        root = first.json()["items"][0]["id"]
        cases = [
            ("list_25", BASE, {"limit": 25}),
            ("page_100", BASE, {"limit": 100}),
            ("next_page", BASE, {"limit": 50, "cursor": first.json()["data"]["next_cursor"]}),
            ("kind", BASE, {"kind": "contract", "limit": 25}),
            ("show", f"{BASE}/{root}", {}),
            ("history", f"{BASE}/{root}/revisions", {"limit": 5}),
            (
                "reviews",
                f"{BASE}/revisions/{stable_id(tenants['orgs'][0], 'revision', 1, REVISIONS)}/reviews",
                {"limit": 25},
            ),
        ]
        for name, path, params in cases:
            await api.get(path, headers=headers[0], params=params)
            timings, sizes, selects, control_counts = [], [], [], []
            queries = []
            for _ in range(SAMPLES):
                captured.clear()
                started = perf_counter()
                response = await api.get(path, headers=headers[0], params=params)
                timings.append((perf_counter() - started) * 1000)
                assert response.status_code == 200, response.text
                value = response.json()
                assert len(response.content) <= 1024 * 1024
                if name != "show":
                    assert len(value["items"]) <= params.get("limit", 50)
                    assert value["data"]["limit"] == params.get("limit", 50)
                    assert all(row["org_id"] == headers[0]["X-Org-Id"] for row in value["items"])
                    if name != "reviews":
                        assert all(
                            row["revised_at"] and row["revised_by"] == str(tenants["users"][0])
                            for row in value["items"]
                        )
                if name == "show":
                    assert value["data"]["attachment"]["revised_by"] == str(tenants["users"][0])
                assert "label" not in json.dumps(value) and "storage_key" not in json.dumps(value)
                sizes.append(len(response.content))
                queries = list(captured)
                read_sql = [
                    pair
                    for pair in queries
                    if pair[0].lstrip().upper().startswith("SELECT")
                    and "set_config(" not in pair[0].lower()
                ]
                selects.append(len(read_sql))
                control_counts.append(len(queries) - len(read_sql))
                assert len(read_sql) <= 12, f"{name}: N+1 SELECT statements: {len(read_sql)}"
                assert all("OFFSET" not in statement.upper() for statement, _ in read_sql)
            plans = []
            async with application.state.db.transaction(tenants["orgs"][0]) as session:
                connection = await session.connection()
                for statement, parameters in queries:
                    if not statement.lstrip().upper().startswith("SELECT") or not any(
                        table in statement
                        for table in (
                            "attachment_archives",
                            "attachment_revisions",
                            "attachment_files",
                            "attachment_reviews",
                            "audit_logs",
                        )
                    ):
                        continue
                    explained = await connection.exec_driver_sql(
                        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, parameters
                    )
                    plan = explained.scalar_one()
                    work = plan_work(plan[0]["Plan"])
                    visits = {}
                    for node in work:
                        relation = node["relation"]
                        if relation in {
                            "attachment_revisions",
                            "attachment_files",
                            "attachment_reviews",
                            "audit_logs",
                        }:
                            visits[relation] = (
                                visits.get(relation, 0)
                                + (node["rows"] + node["rows_removed"]) * node["loops"]
                            )
                    assert all(
                        visited <= 8 * (params.get("limit", 50) + 1) for visited in visits.values()
                    ), (name, visits)
                    plans.append(
                        {"sql": statement, "plan": plan, "history_relation_visits": visits}
                    )
            assert plans
            receipt["measurements"].append(
                {
                    "case": name,
                    "samples": SAMPLES,
                    "warm_p95_ms": round(sorted(timings)[math.ceil(SAMPLES * 0.95) - 1], 3),
                    "max_result_bytes": max(sizes),
                    "max_read_queries": max(selects),
                    "max_context_queries": max(control_counts),
                    "plans": plans,
                }
            )
        other = await api.get(BASE, headers=headers[1], params={"limit": 50})
        assert other.status_code == 200
        assert not {row["id"] for row in first.json()["items"]} & {
            row["id"] for row in other.json()["items"]
        }
        assert (
            await api.get(
                BASE,
                headers=headers[1],
                params={"limit": 50, "cursor": first.json()["data"]["next_cursor"]},
            )
        ).status_code in {404, 409}
        assert (await api.get(BASE, headers=headers[0], params={"limit": 101})).status_code == 422
        receipt["status"] = "passed"
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
        if receipt["status"] == "running":
            receipt["status"] = "failed_before_measurements_completed"
        (OUTPUT / "result.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
        )
