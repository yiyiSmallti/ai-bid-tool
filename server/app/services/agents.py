"""Owner-only agent session management and encrypted durable history."""

import hashlib
import json
import re
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import NoReturn
from uuid import UUID, uuid4

from cryptography.fernet import InvalidToken
from pydantic import BaseModel, TypeAdapter
from sqlalchemy import func, select, tuple_

from app.core.errors import ServiceError, not_found
from app.core.security import Secrets
from app.models.agent import (
    AgentJobLink,
    AgentMessage,
    AgentPause,
    AgentPrincipal,
    AgentSession,
    AgentStep,
)
from app.models.entities import Document, Job, Requirement, Task
from app.models.response_cards import ResponseCard, ResponseCardRevision
from app.providers.agent import reasoning_provider
from app.providers.base import ProviderFailure
from app.providers.configured import model_identity
from app.providers.llm import with_reasoning
from app.schemas.agent_contracts import (
    AgentDecision,
    AgentLimits,
    AgentMessageView,
    AgentMutationData,
    AgentPageData,
    AgentPauseView,
    AgentPreviewData,
    AgentPrincipalView,
    AgentSessionView,
    AgentShowData,
    AgentStepView,
    ToolCall,
    ToolDecision,
)
from app.schemas.contracts import Cost, Result
from app.services import budget_preflight, budgets, confidential, redaction
from app.services.auth import AGENT_SCOPES, ROLE_SCOPES, membership, set_actor_context
from app.services.response_cards import access, generation_materials_stale, linked_evidence
from app.services.task_workflow import access as task_access
from app.services.versioned import audit

TERMINAL = {"completed", "partial", "failed", "cancelled"}
WORKFLOW_SCOPES = {"task:read", "job:read", "card:read", "card:generate", "draft:read", "draft:run"}


def canonical(value) -> str:
    def encode(item):
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        if isinstance(item, (UUID, Decimal)):
            return str(item)
        if isinstance(item, datetime):
            return item.isoformat()
        raise TypeError(f"Unsupported canonical value: {type(item).__name__}")

    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=encode
    )


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def seal(settings, row_id, org_id, session_id, payload) -> str:
    """Bind encrypted JSON to its tenant, session and record, preventing substitution."""
    return Secrets.for_data(settings).encrypt(
        canonical(
            {
                "v": 1,
                "row_id": str(row_id),
                "org_id": str(org_id),
                "session_id": str(session_id),
                "payload": payload,
            }
        )
    )


def unseal(settings, row_id, org_id, session_id, ciphertext):
    try:
        envelope = json.loads(Secrets.for_data(settings).decrypt(ciphertext))
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"v", "row_id", "org_id", "session_id", "payload"}
            or (envelope["v"], envelope["row_id"], envelope["org_id"], envelope["session_id"])
            != (1, str(row_id), str(org_id), str(session_id))
        ):
            raise ValueError("Invalid binding")
        return envelope["payload"]
    except (InvalidToken, ValueError, TypeError, KeyError):
        raise ServiceError(
            "agent_receipt_unreadable", "Stored agent content cannot be read", 503, 4
        ) from None


def fail(code, message, status=409) -> NoReturn:
    raise ServiceError(code, message, status, 2)


async def now(session):
    return await session.scalar(select(func.clock_timestamp()))


async def human_access(session, identity, scope="agent:read", *, cancellation=False):
    # Cancellation survives a role downgrade, while membership/user/org checks stay live.
    if identity.actor_kind != "session" or identity.token_id is not None:
        raise ServiceError("forbidden", "Human session required", 403, 4)
    live = await access(session, identity, "agent:read" if cancellation else scope)
    live.session_expires_at = identity.session_expires_at
    return live


async def bind_owner(session, actor, row):
    """Keep the human executor while indexing the session that caused the action."""
    bound = replace(
        actor,
        principal_id=row.principal_id,
        session_id=row.id,
        step_id=None,
        job_id=None,
        run_id=None,
    )
    await set_actor_context(session, bound)
    return bound


