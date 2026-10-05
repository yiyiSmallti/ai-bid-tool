"""Fixed A01 command broker; command services retain business authorization and gates."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select

from app.core.errors import ServiceError, not_found
from app.models.agent import AgentJobLink
from app.models.entities import Job, Requirement, VendorCall
from app.models.response_cards import DraftRun, ResponseCard
from app.schemas.agent_contracts import (
    AgentLimits,
    AgentProvenance,
    AgentScope,
    AgentToolDefinition,
    AgentToolOutput,
    ToolCall,
    ToolCommand,
)
from app.schemas.budget_contracts import BudgetPreflightData
from app.schemas.contracts import CONTRACT_VERSION, Result
from app.services import card_generation, drafts, jobs, requirements
from app.services import response_cards as cards

TOOL_SCOPES: dict[ToolCommand, tuple[AgentScope, ...]] = {
    "req list": ("task:read",),
    "card list": ("task:read", "card:read"),
    "card show": ("task:read", "card:read"),
    "card generate": ("task:read", "card:read", "card:generate"),
    "draft": ("task:read", "card:read", "draft:run"),
    "draft show": ("task:read", "draft:read"),
    "job status": ("task:read", "job:read"),
}


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


async def definitions(principal) -> list[AgentToolDefinition]:
    from bid_cli.schema import command_schema

    schema = command_schema(version=CONTRACT_VERSION)
    output = []
    for command, scopes in TOOL_SCOPES.items():
        if not set(scopes) <= set(principal.scopes):
            continue
        invocation = schema["commands"][command]["invocation_input"]
        output.append(
            AgentToolDefinition(
                command=command,
                contract_version=CONTRACT_VERSION,
                schema_sha256=digest(
                    {"command": command, "input": invocation, "result": schema["result"]}
                ),
                invocation_input=invocation,
                result_schema=schema["result"],
                required_scopes=list(scopes),
                effect="job" if command in {"card generate", "draft"} else "read",
                supports_dry_run=command in {"card generate", "draft"},
            )
        )
    return output


class CommandToolProvider:
    """The approved broker protocol bound to one live command transaction."""

    def __init__(self, session, actor, state, step, *, storage, llm, settings, queue, execution):
        self.session, self.actor, self.state, self.step = session, actor, state, step
        self.resources = {
            "storage": storage,
            "llm": llm,
            "settings": settings,
            "queue": queue,
            "execution": execution,
        }

    async def definitions(self, principal):
        return await definitions(principal)

    async def check_context(self, context):
        from sqlalchemy import func

        execution = self.resources["execution"]
        job = await execution.owned_job(self.session)
        timestamp = await self.session.scalar(select(func.clock_timestamp()))
        if (
            context.principal.id != self.state.principal_id
            or context.principal.org_id != self.state.org_id
            or context.principal.user_id != self.state.owner_user_id
            or context.session_id != self.state.id
            or context.task_id != self.state.task_id
            or context.extraction_job_id != self.state.extraction_job_id
            or context.step_id != self.step.id
            or context.invocation_id != self.step.invocation_id
            or context.job_id != job.id
            or context.run_id != job.run_id
            or self.state.current_job_id != job.id
            or self.state.current_run_id != job.run_id
            or self.state.state != "running"
            or context.expected_session_revision != self.state.revision
            or context.expected_step_revision != self.step.revision
            or context.expected_step_state != self.step.state
            or context.schema_sha256 != self.state.tool_schema_sha256
            or context.schema_sha256 != self.step.schema_sha256
            or [ref.model_dump(mode="json") for ref in context.input_refs] != self.step.input_refs
            or context.deadline <= timestamp
        ):
            raise ServiceError(
                "agent_invocation_stale", "Tool invocation no longer matches its checkpoint", 409, 4
            )

    async def invoke(self, context, call):
        await self.check_context(context)
        return await invoke(self.session, self.actor, self.state, self.step, call, **self.resources)

    async def recover(self, context):
        await self.check_context(context)
        return await recover(
            self.session,
            self.actor,
            self.state,
            self.step,
            storage=self.resources["storage"],
            execution=self.resources["execution"],
        )


def schema_digest(tool_definitions: list[AgentToolDefinition]) -> str:
    return digest([row.model_dump(mode="json") for row in tool_definitions])


def same_parent(row, state, *, extraction=False) -> bool:
    return (
        row is not None
        and row.org_id == state.org_id
        and row.task_id == state.task_id
        and (not extraction or row.extraction_job_id == state.extraction_job_id)
    )


async def validate_call(session, actor, agent_session, step, call):
    """Validate every selector before invoking a service; a previously seen ID is untrusted."""
    call = TypeAdapter(ToolCall).validate_python(call.model_dump(mode="json"))
    if (
        actor.actor_kind != "agent"
        or actor.token_id is not None
        or actor.org_id != agent_session.org_id
        or actor.user_id != agent_session.owner_user_id
        or actor.principal_id != agent_session.principal_id
        or actor.session_id != agent_session.id
        or actor.step_id != step.id
        or actor.invocation_id != step.invocation_id
        or step.session_id != agent_session.id
        or step.org_id != agent_session.org_id
        or step.task_id != agent_session.task_id
        or step.document_id != agent_session.document_id
    ):
        raise not_found()
    args = call.arguments
    if call.command in {"req list", "card list"}:
        if args.task != agent_session.task_id or args.job != agent_session.extraction_job_id:
            raise not_found()
    elif call.command in {"card generate", "draft"}:
        if (
            args.task != agent_session.task_id
            or args.input.extraction_job_id != agent_session.extraction_job_id
        ):
            raise not_found()
        if call.command == "card generate" and args.input.requirement_ids is not None:
            for rid in args.input.requirement_ids:
                row = await session.get(Requirement, rid)
                if (
                    not same_parent(row, agent_session)
                    or row.job_id != agent_session.extraction_job_id
                ):
                    raise not_found()
    elif call.command in {"card show", "draft show"}:
        model = ResponseCard if call.command == "card show" else DraftRun
        row = await session.get(model, args.id)
        if not same_parent(row, agent_session, extraction=True):
            raise not_found()
        if call.command == "draft show":
            linked = await session.scalar(
                select(AgentJobLink).where(
                    AgentJobLink.session_id == agent_session.id,
                    AgentJobLink.job_id == row.generation_job_id,
                    AgentJobLink.role == "tool",
                )
            )
            if linked is None:
                raise not_found()
    elif call.command == "job status":
        row = await session.get(Job, args.job_id)
        if not same_parent(row, agent_session) or row.document_id != agent_session.document_id:
            raise not_found()
        if row.kind not in {"extract", "agent", "draft", "card_generate"}:
            raise not_found()
        if row.id != agent_session.extraction_job_id:
            linked = await session.scalar(
                select(AgentJobLink).where(
                    AgentJobLink.session_id == agent_session.id,
                    AgentJobLink.job_id == row.id,
                    AgentJobLink.role.in_(["tool", "controller"]),
                )
            )
            if linked is None:
                raise not_found()
    for scope in TOOL_SCOPES[call.command]:
        actor.require(scope)
    registered = await definitions(actor)
    current = schema_digest(registered)
    if current != agent_session.tool_schema_sha256 or current != step.schema_sha256:
        raise ServiceError(
            "tool_schema_changed", "Command schemas or grants changed; preflight again", 409, 2
        )
    return call


async def prove_safe_retry(session, agent_session, kind, input_hash):
    """Lease expiry is insufficient without an owned, unambiguous input and settled calls."""
    candidates = list(
        (
            await session.scalars(
                select(Job)
                .join(
                    AgentJobLink,
                    (AgentJobLink.org_id == Job.org_id) & (AgentJobLink.job_id == Job.id),
                )
                .where(
                    AgentJobLink.session_id == agent_session.id,
                    AgentJobLink.role == "tool",
                    AgentJobLink.owned.is_(True),
                    Job.kind == kind,
                    Job.task_id == agent_session.task_id,
                    Job.document_id == agent_session.document_id,
                    Job.result["submission"]["input_hash"].astext == input_hash,
                )
                .limit(2)
                .with_for_update(of=Job)
            )
        ).all()
    )
    if len(candidates) != 1:
        raise ServiceError(
            "unsafe_tool_retry", "The original owned job cannot be uniquely verified", 409, 4
        )
    job = candidates[0]
    now = await session.scalar(select(func.clock_timestamp()))
    assert isinstance(now, datetime)
    stale = job.status == "running" and job.lease_until is not None and job.lease_until <= now
    if job.status not in {"failed", "cancelled"} and not stale:
        raise ServiceError(
            "unsafe_tool_retry", "The original job is live or already complete", 409, 4
        )
    unresolved = await session.scalar(
        select(VendorCall.id)
        .where(VendorCall.job_id == job.id, VendorCall.state.in_(["pending", "unknown"]))
        .limit(1)
    )
    if unresolved is not None:
        raise ServiceError(
            "unsafe_tool_retry", "Verify unresolved vendor calls before retrying", 409, 4
        )
    return job


async def link_child(session, actor, agent_session, step, job, *, cached):
    if not same_parent(job, agent_session) or job.document_id != agent_session.document_id:
        raise not_found()
    previous = await session.scalar(
        select(AgentJobLink).where(
            AgentJobLink.session_id == agent_session.id,
            AgentJobLink.job_id == job.id,
        )
    )
    if previous is not None:
        if previous.role != "tool":
            raise not_found()
        return previous
    if cached and job.status != "succeeded":
        raise ServiceError(
            "tool_job_in_progress" if job.status in {"queued", "running"} else "unsafe_tool_retry",
            "The existing job cannot be taken over or automatically retried",
            409,
            4,
        )
    link = AgentJobLink(
        id=uuid4(),
        org_id=actor.org_id,
        session_id=agent_session.id,
        task_id=agent_session.task_id,
        document_id=agent_session.document_id,
        job_id=job.id,
        role="tool",
        step_id=step.id,
        owned=not cached,
    )
    session.add(link)
    await session.flush()
    return link


async def generation_preflight(session, actor, state, body, *, storage, llm, settings):
    from app.jobs.execution import job_cost
    from app.services.agent_limits import cost_view

    limits = AgentLimits.model_validate(state.limits)
    cost = await cost_view(session, state)
    remaining = limits.max_platform_charge - cost.platform_charge - cost.reserved_platform_charge
    cap = min(remaining, body.max_charge) if body.max_charge is not None else remaining
    preview_body = body.model_copy(
        update={"dry_run": True, "retry": False, "max_charge": cap if cap > 0 else None}
    )
    preview, _, warnings = await card_generation.submit_generation(
        session, actor, state.task_id, preview_body, storage, llm, settings
    )
    prior, historical_charge = None, Decimal(0)
    if body.retry:
        prior = await prove_safe_retry(session, state, "card_generate", preview["input_hash"])
        historical = await job_cost(session, prior.id, limits.billing_currency)
        if historical["charge"] is None:
            raise ServiceError(
                "billing_bound_unavailable", "Historical retry cost is unresolved", 409, 4
            )
        historical_charge = Decimal(str(historical["charge"]))
        cumulative_cap = historical_charge + remaining
        cap = (
            min(cumulative_cap, body.max_charge) if body.max_charge is not None else cumulative_cap
        )
        preview_body = preview_body.model_copy(update={"max_charge": cap if cap > 0 else None})
        preview, _, warnings = await card_generation.submit_generation(
            session, actor, state.task_id, preview_body, storage, llm, settings
        )
    if body.expected_input_hash is not None and body.expected_input_hash != preview["input_hash"]:
        raise ServiceError("generation_input_changed", "Inputs changed; preflight again", 409, 2)
    if not preview["model_redaction_enabled"]:
        raise ServiceError(
            "agent_redaction_required", "Agent model input requires redaction", 409, 4
        )
    if body.dry_run:
        return preview, warnings, None, prior.id if prior else None
    try:
        budget_contract = BudgetPreflightData.model_validate(preview.get("budget_preflight"))
    except ValidationError:
        raise ServiceError(
            "budget_dependency_unavailable", "Generation budget preflight is unavailable", 503, 3
        ) from None
    if (
        budget_contract.command != "card generate"
        or budget_contract.task_id != state.task_id
        or budget_contract.input_hash != preview["input_hash"]
    ):
        raise ServiceError(
            "budget_dependency_unavailable",
            "Generation budget preflight binding is invalid",
            503,
            3,
        )
    if budget_contract.estimate.billing_currency != limits.billing_currency:
        raise ServiceError(
            "billing_currency_mismatch", "Generation billing currency changed", 409, 4
        )
    blocker = preview.get("admission_blocker")
    budget = budget_contract.model_dump(mode="json")
    budget_blocker = budget.get("admission_blocker")
    if blocker or budget_blocker:
        code = blocker or budget_blocker
        assert isinstance(code, str)
        raise ServiceError(code, "Generation preflight requires human action", 409, 2)
    if budget.get("first_pass_fits") is False:
        task_budget = budget.get("task_budget") or {}
        amount = budget.get("estimate", {}).get("task_amount")
        available = task_budget.get("available")
        code = (
            "task_budget_exceeded"
            if (
                amount is not None
                and available is not None
                and Decimal(str(amount)) > Decimal(str(available))
            )
            else "agent_platform_cost_limit"
        )
        raise ServiceError(code, "Generation preflight exceeds an available spending bound", 409, 2)
    estimate = budget.get("estimate", {})
    estimated_charge = estimate.get("charge", preview.get("estimated_charge"))
    estimated_usd = estimate.get("usd", preview.get("estimated_cost", {}).get("usd"))
    if (
        estimated_charge is None
        or estimated_usd is None
        or cost.reserved_vendor_usd is None
        or cost.cost.usd is None
        or cost.unpriced_calls
        or cost.unresolved_calls
    ):
        raise ServiceError(
            "billing_bound_unavailable", "An enforceable generation cost bound is required", 409, 4
        )
    vendor_remaining = (
        limits.max_vendor_usd - Decimal(str(cost.cost.usd)) - cost.reserved_vendor_usd
    )
    available_charge = max(min(remaining, cap - historical_charge), Decimal(0))
    if (
        Decimal(str(estimated_charge)) > available_charge
        or Decimal(str(estimated_usd)) > vendor_remaining
    ):
        code = (
            "agent_platform_cost_limit"
            if Decimal(str(estimated_charge)) > available_charge
            else "agent_vendor_cost_limit"
        )
        raise ServiceError(code, "Generation exceeds the remaining session cost bound", 409, 4)
    admitted = body.model_copy(
        update={
            "dry_run": False,
            "retry": body.retry,
            "expected_input_hash": preview["input_hash"],
            "max_charge": cap if cap > 0 else None,
        }
    )
    return preview, warnings, admitted, prior.id if prior else None


async def invoke(
    session, actor, agent_session, step, call, *, storage, llm, settings, queue, execution
):
    """Called inside the controller's live fenced transaction; never commits independently."""
    await execution.owned_job(session)
    actor = await cards.access(session, actor, TOOL_SCOPES[call.command][0])
    call = await validate_call(session, actor, agent_session, step, call)
    args, warnings, child, cached = call.arguments, [], None, False
    if call.command == "req list":
        result = Result(
            ok=True,
            command=call.command,
            items=await requirements.list_requirements(session, actor, args.task, args.job),
        )
    elif call.command == "card list":
        data, items = await cards.list_cards(session, actor, args.task, args.job)
        warnings = await cards.scope_warnings(session, args.job)
        result = Result(ok=True, command=call.command, data=data, items=items, warnings=warnings)
    elif call.command == "card show":
        data = await cards.show_card(session, actor, args.id, history=args.history)
        current = data["card"] if args.history else data
        warnings = await cards.scope_warnings(session, UUID(current["extraction_job_id"]))
        result = Result(ok=True, command=call.command, data=data, warnings=warnings)
    elif call.command == "card generate":
        preview, warnings, admitted, prior_id = await generation_preflight(
            session, actor, agent_session, args.input, storage=storage, llm=llm, settings=settings
        )
        if admitted is None:
            data = preview
        else:
            data, child, warnings = await card_generation.submit_generation(
                session, actor, args.task, admitted, storage, llm, settings
            )
            assert child is not None
            if prior_id is not None and child.id != prior_id:
                raise ServiceError(
                    "generation_input_changed", "Retry no longer matches the original job", 409, 2
                )
            cached = data["cached"]
        result = Result(ok=True, command=call.command, data=data, warnings=warnings)
    elif call.command == "draft":
        # Draft assembly is free, but preflight still proves the current confirmed inputs.
        preview_body = args.input.model_copy(update={"dry_run": True, "retry": False})
        preview, _ = await drafts.submit_draft(session, actor, args.task, preview_body, storage)
        prior = (
            await prove_safe_retry(session, agent_session, "draft", preview["input_hash"])
            if args.input.retry
            else None
        )
        if args.input.dry_run:
            data = preview
        else:
            data, child = await drafts.submit_draft(session, actor, args.task, args.input, storage)
            assert child is not None
            if prior is not None and child.id != prior.id:
                raise ServiceError(
                    "draft_input_changed", "Retry no longer matches the original job", 409, 2
                )
            if child.result["submission"]["input_hash"] != preview["input_hash"]:
                raise ServiceError(
                    "draft_input_changed", "Draft inputs changed; preflight again", 409, 2
                )
            cached = data["cached"]
        result = Result(
            ok=True,
            command=call.command,
            data=data,
            warnings=await cards.scope_warnings(session, args.input.extraction_job_id),
        )
    elif call.command == "draft show":
        data = await drafts.show_draft(session, actor, args.id, storage)
        warnings = [
            f"negative_deviation:{row['requirement_id']}"
            for rows in data["tables"].values()
            for row in rows
            if row["deviation"] == "negative"
        ]
        warnings.extend(await cards.scope_warnings(session, UUID(data["extraction_job_id"])))
        if data["validity"] == "stale":
            warnings.append("stale_draft")
        result = Result(
            ok=data["completion"] != "partial", command=call.command, data=data, warnings=warnings
        )
    else:
        result = Result.model_validate(await jobs.status(session, actor, args.job_id, storage))
    if child is not None:
        link = await link_child(session, actor, agent_session, step, child, cached=cached)
        if link.owned and child.status == "queued" and child.queue_id is None:
            child.queue_id = await queue.enqueue_in_transaction(
                session, str(actor.org_id), str(child.id)
            )
        await session.flush()
    exit_code = 0 if result.ok else 5
    if call.command == "job status" and result.data.get("status") in {"failed", "cancelled"}:
        exit_code = 3 if result.data.get("result", {}).get("exit_code") == 3 else 4
    return AgentToolOutput(
        result=result,
        exit_code=exit_code,
        child_job_id=child.id if child else None,
        reused_job=cached,
    )


