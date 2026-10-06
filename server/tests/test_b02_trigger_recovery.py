"""Real PostgreSQL regressions for B02 source producers and publication fences.

Failure inventory, fixed before the implementation change:
* A privileged maintenance source UPDATE with no org GUC must invalidate only
  its actual requirement scope, write metadata-only events atomically and restore
  the missing context before the next statement.
* Stale actor GUCs do not turn deterministic source invalidation into a human
  decision; its immutable event/audit remains a system action without a confirmer.
* An explicit foreign org is not inferred away. A runtime role, including one
  selected by a privileged session with SET LOCAL ROLE, never inherits permission
  to derive an org context.
* Rollback removes source mutations, review revisions, audit/event rows and head
  increments together, while restoring the caller's transaction-local context.
* A previously committed draft remains readable as stale after source mutation.
  New drafts still require a complete B02 manifest at deferred publication and
  cannot publish when requirement membership or review revisions changed.

Providers and inputs are synthetic; API authorization, parser, jobs, PostgreSQL
RLS and triggers are real. These tests do not start or stop a database service.
"""

import copy
import json
from pathlib import Path
from uuid import UUID

import pytest
from app.models.response_cards import DraftRun
from app.services import drafts
from app.services.auth import Identity, set_actor_context
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from test_check import publish_draft
from test_requirement_confirmation import review
from test_response_cards import create_tender, phase_one_client
from test_team_workflow_membership import add_member, person, workflow

ARTIFACTS = Path(__file__).resolve().parents[2] / "data/work/b02-fixes/trigger-regressions"


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
async def trigger_case(tenants, tmp_path):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task, document, job, requirements = await create_tender(
            api, app, headers[0], tmp_path, suffix="trigger-a", confirmed=True
        )
        other_task, _, _, _ = await create_tender(
            api, app, headers[1], tmp_path, suffix="trigger-b", confirmed=True
        )
        yield {
            "api": api,
            "app": app,
            "headers": headers,
            "org": tenants["orgs"][0],
            "other_org": tenants["orgs"][1],
            "other_user": tenants["users"][1],
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


def maintenance_context(connection, case, *, org="", stale_actor=False):
    assert connection.scalar(
        text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
    ), "This regression requires the existing privileged fixture connection"
    connection.execute(
        text(
            "SELECT set_config('app.current_org',:org,true),"
            "set_config('app.actor_kind',:kind,true),"
            "set_config('app.actor_user_id',:actor,true),"
            "set_config('app.actor_token_id','',true),"
            "set_config('app.actor_scopes','[]',true)"
        ),
        {
            "org": org,
            "kind": "session" if stale_actor else "",
            "actor": str(case["other_user"]) if stale_actor else "",
        },
    )


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
async def test_maintenance_source_invalidation_derives_only_missing_org(
    trigger_case, admin_engine, source, stale_actor
):
    case = trigger_case
    with admin_engine.begin() as connection:
        maintenance_context(connection, case, stale_actor=stale_actor)
        before = checkpoints(connection, case)
        mutate_source(connection, case, source)
        assert connection.scalar(text("SELECT current_setting('app.current_org',true)")) == ""
        after = checkpoints(connection, case)
        changed = [row for row in after["reviews"] if row.state == "invalidated"]
        assert changed
        assert after["events"] == before["events"] + len(changed)
        assert after["head"] > before["head"]
        assert after["other_head"] == before["other_head"]
        assert after["audit"] == before["audit"] + len(changed)
        events = connection.execute(
            text(
                "SELECT org_id,source_id,payload FROM task_events "
                "WHERE task_id=:task AND seq>:seq AND jsonb_array_length(payload->'requirement_ids')>0"
            ),
            {"task": case["task"], "seq": before["head"]},
        ).all()
        assert events and all(row.org_id == case["org"] and row.source_id is None for row in events)
        assert all(
            set(row.payload)
            <= {"type", "extraction_job_id", "requirement_ids", "card_ids", "invalidate_all"}
            for row in events
        )
        audits = connection.execute(
            text(
                "SELECT actor_user_id,actor_kind FROM audit_logs WHERE "
                "action='requirement.invalidated' AND details->>'task_id'=:task"
            ),
            {"task": str(case["task"])},
        ).all()
        assert audits and all(
            row.actor_user_id is None and row.actor_kind == "system" for row in audits
        )
    value = await review(case["api"], case["headers"][0], case["requirement"])
    assert value["requirement"]["state"] == "invalidated"
    assert value["requirement"]["confirmed_by_user_id"] is None
    record(
        f"maintenance-{source}-stale-actor-{stale_actor}",
        task_id=case["task"],
        requirement_id=case["requirement"],
        invalidated=len(changed),
        event_org=case["org"],
        restored_org="",
        source_ids=[row.source_id for row in events],
    )


async def test_explicit_foreign_org_is_not_reinterpreted(trigger_case, admin_engine):
    case = trigger_case
    with admin_engine.begin() as connection:
        maintenance_context(connection, case, org=str(case["other_org"]))
        before = checkpoints(connection, case)
        with pytest.raises(DBAPIError, match="Task event org mismatch"):
            with connection.begin_nested():
                mutate_source(connection, case, "requirement")
        assert connection.scalar(text("SELECT current_setting('app.current_org',true)")) == str(
            case["other_org"]
        )
        assert checkpoints(connection, case) == before


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
        assert checkpoints(connection, case)["head"] > before["head"]
        assert connection.scalar(text("SELECT current_setting('app.current_org',true)")) == ""
        savepoint.rollback()
        assert checkpoints(connection, case) == before
        assert (
            connection.scalar(text("SELECT text FROM chunks WHERE id=:id"), {"id": case["chunk"]})
            == before_text
        )
        assert connection.scalar(text("SELECT current_setting('app.current_org',true)")) == ""


async def test_event_write_failure_restores_context_and_business_state(trigger_case, admin_engine):
    case = trigger_case
    with admin_engine.begin() as connection:
        maintenance_context(connection, case)
        before = checkpoints(connection, case)
        # Tighten one permission inside a rolled-back savepoint to reproduce a
        # real event-storage failure. No trigger or acceptance gate is disabled.
        with pytest.raises(DBAPIError, match="permission denied for table task_events"):
            with connection.begin_nested():
                connection.execute(text("REVOKE INSERT ON task_events FROM bid_task_event_writer"))
                mutate_source(connection, case, "requirement")
        assert connection.scalar(text("SELECT current_setting('app.current_org',true)")) == ""
        assert checkpoints(connection, case) == before
        assert connection.scalar(
            text("SELECT has_table_privilege('bid_task_event_writer','task_events','INSERT')")
        )


@pytest.mark.parametrize("org_context", ["missing", "foreign"])
async def test_set_role_runtime_never_inherits_maintenance_derivation(
    trigger_case, admin_engine, org_context
):
    case = trigger_case
    with admin_engine.begin() as connection:
        maintenance_context(
            connection, case, org="" if org_context == "missing" else str(case["other_org"])
        )
        connection.execute(text("SET LOCAL ROLE bid_app"))
        assert connection.scalar(text("SELECT current_user")) == "bid_app"
        changed = connection.execute(
            text("UPDATE requirements SET condition='{}'::jsonb WHERE id=:id"),
            {"id": case["requirement"]},
        )
        assert changed.rowcount == 0
        with pytest.raises(DBAPIError, match="Task event org mismatch"):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "SELECT public.append_task_event(:org,:task,'board_changed',"
                        "jsonb_build_object('type','board_changed','invalidate_all',true,"
                        "'requirement_ids','[]'::jsonb,'card_ids','[]'::jsonb),NULL)"
                    ),
                    {"org": case["org"], "task": case["task"]},
                )


