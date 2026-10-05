"""Agent/task integration through the authenticated API and real worker/database.

Failure matrix, written before implementation: a same-org nonmember (including
an admin using its read/recovery exception) starts a session; a reviewer or
observer starts paid work; an archived task admits start/resume; removing a
contributor leaves an already queued worker or paused session able to continue;
revocation during an already dispatched decision permits publication or drops
the admitted call ledger instead of retaining a failed/uncertain checkpoint;
the database blocks the authority-loss pause/terminal receipt; delegation adds
a human review scope or expands its owner's task review domain; child-generated
task events identify the controller or human rather than the executing child.
Queued rejection must close the Job before clearing its transaction execution
context; a rejected job ID with no admitted run is not a live or recovery fence.
"""

import json
from uuid import UUID, uuid4

import httpx
import pytest
from app.models.agent import AgentJobLink, AgentPrincipal, AgentSession, AgentStep
from app.models.entities import AuditLog, Job, VendorCall
from app.models.response_cards import ResponseCard
from app.models.team_workflow import TaskEvent, TaskMember
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from task_fixtures import actor_context_async
from test_agent_workflow import advance, decision_reply, start_body, workflow_client
from test_card_generation import DraftVendor
from test_response_cards import create_tender, sanitized_artifact, select_real_materials
from test_team_workflow_membership import add_member, person, workflow


@pytest.fixture
async def authority_case(tenants, tmp_path, admin_engine):
    async with workflow_client(tenants, tmp_path) as (api, app, headers, vendor, llm):
        task, _, extraction, _ = await create_tender(api, app, headers[0], tmp_path)
        user, member = await person(api, admin_engine, tenants["orgs"][0])
        yield {
            "api": api,
            "app": app,
            "owner": headers[0],
            "foreign": headers[1],
            "member": member,
            "user": user,
            "task": task,
            "extraction": extraction,
            "vendor": vendor,
            "llm": llm,
            "org": tenants["orgs"][0],
            "tmp_path": tmp_path,
        }


async def enroll(case, role="contributor", domains=None):
    current = await workflow(case["api"], case["owner"], case["task"])
    result = await add_member(
        case["api"], case["owner"], case["task"], case["user"], current["revision"], role, domains
    )
    assert result.status_code == 200, result.text