async def owned(session, actor, session_id, *, lock=False):
    query = select(AgentSession).where(
        AgentSession.id == session_id,
        AgentSession.org_id == actor.org_id,
        AgentSession.owner_user_id == actor.user_id,
    )
    if lock:
        target = await session.scalar(query)
        if target is None:
            raise not_found()
        # Admission and settlement take Task before AgentSession before balance.
        await session.scalar(select(Task).where(Task.id == target.task_id).with_for_update())
    row = await session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise not_found()
    return row


def principal_view(row):
    return AgentPrincipalView.model_validate(row)


def pause_view(row):
    return AgentPauseView.model_validate(row) if row is not None else None


def message_view(row):
    return AgentMessageView.model_validate(row)


def step_view(row):
    return AgentStepView.model_validate(row)


async def session_view(session, row):
    from app.services.agent_limits import active_used, cost_view, ledger

    values = {key: getattr(row, key) for key in AgentSessionView.model_fields if key != "cost"}
    values["active_seconds_used"] = active_used(row, await now(session))
    values["cost"] = await cost_view(session, row)
    _, calls = await ledger(session, row)
    values["vendor_calls_used"] = max(row.vendor_calls_used, len(calls))
    return AgentSessionView.model_validate(values)


async def mutation(session, row, command, *, job_id=None, message_id=None, pending=None):
    view = await session_view(session, row)
    data = AgentMutationData(
        session=view, job_id=job_id, message_id=message_id, pause=pause_view(pending)
    )
    return Result(
        ok=True,
        command=command,
        data=data.model_dump(mode="json"),
        cost=Cost(billing_currency=view.cost.billing_currency),
    )


def replay(settings, record, session_id, ciphertext, request_hash, stored_hash):
    if request_hash != stored_hash:
        fail("agent_idempotency_conflict", "Idempotency key was used for different input")
    result = Result.model_validate(
        unseal(settings, record.id, record.org_id, session_id, ciphertext)
    )
    result.data["deduplicated"] = True
    return result


def revision(row, expected):
    if row.revision != expected:
        fail("agent_revision_conflict", "Session revision changed; read it again")


async def redact_text(session, row, content, settings, *, limit=8000):
    from app.memory.safety import CREDENTIAL

    entries = await confidential.task_entries(session, row.task_id)
    library = confidential.library(entries, Secrets.for_data(settings))
    sent, _ = redaction.redact(content, True, library)
    # Mask the complete Bearer value before the generic labelled-field rule
    # consumes just the word "Bearer" in an Authorization header.
    sent = re.sub(r"(?i)\bBearer[ \t]+[A-Za-z0-9._~+/=-]+", "[REDACTED_CREDENTIAL]", sent)
    sent = CREDENTIAL.sub("[REDACTED_CREDENTIAL]", sent)
    if len(sent) > limit:
        fail("agent_context_limit", "Agent content exceeds its bound")
    return sent


async def add_message(session, row, role, content, settings, step=None):
    sent = await redact_text(session, row, content, settings)
    ordinal = (
        await session.scalar(
            select(func.coalesce(func.max(AgentMessage.ordinal), 0)).where(
                AgentMessage.session_id == row.id
            )
        )
    ) + 1
    record_id = uuid4()
    message = AgentMessage(
        id=record_id,
        org_id=row.org_id,
        session_id=row.id,
        ordinal=ordinal,
        role=role,
        author_user_id=row.owner_user_id if role == "human" else None,
        step_id=step.id if step is not None else None,
        content_enc=seal(settings, record_id, row.org_id, row.id, {"content": content}),
        content=sent,
        content_sha256=hashlib.sha256(sent.encode()).hexdigest(),
        created_at=await now(session),
    )
    session.add(message)
    # A human mutation must fill its receipt before this append-only INSERT.
    return message


