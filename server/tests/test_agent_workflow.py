"""A01 API/worker scenarios, using only synthetic MockTransport responses.

Failure matrix before implementation: parent/child slot deadlock, unreviewed
cards included in drafts, replay duplicates paid work, unknown calls auto-resend,
recovery resets limits, cancellation leaves children active, foreign cursors leak
sessions, budget questions are bypassed, raw conversation leaks, schema changes
silently execute an old plan, and the queue dispatch escapes its transaction.
Artifacts are reproducible with --basetemp=data/work/agent-validation.
"""

import json
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from app.models.agent import AgentJobLink, AgentMessage, AgentSession, AgentStep
from app.models.entities import AuditLog, Job, VendorCall
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from task_fixtures import reviewer_header
from test_card_generation import DraftVendor, drafting_client, slots
from test_response_cards import (
    create_tender,
    require_action,
    sanitized_artifact,
    select_real_materials,
)

SCOPES = (
    "task:read",
    "job:read",
    "card:read",
    "card:generate",
    "draft:read",
    "draft:run",
    "resource:read",
    "certificate:read",
    "certificate:file:read",
    "profile:read",
    "evidence:source:read",
)


def workflow_client(tenants, tmp_path):
    # A01 bounds vendor USD as well as platform charges. The drafting fixture's
    # sale prices alone do not establish a known vendor-price admission bound.
    return drafting_client(
        tenants,
        tmp_path,
        llm_input_usd_per_mtok=1,
        llm_output_usd_per_mtok=1,
    )


def start_body(extraction, **limits):
    return {
        "extraction_job_id": extraction,
        "message": "Prepare proposals, pause, then assemble.",
        "requested_scopes": list(SCOPES),
        "idempotency_key": str(uuid4()),
        "limits": {
            "max_vendor_usd": "2",
            "max_platform_charge": "2",
            "billing_currency": "USD",
            **limits,
        },
    }


def decision_reply(action):
    return httpx.Response(
        200,
        json={
            "model": "synthetic-model",
            "choices": [
                {"finish_reason": "stop", "message": {"content": json.dumps({"action": action})}}
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 40},
        },
    )


async def advance(api, app, header, session_id, *, maximum=30):
    from app.jobs.agent import wake

    for _ in range(maximum):
        data = (await api.get(f"/v4/agent-sessions/{session_id}", headers=header)).json()["data"]
        state = data["session"]
        if state["state"] in {"paused", "completed", "partial", "failed", "cancelled"}:
            return data
        async with app.state.db.transaction(UUID(header["X-Org-Id"])) as db:
            links = (
                await db.scalars(
                    select(AgentJobLink).where(
                        AgentJobLink.session_id == UUID(session_id), AgentJobLink.owned.is_(True)
                    )
                )
            ).all()
            queued = []
            for link in links:
                job = await db.get(Job, link.job_id)
                if job.status == "queued":
                    queued.append(str(job.id))
        for job_id in queued:
            await app.state.processor(header["X-Org-Id"], job_id)
        await wake(app.state.processor, UUID(header["X-Org-Id"]), UUID(session_id))
    raise AssertionError("Agent failed to reach a checkpoint")


async def checkpoint_receipt(app, header, session_id, expected_state, *, reason=None):
    """A committed pause/terminal checkpoint releases the binding and the worker lease."""
    async with app.state.db.transaction(UUID(header["X-Org-Id"])) as session:
        state = await session.get(AgentSession, UUID(session_id))
        assert state is not None and state.state == expected_state
        assert state.current_job_id is None and state.current_run_id is None
        controllers = list(
            (
                await session.scalars(
                    select(Job)
                    .join(AgentJobLink, AgentJobLink.job_id == Job.id)
                    .where(AgentJobLink.session_id == state.id, AgentJobLink.role == "controller")
                    .order_by(Job.finished_at.desc(), Job.id.desc())
                )
            ).all()
        )
        assert controllers
        assert all(
            job.status == "succeeded" and job.lease_until is None and job.finished_at
            for job in controllers
        )
        latest = controllers[0]
        assert latest.result["checkpoint_revision"] == state.revision
        assert latest.result["session_state"] == expected_state
        assert latest.result["disposition"] == (
            "paused" if expected_state == "paused" else "terminal"
        )
        if reason is not None:
            assert latest.result["stop_reason"] == reason
        return {
            "job_id": str(latest.id),
            "run_id": str(latest.run_id),
            "checkpoint_revision": state.revision,
            "state": state.state,
            "controller_status": latest.status,
            "binding_released": True,
            "lease_released": True,
            "stop_reason": latest.result["stop_reason"],
        }


