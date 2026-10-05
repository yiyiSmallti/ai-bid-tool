"""A01 broker failure matrix, specified before implementation.

Failures covered: a forbidden command/extra argument; insufficient scope; changed
registry; forged task, extraction, card, draft or job parent; another running job;
ambiguous retry; cache ownership/cost duplication; raw file/shell/HTTP capability;
memory retrieval in A01; and lost principal/session/step identity in workers.
These database-free checks complement the real workflow and RLS acceptance tests.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.errors import ServiceError
from app.models.entities import Job
from app.models.response_cards import DraftRun, ResponseCard
from app.schemas.agent_contracts import (
    AgentPrincipalView,
    CardShowCall,
    DraftShowCall,
    ExtractionArguments,
    JobStatusCall,
    ReqListCall,
    ToolCall,
)
from app.schemas.contracts import CONTRACT_VERSION
from app.services import agent_tools, card_generation
from app.services.auth import Identity
from bid_cli.schema import command_schema
from pydantic import TypeAdapter, ValidationError


def context():
    org, user, principal_id, session_id, task, document, extraction, step_id = (
        uuid4() for _ in range(8)
    )
    actor = Identity(
        user,
        org,
        {"task:read", "card:read", "card:generate", "draft:read", "draft:run", "job:read"},
        "bidder",
        actor_kind="agent",
        principal_id=principal_id,
        session_id=session_id,
        step_id=step_id,
        invocation_id=uuid4(),
    )
    principal = AgentPrincipalView(
        id=principal_id,
        org_id=org,
        user_id=user,
        membership_id=uuid4(),
        scopes=sorted(actor.scopes),
        authority_expires_at=datetime.now(UTC) + timedelta(hours=1),
        created_at=datetime.now(UTC),
    )
    state = SimpleNamespace(
        id=session_id,
        org_id=org,
        owner_user_id=user,
        principal_id=principal_id,
        task_id=task,
        document_id=document,
        extraction_job_id=extraction,
        tool_schema_sha256=None,
    )
    step = SimpleNamespace(
        id=step_id,
        org_id=org,
        session_id=session_id,
        task_id=task,
        document_id=document,
        schema_sha256=None,
        invocation_id=actor.invocation_id,
        child_job_id=None,
    )
    return actor, principal, state, step


@pytest.mark.asyncio
async def test_tools_are_exact_cli_invocation_schemas():
    _, principal, _, _ = context()
    definitions = await agent_tools.definitions(principal)
    schemas = command_schema(version=CONTRACT_VERSION)
    assert {row.command for row in definitions} == {
        "req list",
        "card list",
        "card show",
        "card generate",
        "draft",
        "draft show",
        "job status",
    }
    for row in definitions:
        assert row.invocation_input == schemas["commands"][row.command]["invocation_input"]
        assert row.result_schema == schemas["result"]
    assert agent_tools.schema_digest(definitions) == agent_tools.schema_digest(definitions)
    assert not any(word in row.command for row in definitions for word in ("shell", "http", "file"))


@pytest.mark.asyncio
async def test_tools_require_every_grant():
    _, principal, _, _ = context()
    principal.scopes = ["task:read"]
    assert [row.command for row in await agent_tools.definitions(principal)] == ["req list"]


@pytest.mark.parametrize("command", ["export", "card confirm", "task create", "shell", "http"])
def test_unregistered_commands_are_rejected(command):
    with pytest.raises(ValidationError):
        TypeAdapter(ToolCall).validate_python({"command": command, "arguments": {}})


def test_extra_arguments_cannot_create_a_capability():
    with pytest.raises(ValidationError):
        ExtractionArguments(task=uuid4(), job=uuid4(), url="https://example.test/")


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["task", "extraction"])
async def test_forged_selectors_are_rejected_before_service(changed):
    actor, _, state, step = context()
    call = ReqListCall(
        command="req list",
        arguments=ExtractionArguments(
            task=uuid4() if changed == "task" else state.task_id,
            job=uuid4() if changed == "extraction" else state.extraction_job_id,
        ),
    )
    session = SimpleNamespace(get=AsyncMock(), scalar=AsyncMock())
    with pytest.raises(ServiceError, match="Resource not found"):
        await agent_tools.validate_call(session, actor, state, step, call)
    session.get.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["card", "draft", "job"])
async def test_cross_task_parent_is_not_found(kind):
    actor, _, state, step = context()
    rid = uuid4()
    row = SimpleNamespace(
        org_id=state.org_id,
        task_id=uuid4(),
        document_id=state.document_id,
        extraction_job_id=state.extraction_job_id,
    )
    session = SimpleNamespace(get=AsyncMock(return_value=row), scalar=AsyncMock(return_value=None))
    calls = {
        "card": CardShowCall(command="card show", arguments={"id": rid}),
        "draft": DraftShowCall(command="draft show", arguments={"id": rid}),
        "job": JobStatusCall(command="job status", arguments={"job_id": rid}),
    }
    with pytest.raises(ServiceError, match="Resource not found"):
        await agent_tools.validate_call(session, actor, state, step, calls[kind])
    session.get.assert_awaited_once_with(
        {"card": ResponseCard, "draft": DraftRun, "job": Job}[kind], rid
    )


@pytest.mark.asyncio
async def test_changed_schema_cannot_execute():
    actor, _, state, step = context()
    state.tool_schema_sha256 = step.schema_sha256 = "f" * 64
    call = ReqListCall(
        command="req list", arguments={"task": state.task_id, "job": state.extraction_job_id}
    )
    with pytest.raises(ServiceError) as error:
        await agent_tools.validate_call(SimpleNamespace(), actor, state, step, call)
    assert error.value.code == "tool_schema_changed"


@pytest.mark.asyncio
async def test_retry_cannot_resend_an_ambiguous_vendor_call():
    _, _, state, _ = context()
    session = SimpleNamespace(scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
    with pytest.raises(ServiceError) as error:
        await agent_tools.prove_safe_retry(session, state, "card_generate", "a" * 64)
    assert error.value.code == "unsafe_tool_retry"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,unresolved,allowed",
    [
        ("failed", None, True),
        ("cancelled", None, True),
        ("queued", None, False),
        ("running", None, False),
        ("succeeded", None, False),
        ("failed", uuid4(), False),
    ],
)
async def test_retry_requires_unique_owned_terminal_job_and_settled_calls(
    status, unresolved, allowed
):
    _, _, state, _ = context()
    job = SimpleNamespace(
        id=uuid4(),
        org_id=state.org_id,
        task_id=state.task_id,
        document_id=state.document_id,
        status=status,
        lease_until=datetime.now(UTC) + timedelta(minutes=1),
    )
    session = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [job])),
        scalar=AsyncMock(side_effect=[datetime.now(UTC), unresolved]),
    )
    if allowed:
        assert await agent_tools.prove_safe_retry(session, state, "card_generate", "a" * 64) is job
    else:
        with pytest.raises(ServiceError) as error:
            await agent_tools.prove_safe_retry(session, state, "card_generate", "a" * 64)
        assert error.value.code == "unsafe_tool_retry"


@pytest.mark.asyncio
async def test_running_cache_is_never_taken_over():
    actor, _, state, step = context()
    job = SimpleNamespace(
        id=uuid4(),
        task_id=state.task_id,
        document_id=state.document_id,
        org_id=state.org_id,
        status="running",
    )
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=None), add=lambda _: None, flush=AsyncMock()
    )
    with pytest.raises(ServiceError) as error:
        await agent_tools.link_child(session, actor, state, step, job, cached=True)
    assert error.value.code == "tool_job_in_progress"


@pytest.mark.asyncio
async def test_completed_cache_is_not_owned():
    actor, _, state, step = context()
    job = SimpleNamespace(
        id=uuid4(),
        task_id=state.task_id,
        document_id=state.document_id,
        org_id=state.org_id,
        status="succeeded",
    )
    added = []
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=None), add=added.append, flush=AsyncMock()
    )
    link = await agent_tools.link_child(session, actor, state, step, job, cached=True)
    assert link.owned is False and link.role == "tool" and link.step_id == step.id
    assert added == [link]


def test_no_memory_context_is_explicit():
    assert card_generation.prompt_memory({"requirements": []}) is None


def test_worker_identity_retains_agent_origin():
    actor, _, state, step = context()
    submitted = {
        "actor_user_id": str(actor.user_id),
        "actor_token_id": None,
        "scopes": sorted(actor.scopes),
        "agent_principal_id": str(actor.principal_id),
        "agent_session_id": str(state.id),
        "agent_step_id": str(step.id),
        "invocation_id": str(actor.invocation_id),
    }
    worker = card_generation.worker(
        SimpleNamespace(org_id=actor.org_id, result={"submission": submitted})
    )
    assert worker.actor_kind == "worker" and worker.principal_id == actor.principal_id
    assert worker.session_id == state.id and worker.step_id == step.id
    assert worker.invocation_id == actor.invocation_id