async def test_live_org_role_remains_required_for_human_review(trigger_case, admin_engine):
    case = trigger_case
    uid, _ = await person(case["api"], admin_engine, case["org"], role="viewer")
    state = await workflow(case["api"], case["headers"][0], str(case["task"]))
    added = await add_member(
        case["api"],
        case["headers"][0],
        str(case["task"]),
        uid,
        state["revision"],
        role="contributor",
    )
    assert added.status_code == 200, added.text
    with pytest.raises(DBAPIError, match="requirement human role required"):
        async with case["app"].state.db.transaction(case["org"]) as session:
            # Emulate stale actor grants; the DB must intersect the live membership.
            await set_actor_context(
                session, Identity(uid, case["org"], {"task:read", "req:confirm"}, "bidder")
            )
            await session.execute(
                text("UPDATE requirement_reviews SET revision=revision+1 WHERE requirement_id=:id"),
                {"id": case["requirement"]},
            )


async def test_committed_draft_becomes_readable_stale_history(trigger_case, admin_engine):
    case = trigger_case
    published = await publish_draft(
        case["api"], case["app"], case["headers"][0], str(case["task"]), str(case["job"])
    )
    draft_id = published["draft_id"]
    with admin_engine.begin() as connection:
        maintenance_context(connection, case)
        assert connection.scalar(
            text("SELECT requirement_draft_current(:org,:draft)"),
            {"org": case["org"], "draft": UUID(draft_id)},
        )
        mutate_source(connection, case, "requirement")
        assert not connection.scalar(
            text("SELECT requirement_draft_current(:org,:draft)"),
            {"org": case["org"], "draft": UUID(draft_id)},
        )
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    shown = await case["api"].get(f"/drafts/{draft_id}", headers=case["headers"][0])
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
        headers=case["headers"][0],
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
        await case["app"].state.processor(str(case["org"]), str(job_id))
    finally:
        event.remove(DraftRun, "before_insert", invalid_manifest)
    assert observed == [("23514", True)]
    status = await case["api"].get(f"/jobs/{job_id}", headers=case["headers"][0])
    assert status.status_code == 200 and status.json()["data"]["status"] == "failed", status.text
    async with case["app"].state.db.transaction(case["org"]) as session:
        assert (
            await session.scalar(
                text("SELECT count(*) FROM draft_runs WHERE generation_job_id=:job"),
                {"job": job_id},
            )
            == 0
        )
        assert await session.scalar(
            text("SELECT requirement_review_current(:org,:req)"),
            {"org": case["org"], "req": case["requirement"]},
        )
    record(
        f"draft-publication-{fault}",
        task_id=case["task"],
        job_id=job_id,
        db_sqlstate=observed[0][0],
        persisted_drafts=0,
    )
