"""One durable A01 control fragment per worker job; waits never occupy a slot."""

import logging
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.errors import ServiceError, log_unexpected
from app.jobs.execution import ADMISSION_STOPS, JobExecution, job_cost, locked_job
from app.models.agent import AgentJobLink, AgentMessage, AgentPause, AgentPrincipal, AgentStep
from app.models.entities import Job, Requirement, UsageRecord, VendorCall
from app.models.response_cards import DraftRun
from app.providers.agent import reasoning_provider
from app.providers.base import ProviderFailure
from app.providers.configured import model_identity
from app.providers.llm import with_reasoning
from app.schemas.agent_contracts import (
    AgentDecision,
    AgentDecisionRequest,
    AgentInputRef,
    AgentInvocationContext,
    AgentJobResult,
    AgentLimits,
    AgentMessageView,
    CompletionDecision,
    HumanActionNeeded,
    ToolDecision,
)
from app.schemas.contracts import Cost
from app.services import agent_limits, agent_tools, agents, budget_preflight
from app.services.auth import Identity, agent_identity, set_actor_context
from app.services.versioned import audit

logger = logging.getLogger(__name__)
AUTHORITY_STOPS = {
    "agent_authority_expired",
    "agent_authority_changed",
    "forbidden",
    "invalid_session",
    "not_found",
    "org_inactive",
    "task_archived",
}
RECOVERY_STOPS = {"agent_request_uncertain", "usage_accounting_failed", "agent_receipt_unreadable"}
CHANGE_STOPS = {
    "agent_inputs_changed",
    "agent_model_changed",
    "tool_schema_changed",
    "agent_context_limit",
}
BUDGET_STOPS = ADMISSION_STOPS | {
    "billing_price_unavailable",
    "billing_bound_unavailable",
    "billing_currency_mismatch",
    "agent_unpriced",
    "provider_unavailable",
    "spend_cap_below_first_call",
}


async def actor_for(session, row, *, job=None, step=None, kind="worker"):
    """Cleanup identity is inert; live authority is checked separately before work."""
    principal = await session.get(AgentPrincipal, row.principal_id)
    if principal is None:
        raise ProviderFailure("Agent authority unavailable", code="agent_authority_expired")
    return Identity(
        row.owner_user_id,
        row.org_id,
        set(principal.scopes),
        "viewer",
        actor_kind=kind,
        principal_id=row.principal_id,
        session_id=row.id,
        step_id=step.id if step is not None else None,
        invocation_id=step.invocation_id
        if step is not None
        else job.invocation_id
        if job
        else None,
        job_id=job.id if job else None,
        run_id=job.run_id if job else None,
    )


async def enqueue_controller(session, row, actor, settings, queue):
    """Persist Job, ownership and queue delivery in the caller's transaction."""
    if queue is None:
        raise ServiceError("queue_unavailable", "Agent queue is unavailable", 503, 3)
    if row.current_job_id is not None:
        existing = await session.get(Job, row.current_job_id)
        if existing is not None and existing.status in {"queued", "running"}:
            return existing
    principal = await session.get(AgentPrincipal, row.principal_id)
    assert principal is not None
    submitted = replace(
        actor,
        scopes=set(principal.scopes),
        principal_id=row.principal_id,
        session_id=row.id,
        step_id=None,
        invocation_id=uuid4(),
        job_id=None,
        run_id=None,
    )
    await set_actor_context(session, submitted)
    model = row.model_snapshot["model"]
    job = Job(
        id=uuid4(),
        org_id=row.org_id,
        task_id=row.task_id,
        document_id=row.document_id,
        kind="agent",
        status="queued",
        cache_key=agents.digest({"session": row.id, "controller": uuid4()}),
        provider_config_id=UUID(model["provider_config_id"])
        if model.get("provider_config_id")
        else None,
        provider_identity=model,
        command="agent start",
        initiated_by="builtin_agent",
        on_behalf_of_user_id=row.owner_user_id,
        agent_principal_id=row.principal_id,
        agent_session_id=row.id,
        invocation_id=submitted.invocation_id,
        result={"submission": {"agent_session_id": str(row.id)}},
    )
    session.add(job)
    await session.flush([job])
    session.add(
        AgentJobLink(
            id=uuid4(),
            org_id=row.org_id,
            session_id=row.id,
            task_id=row.task_id,
            document_id=row.document_id,
            job_id=job.id,
            role="controller",
            owned=True,
        )
    )
    row.current_job_id, row.current_run_id = job.id, None
    row.state = "queued"
    job.queue_id = await queue.enqueue_in_transaction(session, str(row.org_id), str(job.id))
    await queue.enqueue_agent_wake(session, str(row.org_id), str(row.id))
    await session.flush()
    await set_actor_context(session, actor)
    return job


