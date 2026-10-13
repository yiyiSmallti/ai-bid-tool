"""Agent/task integration through the authenticated API and real worker/database.

Failure matrix: token automation starts a human-managed session; principal
revocation during an already dispatched decision permits publication or drops
the admitted call ledger instead of retaining a failed/uncertain checkpoint;
the database blocks the authority-loss pause/terminal receipt; delegation adds
a human review scope; child-generated task events identify the controller
rather than the executing child.
Queued rejection must close the Job before clearing its transaction execution
context; a rejected job ID with no admitted run is not a live or recovery fence.
"""

import json
from uuid import UUID

import pytest
from app.models.agent import AgentJobLink, AgentPrincipal, AgentSession, AgentStep
from app.models.entities import AuditLog, Job, VendorCall
from app.models.response_cards import ResponseCard
from app.models.task_events import TaskEvent
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from task_fixtures import actor_context_async
from test_agent_workflow import advance, decision_reply, start_body, workflow_client
from test_card_generation import DraftVendor
from test_response_cards import create_tender, sanitized_artifact, select_real_materials


@pytest.fixture
async def authority_case(database, tmp_path, admin_engine):
    async with workflow_client(database, tmp_path) as (api, app, headers, vendor, llm):
        task, _, extraction, _ = await create_tender(api, app, headers, tmp_path)
        yield {
            "api": api,
            "app": app,
            "owner": headers,
            "task": task,
            "extraction": extraction,
            "vendor": vendor,
            "llm": llm,
            "tmp_path": tmp_path,
        }


async def start(case, header=None, body=None):
    return await case["api"].post(
        f"/v4/tasks/{case['task']}/agent-sessions",
        headers=header or case["owner"],
        json=body or start_body(case["extraction"]),
    )


def artifact(case, name, value):
    path = case["tmp_path"] / f"agent-task-{name}.json"
    path.write_text(
        json.dumps(
            sanitized_artifact(
                {"result": value, "rerun": "pytest -q server/tests/test_agent_task_authority.py"}
            ),
            indent=2,
        )
    )


async def test_revocation_during_dispatched_decision_retains_call_and_stops_output(authority_case):
    case = authority_case
    sent = []
    removals = []

    async def revoke_after_dispatch(body):
        assert "session_id" in body
        sent.append(body["session_id"])
        async with case["app"].state.db.transaction() as session:
            await actor_context_async(session)
            state = await session.get(AgentSession, UUID(body["session_id"]))
            principal = await session.get(AgentPrincipal, state.principal_id)
            from datetime import UTC, datetime

            principal.revoked_at = datetime.now(UTC)
        removals.append(body["session_id"])
        return decision_reply(
            {
                "kind": "tool",
                "call": {
                    "command": "card generate",
                    "arguments": {
                        "task": case["task"],
                        "input": {"extraction_job_id": case["extraction"]},
                    },
                },
                "input_refs": [],
            }
        )

    case["vendor"].respond = revoke_after_dispatch
    accepted = await start(case)
    assert accepted.status_code == 200, accepted.text
    sid = UUID(accepted.json()["data"]["session"]["id"])
    async with case["app"].state.db.transaction() as session:
        initial = await session.get(AgentSession, sid)
        assert initial is not None and initial.current_job_id is not None
        controller_id = initial.current_job_id
    # Execute the admitted controller and inspect the checkpoint after revocation.
    await case["app"].state.processor(str(controller_id))
    assert len(sent) == 1 and removals == [str(sid)]
    async with case["app"].state.db.transaction() as session:
        state = await session.get(AgentSession, sid)
        assert state is not None and state.state in {"paused", "failed"}
        assert state.current_job_id is None and state.current_run_id is None
        controller = await session.get(Job, controller_id)
        assert controller is not None and controller.status in {"succeeded", "failed"}
        assert controller.lease_until is None and controller.finished_at is not None
        steps = list(await session.scalars(select(AgentStep).where(AgentStep.session_id == sid)))
        assert len(steps) == 1 and steps[0].kind == "decision"
        assert steps[0].state in {"failed", "uncertain"}
        assert steps[0].child_job_id is None and steps[0].result_enc is None
        calls = list(
            await session.scalars(select(VendorCall).where(VendorCall.job_id == controller_id))
        )
        assert len(calls) == 1 and calls[0].state != "not_sent"
        assert calls[0].run_id == controller.run_id
        assert not list(
            await session.scalars(
                select(Job).where(Job.agent_session_id == sid, Job.kind != "agent")
            )
        )
        assert not list(
            await session.scalars(
                select(ResponseCard).where(ResponseCard.task_id == UUID(case["task"]))
            )
        )
        receipt = {
            "session_state": state.state,
            "controller_status": controller.status,
            "step_state": steps[0].state,
            "step_error": steps[0].error_code,
            "call_id": str(calls[0].id),
            "call_state": calls[0].state,
            "calls_sent": len(sent),
            "binding_released": True,
            "lease_released": True,
            "child_jobs": 0,
            "response_cards": 0,
        }
    artifact(case, "revoked-during-decision", receipt)


