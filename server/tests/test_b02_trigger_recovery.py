"""Real PostgreSQL regressions for B02 source producers and publication fences.

Failure inventory, fixed before the implementation change:
* A privileged maintenance source UPDATE must invalidate only its actual
  requirement scope and write metadata-only events atomically.
* Stale actor GUCs do not turn deterministic source invalidation into a human
  decision; its immutable event/audit remains a system action without a confirmer.
* Rollback removes source mutations, review revisions, audit/event rows and head
  increments together, while restoring the caller's transaction-local context.
* Savepoint-created events carry a subxid, not the top-level transaction ID;
  the backend must prove ownership of that exact live transaction-ID lock.
* Child release restores caller context; parent/savepoint rollback still removes
  every event. Committed events cannot be replayed through a nested trigger,
  even when their tuple is locked or an advisory lock uses the same number.
* A previously committed draft remains readable as stale after source mutation.
  New drafts still require a complete B02 manifest at deferred publication and
  cannot publish when requirement membership or review revisions changed.

Providers and inputs are synthetic; API authorization, parser, jobs, PostgreSQL
human gates and triggers are real. These tests do not start or stop a database service.
"""

import copy
import json
from pathlib import Path
from uuid import UUID

import pytest
from app.models.response_cards import DraftRun
from app.services import drafts
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from test_check import publish_draft
from test_requirement_confirmation import review
from test_response_cards import create_tender, phase_one_client

ARTIFACTS = Path(__file__).resolve().parents[2] / "data/work/b02-residual/trigger-regressions"


def record(name, **values):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / f"{name}.json").write_text(
        json.dumps(
            {
                "command": ".venv/bin/pytest server/tests/test_b02_trigger_recovery.py -q",
                "scenario": name,
                **values,
            },
            indent=2,
            default=str,
        )
        + "\n"
    )


@pytest.fixture
async def trigger_case(database, tmp_path):
    async with phase_one_client(database, tmp_path) as (api, app, headers, _):
        task, document, job, requirements = await create_tender(
            api, app, headers, tmp_path, suffix="trigger-a", confirmed=True
        )
        other_task, _, _, _ = await create_tender(
            api, app, headers, tmp_path, suffix="trigger-b", confirmed=True
        )
        yield {
            "api": api,
            "app": app,
            "headers": headers,
            "task": UUID(task),
            "other_task": UUID(other_task),
            "document": UUID(document),
            "job": UUID(job),
            "requirements": requirements,
            "requirement": UUID(requirements[0]["id"]),
            "chunk": UUID(requirements[0]["source"]["chunk_id"]),
        }


def checkpoints(connection, case):
    return {
        "reviews": connection.execute(
            text(
                "SELECT requirement_id,revision,state,review_hash,current_event_id "
                "FROM requirement_reviews WHERE task_id=:task ORDER BY requirement_id"
            ),
            {"task": case["task"]},
        ).all(),
        "events": connection.scalar(
            text("SELECT count(*) FROM requirement_review_events WHERE task_id=:task"),
            {"task": case["task"]},
        ),
        "head": connection.scalar(
            text("SELECT last_seq FROM task_event_heads WHERE task_id=:task"),
            {"task": case["task"]},
        ),
        "other_head": connection.scalar(
            text("SELECT last_seq FROM task_event_heads WHERE task_id=:task"),
            {"task": case["other_task"]},
        ),
        "audit": connection.scalar(
            text(
                "SELECT count(*) FROM audit_logs WHERE action IN "
                "('requirement.invalidated','requirement.repair_citation') "
                "AND details->>'task_id'=:task"
            ),
            {"task": str(case["task"])},
        ),
    }


def current_event_creation_proof(connection, case):
    """Inspect PostgreSQL's actual writer xid and lock owner, not an app marker."""
    return (
        connection.execute(
            text(
                "SELECT e.id,e.xmin::text AS writer_xid,pg_current_xact_id()::xid::text AS top_xid,"
                "pg_backend_pid() AS backend_pid,ARRAY(SELECT writing.pid FROM pg_catalog.pg_locks writing "
                "WHERE writing.locktype='transactionid' AND writing.transactionid=e.xmin "
                "AND writing.mode='ExclusiveLock' AND writing.granted) AS exclusive_owners "
                "FROM requirement_reviews v JOIN requirement_review_events e ON e.id=v.current_event_id WHERE v.requirement_id=:req"
            ),
            {"req": case["requirement"]},
        )
        .mappings()
        .one()
    )


def maintenance_context(connection, case, *, stale_actor=False):
    assert connection.scalar(
        text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
    ), "This regression requires the existing privileged fixture connection"