async def decision_request(
    session, row, step_id, settings, actor=None, initial_message=None, *, allow_exhausted=False
):
    if actor is None:
        actor = await actor_for(session, row, kind="agent")
    timestamp = await agents.now(session)
    messages = []
    if initial_message is None:
        stored = (
            await session.scalars(
                select(AgentMessage)
                .where(AgentMessage.session_id == row.id)
                .order_by(AgentMessage.ordinal)
            )
        ).all()
        if len(stored) >= 40:
            raise ProviderFailure(
                "Conversation needs a smaller selection", code="agent_context_limit"
            )
        for message in stored:
            # Newly registered confidential values are replaced again on every send.
            content = await agents.redact_text(session, row, message.content, settings)
            messages.append(agents.message_view(message).model_copy(update={"content": content}))
    else:
        content = await agents.redact_text(session, row, initial_message, settings)
        messages.append(
            AgentMessageView(
                id=uuid4(),
                org_id=row.org_id,
                session_id=row.id,
                ordinal=1,
                role="human",
                author_user_id=row.owner_user_id,
                content=content,
                content_sha256=agents.digest(content),
                created_at=timestamp,
            )
        )
    scope = agents.canonical(
        {"task": str(row.task_id), "extraction_job_id": str(row.extraction_job_id)}
    )
    messages.insert(
        0,
        AgentMessageView(
            id=row.id,
            org_id=row.org_id,
            session_id=row.id,
            ordinal=1,
            role="system",
            content="Use only this fixed task and extraction: " + scope,
            content_sha256=agents.digest(scope),
            created_at=row.created_at,
        ),
    )
    refs = [
        AgentInputRef(
            kind="message",
            id=message.id,
            revision_ref=str(message.ordinal),
            sha256=message.content_sha256,
        )
        for message in messages[1:]
    ]
    requirements = (
        await session.scalars(
            select(Requirement)
            .where(
                Requirement.task_id == row.task_id,
                Requirement.job_id == row.extraction_job_id,
                Requirement.document_id == row.document_id,
            )
            .order_by(Requirement.id)
        )
    ).all()
    for requirement in requirements:
        refs.append(
            AgentInputRef(
                kind="requirement",
                id=requirement.id,
                revision_ref=requirement.fingerprint,
                sha256=agents.digest(
                    {
                        "text": requirement.text,
                        "quote": requirement.quote,
                        "source": str(requirement.chunk_id),
                        "location": requirement.location,
                    }
                ),
            )
        )
    refs.extend(
        AgentInputRef.model_validate(ref) for ref in await agents.card_review_refs(session, row)
    )
    linked_drafts = (
        await session.scalars(
            select(DraftRun)
            .join(AgentJobLink, AgentJobLink.job_id == DraftRun.generation_job_id)
            .where(AgentJobLink.session_id == row.id)
        )
    ).all()
    refs.extend(
        AgentInputRef(
            kind="draft",
            id=draft.id,
            revision_ref=str(draft.generation_job_id),
            sha256=draft.input_hash,
        )
        for draft in linked_drafts
    )
    extraction = await session.get(Job, row.extraction_job_id)
    if extraction is not None:
        refs.append(
            AgentInputRef(
                kind="job",
                id=extraction.id,
                revision_ref=str(extraction.run_id),
                sha256=agents.digest(
                    {
                        "kind": extraction.kind,
                        "status": extraction.status,
                        "document": extraction.document_id,
                    }
                ),
            )
        )
    if len(refs) > 100:
        raise ProviderFailure("Select fewer requirements or cards", code="agent_context_limit")
    limits = AgentLimits.model_validate(row.limits)
    remaining = limits.max_steps - row.steps_used
    if remaining <= 0 and not allow_exhausted:
        raise ProviderFailure("Agent step ceiling exhausted", code="agent_step_limit")
    return AgentDecisionRequest(
        session_id=row.id,
        step_id=step_id,
        messages=messages,
        tools=await agent_tools.definitions(actor),
        input_refs=refs,
        remaining_steps=max(1, remaining),
        deadline=timestamp + timedelta(seconds=agent_limits.remaining_seconds(row, timestamp)),
    )


async def fence(session, execution, *, expected_revision=None, step=None):
    job = await locked_job(session, execution.job_id)
    if job is None or job.agent_session_id is None:
        raise ProviderFailure("Controller is unavailable", code="job_attempt_stopped")
    row = await agent_limits.locked_session(session, job.agent_session_id)
    timestamp = await agents.now(session)
    if (
        job.status != "running"
        or job.run_id != execution.run_id
        or job.lease_until is None
        or job.lease_until <= timestamp
        or row.state != "running"
        or row.current_job_id != job.id
        or row.current_run_id != job.run_id
        or (expected_revision is not None and row.revision != expected_revision)
    ):
        raise ProviderFailure("Controller attempt was superseded", code="job_attempt_stopped")
    if step is not None and step.session_id != row.id:
        raise ProviderFailure("Step binding changed", code="job_attempt_stopped")
    actor = await actor_for(session, row, job=job, step=step)
    await set_actor_context(session, actor)
    session.info["command"] = step.command if step and step.command else "agent start"
    return row, job, actor


async def stopped_state(session, row):
    jobs = (await session.scalars(select(Job).where(Job.id.in_(agent_limits.own_jobs(row))))).all()
    return (
        "partial"
        if any(job.result.get("draft_id") or job.result.get("created_revision_ids") for job in jobs)
        else "failed"
    )


def advance_step(step, job, state, **values):
    step.state = state
    step.revision += 1
    step.last_transition_job_id, step.last_transition_run_id = job.id, job.run_id
    for key, value in values.items():
        setattr(step, key, value)


async def new_step(session, row, job, kind, settings, *, call=None, refs=(), request=None):
    limits = AgentLimits.model_validate(row.limits)
    if row.steps_used >= limits.max_steps:
        raise ProviderFailure("Agent step ceiling exhausted", code="agent_step_limit")
    ordinal = (
        await session.scalar(
            select(func.coalesce(func.max(AgentStep.ordinal), 0)).where(
                AgentStep.session_id == row.id
            )
        )
    ) + 1
    step_id = request.step_id if request is not None else uuid4()
    payload = (
        call.model_dump(mode="json")
        if call is not None
        else request.model_dump(mode="json")
        if request is not None
        else None
    )
    step = AgentStep(
        id=step_id,
        org_id=row.org_id,
        session_id=row.id,
        task_id=row.task_id,
        document_id=row.document_id,
        ordinal=ordinal,
        kind=kind,
        state="planned",
        command=call.command if call is not None else None,
        created_by_job_id=job.id,
        created_by_run_id=job.run_id,
        last_transition_job_id=job.id,
        last_transition_run_id=job.run_id,
        revision=1,
        invocation_id=uuid4(),
        schema_sha256=row.tool_schema_sha256,
        input_refs=[
            ref.model_dump(mode="json") if isinstance(ref, AgentInputRef) else ref for ref in refs
        ],
        arguments_enc=agents.seal(settings, step_id, row.org_id, row.id, payload)
        if payload
        else None,
        arguments_sha256=agents.digest(payload) if payload else None,
        usage_ids=[],
        created_at=await agents.now(session),
    )
    row.steps_used += 1
    row.revision += 1
    session.add(step)
    await session.flush()
    if kind == "tool":
        actor = await actor_for(session, row, job=job, step=step)
        await set_actor_context(session, actor)
        audit(
            session,
            actor,
            "agent.tool.planned",
            step.id,
            {"command": step.command, "arguments_sha256": step.arguments_sha256},
        )
    return step