@pytest.mark.parametrize(
    "publication_error", [False, True], ids=["complete", "publication-failure"]
)
async def test_proposals_human_review_and_confirmed_draft(
    tenants, tmp_path, admin_engine, monkeypatch, publication_error
):
    from app.jobs import agent as controller

    record_output = controller.record_tool_output
    publications = []

    async def observe_output(session, state, step, job, output, settings):
        # A nested command read may refresh its identity. The real SQL context
        # must name the collecting controller again before its guarded writes.
        context = (
            await session.execute(
                text(
                    "SELECT current_setting('app.actor_kind') AS kind, "
                    "current_setting('app.execution_job_id') AS job_id, "
                    "current_setting('app.execution_run_id') AS run_id, "
                    "current_setting('app.agent_step_id') AS step_id"
                )
            )
        ).one()
        assert context.kind == "worker"
        assert (context.job_id, context.run_id, context.step_id) == (
            str(job.id),
            str(job.run_id),
            str(step.id),
        )
        publications.append(
            {
                "command": step.command,
                "step_id": str(step.id),
                "created_by_job_id": str(step.created_by_job_id),
                "transition_job_id": str(job.id),
            }
        )
        if publication_error and step.command == "draft" and output.child_job_id is not None:
            raise IntegrityError("Synthetic checkpoint publication failure", {}, Exception())
        await record_output(session, state, step, job, output, settings)

    monkeypatch.setattr(controller, "record_tool_output", observe_output)
    async with workflow_client(tenants, tmp_path) as (api, app, headers, vendor, llm):
        header = headers[0]
        task, _, extraction, requirements = await create_tender(
            api, app, header, tmp_path, confirmed=True
        )
        await select_real_materials(api, header, task, tmp_path)
        actions = [
            {
                "kind": "tool",
                "call": {"command": "req list", "arguments": {"task": task, "job": extraction}},
                "input_refs": [],
            },
            {
                "kind": "tool",
                "call": {
                    "command": "card generate",
                    "arguments": {"task": task, "input": {"extraction_job_id": extraction}},
                },
                "input_refs": [],
            },
            {
                "kind": "tool",
                "call": {
                    "command": "draft",
                    "arguments": {"task": task, "input": {"extraction_job_id": extraction}},
                },
                "input_refs": [],
            },
            {"kind": "complete", "summary": "Confirmed draft assembled.", "output_refs": []},
        ]

        async def respond(sent):
            if "session_id" in sent:
                return decision_reply(actions.pop(0))
            from test_llm_providers import provider_reply

            return provider_reply("openai", DraftVendor.proposals(sent))

        vendor.respond = respond
        body = start_body(extraction)
        body["message"] += " Authorization: Bearer synthetic-private-context-token"
        started = await api.post(f"/v4/tasks/{task}/agent-sessions", headers=header, json=body)
        assert started.status_code == 200, started.text
        replay = await api.post(f"/v4/tasks/{task}/agent-sessions", headers=header, json=body)
        assert replay.json()["data"]["deduplicated"] is True
        session_id = started.json()["data"]["session"]["id"]
        paused = await advance(api, app, header, session_id)
        assert (
            paused["session"]["state"] == "paused" and paused["pause"]["action"] == "review_cards"
        )
        paused_checkpoint = await checkpoint_receipt(app, header, session_id, "paused")
        cards = await slots(api, header, task, extraction)
        assert len(cards) == len(requirements)
        assert all(row["card"]["confirmed_by"] is None for row in cards)
        async with app.state.db.transaction(tenants["orgs"][0]) as db:
            messages = (await db.scalars(select(AgentMessage))).all()
            assert all(row.content_enc != row.content for row in messages)
            controls = (await db.scalars(select(Job).where(Job.kind == "agent"))).all()
            assert all(row.status == "succeeded" and row.lease_until is None for row in controls)
        review_headers = {}
        for domain in ("technical", "commercial"):
            _, review_headers[domain] = await reviewer_header(
                api, admin_engine, tenants["orgs"][0], UUID(task), domain
            )
        for row in cards:
            review_header = review_headers[row["card"]["review_domain"]]
            pending = await require_action(api, review_header, row["card"], "submit")
            extra = {"reviewed_evidence_ids": [e["id"] for e in pending["evidence"]]}
            if pending["warning_codes"]:
                extra |= {
                    "reviewed_warning_codes": pending["warning_codes"],
                    "reason": "Reviewed synthetic source.",
                }
            await require_action(api, review_header, pending, "confirm", **extra)
        resumed = await api.post(
            f"/v4/agent-sessions/{session_id}/resume",
            headers=header,
            json={
                "expected_revision": paused["session"]["revision"],
                "pause_id": paused["pause"]["id"],
                "idempotency_key": str(uuid4()),
            },
        )
        assert resumed.status_code == 200, resumed.text
        final = await advance(api, app, header, session_id)
        expected = "partial" if publication_error else "completed"
        assert final["session"]["state"] == expected, final
        final_checkpoint = await checkpoint_receipt(
            app,
            header,
            session_id,
            expected,
            reason="agent_processing_failed" if publication_error else None,
        )
        async with app.state.db.transaction(tenants["orgs"][0]) as db:
            collected = list(
                (
                    await db.scalars(
                        select(AgentStep)
                        .where(
                            AgentStep.session_id == UUID(session_id),
                            AgentStep.child_job_id.is_not(None),
                        )
                        .order_by(AgentStep.ordinal)
                    )
                ).all()
            )
            assert [step.command for step in collected] == ["card generate", "draft"]
            assert all(step.created_by_job_id != step.last_transition_job_id for step in collected)
            assert collected[0].state == "completed"
            assert collected[1].state == ("failed" if publication_error else "completed")
            if publication_error:
                assert collected[1].error_code == "agent_processing_failed"
                failure = await db.scalar(
                    select(AuditLog).where(
                        AuditLog.action == "agent.tool.failed",
                        AuditLog.object_id == collected[1].id,
                    )
                )
                assert (
                    failure is not None
                    and failure.details["reason_code"] == "agent_processing_failed"
                )
            draft_job = await db.get(Job, collected[1].child_job_id)
            assert draft_job is not None and draft_job.status == "succeeded"
            draft_id = draft_job.result["draft_id"]
        draft = await api.get(f"/v4/drafts/{draft_id}", headers=header)
        assert draft.status_code == 200 and draft.json()["data"]["validity"] == "current"
        assert draft.json()["data"]["completion"] == "complete"
        assert "synthetic-private-context-token" not in json.dumps(vendor.bodies)
        assert final["session"]["steps_used"] > paused["session"]["steps_used"]
        (tmp_path / "agent-review-draft.json").write_text(
            json.dumps(
                sanitized_artifact(
                    {
                        "start": started.json(),
                        "pause": paused,
                        "final": final,
                        "pause_checkpoint": paused_checkpoint,
                        "final_checkpoint": final_checkpoint,
                        "publications": publications,
                        "publication_error_injected": publication_error,
                        "rerun": "pytest -q server/tests/test_agent_workflow.py -k confirmed_draft",
                    }
                ),
                indent=2,
            )
        )


