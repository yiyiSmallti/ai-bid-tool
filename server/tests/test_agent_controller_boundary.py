"""DB-free controller/command identity boundary.

Failure modes specified before this fix: a command's legitimate read-identity
refresh clears the controller job/run; successful, failed, or waiting child
receipts and final completion messages then publish under that read identity.
This isolates real SET LOCAL
parameter construction; PostgreSQL guards and artifacts remain E2E coverage.
"""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.jobs import agent as controller
from app.models.agent import AgentPrincipal, AgentStep
from app.schemas.agent_contracts import AgentToolOutput, CompletionDecision
from app.schemas.contracts import Result
from app.services import drafts
from app.services.auth import Identity, set_actor_context


@pytest.mark.parametrize("child_status", ["succeeded", "failed", "running", "completion"])
async def test_child_read_identity_cannot_publish_controller_receipts(
    monkeypatch, tmp_path, child_status
):
    org_id, owner_id, principal_id, session_id, job_id, run_id, step_id, invocation_id = (
        uuid4() for _ in range(8)
    )
    scopes = {"task:read", "card:read", "draft:read", "job:read"}
    row = SimpleNamespace(
        id=session_id,
        org_id=org_id,
        owner_user_id=owner_id,
        principal_id=principal_id,
        task_id=uuid4(),
        extraction_job_id=uuid4(),
    )
    job = SimpleNamespace(
        id=job_id,
        org_id=org_id,
        actor_user_id=owner_id,
        kind="agent",
        run_id=run_id,
        agent_principal_id=principal_id,
        agent_session_id=session_id,
        invocation_id=uuid4(),
    )
    step = SimpleNamespace(
        id=step_id,
        session_id=session_id,
        invocation_id=invocation_id,
        command="draft",
        state="waiting_job",
        revision=2,
    )
    principal = SimpleNamespace(scopes=sorted(scopes))
    draft = SimpleNamespace(id=uuid4(), generation_job_id=uuid4(), input_hash="a" * 64)
    live = Identity(
        owner_id,
        org_id,
        scopes,
        "technical",
        actor_kind="agent",
        principal_id=principal_id,
        session_id=session_id,
    )

    class Session:
        def __init__(self):
            self.info = {"execution_job": job}
            self.context = {}

        async def execute(self, statement, parameters):
            # Capture the real auth.set_actor_context SQL parameters without a DB.
            assert "app.execution_job_id" in str(statement)
            self.context = dict(parameters)

        async def get(self, model, identifier):
            if model is AgentStep and identifier == step_id:
                return step
            if model is AgentPrincipal and identifier == principal_id:
                return principal
            raise AssertionError("Unexpected lookup")

        async def scalar(self, statement):
            return draft

        async def flush(self, objects):
            assert objects == [step]
            assert_publication_context()

    session = Session()

    def assert_publication_context():
        assert session.context["kind"] == "worker"
        assert session.context["execution_job"] == str(job_id)
        assert session.context["execution_run"] == str(run_id)
        assert session.context["agent_session"] == str(session_id)
        assert session.context["principal"] == str(principal_id)
        assert session.context["step"] == str(step_id)
        assert session.context["invocation"] == str(invocation_id)

    class Database:
        @asynccontextmanager
        async def transaction(self, org):
            assert org == org_id
            yield session

    async def current_fence(db, execution, *, step=None):
        actor = await controller.actor_for(db, row, job=job, step=step)
        await set_actor_context(db, actor)
        return row, job, actor

    observed = {}

    class Broker:
        def __init__(self, db, actor, *args, **kwargs):
            self.db, self.actor = db, actor

        async def recover(self, context):
            # The existing draft read calls cards.access -> set_actor_context.
            # Its delegated read identity has no controller execution binding.
            await set_actor_context(self.db, self.actor)
            observed["read"] = dict(self.db.context)
            assert self.db.context["execution_job"] == ""
            assert self.db.context["execution_run"] == ""
            return AgentToolOutput(
                result=Result(
                    ok=child_status != "failed",
                    command="job status",
                    data={"status": child_status, "error": {"code": "synthetic_child_failure"}},
                ),
                exit_code=4 if child_status == "failed" else 0,
            )

    async def publish(*args):
        assert_publication_context()
        observed["publication"] = dict(session.context)

    async def save_checkpoint(*args, **kwargs):
        assert_publication_context()
        observed["checkpoint"] = dict(session.context)

    async def show_draft(db, actor, *args):
        await set_actor_context(db, actor)
        observed["read"] = dict(db.context)
        assert db.context["execution_job"] == "" and db.context["execution_run"] == ""
        return {"completion": "complete", "validity": "current"}

    monkeypatch.setattr(controller, "fence", current_fence)
    monkeypatch.setattr(controller.agent_limits, "enforce", AsyncMock(return_value=live))
    monkeypatch.setattr(controller.agent_tools, "CommandToolProvider", Broker)
    monkeypatch.setattr(controller, "invocation_context", AsyncMock(return_value=None))
    monkeypatch.setattr(controller, "record_tool_output", publish)
    monkeypatch.setattr(controller, "checkpoint", save_checkpoint)
    monkeypatch.setattr(controller, "stopped_state", AsyncMock(return_value="failed"))
    monkeypatch.setattr(controller.agents, "now", AsyncMock(return_value=datetime.now(UTC)))
    monkeypatch.setattr(controller.agents, "add_message", publish)
    monkeypatch.setattr(drafts, "show_draft", show_draft)
    processor = SimpleNamespace(db=Database(), storage=None, settings=None, queue=None)
    execution = SimpleNamespace(org_id=org_id, job_id=job_id, run_id=run_id)
    if child_status == "completion":
        await controller.finish_goal(
            session,
            row,
            job,
            live,
            processor,
            step,
            CompletionDecision(
                kind="complete", summary="Synthetic completed draft", output_refs=[]
            ),
            execution=execution,
        )
    else:
        await controller.collect_child(processor, execution, step_id)
    assert "checkpoint" in observed
    assert ("publication" in observed) == (child_status in {"succeeded", "completion"})
    (tmp_path / f"controller-after-{child_status}.json").write_text(json.dumps(observed, indent=2))