async def checkpoint(
    session, row, job, actor, processor, state, *, step=None, pause=None, reason=None, outputs=()
):
    timestamp = await agents.now(session)
    usages, calls = await agent_limits.ledger(session, row)
    # Flush steps and messages while the session still names the live controller.
    await session.flush()
    # ORM cost queries may autoflush. Complete them before marking a session
    # terminal: that transition must include its cleared execution binding.
    cost = Cost.model_validate(await job_cost(session, job.id, processor.settings.billing_currency))
    from app.models.team_workflow import TaskWorkflow

    archived = (
        await session.scalar(select(TaskWorkflow.state).where(TaskWorkflow.task_id == row.task_id))
        == "archived"
    )
    checkpoint_revision = row.revision + 1
    disposition = (
        "terminal"
        if state in agent_limits.TERMINAL
        else "paused"
        if state == "paused"
        else "waiting_job"
        if state == "waiting_job"
        else "continue"
    )
    result = AgentJobResult(
        session_id=row.id,
        checkpoint_revision=checkpoint_revision,
        disposition=disposition,
        session_state=state,
        step_id=step.id if step else None,
        child_job_id=step.child_job_id if step else None,
        pause_id=pause.id if pause else None,
        completion="complete"
        if state == "completed"
        else "partial"
        if state == "partial"
        else None,
        stop_reason=reason,
        output_refs=list(outputs),
        usage_ids=[usage.id for usage in usages if usage.job_id == job.id],
        cost=cost,
    )
    with session.no_autoflush:
        row.vendor_calls_used = len(calls)
        if state in agent_limits.TERMINAL or state == "paused":
            row.active_seconds_used = agent_limits.active_used(row, timestamp)
            row.active_since = None
        row.state, row.updated_at = state, timestamp
        row.revision = checkpoint_revision
        row.current_job_id, row.current_run_id = None, None
        await session.flush([row])
    job.result = result.model_dump(mode="json")
    job.status, job.finished_at, job.lease_until, job.error = "succeeded", timestamp, None, None
    if archived:
        # Archive permits failure settlement, but never a new success publication.
        job.status = "failed"
        job.error = {"code": "task_archived", "message": "Task is archived", "exit_code": 2}
    audit(
        session,
        actor,
        "agent.session." + (state if state in agent_limits.TERMINAL else "checkpoint"),
        row.id,
        {
            "session_id": str(row.id),
            "revision": row.revision,
            "state": state,
            "reason_code": reason,
            "job_id": str(job.id),
            "run_id": str(job.run_id),
        },
    )
    if state not in agent_limits.TERMINAL:
        await processor.queue.enqueue_agent_wake(
            session, str(row.org_id), str(row.id), delay=0 if state == "queued" else 30
        )
    await session.flush()


async def projection(session, row, output, settings):
    """Keep a bounded, masked context projection separate from the original receipt."""
    forbidden = {
        "submission",
        "encrypted_input",
        "storage_key",
        "download_url",
        "signed_url",
        "url",
        "api_key",
        "token",
        "authorization",
        "cookie",
        "last4",
        "last_four",
        "value_tail",
    }

    def visit(value):
        if isinstance(value, dict):
            return {key: visit(item) for key, item in value.items() if key.lower() not in forbidden}
        if isinstance(value, list):
            return [visit(item) for item in value]
        return value

    sent = await agents.redact_text(
        session, row, agents.canonical(visit(output.model_dump(mode="json"))), settings, limit=65536
    )
    if len(sent.encode()) > 65536:
        raise ProviderFailure("Tool projection exceeds its byte limit", code="agent_context_limit")
    return sent


async def record_tool_output(session, row, step, job, output, settings):
    payload = output.model_dump(mode="json")
    usage_ids = []
    if output.child_job_id is not None and not output.reused_job:
        usage_ids = [
            str(value)
            for value in (
                await session.scalars(
                    select(UsageRecord.id)
                    .where(UsageRecord.job_id == output.child_job_id)
                    .order_by(UsageRecord.created_at, UsageRecord.id)
                )
            ).all()
        ]
    advance_step(
        step,
        job,
        "completed",
        result_enc=agents.seal(settings, step.id, row.org_id, row.id, payload),
        result_sha256=agents.digest(payload),
        exit_code=output.exit_code,
        usage_ids=usage_ids,
        finished_at=await agents.now(session),
    )
    await session.flush([step])
    text = await projection(session, row, output, settings)
    # Preserve the full bounded projection across append-only messages instead of
    # silently truncating a source or claiming that omitted content was read.
    chunks = [text[index : index + 7600] for index in range(0, len(text), 7600)]
    total = await session.scalar(
        select(func.count()).select_from(AgentMessage).where(AgentMessage.session_id == row.id)
    )
    if total + len(chunks) >= 40:
        raise ProviderFailure("Conversation needs a smaller selection", code="agent_context_limit")
    for index, chunk in enumerate(chunks):
        await agents.add_message(
            session,
            row,
            "tool",
            f"{step.command} result {index + 1}/{len(chunks)}:\n{chunk}",
            settings,
            step,
        )
        await session.flush()
    actor = await actor_for(session, row, job=job, step=step)
    await set_actor_context(session, actor)
    audit(
        session,
        actor,
        "agent.tool.completed",
        step.id,
        {
            "command": step.command,
            "result_sha256": step.result_sha256,
            "exit_code": step.exit_code,
            "child_job_id": str(step.child_job_id) if step.child_job_id else None,
        },
    )