async def test_unknown_decision_pauses_without_resending(tenants, tmp_path):
    async with workflow_client(tenants, tmp_path) as (api, app, headers, vendor, llm):
        header = headers[0]
        task, _, extraction, _ = await create_tender(api, app, header, tmp_path)
        sent = []

        async def unknown(sent_body):
            sent.append(sent_body)
            raise httpx.ReadTimeout("Synthetic unknown request")

        vendor.respond = unknown
        llm.retry_delays = (0, 0)
        started = await api.post(
            f"/v4/tasks/{task}/agent-sessions", headers=header, json=start_body(extraction)
        )
        assert started.status_code == 200, started.text
        session_id = started.json()["data"]["session"]["id"]
        paused = await advance(api, app, header, session_id)
        assert paused["pause"]["kind"] == "recovery" and len(sent) == 1
        checkpoint = await checkpoint_receipt(
            app, header, session_id, "paused", reason="agent_request_uncertain"
        )
        again = await api.post(
            f"/v4/agent-sessions/{session_id}/resume",
            headers=header,
            json={
                "expected_revision": paused["session"]["revision"],
                "pause_id": paused["pause"]["id"],
                "idempotency_key": str(uuid4()),
            },
        )
        assert again.status_code == 409 and len(sent) == 1
        async with app.state.db.transaction(tenants["orgs"][0]) as db:
            uncertain = (
                await db.scalars(
                    select(AgentStep).where(
                        AgentStep.session_id == UUID(session_id), AgentStep.state == "uncertain"
                    )
                )
            ).all()
            holds = (
                await db.scalars(select(VendorCall).where(VendorCall.state == "unknown"))
            ).all()
            assert uncertain and holds
        (tmp_path / "agent-unknown.json").write_text(
            json.dumps(sanitized_artifact({"session": paused, "checkpoint": checkpoint}), indent=2)
        )