async def card_review_refs(session, row, resource_ids=None):
    query = (
        select(ResponseCard)
        .where(
            ResponseCard.task_id == row.task_id,
            ResponseCard.extraction_job_id == row.extraction_job_id,
        )
        .order_by(ResponseCard.id)
    )
    if resource_ids:
        query = query.where(ResponseCard.id.in_([UUID(str(value)) for value in resource_ids]))
    cards = (await session.scalars(query)).all()
    if resource_ids and len(cards) != len(set(map(str, resource_ids))):
        raise not_found()
    refs = []
    for card in cards:
        current = await session.get(ResponseCardRevision, card.current_revision_id)
        if current is None:
            raise not_found()
        # Submit/confirm transitions create revisions too; only proposal changes
        # require another review. The immutable revision pointer remains visible.
        content = {
            key: getattr(current, key)
            for key in (
                "quote_sha256",
                "response_kind",
                "response_text",
                "deviation",
                "deviation_note",
                "model_job_id",
            )
        }
        evidence = await linked_evidence(session, current.id)
        content["evidence"] = [
            {
                key: getattr(item, key)
                for key in (
                    "id",
                    "kind",
                    "task_resource_id",
                    "product_revision_id",
                    "task_feature_id",
                    "feature_revision_id",
                    "task_certificate_id",
                    "certificate_revision_id",
                    "task_org_profile_id",
                    "profile_revision_id",
                    "evidence_source_id",
                    "field_path",
                    "quote",
                    "source_sha256",
                    "page",
                    "image_sha256",
                    "region",
                )
            }
            for item in sorted(evidence, key=lambda item: str(item.id))
        ]
        requirement = await session.get(Requirement, card.requirement_id)
        if requirement is None:
            raise not_found()
        content["requirement"] = {
            key: getattr(requirement, key)
            for key in (
                "id",
                "chunk_id",
                "page",
                "location",
                "quote",
                "text",
                "condition",
            )
        }
        content["materials_stale"] = await generation_materials_stale(
            session, session.info["actor"], current
        )
        refs.append(
            {
                "kind": "card",
                "id": str(card.id),
                "revision_ref": str(card.current_revision_id),
                "sha256": digest(content),
            }
        )
    return refs


async def budget_fact_ref(session, row, question_ref, settings):
    task = await session.get(Task, row.task_id)
    if task is None:
        raise not_found()
    fact = await budgets.view(session, task, settings.billing_currency)
    return {
        "contract": "docs/plan/budget.md",
        "question_ref": str(question_ref),
        "revision_ref": str(fact.revision),
    }


async def pause(
    session,
    row,
    kind,
    question,
    settings,
    step=None,
    action=None,
    resource_ids=None,
    budget_ref=None,
    input_refs=None,
):
    from app.services.agent_limits import active_used

    if action == "export":
        question = "Continue through the existing human export workflow. The agent cannot export."
    elif action == "confidential_reveal":
        question = "Use the existing human confidential-value workflow. Keep revealed values outside this conversation."
    record_id = uuid4()
    refs = input_refs or []
    resources = [str(value) for value in (resource_ids or [])]
    if action == "review_cards" and not refs:
        refs = await card_review_refs(session, row, resources)
        resources = [ref["id"] for ref in refs]
    if kind == "budget" and budget_ref is None:
        budget_job_id = (
            step.child_job_id if step is not None and step.child_job_id else row.current_job_id
        )
        if budget_job_id is None:
            fail("agent_budget_dependency_unavailable", "The existing budget job is unavailable")
        budget_ref = await budget_fact_ref(session, row, f"job:{budget_job_id}:budget", settings)
        if not refs and step is not None:
            refs = step.input_refs
    sent = await redact_text(session, row, question, settings, limit=2000)
    timestamp = await now(session)
    record = AgentPause(
        id=record_id,
        org_id=row.org_id,
        session_id=row.id,
        step_id=step.id if step is not None else None,
        kind=kind,
        status="pending",
        question=sent,
        question_enc=seal(settings, record_id, row.org_id, row.id, {"question": question}),
        action=action,
        resource_ids=resources,
        budget_ref=budget_ref,
        input_refs=refs,
        input_sha256=digest(refs),
        created_at=timestamp,
    )
    row.active_seconds_used = active_used(row, timestamp)
    row.active_since, row.state, row.pause_id = None, "paused", record_id
    row.updated_at = timestamp
    row.revision += 1
    session.add(record)
    audit(
        session,
        session.info["actor"],
        "agent.pause.created",
        record.id,
        {"session_id": str(row.id), "kind": kind, "action": action},
    )
    return record


async def _provider(session):
    resolver = session.info.get("agent_resolve")
    llm = await resolver(session) if resolver is not None else session.info["agent_llm"]
    return with_reasoning(llm, None)[0]