async def resolve_llm(processor, session, job):
    llm = await processor.resolve(session, job) if processor.resolve else processor.llm
    return with_reasoning(llm, job.reasoning)[0]


async def invocation_context(session, row, step, job):
    principal = await session.get(AgentPrincipal, row.principal_id)
    assert principal is not None
    timestamp = await agents.now(session)
    return AgentInvocationContext.model_validate(
        {
            "principal": agents.principal_view(principal),
            "session_id": row.id,
            "task_id": row.task_id,
            "extraction_job_id": row.extraction_job_id,
            "step_id": step.id,
            "job_id": job.id,
            "run_id": job.run_id,
            "invocation_id": step.invocation_id,
            "expected_session_revision": row.revision,
            "expected_step_revision": step.revision,
            "expected_step_state": step.state,
            "schema_sha256": row.tool_schema_sha256,
            "input_refs": step.input_refs,
            "deadline": timestamp
            + timedelta(seconds=agent_limits.remaining_seconds(row, timestamp)),
        }
    )


async def decide(processor, execution):
    settings = processor.settings
    async with processor.db.transaction(execution.org_id) as session:
        row, job, actor = await fence(session, execution)
        await agent_limits.enforce(session, row, settings)
        llm = await resolve_llm(processor, session, job)
        if agents.digest(model_identity(llm)) != row.model_sha256:
            raise ProviderFailure("Pinned model changed", code="agent_model_changed")
        request = await decision_request(session, row, uuid4(), settings, actor=actor)
        if agent_tools.schema_digest(request.tools) != row.tool_schema_sha256:
            raise ProviderFailure("Tool registry changed", code="tool_schema_changed")
        provider = reasoning_provider(llm)
        step = await new_step(
            session, row, job, "decision", settings, refs=request.input_refs, request=request
        )
        expected = row.revision
        audit(
            session,
            actor,
            "agent.decision.started",
            step.id,
            {
                "session_id": str(row.id),
                "step_id": str(step.id),
                "input_sha256": agents.digest(request),
                "job_id": str(job.id),
                "run_id": str(job.run_id),
            },
        )
    async with processor.db.transaction(execution.org_id) as session:
        row, job, _ = await fence(session, execution, expected_revision=expected)
        quote = provider.quote(request)
        await agent_limits.enforce(session, row, settings, quote)
        preflight = (
            await budget_preflight.attach(
                session,
                {},
                command="agent start",
                task_id=row.task_id,
                input_hash=agents.digest(request),
                currency=settings.billing_currency,
                settings=settings,
                quotes=[quote],
                planned_calls=1,
            )
        )["budget_preflight"]
        blocker = preflight["admission_blocker"]
        if blocker:
            await execution.admission_failure(
                session, job, quote, blocker, "Review the next decision's budget"
            )

    async def before_admit(session):
        row, _, actor = await fence(session, execution, expected_revision=expected)
        live = await decision_request(
            session, row, step.id, settings, actor=actor, allow_exhausted=True
        )
        if (
            live.input_refs != request.input_refs
            or agent_tools.schema_digest(live.tools) != row.tool_schema_sha256
        ):
            raise ProviderFailure(
                "Decision input changed before dispatch", code="agent_inputs_changed"
            )

    execution.before_admit = before_admit
    output = await provider.decide(request)
    async with processor.db.transaction(execution.org_id) as session:
        row, job, actor = await fence(session, execution, expected_revision=expected)
        await agent_limits.enforce(session, row, settings)
        step = await session.get(AgentStep, step.id)
        assert step is not None
        await set_actor_context(session, await actor_for(session, row, job=job, step=step))
        current_request = await decision_request(
            session, row, step.id, settings, actor=actor, allow_exhausted=True
        )
        if [ref.model_dump(mode="json") for ref in current_request.input_refs] != step.input_refs:
            raise ProviderFailure("Decision inputs changed in flight", code="agent_inputs_changed")
        action = output.decision.action
        refs = (
            action.input_refs
            if isinstance(action, ToolDecision)
            else action.output_refs
            if isinstance(action, CompletionDecision)
            else []
        )
        available = {(ref.kind, ref.id, ref.revision_ref, ref.sha256) for ref in request.input_refs}
        if any((ref.kind, ref.id, ref.revision_ref, ref.sha256) not in available for ref in refs):
            raise ProviderFailure(
                "Decision references unknown input", code="invalid_provider_output"
            )
        if isinstance(action, HumanActionNeeded) and not set(action.resource_ids) <= {
            ref.id for ref in request.input_refs
        }:
            raise ProviderFailure(
                "Human action references unknown input", code="invalid_provider_output"
            )
        payload = output.decision.model_dump(mode="json")
        usage_ids = list(
            (
                await session.scalars(select(UsageRecord.id).where(UsageRecord.job_id == job.id))
            ).all()
        )
        advance_step(
            step,
            job,
            "completed",
            result_enc=agents.seal(settings, step.id, row.org_id, row.id, payload),
            result_sha256=agents.digest(payload),
            exit_code=0,
            usage_ids=[str(value) for value in usage_ids],
            finished_at=await agents.now(session),
        )
        await session.flush([step])
        audit(
            session,
            actor,
            "agent.decision.completed",
            step.id,
            {
                "session_id": str(row.id),
                "step_id": str(step.id),
                "result_sha256": step.result_sha256,
                "job_id": str(job.id),
                "run_id": str(job.run_id),
            },
        )
        if isinstance(action, HumanActionNeeded):
            fixed = {
                "export": "Export through the human export command after reviewing the draft.",
                "confidential_reveal": "Handle confidential values through the human-only confidential commands; do not send values to the agent.",
            }
            pending = await agents.pause(
                session,
                row,
                "human_action",
                fixed.get(action.action, action.question),
                settings,
                step,
                action=action.action,
                resource_ids=action.resource_ids,
            )
            await checkpoint(
                session, row, job, actor, processor, "paused", step=step, pause=pending
            )
        elif isinstance(action, CompletionDecision):
            await finish_goal(
                session, row, job, actor, processor, step, action, execution=execution
            )
        else:
            await checkpoint(session, row, job, actor, processor, "queued", step=step)