async def test_delegation_cannot_add_review_authority(authority_case):
    case = authority_case
    body = start_body(case["extraction"])
    body["requested_scopes"].append("evidence:confirm")
    denied = await start(case, body=body)
    assert denied.status_code == 422, denied.text
    # Even adding a human scope to a worker context cannot turn it into a signer.
    with pytest.raises(DBAPIError):
        async with case["app"].state.db.transaction() as session:
            await actor_context_async(session, kind="worker")
            await session.execute(
                text(
                    "SELECT public.task_write_authority(:task,'evidence:confirm','commercial',true,true)"
                ),
                {"task": UUID(case["task"])},
            )
    artifact(
        case,
        "review-ceiling",
        {
            "agent": denied.json(),
            "worker_human_scope_denied": True,
        },
    )


async def test_child_publication_preserves_job_event_provenance(authority_case):
    case = authority_case
    await select_real_materials(case["api"], case["owner"], case["task"], case["tmp_path"])

    async def respond(body):
        if "session_id" in body:
            return decision_reply(
                {
                    "kind": "tool",
                    "call": {
                        "command": "card generate",
                        "arguments": {
                            "task": case["task"],
                            "input": {"extraction_job_id": case["extraction"]},
                        },
                    },
                    "input_refs": [],
                }
            )
        from test_llm_providers import provider_reply

        return provider_reply("openai", DraftVendor.proposals(body))

    case["vendor"].respond = respond
    accepted = await start(case)
    assert accepted.status_code == 200, accepted.text
    sid = UUID(accepted.json()["data"]["session"]["id"])
    paused = await advance(case["api"], case["app"], case["owner"], str(sid))
    assert paused["session"]["state"] == "paused", paused
    assert paused["pause"]["action"] == "review_cards", paused
    async with case["app"].state.db.transaction() as session:
        child = await session.scalar(
            select(Job)
            .join(AgentJobLink, AgentJobLink.job_id == Job.id)
            .where(AgentJobLink.session_id == sid, AgentJobLink.role == "tool")
        )
        assert child is not None and child.status == "succeeded"
        assert child.initiated_by == "builtin_agent"
        assert child.actor_token_id is None and child.agent_step_id is not None
        events = list(
            await session.scalars(
                select(TaskEvent).where(
                    TaskEvent.source_id == child.id, TaskEvent.event_kind == "job_progress"
                )
            )
        )
        assert any(
            event.payload["state"] == "succeeded" and event.payload["run_id"] == str(child.run_id)
            for event in events
        )
        audits = list(
            await session.scalars(
                select(AuditLog).where(AuditLog.job_id == child.id, AuditLog.actor_kind == "worker")
            )
        )
        assert audits
        assert all(row.run_id == child.run_id and row.agent_session_id == sid for row in audits)
        receipt = {
            "child_job_id": str(child.id),
            "run_id": str(child.run_id),
            "events": [event.payload for event in events],
            "audit_actor_kinds": [row.actor_kind for row in audits],
        }
    artifact(case, "child-events", receipt)