async def test_task_budget_pause_before_first_decision_and_reference_resume(tenants, tmp_path):
    async with workflow_client(tenants, tmp_path) as (api, app, headers, vendor, llm):
        from test_task_budget_execution import set_limit

        header = headers[0]
        task, _, extraction, _ = await create_tender(api, app, header, tmp_path)
        current = await api.get(f"/v4/tasks/{task}/budget", headers=header)
        assert current.status_code == 200, current.text
        budget = current.json()["data"]["budget"]
        # Extraction is already settled. Exhaust the remaining task budget
        # without lowering the cap below its existing spent/reserved liability.
        exposure = Decimal(budget["spent"]) + Decimal(budget["reserved"])
        exhausted = await set_limit(api, header, task, str(exposure))
        assert Decimal(exhausted["available"]) == 0
        before = len(vendor.bodies)
        body = start_body(extraction)
        started = await api.post(f"/v4/tasks/{task}/agent-sessions", headers=header, json=body)
        assert started.status_code == 200, started.text
        session_id = started.json()["data"]["session"]["id"]
        paused = await advance(api, app, header, session_id)
        assert paused["pause"]["kind"] == "budget" and len(vendor.bodies) == before
        assert paused["pause"]["budget_ref"]["contract"] == "docs/plan/budget.md"
        denied = await api.post(
            f"/v4/agent-sessions/{session_id}/resume",
            headers=header,
            json={
                "expected_revision": paused["session"]["revision"],
                "pause_id": paused["pause"]["id"],
                "idempotency_key": str(uuid4()),
                "budget_ref": paused["pause"]["budget_ref"],
            },
        )
        assert denied.status_code == 409
        assert len(vendor.bodies) == before
        (tmp_path / "agent-budget-pause.json").write_text(
            json.dumps(sanitized_artifact(paused), indent=2)
        )