async def finish_goal(session, row, job, actor, processor, step, action, *, execution):
    draft = await session.scalar(
        select(DraftRun)
        .join(AgentJobLink, AgentJobLink.job_id == DraftRun.generation_job_id)
        .where(
            AgentJobLink.session_id == row.id,
            DraftRun.task_id == row.task_id,
            DraftRun.extraction_job_id == row.extraction_job_id,
        )
        .order_by(DraftRun.created_at.desc(), DraftRun.id.desc())
        .limit(1)
    )
    if draft is None:
        pending = await agents.pause(
            session,
            row,
            "human_action",
            "The agent has not assembled a draft. Clarify or cancel this session.",
            processor.settings,
            step,
            action="clarify",
        )
        await checkpoint(session, row, job, actor, processor, "paused", step=step, pause=pending)
        return
    from app.services.drafts import show_draft

    live = await agent_limits.enforce(session, row, processor.settings)
    view = await show_draft(session, live, draft.id, processor.storage)
    row, job, actor = await fence(session, execution, step=step)
    if view["validity"] != "current":
        raise ProviderFailure("Confirmed inputs changed", code="agent_inputs_changed")
    output_ref = AgentInputRef(
        kind="draft",
        id=draft.id,
        revision_ref=str(draft.generation_job_id),
        sha256=draft.input_hash,
    )
    await agents.add_message(session, row, "assistant", action.summary, processor.settings, step)
    await checkpoint(
        session,
        row,
        job,
        actor,
        processor,
        "partial" if view["completion"] == "partial" else "completed",
        step=step,
        outputs=[output_ref],
    )


async def execute_tool(
    processor, execution, decision_step_id=None, *, tool_step_id=None, retry_step_id=None
):
    # Save the counted intent before trying the command. A failed preflight or a
    # rolled-back child submission still consumes its step, but no vendor call.
    async with processor.db.transaction(execution.org_id) as session:
        row, job, actor = await fence(session, execution)
        await agent_limits.enforce(session, row, processor.settings)
        if tool_step_id is None:
            prior = await session.get(AgentStep, retry_step_id or decision_step_id)
            assert prior is not None
            if retry_step_id is not None:
                from pydantic import TypeAdapter

                from app.schemas.agent_contracts import ToolCall

                call = TypeAdapter(ToolCall).validate_python(
                    agents.unseal(
                        processor.settings, prior.id, row.org_id, row.id, prior.arguments_enc
                    )
                )
                if prior.child_job_id is not None and call.command in {"card generate", "draft"}:
                    call = call.model_copy(
                        update={
                            "arguments": call.arguments.model_copy(
                                update={
                                    "input": call.arguments.input.model_copy(update={"retry": True})
                                }
                            )
                        }
                    )
            else:
                decision = AgentDecision.model_validate(
                    agents.unseal(
                        processor.settings, prior.id, row.org_id, row.id, prior.result_enc
                    )
                )
                assert isinstance(decision.action, ToolDecision)
                call = decision.action.call
            step = await new_step(
                session, row, job, "tool", processor.settings, call=call, refs=prior.input_refs
            )
            tool_step_id = step.id
    async with processor.db.transaction(execution.org_id) as session:
        from pydantic import TypeAdapter

        from app.schemas.agent_contracts import ToolCall

        row, job, actor = await fence(session, execution)
        live = await agent_limits.enforce(session, row, processor.settings)
        step = await session.get(AgentStep, tool_step_id)
        assert step is not None
        if step.state != "planned":
            raise ProviderFailure("Tool step was already advanced", code="job_attempt_stopped")
        call = TypeAdapter(ToolCall).validate_python(
            agents.unseal(processor.settings, step.id, row.org_id, row.id, step.arguments_enc)
        )
        agent = replace(
            live,
            actor_kind="agent",
            step_id=step.id,
            invocation_id=step.invocation_id,
            job_id=job.id,
            run_id=job.run_id,
        )
        await set_actor_context(session, agent)
        llm = await resolve_llm(processor, session, job)
        if agents.digest(model_identity(llm)) != row.model_sha256:
            raise ProviderFailure("Pinned model changed", code="agent_model_changed")
        current_inputs = await decision_request(
            session, row, step.id, processor.settings, actor=agent, allow_exhausted=True
        )
        known = {(ref.kind, str(ref.id)): ref for ref in current_inputs.input_refs}
        if any(
            (ref["kind"], ref["id"]) not in known
            or known[(ref["kind"], ref["id"])].model_dump(mode="json") != ref
            for ref in step.input_refs
        ):
            raise ProviderFailure(
                "Tool inputs changed after the decision", code="agent_inputs_changed"
            )
        broker = agent_tools.CommandToolProvider(
            session,
            agent,
            row,
            step,
            storage=processor.storage,
            llm=llm,
            settings=processor.settings,
            queue=processor.queue,
            execution=execution,
        )
        audit(
            session,
            agent,
            "command.invoked",
            step.id,
            {"command": step.command, "arguments_sha256": step.arguments_sha256},
        )
        output = await broker.invoke(await invocation_context(session, row, step, job), call)
        row, job, actor = await fence(session, execution, step=step)
        audit(
            session,
            actor,
            "command.completed",
            step.id,
            {"command": step.command, "exit_code": output.exit_code},
        )
        step.child_job_id = output.child_job_id
        if output.child_job_id is not None:
            payload = output.model_dump(mode="json")
            advance_step(
                step,
                job,
                "waiting_job",
                result_enc=agents.seal(processor.settings, step.id, row.org_id, row.id, payload),
                result_sha256=agents.digest(payload),
            )
            await session.flush([step])
            audit(
                session,
                actor,
                "agent.tool.submitted",
                step.id,
                {
                    "command": step.command,
                    "child_job_id": str(output.child_job_id),
                    "invocation_id": str(step.invocation_id),
                    "job_id": str(job.id),
                    "run_id": str(job.run_id),
                },
            )
            await checkpoint(session, row, job, actor, processor, "waiting_job", step=step)
        else:
            await record_tool_output(session, row, step, job, output, processor.settings)
            await checkpoint(session, row, job, actor, processor, "queued", step=step)