async def _preview(session, actor, row, principal, message, settings, llm):
    from app.jobs.agent import decision_request
    from app.services.agent_tools import definitions

    tools = await definitions(principal_view(principal))
    blockers, quotes = [], []
    try:
        request = await decision_request(
            session,
            row,
            uuid4(),
            settings,
            actor=replace(actor, scopes=set(principal.scopes)),
            initial_message=message,
        )
        quotes = [reasoning_provider(llm).quote(request)]
    except ProviderFailure as error:
        blockers.append(error.code)
    attached = await budget_preflight.attach(
        session,
        {},
        command="agent start",
        task_id=row.task_id,
        input_hash=row.input_sha256,
        currency=row.limits["billing_currency"],
        settings=settings,
        quotes=quotes,
        planned_calls=1,
        max_charge=Decimal(row.limits["max_platform_charge"]),
    )
    preflight = attached["budget_preflight"]
    if preflight["admission_blocker"]:
        blockers.append(preflight["admission_blocker"])
    if quotes:
        quote = quotes[0]
        if quote.vendor_usd_upper_bound is None:
            blockers.append("billing_price_unavailable")
        elif quote.vendor_usd_upper_bound > Decimal(row.limits["max_vendor_usd"]):
            blockers.append("agent_vendor_cost_limit")
        if quote.currency != row.limits["billing_currency"]:
            blockers.append("billing_currency_mismatch")
    estimate = Cost.model_validate(
        {key: value for key, value in preflight["estimate"].items() if key in Cost.model_fields}
    )
    preview = AgentPreviewData(
        task_id=row.task_id,
        extraction_job_id=row.extraction_job_id,
        effective_scopes=principal.scopes,
        tools=tools,
        limits=AgentLimits.model_validate(row.limits),
        estimated_cost=estimate,
        estimate_basis="first_decision_upper_bound" if quotes else "unknown",
        blockers=list(dict.fromkeys(blockers)),
    )
    return Result(
        ok=True, command="agent start", data=preview.model_dump(mode="json"), cost=estimate
    )


async def resume_preflight(session, row, actor, settings):
    """An opaque budget answer cannot bypass the existing call admission facts."""
    from app.jobs.agent import BUDGET_STOPS, decision_request
    from app.services import drafts
    from app.services.agent_limits import enforce
    from app.services.agent_tools import generation_preflight, validate_call

    llm = await _provider(session)
    if digest(model_identity(llm)) != row.model_sha256:
        fail("agent_model_changed", "The session model changed; start a new session")
    last = await session.scalar(
        select(AgentStep)
        .where(AgentStep.session_id == row.id)
        .order_by(AgentStep.ordinal.desc())
        .limit(1)
    )
    call = None
    if last is not None:
        if last.kind == "tool" and (
            last.state == "planned" or (last.state == "failed" and last.error_code in BUDGET_STOPS)
        ):
            call = TypeAdapter(ToolCall).validate_python(
                unseal(settings, last.id, row.org_id, row.id, last.arguments_enc)
            )
            if last.child_job_id is not None and call.command in {"card generate", "draft"}:
                call = call.model_copy(
                    update={
                        "arguments": call.arguments.model_copy(
                            update={
                                "input": call.arguments.input.model_copy(update={"retry": True})
                            }
                        )
                    }
                )
        elif last.kind == "decision" and last.state == "completed":
            decision = AgentDecision.model_validate(
                unseal(settings, last.id, row.org_id, row.id, last.result_enc)
            )
            if isinstance(decision.action, ToolDecision):
                call = decision.action.call
        elif last.state == "waiting_job":
            # Reconciliation is a database read; its next charge is admitted by
            # the child worker if needed, rather than an unrelated model quote.
            return
    if call is not None and last is not None:
        if (
            last.state != "planned"
            and row.steps_used >= AgentLimits.model_validate(row.limits).max_steps
        ):
            fail("agent_step_limit", "No step remains for the next tool execution")
        scoped = replace(actor, step_id=last.id, invocation_id=last.invocation_id)
        call = await validate_call(session, scoped, row, last, call)
        if call.command == "card generate":
            await generation_preflight(
                session,
                scoped,
                row,
                call.arguments.input,
                storage=session.info["agent_storage"],
                llm=llm,
                settings=settings,
            )
        elif call.command == "draft":
            await drafts.submit_draft(
                session,
                scoped,
                row.task_id,
                call.arguments.input.model_copy(update={"dry_run": True, "retry": False}),
                session.info["agent_storage"],
            )
        return
    request = await decision_request(session, row, uuid4(), settings, actor=actor)
    quote = reasoning_provider(llm).quote(request)
    await enforce(session, row, settings, quote)
    attached = await budget_preflight.attach(
        session,
        {},
        command="agent resume",
        task_id=row.task_id,
        input_hash=row.input_sha256,
        currency=row.limits["billing_currency"],
        settings=settings,
        quotes=[quote],
        planned_calls=1,
        max_charge=Decimal(row.limits["max_platform_charge"]),
    )
    blocker = attached["budget_preflight"]["admission_blocker"]
    if blocker is not None:
        fail(blocker, "Current call admission still requires intervention")