def mutate_source(connection, case, source):
    statements = {
        "requirement": (
            "UPDATE requirements SET condition=jsonb_build_object('inspected',true) WHERE id=:id",
            case["requirement"],
        ),
        "chunk": (
            "UPDATE chunks SET text=text||E'\\nAdditional synthetic source text' WHERE id=:id",
            case["chunk"],
        ),
        "document": ("UPDATE documents SET sha256=repeat('7',64) WHERE id=:id", case["document"]),
    }
    statement, identifier = statements[source]
    connection.execute(text(statement), {"id": identifier})


@pytest.mark.parametrize("source", ["requirement", "chunk", "document"])
@pytest.mark.parametrize("stale_actor", [False, True])
async def test_maintenance_source_invalidation_is_atomic(
    trigger_case, admin_engine, source, stale_actor
):
    case = trigger_case
    with admin_engine.begin() as connection:
        maintenance_context(connection, case, stale_actor=stale_actor)
        before = checkpoints(connection, case)
        mutate_source(connection, case, source)

        after = checkpoints(connection, case)
        changed = [row for row in after["reviews"] if row.state == "invalidated"]
        assert changed
        assert after["events"] == before["events"] + len(changed)
        assert after["head"] > before["head"]
        assert after["other_head"] == before["other_head"]
        assert after["audit"] == before["audit"] + len(changed)
        events = connection.execute(
            text(
                "SELECT source_id,payload FROM task_events "
                "WHERE task_id=:task AND seq>:seq AND jsonb_array_length(payload->'requirement_ids')>0"
            ),
            {"task": case["task"], "seq": before["head"]},
        ).all()
        assert events and all(row.source_id is None for row in events)
        assert all(
            set(row.payload)
            <= {"type", "extraction_job_id", "requirement_ids", "card_ids", "invalidate_all"}
            for row in events
        )
        audits = connection.execute(
            text(
                "SELECT actor_kind FROM audit_logs WHERE "
                "action='requirement.invalidated' AND details->>'task_id'=:task"
            ),
            {"task": str(case["task"])},
        ).all()
        assert audits and all(row.actor_kind == "system" for row in audits)
    value = await review(case["api"], case["headers"], case["requirement"])
    assert value["requirement"]["state"] == "invalidated"
    assert value["requirement"]["confirmed_at"] is None
    record(
        f"maintenance-{source}-stale-actor-{stale_actor}",
        task_id=case["task"],
        requirement_id=case["requirement"],
        invalidated=len(changed),
        source_ids=[row.source_id for row in events],
    )


async def test_source_and_events_rollback_together(trigger_case, admin_engine):
    case = trigger_case
    with admin_engine.begin() as connection:
        maintenance_context(connection, case)
        before = checkpoints(connection, case)
        before_text = connection.scalar(
            text("SELECT text FROM chunks WHERE id=:id"), {"id": case["chunk"]}
        )
        savepoint = connection.begin_nested()
        mutate_source(connection, case, "chunk")
        proof = current_event_creation_proof(connection, case)
        assert proof["writer_xid"] != proof["top_xid"]
        assert proof["exclusive_owners"] == [proof["backend_pid"]]
        assert checkpoints(connection, case)["head"] > before["head"]

        savepoint.rollback()
        assert checkpoints(connection, case) == before
        assert (
            connection.scalar(text("SELECT text FROM chunks WHERE id=:id"), {"id": case["chunk"]})
            == before_text
        )


@pytest.mark.parametrize("rollback_parent", [False, True], ids=["commit", "rollback"])
async def test_nested_source_audit_release_and_parent_outcome(
    trigger_case, admin_engine, rollback_parent
):
    case = trigger_case
    with admin_engine.begin() as connection:
        maintenance_context(connection, case)
        before = checkpoints(connection, case)
        outer = connection.begin_nested()
        first_child = connection.begin_nested()
        mutate_source(connection, case, "chunk")
        first = current_event_creation_proof(connection, case)
        assert first["writer_xid"] != first["top_xid"]
        assert first["exclusive_owners"] == [first["backend_pid"]]
        first_child.commit()

        assert current_event_creation_proof(connection, case)["id"] == first["id"]

        second_child = connection.begin_nested()
        mutate_source(connection, case, "requirement")
        second = current_event_creation_proof(connection, case)
        assert second["writer_xid"] not in {second["top_xid"], first["writer_xid"]}
        assert second["exclusive_owners"] == [second["backend_pid"]]
        assert second["id"] != first["id"]
        second_child.commit()

        if rollback_parent:
            outer.rollback()
            assert checkpoints(connection, case) == before
        else:
            outer.commit()
            after = checkpoints(connection, case)
            assert after["events"] == before["events"] + 2
            assert after["audit"] == before["audit"] + 2
            assert after["other_head"] == before["other_head"]
            assert current_event_creation_proof(connection, case)["id"] == second["id"]

        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    shown = await review(case["api"], case["headers"], case["requirement"])
    assert shown["requirement"]["state"] == ("confirmed" if rollback_parent else "invalidated")
    record(
        f"nested-audit-{'rollback' if rollback_parent else 'commit'}",
        requirement_id=case["requirement"],
        first_writer_xid=first["writer_xid"],
        second_writer_xid=second["writer_xid"],
        top_xid=second["top_xid"],
        final_state=shown["requirement"]["state"],
    )