async def collect_child(processor, execution, step_id):
    async with processor.db.transaction(execution.org_id) as session:
        row, job, actor = await fence(session, execution)
        live = await agent_limits.enforce(session, row, processor.settings)
        step = await session.get(AgentStep, step_id)
        assert step is not None
        await set_actor_context(session, await actor_for(session, row, job=job, step=step))
        broker = agent_tools.CommandToolProvider(
            session,
            live,
            row,
            step,
            storage=processor.storage,
            llm=None,
            settings=processor.settings,
            queue=processor.queue,
            execution=execution,
        )
        output = await broker.recover(await invocation_context(session, row, step, job))
        # Command reads may install a delegated read identity without execution
        # IDs. Recheck the live claim and restore the controller before any write.
        row, job, actor = await fence(session, execution, step=step)
        if output is None:
            raise ProviderFailure(
                "Tool submission cannot be verified", code="agent_request_uncertain"
            )
        status = output.result.data.get("status")
        if status in {"queued", "running"}:
            await checkpoint(session, row, job, actor, processor, "waiting_job", step=step)
            return
        if status in {"failed", "cancelled"}:
            error = output.result.data.get("error") or {}
            code = error.get("code", "agent_child_failed")
            if code in RECOVERY_STOPS:
                raise ProviderFailure("Verify the interrupted child request", code=code)
            if code in BUDGET_STOPS:
                advance_step(
                    step,
                    job,
                    "failed",
                    error_code=code,
                    exit_code=output.exit_code,
                    finished_at=await agents.now(session),
                )
                await session.flush([step])
                pending = await agents.pause(
                    session,
                    row,
                    "budget",
                    "Review the child job's existing budget intervention before retrying its fixed input.",
                    processor.settings,
                    step,
                )
                await checkpoint(
                    session,
                    row,
                    job,
                    actor,
                    processor,
                    "paused",
                    step=step,
                    pause=pending,
                    reason=code,
                )
                return
            if code in AUTHORITY_STOPS or code in CHANGE_STOPS:
                raise ProviderFailure("Child inputs or authority changed", code=code)
            advance_step(
                step,
                job,
                "failed",
                error_code=code,
                exit_code=output.exit_code,
                finished_at=await agents.now(session),
            )
            await session.flush([step])
            await checkpoint(
                session,
                row,
                job,
                actor,
                processor,
                await stopped_state(session, row),
                step=step,
                reason=code,
            )
            return
        await record_tool_output(session, row, step, job, output, processor.settings)
        if step.command == "card generate":
            pending = await agents.pause(
                session,
                row,
                "human_action",
                "Review, submit and confirm the response cards through the existing card commands, then resume. You may finish review with gaps.",
                processor.settings,
                step,
                action="review_cards",
            )
            await checkpoint(
                session, row, job, actor, processor, "paused", step=step, pause=pending
            )
        else:
            await checkpoint(session, row, job, actor, processor, "queued", step=step)


async def fragment(processor, execution):
    async with processor.db.transaction(execution.org_id) as session:
        row, job, actor = await fence(session, execution)
        await agent_limits.enforce(session, row, processor.settings)
        last = await session.scalar(
            select(AgentStep)
            .where(AgentStep.session_id == row.id)
            .order_by(AgentStep.ordinal.desc())
            .limit(1)
        )
        retry_planned_tool = last is not None and last.kind == "tool" and last.state == "planned"
        if (
            last is not None
            and last.state in {"planned", "submitted", "uncertain"}
            and not retry_planned_tool
        ):
            # A decision admitted before its receipt was saved cannot safely be
            # regenerated, even if its HTTP call has already settled.
            admitted = await session.scalar(
                select(VendorCall.id)
                .where(
                    VendorCall.job_id == last.created_by_job_id,
                    VendorCall.run_id == last.created_by_run_id,
                    VendorCall.state != "not_sent",
                )
                .limit(1)
            )
            if admitted is not None or last.state == "uncertain":
                raise ProviderFailure(
                    "Verify the interrupted request", code="agent_request_uncertain"
                )
            # Proven unsent decisions can be replaced with a new counted step.
            advance_step(
                last,
                job,
                "failed",
                error_code="agent_unsent_interruption",
                exit_code=3,
                finished_at=await agents.now(session),
            )
            await session.flush([last])
            await checkpoint(session, row, job, actor, processor, "queued", step=last)
            return
        step_id = last.id if last else None
        state, kind = (last.state, last.kind) if last else (None, None)
        retry_budget_tool = (
            last is not None
            and last.kind == "tool"
            and last.state == "failed"
            and last.error_code in BUDGET_STOPS
        )
        changed_intent = (
            last is not None
            and await session.scalar(
                select(AgentMessage.id)
                .where(
                    AgentMessage.session_id == row.id,
                    AgentMessage.role == "human",
                    AgentMessage.created_at > last.created_at,
                )
                .limit(1)
            )
            is not None
        )
        if changed_intent:
            retry_budget_tool = False
        saved_action = None
        if last is not None and kind == "decision" and state == "completed":
            saved_action = AgentDecision.model_validate(
                agents.unseal(processor.settings, last.id, row.org_id, row.id, last.result_enc)
            ).action
        if changed_intent:
            saved_action = None
    if retry_planned_tool:
        await execute_tool(processor, execution, tool_step_id=step_id)
    elif retry_budget_tool:
        await execute_tool(processor, execution, retry_step_id=step_id)
    elif step_id is not None and state == "waiting_job":
        await collect_child(processor, execution, step_id)
    elif step_id is not None and isinstance(saved_action, ToolDecision):
        await execute_tool(processor, execution, step_id)
    else:
        await decide(processor, execution)