async def start(session, identity, task_id, body, settings, queue):
    from app.jobs.agent import enqueue_controller
    from app.services.agent_tools import definitions, schema_digest

    task, _, _ = await task_access(
        session, identity, task_id, scope="agent:run", write=True, require_member=True
    )
    actor = await human_access(session, identity, "agent:run")
    request_hash = digest(body.model_dump(mode="json"))
    existing = await session.scalar(
        select(AgentSession).where(
            AgentSession.task_id == task_id,
            AgentSession.owner_user_id == actor.user_id,
            AgentSession.start_idempotency_key == body.idempotency_key,
        )
    )
    if existing is not None and not body.dry_run:
        return replay(
            settings,
            existing,
            existing.id,
            existing.start_receipt_enc,
            request_hash,
            existing.start_request_sha256,
        )
    extraction = await session.get(Job, body.extraction_job_id)
    if extraction is None or extraction.task_id != task.id or extraction.kind != "extract":
        raise not_found()
    if extraction.status != "succeeded":
        fail("agent_extraction_required", "A successful extraction is required")
    document = await session.get(Document, extraction.document_id)
    if document is None or document.task_id != task.id:
        raise not_found()
    if not task.model_redaction_enabled:
        fail("agent_redaction_required", "Agent outbound redaction must be enabled")
    timestamp = await now(session)
    if actor.session_expires_at is None or actor.session_expires_at <= timestamp:
        raise ServiceError("invalid_token", "A valid human session is required", 401, 4)
    member = await membership(session, actor.user_id, actor.org_id)
    initial = actor.scopes & ROLE_SCOPES[member.role]
    scopes = sorted(initial & set(body.requested_scopes) & AGENT_SCOPES)
    if not WORKFLOW_SCOPES <= set(scopes):
        raise ServiceError("forbidden", "The agent workflow permissions are required", 403, 4)
    principal = AgentPrincipal(
        id=uuid4(),
        org_id=actor.org_id,
        user_id=actor.user_id,
        membership_id=member.id,
        initial_grants=sorted(initial),
        scopes=scopes,
        created_at=timestamp,
        authority_expires_at=min(actor.session_expires_at, timestamp + timedelta(hours=8)),
    )
    llm = await _provider(session)
    entries = await confidential.task_entries(session, task.id)
    values = sorted(str(entry.value.id) for entry in entries.values() if entry.value is not None)
    model = model_identity(llm)
    row = AgentSession(
        id=uuid4(),
        org_id=actor.org_id,
        task_id=task.id,
        document_id=document.id,
        extraction_job_id=extraction.id,
        principal_id=principal.id,
        owner_user_id=actor.user_id,
        state="queued",
        revision=1,
        limits=body.limits.model_dump(mode="json"),
        model_snapshot={
            "model": model,
            "redaction_revision": task.model_redaction_revision,
            "redaction_rule_version": redaction.RULE_VERSION,
            "confidential_value_ids": values,
        },
        steps_used=0,
        active_seconds_used=0,
        vendor_calls_used=0,
        active_since=timestamp,
        tool_schema_sha256=schema_digest(await definitions(principal_view(principal))),
        model_sha256=digest(model),
        input_sha256=digest(
            {
                "task_id": str(task.id),
                "document_id": str(document.id),
                "extraction_job_id": str(extraction.id),
                "redaction_revision": task.model_redaction_revision,
                "confidential_value_ids": values,
            }
        ),
        start_idempotency_key=body.idempotency_key,
        start_request_sha256=request_hash,
        expires_at=timestamp + timedelta(seconds=body.limits.max_lifetime_seconds),
        created_at=timestamp,
        updated_at=timestamp,
    )
    if body.dry_run:
        return await _preview(session, actor, row, principal, body.message, settings, llm)
    session.add(principal)
    await session.flush([principal])
    session.add(row)
    await session.flush()
    actor = await bind_owner(session, actor, row)
    first = await add_message(session, row, "human", body.message, settings)
    await session.flush()
    controller = await enqueue_controller(session, row, actor, settings, queue)
    with session.no_autoflush:
        result = await mutation(
            session, row, "agent start", job_id=controller.id, message_id=first.id
        )
        row.start_receipt_enc = seal(settings, row.id, row.org_id, row.id, result)
    audit(session, actor, "agent.session.started", row.id, {"task_id": str(row.task_id)})
    await session.flush()
    return result