@pytest.mark.parametrize("fake_advisory_lock", [False, True], ids=["tuple-lock", "advisory-lock"])
async def test_committed_event_cannot_forge_nested_system_audit(
    trigger_case, admin_engine, fake_advisory_lock
):
    case = trigger_case
    with admin_engine.begin() as connection:
        maintenance_context(connection, case)
        mutate_source(connection, case, "chunk")
        committed = current_event_creation_proof(connection, case)
        assert committed["exclusive_owners"] == [committed["backend_pid"]]
        event_id = committed["id"]

    with admin_engine.begin() as connection:
        maintenance_context(connection, case)
        # A tuple lock only changes locking metadata; it does not make its
        # historical inserting transaction belong to this backend.
        connection.execute(
            text("SELECT id FROM requirement_review_events WHERE id=:id FOR UPDATE"),
            {"id": event_id},
        )
        old = current_event_creation_proof(connection, case)
        assert old["id"] == event_id and old["writer_xid"] == committed["writer_xid"]
        assert old["exclusive_owners"] == []
        original_audits = connection.scalar(
            text("SELECT count(*) FROM audit_logs WHERE object_id=:req"),
            {"req": case["requirement"]},
        )
        # A temporary test relay supplies a genuine nested trigger call and the
        # exact historical payload. The production guard must still reject it;
        # only checking pg_trigger_depth or matching JSON would accept a forgery.
        connection.execute(
            text("CREATE TEMP TABLE b02_audit_relay_request(event_id uuid NOT NULL) ON COMMIT DROP")
        )
        connection.execute(
            text("""
            CREATE FUNCTION pg_temp.b02_audit_relay() RETURNS trigger
            LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
            BEGIN
              INSERT INTO public.audit_logs(id,actor_token_id,action,object_id,details)
              SELECT gen_random_uuid(),NULL,
                CASE WHEN e.action='source_repair' THEN 'requirement.repair_citation' ELSE 'requirement.invalidated' END,
                e.requirement_id,jsonb_build_object('task_id',e.task_id,'extraction_job_id',e.extraction_job_id,
                  'requirement_id',e.requirement_id,'event_id',e.id,'revision',e.revision,'actor_kind','system')
              FROM public.requirement_review_events e WHERE e.id=NEW.event_id;
              RETURN NULL;
            END $$;
        """)
        )
        connection.execute(
            text(
                "CREATE TRIGGER b02_audit_relay AFTER INSERT ON pg_temp.b02_audit_relay_request FOR EACH ROW EXECUTE FUNCTION pg_temp.b02_audit_relay()"
            )
        )
        connection.execute(text("GRANT INSERT ON pg_temp.b02_audit_relay_request TO bid_app"))
        connection.execute(text("REVOKE ALL ON FUNCTION pg_temp.b02_audit_relay() FROM PUBLIC"))
        connection.execute(text("GRANT EXECUTE ON FUNCTION pg_temp.b02_audit_relay() TO bid_app"))
        connection.execute(text("SET LOCAL ROLE bid_app"))
        if fake_advisory_lock:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(CAST(:key AS bigint))"),
                {"key": old["writer_xid"]},
            )
            assert connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_locks WHERE locktype='advisory' AND pid=pg_backend_pid() AND granted)"
                )
            )
        with pytest.raises(
            DBAPIError, match="requirement invalidation audit requires immutable event"
        ):
            with connection.begin_nested():
                connection.execute(
                    text("INSERT INTO pg_temp.b02_audit_relay_request VALUES(:event)"),
                    {"event": event_id},
                )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM audit_logs WHERE object_id=:req"),
                {"req": case["requirement"]},
            )
            == original_audits
        )
        assert current_event_creation_proof(connection, case)["exclusive_owners"] == []
        connection.execute(text("RESET ROLE"))
        connection.execute(text("DROP TABLE pg_temp.b02_audit_relay_request"))
        connection.execute(text("DROP FUNCTION pg_temp.b02_audit_relay()"))
    record(
        f"historical-event-forgery-{'advisory' if fake_advisory_lock else 'tuple'}",
        event_id=event_id,
        requirement_id=case["requirement"],
        committed_writer_xid=committed["writer_xid"],
        forged_audit_rejected=True,
    )