async def fail_fragment(processor, execution, error):
    code = (
        error.code
        if isinstance(error, (ServiceError, ProviderFailure))
        else "agent_processing_failed"
    )
    async with processor.db.transaction(execution.org_id) as session:
        row, job, actor = await fence(session, execution)
        last = await session.scalar(
            select(AgentStep)
            .where(AgentStep.session_id == row.id)
            .order_by(AgentStep.ordinal.desc())
            .limit(1)
        )
        _, calls = await agent_limits.ledger(session, row)
        ambiguous = any(call.state in {"pending", "unknown"} for call in calls)
        if (
            last is not None
            and last.kind == "decision"
            and last.state in {"planned", "submitted"}
            and code not in {"invalid_provider_output", "provider_refused"}
        ):
            ambiguous = ambiguous or any(
                call.job_id == last.created_by_job_id and call.state != "not_sent" for call in calls
            )
        timestamp = await agents.now(session)
        if timestamp >= row.expires_at:
            code = "agent_lifetime_limit"
        elif agent_limits.remaining_seconds(row, timestamp) <= 0:
            code = "agent_time_limit"
        if ambiguous and code not in agent_limits.HARD_STOPS:
            code = "agent_request_uncertain"
        failed_collection = False
        if (
            isinstance(error, IntegrityError)
            and last is not None
            and last.state == "waiting_job"
            and last.child_job_id is not None
        ):
            child = await session.get(Job, last.child_job_id)
            # A published child survives the rolled-back collection transaction.
            # Record that collection failed rather than claiming it is still waiting.
            failed_collection = child is not None and child.status in {
                "succeeded",
                "failed",
                "cancelled",
            }
        if last is not None and (
            last.state not in {"completed", "failed", "waiting_job"} or failed_collection
        ):
            advance_step(
                last,
                job,
                "uncertain" if code in RECOVERY_STOPS else "failed",
                error_code=code,
                exit_code=4,
                finished_at=await agents.now(session),
            )
            await session.flush([last])
            actor = await actor_for(session, row, job=job, step=last)
            await set_actor_context(session, actor)
            audit(
                session,
                actor,
                f"agent.{last.kind}.{'uncertain' if code in RECOVERY_STOPS else 'failed'}",
                last.id,
                {"command": last.command, "reason_code": code},
            )
        if code in agent_limits.HARD_STOPS or code == "agent_cost_limit_reached":
            audit(
                session,
                actor,
                "agent.limit_reached",
                row.id,
                {"reason_code": code, "job_id": str(job.id), "run_id": str(job.run_id)},
            )
            await checkpoint(
                session,
                row,
                job,
                actor,
                processor,
                await stopped_state(session, row),
                step=last,
                reason=code,
            )
            return
        if code in RECOVERY_STOPS:
            kind, question = (
                "recovery",
                "An admitted request has no verified saved outcome. Verify its ledger and result; do not resend. Cancel and start a new session if it cannot be reconciled.",
            )
        elif code in AUTHORITY_STOPS:
            kind, question = (
                "authority",
                "The owner's delegation expired or permissions changed. Resume from a current human session after restoring the required access.",
            )
        elif code in BUDGET_STOPS:
            kind, question = (
                "budget",
                "The existing budget preflight or admission blocked further work. Review the task budget, prepaid balance or configured pricing before resuming.",
            )
        elif code in CHANGE_STOPS:
            kind, question = (
                "human_action",
                "Inputs, schemas or context limits changed. Review the selected scope; this fixed session cannot silently adopt a different plan.",
            )
        else:
            await checkpoint(
                session,
                row,
                job,
                actor,
                processor,
                await stopped_state(session, row),
                step=last,
                reason=code,
            )
            return
        pending = await agents.pause(
            session,
            row,
            kind,
            question,
            processor.settings,
            last,
            action="clarify" if kind == "human_action" else None,
        )
        await checkpoint(
            session, row, job, actor, processor, "paused", step=last, pause=pending, reason=code
        )


async def process_if_agent(processor, org_id, job_id):
    async with processor.db.transaction(org_id) as session:
        kind = await session.scalar(select(Job.kind).where(Job.id == job_id))
        if kind != "agent":
            return False
        job = await locked_job(session, job_id)
        assert job is not None and job.agent_session_id is not None
        row = await agent_limits.locked_session(session, job.agent_session_id)
        timestamp = await agents.now(session)
        if job.status in {"succeeded", "failed", "cancelled"} or (
            job.status == "running" and job.lease_until and job.lease_until > timestamp
        ):
            return True
        if job.status == "running":
            # The durable wake, rather than a queue retry, owns expired-attempt
            # recovery and creates a fresh controller with the saved checkpoint.
            await processor.queue.enqueue_agent_wake(session, str(org_id), str(row.id), delay=0)
            return True
        if (
            row.state in agent_limits.TERMINAL
            or row.state == "paused"
            or row.current_job_id != job.id
        ):
            return True
        principal = await session.get(AgentPrincipal, row.principal_id)
        try:
            if principal is None:
                raise ServiceError(
                    "agent_authority_expired", "Agent authority is unavailable", 403, 4
                )
            live_actor = await agent_identity(session, principal)
            live_actor.session_id = row.id
            await agent_limits.task_authority(session, live_actor, row.task_id)
        except (ServiceError, ProviderFailure):
            job.status, job.finished_at, job.lease_until = "failed", timestamp, None
            await session.flush([job])
            # locked_job installed a queued Job, not an admitted execution. Close
            # it first, then stop set_actor_context from rebinding its stale ID
            # so the pause uses the database's existing no-execution cleanup fence.
            session.info.pop("execution_job", None)
            actor = await actor_for(session, row)
            await set_actor_context(session, actor)
            await agents.pause(
                session,
                row,
                "authority",
                "Restore the owner's current membership and delegation before resuming.",
                processor.settings,
            )
            row.current_job_id, row.current_run_id = None, None
            return True
        job.status, job.run_id = "running", uuid4()
        job.attempts += 1
        job.lease_until = timestamp + timedelta(seconds=processor.settings.job_lease_seconds)
        actor = await actor_for(session, row, job=job)
        await set_actor_context(session, actor)
        await session.flush([job])
        row.state, row.current_run_id = "running", job.run_id
        row.revision += 1
        await session.flush([row])
        run_id = job.run_id
        assert run_id is not None
    execution = JobExecution(processor.settings, processor.db, org_id, job_id, run_id)
    async with execution.activate():
        try:
            await fragment(processor, execution)
        except Exception as error:
            if not isinstance(error, (ProviderFailure, ServiceError)):
                log_unexpected(logger, "Agent controller", error)
            try:
                await fail_fragment(processor, execution, error)
            except ProviderFailure as fenced:
                if fenced.code != "job_attempt_stopped":
                    raise
    return True