async def start(case, header=None, body=None):
    return await case["api"].post(
        f"/v4/tasks/{case['task']}/agent-sessions",
        headers=header or case["member"],
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


async def test_nonmember_start_is_hidden_even_for_admin(authority_case, admin_engine):
    case = authority_case
    _, admin = await person(case["api"], admin_engine, case["org"], "admin")
    denied = []
    for header in (case["member"], admin, case["foreign"]):
        result = await start(case, header)
        assert result.status_code == 404, result.text
        assert result.json()["data"]["error"]["code"] == "not_found"
        denied.append(result.json())
    async with case["app"].state.db.transaction(case["org"]) as session:
        assert not list(await session.scalars(select(AgentSession)))
        assert not list(await session.scalars(select(AgentPrincipal)))
    artifact(case, "nonmember", denied)


@pytest.mark.parametrize("role,domains", [("observer", []), ("reviewer", ["commercial"])])
async def test_read_or_review_members_cannot_start_work(authority_case, role, domains):
    case = authority_case
    await enroll(case, role, domains)
    denied = await start(case)
    assert denied.status_code == 403, denied.text
    artifact(case, role, denied.json())


async def test_archived_task_blocks_agent_start(authority_case):
    case = authority_case
    await enroll(case)
    current = await workflow(case["api"], case["owner"], case["task"])
    archived = await case["api"].post(
        f"/tasks/{case['task']}/archive",
        headers=case["owner"],
        json={"expected_revision": current["revision"], "reason": "Synthetic idle archive"},
    )
    assert archived.status_code == 200, archived.text
    denied = await start(case)
    assert denied.status_code == 409, denied.text
    assert denied.json()["data"]["error"]["code"] == "task_archived"
    async with case["app"].state.db.transaction(case["org"]) as session:
        assert not await session.scalar(
            text("SELECT public.agent_task_authority(:org,:task,:user)"),
            {"org": case["org"], "task": UUID(case["task"]), "user": case["user"]},
        )
    artifact(case, "archived-start", denied.json())


@pytest.mark.parametrize("checkpoint", ["queued", "paused"])
async def test_removed_member_stops_worker_and_cannot_resume(
    authority_case, checkpoint, monkeypatch
):
    from app.jobs.agent import wake
    from app.services import agents

    case = authority_case
    await enroll(case)
    sent = []

    async def unknown(body):
        sent.append(body)
        raise httpx.ReadTimeout("Synthetic unknown agent decision")

    case["vendor"].respond = unknown
    case["llm"].retry_delays = (0, 0)
    accepted = await start(case)
    assert accepted.status_code == 200, accepted.text
    sid = UUID(accepted.json()["data"]["session"]["id"])
    if checkpoint == "paused":
        paused = await advance(case["api"], case["app"], case["member"], str(sid))
        assert paused["session"]["state"] == "paused"
    current = await workflow(case["api"], case["owner"], case["task"])
    removed = await case["api"].post(
        f"/tasks/{case['task']}/members/{case['user']}/remove",
        headers=case["owner"],
        json={"expected_revision": current["revision"], "reason": "Synthetic task revocation"},
    )
    assert removed.status_code == 200, removed.text
    before = len(sent)
    cleanup_context = {}
    original_pause = agents.pause

    async def verify_cleanup_fence(session, state, *args, **kwargs):
        context = (
            await session.execute(
                text(
                    "SELECT current_setting('app.execution_job_id') AS job_id, "
                    "current_setting('app.execution_run_id') AS run_id, "
                    "current_setting('app.agent_session_id') AS session_id"
                )
            )
        ).one()
        assert (context.job_id, context.run_id, context.session_id) == ("", "", str(sid))
        rejected = await session.get(Job, state.current_job_id)
        assert rejected is not None and rejected.status == "failed" and rejected.lease_until is None
        cleanup_context.update(job_id=context.job_id, run_id=context.run_id)
        return await original_pause(session, state, *args, **kwargs)

    if checkpoint == "queued":
        monkeypatch.setattr(agents, "pause", verify_cleanup_fence)
    async with case["app"].state.db.transaction(case["org"]) as session:
        queued = list(
            await session.scalars(
                select(Job.id).where(Job.agent_session_id == sid, Job.status == "queued")
            )
        )
    for job in queued:
        await case["app"].state.processor(str(case["org"]), str(job))
    await wake(case["app"].state.processor, case["org"], sid)
    if checkpoint == "queued":
        assert cleanup_context == {"job_id": "", "run_id": ""}
    async with case["app"].state.db.transaction(case["org"]) as session:
        state = await session.get(AgentSession, sid)
        assert state is not None and state.state == "paused"
        assert state.current_job_id is None and state.current_run_id is None
        assert state.pause_id is not None
        resume = {
            "expected_revision": state.revision,
            "pause_id": str(state.pause_id),
            "idempotency_key": str(uuid4()),
        }
        assert not list(
            await session.scalars(
                select(Job).where(
                    Job.agent_session_id == sid, Job.status.in_(["queued", "running"])
                )
            )
        )
    refused = await case["api"].post(
        f"/v4/agent-sessions/{sid}/resume", headers=case["member"], json=resume
    )
    assert refused.status_code == 404, refused.text
    assert len(sent) == before
    # Direct SQL cannot bypass the live task gate, even for the historical owner.
    with pytest.raises(DBAPIError):
        async with case["app"].state.db.transaction(case["org"]) as session:
            await actor_context_async(session, case["org"], case["user"])
            await session.execute(
                text("UPDATE agent_sessions SET state='queued', revision=revision+1 WHERE id=:id"),
                {"id": sid},
            )
    cancelled = await case["api"].post(
        f"/v4/agent-sessions/{sid}/cancel",
        headers=case["member"],
        json={"expected_revision": resume["expected_revision"], "idempotency_key": str(uuid4())},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["data"]["session"]["state"] == "cancelled"
    artifact(
        case,
        f"removed-{checkpoint}",
        {
            "resume": refused.json(),
            "calls_before": before,
            "calls_after": len(sent),
            "direct_sql_denied": True,
            "cleanup_context": cleanup_context,
            "cancel": cancelled.json(),
        },
    )


async def test_revocation_during_dispatched_decision_retains_call_and_stops_output(authority_case):
    case = authority_case
    await enroll(case)
    sent = []
    removals = []

    async def revoke_after_dispatch(body):
        assert "session_id" in body
        sent.append(body["session_id"])
        current = await workflow(case["api"], case["owner"], case["task"])
        removed = await case["api"].post(
            f"/tasks/{case['task']}/members/{case['user']}/remove",
            headers=case["owner"],
            json={
                "expected_revision": current["revision"],
                "reason": "Synthetic revocation after provider dispatch",
            },
        )
        removals.append(removed.status_code)
        assert removed.status_code == 200, removed.text
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
    async with case["app"].state.db.transaction(case["org"]) as session:
        initial = await session.get(AgentSession, sid)
        assert initial is not None and initial.current_job_id is not None
        controller_id = initial.current_job_id
    # The removed member can no longer poll through advance; execute the admitted
    # controller and inspect its retained checkpoint in the same tenant directly.
    await case["app"].state.processor(str(case["org"]), str(controller_id))
    assert len(sent) == 1 and removals == [200]
    async with case["app"].state.db.transaction(case["org"]) as session:
        member = await session.scalar(
            select(TaskMember).where(
                TaskMember.task_id == UUID(case["task"]), TaskMember.user_id == case["user"]
            )
        )
        assert member is not None and not member.active
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
    await enroll(case, domains=[])
    body = start_body(case["extraction"])
    body["requested_scopes"].append("evidence:confirm")
    denied = await start(case, body=body)
    assert denied.status_code == 422, denied.text
    # The task member's org role alone does not grant its commercial domain.
    with pytest.raises(DBAPIError):
        async with case["app"].state.db.transaction(case["org"]) as session:
            await actor_context_async(session, case["org"], case["user"])
            await session.execute(
                text(
                    "SELECT public.task_write_authority(:org,:task,'evidence:confirm','commercial',true,true)"
                ),
                {"org": case["org"], "task": UUID(case["task"])},
            )
    await enroll(case, domains=["commercial"])
    # Even adding a human scope to a worker context cannot turn it into a signer.
    with pytest.raises(DBAPIError):
        async with case["app"].state.db.transaction(case["org"]) as session:
            await actor_context_async(session, case["org"], case["user"], kind="worker")
            await session.execute(
                text(
                    "SELECT public.task_write_authority(:org,:task,'evidence:confirm','commercial',true,true)"
                ),
                {"org": case["org"], "task": UUID(case["task"])},
            )
    artifact(
        case,
        "review-ceiling",
        {
            "agent": denied.json(),
            "unassigned_domain_denied": True,
            "worker_human_scope_denied": True,
        },
    )


async def test_child_publication_preserves_job_event_provenance(authority_case):
    case = authority_case
    await enroll(case)
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
    paused = await advance(case["api"], case["app"], case["member"], str(sid))
    assert paused["session"]["state"] == "paused", paused
    assert paused["pause"]["action"] == "review_cards", paused
    async with case["app"].state.db.transaction(case["org"]) as session:
        child = await session.scalar(
            select(Job)
            .join(AgentJobLink, AgentJobLink.job_id == Job.id)
            .where(AgentJobLink.session_id == sid, AgentJobLink.role == "tool")
        )
        assert child is not None and child.status == "succeeded"
        assert child.initiated_by == "builtin_agent"
        assert child.on_behalf_of_user_id == case["user"] and child.actor_user_id == case["user"]
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
        assert all(
            row.run_id == child.run_id
            and row.agent_session_id == sid
            and row.on_behalf_of_user_id == case["user"]
            for row in audits
        )
        receipt = {
            "child_job_id": str(child.id),
            "run_id": str(child.run_id),
            "events": [event.payload for event in events],
            "audit_actor_kinds": [row.actor_kind for row in audits],
        }
    current = await workflow(case["api"], case["owner"], case["task"])
    archived = await case["api"].post(
        f"/tasks/{case['task']}/archive",
        headers=case["owner"],
        json={"expected_revision": current["revision"], "reason": "Synthetic paused archive"},
    )
    assert archived.status_code == 200, archived.text
    resumed = await case["api"].post(
        f"/v4/agent-sessions/{sid}/resume",
        headers=case["member"],
        json={
            "expected_revision": paused["session"]["revision"],
            "pause_id": paused["pause"]["id"],
            "idempotency_key": str(uuid4()),
        },
    )
    assert resumed.status_code == 409, resumed.text
    assert resumed.json()["data"]["error"]["code"] == "task_archived"
    cancelled = await case["api"].post(
        f"/v4/agent-sessions/{sid}/cancel",
        headers=case["member"],
        json={"expected_revision": paused["session"]["revision"], "idempotency_key": str(uuid4())},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["data"]["session"]["state"] == "cancelled"
    receipt.update(archived_resume=resumed.json(), cancelled=cancelled.json())
    artifact(case, "child-events", receipt)