async def test_event_write_failure_restores_context_and_business_state(trigger_case, admin_engine):
    case = trigger_case
    with admin_engine.begin() as connection:
        maintenance_context(connection, case)
        before = checkpoints(connection, case)
        # Reject new events for this task inside a rolled-back savepoint. Existing
        # history and every production trigger remain intact during the failure.
        with pytest.raises(DBAPIError, match="synthetic_event_storage_failure"):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "ALTER TABLE task_events ADD CONSTRAINT synthetic_event_storage_failure "
                        f"CHECK (task_id <> '{case['task']}'::uuid) NOT VALID"
                    )
                )
                mutate_source(connection, case, "requirement")

        assert checkpoints(connection, case) == before
        assert not connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_constraint "
                "WHERE conrelid='task_events'::regclass "
                "AND conname='synthetic_event_storage_failure')"
            )
        )


async def test_committed_draft_becomes_readable_stale_history(trigger_case, admin_engine):
    case = trigger_case
    published = await publish_draft(
        case["api"], case["app"], case["headers"], str(case["task"]), str(case["job"])
    )
    draft_id = published["draft_id"]
    with admin_engine.begin() as connection:
        maintenance_context(connection, case)
        assert connection.scalar(
            text("SELECT requirement_draft_current(:draft)"),
            {"draft": UUID(draft_id)},
        )
        mutate_source(connection, case, "requirement")
        assert not connection.scalar(
            text("SELECT requirement_draft_current(:draft)"),
            {"draft": UUID(draft_id)},
        )
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    shown = await case["api"].get(f"/drafts/{draft_id}", headers=case["headers"])
    assert shown.status_code == 200, shown.text
    assert shown.json()["data"]["validity"] == "stale"
    record("historical-draft", task_id=case["task"], draft_id=draft_id, validity="stale")


@pytest.mark.parametrize(
    "fault", ["legacy_ids", "missing_binding", "duplicate_member", "change_before_commit"]
)
async def test_deferred_draft_gate_rejects_invalid_new_publication(
    trigger_case, monkeypatch, fault
):
    case = trigger_case
    queued = await case["api"].post(
        f"/tasks/{case['task']}/drafts",
        headers=case["headers"],
        json={"extraction_job_id": str(case["job"])},
    )
    assert queued.status_code == 200, queued.text
    job_id = UUID(queued.json()["data"]["job_id"])
    observed = []
    original = drafts.complete_draft

    def invalid_manifest(mapper, connection, run):
        if run.generation_job_id != job_id or fault == "change_before_commit":
            return
        manifest = copy.deepcopy(run.input_manifest)
        entries = manifest["requirements"]
        if fault == "legacy_ids":
            manifest["requirements"] = [entry["requirement_id"] for entry in entries]
        elif fault == "missing_binding":
            for entry in entries:
                entry.pop("requirement_review")
        else:
            assert len(entries) >= 2
            entries[1] = copy.deepcopy(entries[0])
        run.input_manifest = manifest

    async def complete_with_failure(session, job, storage):
        result = await original(session, job, storage)
        if job.id == job_id:
            if fault == "change_before_commit":
                await session.execute(
                    text(
                        "UPDATE requirements SET condition=jsonb_build_object('during_publication',true) WHERE id=:id"
                    ),
                    {"id": case["requirement"]},
                )
            try:
                await session.execute(text("SET CONSTRAINTS requirement_draft_gate IMMEDIATE"))
            except DBAPIError as exc:
                observed.append(
                    (
                        getattr(exc.orig, "sqlstate", None),
                        "requirement review manifest is stale" in str(exc.orig),
                    )
                )
                raise
        return result

    monkeypatch.setattr(drafts, "complete_draft", complete_with_failure)
    event.listen(DraftRun, "before_insert", invalid_manifest)
    try:
        await case["app"].state.processor(str(job_id))
    finally:
        event.remove(DraftRun, "before_insert", invalid_manifest)
    assert observed == [("23514", True)]
    status = await case["api"].get(f"/jobs/{job_id}", headers=case["headers"])
    assert status.status_code == 200 and status.json()["data"]["status"] == "failed", status.text
    async with case["app"].state.db.transaction() as session:
        assert (
            await session.scalar(
                text("SELECT count(*) FROM draft_runs WHERE generation_job_id=:job"),
                {"job": job_id},
            )
            == 0
        )
        assert await session.scalar(
            text("SELECT requirement_review_current(:req)"),
            {"req": case["requirement"]},
        )
    record(
        f"draft-publication-{fault}",
        task_id=case["task"],
        job_id=job_id,
        db_sqlstate=observed[0][0],
        persisted_drafts=0,
    )