async def recover(session, actor, agent_session, step, *, storage, execution=None):
    """Resolve a persisted link only; absent/uncertain submission never dispatches work."""
    if execution is not None:
        await execution.owned_job(session)
    if (
        actor.org_id != agent_session.org_id
        or actor.user_id != agent_session.owner_user_id
        or actor.principal_id != agent_session.principal_id
        or actor.session_id != agent_session.id
        or step.org_id != agent_session.org_id
        or step.session_id != agent_session.id
    ):
        raise not_found()
    selector = (
        AgentJobLink.job_id == step.child_job_id
        if step.child_job_id
        else AgentJobLink.step_id == step.id
    )
    link = await session.scalar(
        select(AgentJobLink).where(
            AgentJobLink.session_id == agent_session.id,
            selector,
            AgentJobLink.role == "tool",
        )
    )
    if link is None:
        return None
    job = await session.get(Job, link.job_id)
    if not same_parent(job, agent_session) or job.document_id != agent_session.document_id:
        raise not_found()
    result = Result.model_validate(await jobs.status(session, actor, job.id, storage))
    exit_code = 0 if result.ok else 5
    if job.status in {"failed", "cancelled"}:
        exit_code = 3 if job.result.get("exit_code") == 3 else 4
    return AgentToolOutput(
        result=result, exit_code=exit_code, child_job_id=job.id, reused_job=not link.owned
    )