async def paginate(session, query, model, request):
    if request.cursor is not None:
        cursor = await session.scalar(query.where(model.id == request.cursor))
        if cursor is None:
            raise not_found()
        query = query.where(tuple_(model.created_at, model.id) > (cursor.created_at, cursor.id))
    rows = (
        await session.scalars(query.order_by(model.created_at, model.id).limit(request.limit + 1))
    ).all()
    return rows[: request.limit], rows[request.limit - 1].id if len(rows) > request.limit else None


async def list_sessions(session, identity, task_id, request):
    await task_access(session, identity, task_id, scope="agent:read")
    actor = await human_access(session, identity)
    if await session.get(Task, task_id) is None:
        raise not_found()
    rows, cursor = await paginate(
        session,
        select(AgentSession).where(
            AgentSession.task_id == task_id, AgentSession.owner_user_id == actor.user_id
        ),
        AgentSession,
        request,
    )
    views = [await session_view(session, row) for row in rows]
    audit(session, actor, "agent.session.read", task_id, {"command": "agent list"})
    return Result(
        ok=True,
        command="agent list",
        data=AgentPageData(next_cursor=cursor).model_dump(mode="json"),
        items=[view.model_dump(mode="json") for view in views],
    )


async def show(session, identity, session_id):
    actor = await human_access(session, identity)
    row = await owned(session, actor, session_id)
    await task_access(session, actor, row.task_id, scope="agent:read")
    actor = await bind_owner(session, actor, row)
    principal = await session.get(AgentPrincipal, row.principal_id)
    pending = await session.get(AgentPause, row.pause_id) if row.pause_id else None
    view = await session_view(session, row)
    data = AgentShowData(
        session=view, principal=principal_view(principal), pause=pause_view(pending)
    )
    audit(session, actor, "agent.session.read", row.id, {"command": "agent show"})
    last = await session.scalar(
        select(AgentStep)
        .where(AgentStep.session_id == row.id)
        .order_by(AgentStep.ordinal.desc())
        .limit(1)
    )
    warnings = (
        ["agent_retryable_failure"]
        if row.state == "failed" and last is not None and last.exit_code == 3
        else []
    )
    return Result(
        ok=row.state not in {"failed", "cancelled", "partial"},
        command="agent show",
        data=data.model_dump(mode="json"),
        cost=view.cost.cost,
        warnings=warnings,
    )


async def history(session, identity, session_id, request, settings, model, view, command):
    actor = await human_access(session, identity)
    row = await owned(session, actor, session_id)
    await task_access(session, actor, row.task_id, scope="agent:read")
    actor = await bind_owner(session, actor, row)
    rows, cursor = await paginate(
        session, select(model).where(model.session_id == row.id), model, request
    )
    items = []
    for record in rows:
        projected = view(record).model_dump(mode="json")
        if model is AgentMessage:
            # A newly registered confidential value also masks old public history.
            projected["content"] = await redact_text(session, row, record.content, settings)
        items.append(projected)
    audit(session, actor, "agent.session.read", row.id, {"command": command})
    return Result(
        ok=True,
        command=command,
        data=AgentPageData(next_cursor=cursor).model_dump(mode="json"),
        items=items,
    )