@pytest.mark.parametrize("max_steps,max_calls", [(1, 32), (24, 1)])
async def test_session_limits_do_not_reset_across_control_jobs(
    tenants, tmp_path, max_steps, max_calls
):
    async with workflow_client(tenants, tmp_path) as (api, app, headers, vendor, llm):
        header = headers[0]
        task, _, extraction, _ = await create_tender(api, app, header, tmp_path)

        async def read_again(sent):
            return decision_reply(
                {
                    "kind": "tool",
                    "call": {"command": "req list", "arguments": {"task": task, "job": extraction}},
                    "input_refs": [],
                }
            )

        vendor.respond = read_again
        started = await api.post(
            f"/v4/tasks/{task}/agent-sessions",
            headers=header,
            json=start_body(
                extraction,
                max_steps=max_steps,
                max_vendor_calls=max_calls,
            ),
        )
        assert started.status_code == 200, started.text
        session_id = started.json()["data"]["session"]["id"]
        terminal = await advance(api, app, header, session_id)
        assert terminal["session"]["state"] == "failed", terminal
        checkpoint = await checkpoint_receipt(
            app,
            header,
            session_id,
            "failed",
            reason="agent_step_limit" if max_steps == 1 else "agent_call_limit",
        )
        assert terminal["session"]["steps_used"] <= max_steps
        assert terminal["session"]["vendor_calls_used"] <= max_calls
        assert len(vendor.bodies) <= 1
        (tmp_path / f"agent-hard-limits-{max_steps}-{max_calls}.json").write_text(
            json.dumps(
                sanitized_artifact({"session": terminal, "checkpoint": checkpoint}), indent=2
            )
        )


async def test_atomic_queue_deferral_rolls_back_with_business_transaction(
    tenants, tmp_path, admin_engine
):
    from app.core.config import Settings
    from app.core.db import Database
    from app.jobs.queue import Queue
    from procrastinate.schema import SchemaManager
    from psycopg import sql
    from sqlalchemy import text

    # Ordinary API fixtures use FakeQueue. This case exercises the real queue
    # schema and grants, in the main session's disposable test database only.
    with admin_engine.begin() as connection:
        if connection.scalar(text("SELECT to_regclass('public.procrastinate_jobs')")) is None:
            connection.connection.driver_connection.execute(SchemaManager.get_schema())
        for table in (
            "procrastinate_jobs",
            "procrastinate_workers",
            "procrastinate_events",
            "procrastinate_periodic_defers",
        ):
            connection.exec_driver_sql(
                sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON {} TO bid_app")
                .format(sql.Identifier(table))
                .as_string()
            )
        sequences = list(
            connection.scalars(
                text(
                    "SELECT sequencename FROM pg_sequences WHERE schemaname='public' AND sequencename LIKE 'procrastinate_%'"
                )
            )
        )
        for name in sequences:
            connection.exec_driver_sql(
                sql.SQL("GRANT USAGE, SELECT ON SEQUENCE {} TO bid_app")
                .format(sql.Identifier(name))
                .as_string()
            )

    db, queue = Database(Settings(data_dir=tmp_path)), Queue(Settings(data_dir=tmp_path))
    probe = str(uuid4())
    try:
        with pytest.raises(RuntimeError, match="Synthetic rollback"):
            async with db.transaction(tenants["orgs"][0]) as session:
                await queue.enqueue_agent_wake(session, str(tenants["orgs"][0]), probe)
                await queue.enqueue_in_transaction(session, str(tenants["orgs"][0]), probe)
                raise RuntimeError("Synthetic rollback")
        async with db.transaction() as session:
            assert not await session.scalar(
                text(
                    "SELECT count(*) FROM procrastinate_jobs WHERE args->>'session_id'=:probe "
                    "OR args->>'job_id'=:probe"
                ),
                {"probe": probe},
            )
        (tmp_path / "agent-atomic-queue.json").write_text(
            json.dumps(
                {
                    "rolled_back": True,
                    "queue_rows_after_rollback": 0,
                    "rerun": "pytest -q server/tests/test_agent_workflow.py -k atomic_queue",
                },
                indent=2,
            )
        )
    finally:
        await db.engine.dispose()