async def wake(processor, org_id, session_id):
    """An org-scoped durable wake advances a checkpoint or saves its successor."""
    async with processor.db.transaction(org_id) as session:
        row = await agent_limits.locked_session(session, session_id)
        if row.state in agent_limits.TERMINAL:
            return
        timestamp = await agents.now(session)
        current = await session.get(Job, row.current_job_id) if row.current_job_id else None
        live = (
            current is not None
            and current.status == "running"
            and current.lease_until
            and current.lease_until > timestamp
        )
        await processor.queue.enqueue_agent_wake(session, str(org_id), str(session_id))
        actor = await actor_for(session, row)
        await set_actor_context(session, actor)
        if timestamp >= row.expires_at or (
            row.state != "paused" and agent_limits.remaining_seconds(row, timestamp) <= 0
        ):
            if row.pause_id:
                pending = await session.get(AgentPause, row.pause_id)
                if pending is not None and pending.status == "pending":
                    pending.status = "expired"
            unfinished = (
                await session.scalars(
                    select(Job).where(
                        Job.id.in_(agent_limits.own_jobs(row)),
                        Job.status.in_(["queued", "running"]),
                    )
                )
            ).all()
            for child in unfinished:
                child.status, child.finished_at, child.lease_until = "cancelled", timestamp, None
            # Closing the live job first admits only the cleanup transition under
            # the database's no-execution recovery fence.
            await session.flush(unfinished)
            row.active_seconds_used = agent_limits.active_used(row, timestamp)
            row.state, row.active_since = "failed", None
            row.current_job_id, row.current_run_id = None, None
            row.revision += 1
            row.updated_at = timestamp
            audit(
                session,
                actor,
                "agent.limit_reached",
                row.id,
                {
                    "reason_code": "agent_lifetime_limit"
                    if timestamp >= row.expires_at
                    else "agent_time_limit"
                },
            )
            return
        if live:
            return
        if row.state == "paused":
            return
        principal = await session.get(AgentPrincipal, row.principal_id)
        try:
            if principal is None:
                raise ServiceError(
                    "agent_authority_expired", "Agent authority is unavailable", 403, 4
                )
            live_actor = await agent_identity(session, principal)
            live_actor.session_id = row.id
            await agent_limits.task_authority(session, live_actor, row.task_id)
            valid_authority = True
        except (ServiceError, ProviderFailure):
            valid_authority = False
        if not valid_authority:
            if current is not None and current.status in {"queued", "running"}:
                current.status, current.finished_at, current.lease_until = "failed", timestamp, None
            await agents.pause(
                session,
                row,
                "authority",
                "Renew the owner's delegation from a valid human session.",
                processor.settings,
            )
            row.current_job_id, row.current_run_id = None, None
            return
        if current is not None and current.status == "queued":
            await processor.queue.ensure_process_delivery(session, current)
            return
        if current is not None and current.status == "running":
            current.status, current.finished_at, current.lease_until = "failed", timestamp, None
            current.error = {
                "code": "agent_controller_interrupted",
                "message": "Recover the saved checkpoint",
                "exit_code": 4,
            }
            await session.flush([current])
        if row.state == "waiting_job":
            waiting = await session.scalar(
                select(AgentStep)
                .where(AgentStep.session_id == row.id, AgentStep.state == "waiting_job")
                .order_by(AgentStep.ordinal.desc())
                .limit(1)
            )
            child = (
                await session.get(Job, waiting.child_job_id)
                if waiting and waiting.child_job_id
                else None
            )
            if child is not None and child.status in {"queued", "running"}:
                if child.status == "queued" or (
                    child.lease_until and child.lease_until > timestamp
                ):
                    if child.status == "queued":
                        await processor.queue.ensure_process_delivery(session, child)
                    return
                # Reclaiming an unknown paid child is forbidden. The next controller
                # inspects accounting and produces a recovery pause instead.
                child.status, child.finished_at = "failed", timestamp
                admitted = await session.scalar(
                    select(VendorCall.id)
                    .where(VendorCall.job_id == child.id, VendorCall.state != "not_sent")
                    .limit(1)
                )
                child.error = {
                    "code": "agent_request_uncertain" if admitted else "agent_child_interrupted",
                    "message": "Verify the saved child job",
                    "exit_code": 4,
                }
        row.current_job_id, row.current_run_id = None, None
        row.state, row.updated_at = "queued", timestamp
        row.revision += 1
        await session.flush([row])
        await enqueue_controller(session, row, actor, processor.settings, processor.queue)