async def messages(session, identity, session_id, request, settings):
    return await history(
        session,
        identity,
        session_id,
        request,
        settings,
        AgentMessage,
        message_view,
        "agent messages",
    )


async def steps(session, identity, session_id, request, settings):
    return await history(
        session, identity, session_id, request, settings, AgentStep, step_view, "agent steps"
    )


async def message(session, identity, session_id, body, settings):
    actor = await human_access(session, identity, "agent:run")
    row = await owned(session, actor, session_id, lock=True)
    await task_access(
        session, actor, row.task_id, scope="agent:run", write=True, require_member=True
    )
    actor = await bind_owner(session, actor, row)
    request_hash = digest(body)
    existing = await session.scalar(
        select(AgentMessage).where(
            AgentMessage.session_id == row.id, AgentMessage.idempotency_key == body.idempotency_key
        )
    )
    if existing is not None:
        return replay(
            settings, existing, row.id, existing.receipt_enc, request_hash, existing.request_sha256
        )
    revision(row, body.expected_revision)
    if row.state != "paused":
        fail("agent_not_paused", "Messages can only be added to paused sessions")
    first = await add_message(session, row, "human", body.message, settings)
    with session.no_autoflush:
        first.idempotency_key, first.request_sha256 = body.idempotency_key, request_hash
        row.revision += 1
        row.updated_at = await now(session)
        pending = await session.get(AgentPause, row.pause_id)
        result = await mutation(session, row, "agent message", message_id=first.id, pending=pending)
        first.receipt_enc = seal(settings, first.id, first.org_id, row.id, result)
    audit(session, actor, "agent.message.added", first.id, {"session_id": str(row.id)})
    await session.flush()
    return result


async def cancel(session, identity, session_id, body, settings):
    from app.services.agent_limits import active_used

    actor = await human_access(session, identity, cancellation=True)
    row = await owned(session, actor, session_id, lock=True)
    actor = await bind_owner(session, actor, row)
    request_hash = digest(body)
    if row.cancel_idempotency_key == body.idempotency_key:
        return replay(
            settings, row, row.id, row.cancel_receipt_enc, request_hash, row.cancel_request_sha256
        )
    revision(row, body.expected_revision)
    if row.state in TERMINAL:
        fail("agent_terminal_session", "Terminal sessions cannot be cancelled again")
    links = (
        await session.scalars(
            select(AgentJobLink).where(
                AgentJobLink.session_id == row.id, AgentJobLink.owned.is_(True)
            )
        )
    ).all()
    timestamp = await now(session)
    for link in links:
        job = await session.scalar(select(Job).where(Job.id == link.job_id).with_for_update())
        if job.status in {"queued", "running"}:
            job.status, job.finished_at, job.lease_until = (
                "cancelled",
                timestamp,
                None,
            )
    pending = await session.get(AgentPause, row.pause_id) if row.pause_id else None
    if pending is not None and pending.status == "pending":
        pending.status = "cancelled"
    with session.no_autoflush:
        row.active_seconds_used = active_used(row, timestamp)
        row.active_since, row.state = None, "cancelled"
        row.revision += 1
        row.updated_at = timestamp
        row.current_run_id = None
        row.cancel_idempotency_key, row.cancel_request_sha256 = body.idempotency_key, request_hash
        principal = await session.get(AgentPrincipal, row.principal_id)
        principal.revoked_at = timestamp
        result = await mutation(session, row, "agent cancel", pending=pending)
        row.cancel_receipt_enc = seal(settings, row.id, row.org_id, row.id, result)
    audit(session, actor, "agent.session.cancelled", row.id, {"reason": body.reason})
    await session.flush()
    return result