async def provenance(session, job_id):
    """Read immutable, database-validated Job origin without reading private sessions."""
    if job_id is None:
        return None
    job = await session.get(Job, job_id)
    if job is None:
        return None
    if job.invocation_id is None or not job.command:
        # Older token origin can be proven without a complete invocation receipt.
        # Never invent a command or correlation UUID to fill that historical gap.
        return None
    if job.initiated_by == "builtin_agent":
        return AgentProvenance.model_validate(
            {
                "initiated_by": "builtin_agent",
                "on_behalf_of_user_id": job.on_behalf_of_user_id,
                "principal_id": job.agent_principal_id,
                "session_id": job.agent_session_id,
                "step_id": job.agent_step_id,
                "invocation_id": job.invocation_id,
                "command": job.command,
                "job_id": job.id,
                "run_id": job.run_id,
            }
        ).model_dump(mode="json")
    if job.initiated_by == "external_agent":
        return AgentProvenance.model_validate(
            {
                "initiated_by": "external_agent",
                "on_behalf_of_user_id": job.on_behalf_of_user_id,
                "token_id": job.actor_token_id,
                "invocation_id": job.invocation_id,
                "command": job.command,
                "job_id": job.id,
                "run_id": job.run_id,
            }
        ).model_dump(mode="json")
    return None