async def resume(session, identity, session_id, body, settings, queue):
    from app.jobs.agent import enqueue_controller
    from app.services.agent_limits import enforce

    actor = await human_access(session, identity, "agent:run")
    row = await owned(session, actor, session_id, lock=True)
    await task_access(
        session, actor, row.task_id, scope="agent:run", write=True, require_member=True
    )
    actor = await bind_owner(session, actor, row)
    request_hash = digest(body)
    previous = await session.scalar(
        select(AgentPause).where(
            AgentPause.session_id == row.id,
            AgentPause.resume_idempotency_key == body.idempotency_key,
        )
    )
    if previous is not None:
        return replay(
            settings, previous, row.id, previous.receipt_enc, request_hash, previous.request_sha256
        )
    revision(row, body.expected_revision)
    if row.state != "paused" or row.pause_id != body.pause_id:
        fail("agent_not_paused", "The specified pending pause is not current")
    pending = await session.get(AgentPause, row.pause_id)
    if pending is None or pending.status != "pending":
        fail("agent_pause_conflict", "The specified pause is not pending")
    if pending.kind == "recovery":
        fail("agent_recovery_required", "An uncertain outcome must be reconciled before resuming")
    principal = await session.get(AgentPrincipal, row.principal_id)
    timestamp = await now(session)
    if principal.revoked_at is not None:
        fail("agent_authority_revoked", "Agent authority has been revoked")
    if actor.session_expires_at is None or actor.session_expires_at <= timestamp:
        raise ServiceError("invalid_token", "A valid human session is required", 401, 4)
    principal.scopes = sorted(
        set(principal.scopes) & set(principal.initial_grants) & actor.scopes & AGENT_SCOPES
    )
    principal.authority_expires_at = min(actor.session_expires_at, timestamp + timedelta(hours=8))
    if pending.kind == "budget":
        if body.budget_ref is None:
            fail(
                "agent_budget_reference_required", "Resolve the existing budget intervention first"
            )
        if pending.budget_ref is None:
            fail(
                "agent_budget_dependency_unavailable",
                "The saved budget intervention is unavailable",
            )
        current = await budget_fact_ref(session, row, pending.budget_ref["question_ref"], settings)
        if body.budget_ref.model_dump(mode="json") != current:
            fail("agent_budget_reference_stale", "Budget reference does not match current facts")
    elif body.budget_ref is not None:
        fail("agent_budget_reference_unexpected", "This pause has no budget intervention")
    try:
        admitted = await enforce(session, row, settings)
        await resume_preflight(session, row, admitted, settings)
    except ProviderFailure as error:
        raise ServiceError(
            error.code, "Session admission requirements are not satisfied", 409, 2
        ) from None
    # Enforce may install an agent actor for worker admission; resolution is human.
    await set_actor_context(session, actor)
    refresh = None
    if pending.action == "review_cards":
        current = await card_review_refs(session, row, pending.resource_ids)
        old = {ref["id"]: ref["sha256"] for ref in pending.input_refs if ref["kind"] == "card"}
        fresh = {ref["id"]: ref["sha256"] for ref in current}
        if old != fresh:
            refresh = current
    # Compute the whole receipt before resolving an immutable pause. Enqueue may
    # flush jobs; its creation must occur before assigning the resolution trio.
    if refresh is None:
        controller = await enqueue_controller(session, row, actor, settings, queue)
        job_id = controller.id
    else:
        job_id = None
    with session.no_autoflush:
        pending.status, pending.resolved_by, pending.resolved_at = (
            "resolved",
            actor.user_id,
            timestamp,
        )
        pending.resume_idempotency_key, pending.request_sha256 = body.idempotency_key, request_hash
        row.revision += 1
        row.updated_at = timestamp
        if refresh is not None:
            replacement = await pause(
                session,
                row,
                pending.kind,
                pending.question,
                settings,
                action=pending.action,
                resource_ids=pending.resource_ids,
                input_refs=refresh,
            )
        else:
            replacement = None
            row.pause_id, row.active_since, row.state = None, timestamp, "queued"
        result = await mutation(session, row, "agent resume", job_id=job_id, pending=replacement)
        pending.receipt_enc = seal(settings, pending.id, pending.org_id, row.id, result)
    # The unique pending index and resolution guard require one complete UPDATE
    # before the replacement INSERT; no incomplete receipt ever reaches SQL.
    await session.flush([pending])
    audit(
        session,
        actor,
        "agent.pause.resolved",
        pending.id,
        {
            "session_id": str(row.id),
            "replacement_pause_id": str(replacement.id) if replacement else None,
        },
    )
    audit(
        session,
        actor,
        "agent.session.resumed" if refresh is None else "agent.pause.refreshed",
        row.id,
        {"pause_id": str(pending.id)},
    )
    await session.flush()
    return result
