"""Local provider revisions, safe views and accounted connectivity tests."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.core.errors import ServiceError
from app.core.provider_secrets import ProviderSecrets
from app.jobs.execution import JobExecution, job_cost
from app.models.entities import Job, UsageRecord
from app.models.provider_configs import ProviderConfig, ProviderSlot
from app.providers.configured import (
    current_config,
    default_slot,
    latest_revisions,
    live_slots,
    model_identity,
)
from app.providers.llm import HTTPExtractor, with_reasoning
from app.schemas.contracts import Extraction
from app.schemas.provider_contracts import ProviderConfigSet, ProviderConfigView, ProviderTest
from app.services.auth import Identity, set_actor_context
from app.services.versioned import audit


async def require_access(session, actor: Identity, *, write=False):
    actor.require("provider:write" if write else "provider:read")


MAX_MODELS = 20


def config_view(row: ProviderConfig, default: UUID | None = None) -> dict:
    return ProviderConfigView(
        id=row.id,
        revision=row.revision,
        capability=row.capability,
        slot=row.slot,
        name=row.data.get("name"),
        is_default=row.slot == default,
        key_last4=row.key_last4,
        updated_at=row.created_at,
        **{
            field: row.data[field]
            for field in (
                "provider",
                "model",
                "base_url",
                "json_mode",
                "reasoning",
                "default_reasoning",
            )
        },
    ).model_dump(mode="json")


async def lock(session, actor):
    await require_access(session, actor, write=True)
    await set_actor_context(session, actor)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": "provider:llm_extract"},
    )


async def live_models(session) -> list[ProviderConfig]:
    """The latest revision of every model that was not removed, newest first."""
    slots, _ = await live_slots(session)
    if not slots:
        return []
    ids = (await session.scalars(latest_revisions([ProviderConfig.id], slots))).all()
    rows = (await session.scalars(select(ProviderConfig).where(ProviderConfig.id.in_(ids)))).all()
    return sorted(rows, key=lambda row: -row.revision)


async def mark_default(session, slot: UUID | None) -> None:
    await session.execute(
        update(ProviderSlot).where(ProviderSlot.is_default).values(is_default=False)
    )
    if slot is not None:
        await session.execute(
            insert(ProviderSlot)
            .values(slot=slot, is_default=True)
            .on_conflict_do_update(index_elements=["slot"], set_={"is_default": True})
        )


async def require_model(session, slot: UUID) -> ProviderConfig:
    config = await current_config(session, slot)
    if config is None:
        raise ServiceError(
            "provider_model_unavailable", "The model is not saved or was removed", 404, 2
        )
    return config


async def set_config(session, actor, body: ProviderConfigSet, settings):
    await lock(session, actor)
    default = await default_slot(session)
    models = await live_models(session)
    if body.new:
        old = None
        if len(models) >= MAX_MODELS:
            raise ServiceError(
                "provider_model_limit", f"At most {MAX_MODELS} models can be saved", 409, 2
            )
    elif body.slot is not None:
        old = await require_model(session, body.slot)
    else:
        old = await current_config(session)
    if body.expected_revision != (old.revision if old else None):
        raise ServiceError(
            "revision_conflict",
            "Provider revision changed; read the current configuration before updating",
            409,
            2,
        )
    config_id = uuid4()
    crypto = ProviderSecrets(settings)
    key = body.api_key
    if key is None:
        # A key is reused only for the same provider and endpoint: this model's
        # previous revision first, else another saved model on that endpoint.
        same = [
            row
            for row in ([old] if old else []) + models
            if row.data["provider"] == body.provider
            and row.data["base_url"] == body.base_url
            and row.encrypted_key is not None
        ]
        if not same:
            raise ServiceError(
                "provider_key_required", "Supply a key for a new provider or endpoint", 400, 2
            )
        source = same[0]
        assert source.encrypted_key is not None
        key = crypto.decrypt(source.encrypted_key, source.id)
    encrypted = crypto.encrypt(key.get_secret_value(), config_id)
    last4 = key.get_secret_value()[-4:]
    data = body.model_dump(
        mode="json", exclude={"capability", "expected_revision", "api_key", "slot", "new"}
    )
    previous = await session.scalar(
        select(func.max(ProviderConfig.revision)).where(
            ProviderConfig.capability == body.capability
        )
    )
    slot = old.slot if old else uuid4()
    await session.execute(
        insert(ProviderSlot).values(slot=slot).on_conflict_do_nothing(index_elements=["slot"])
    )
    if default is None or default == slot:
        default = slot
        await mark_default(session, slot)
    config = ProviderConfig(
        id=config_id,
        capability=body.capability,
        slot=slot,
        # Revision numbers are one sequence across models, as the revision gate requires.
        revision=(previous or 0) + 1,
        data=data,
        encrypted_key=encrypted,
        key_last4=last4,
    )
    session.add(config)
    await session.flush()
    audit(
        session,
        actor,
        "provider.set",
        config.id,
        {
            "revision": config.revision,
            "slot": str(slot),
            "previous_config_id": str(old.id) if old else None,
        },
    )
    return config_view(config, default)


async def set_default(session, actor, slot: UUID) -> dict:
    await lock(session, actor)
    config = await require_model(session, slot)
    await mark_default(session, slot)
    audit(session, actor, "provider.default", config.id, {"slot": str(slot)})
    return config_view(config, slot)


async def remove_model(session, actor, slot: UUID) -> dict:
    """Removes a model from choice; jobs already pinned to its revisions still resolve them."""
    await lock(session, actor)
    config = await require_model(session, slot)
    default = await default_slot(session)
    await session.execute(
        insert(ProviderSlot)
        .values(slot=slot, removed_at=func.now())
        .on_conflict_do_update(
            index_elements=["slot"], set_={"removed_at": func.now(), "is_default": False}
        )
    )
    if default == slot:
        # The most recently saved remaining model becomes the default.
        remaining = await live_models(session)
        await mark_default(session, remaining[0].slot if remaining else None)
    audit(session, actor, "provider.remove", config.id, {"slot": str(slot)})
    default = await default_slot(session)
    return {"slot": str(slot), "removed": True, "default": str(default) if default else None}


async def monthly_usage(session, config_id: UUID | None = None) -> dict:
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    statement = select(
        func.count(UsageRecord.id),
        func.coalesce(func.sum(UsageRecord.input_tokens), 0),
        func.coalesce(func.sum(UsageRecord.output_tokens), 0),
    ).where(UsageRecord.created_at >= start, UsageRecord.ocr_pages == 0)
    if config_id is not None:
        statement = statement.where(UsageRecord.provider_config_id == config_id)
    count, inputs, outputs = (await session.execute(statement)).one()
    return {
        "month": start.strftime("%Y-%m"),
        "timezone": "UTC",
        "calls": count,
        "input_tokens": inputs,
        "output_tokens": outputs,
        "tokens": inputs + outputs,
    }


async def list_configs(session, actor, settings, *, history=False):
    await require_access(session, actor)
    default = await default_slot(session)
    if history:
        rows = list(
            (
                await session.scalars(
                    select(ProviderConfig).order_by(ProviderConfig.revision.desc())
                )
            ).all()
        )
    else:
        # Every saved model's current revision, the default model first.
        rows = sorted(await live_models(session), key=lambda row: row.slot != default)
    items = []
    for row in rows:
        items.append(
            {**config_view(row, default), "month_usage": await monthly_usage(session, row.id)}
        )
    return (
        {
            "configured": default is not None,
            "month_usage": await monthly_usage(session),
        },
        items,
        rows[0] if rows else None,
    )


async def submit_test(session, actor, body: ProviderTest, llm, settings, *, probe_levels=None):
    await require_access(session, actor, write=True)
    await set_actor_context(session, actor)
    llm, reasoning, warnings = with_reasoning(llm, body.reasoning)
    job = Job(
        id=uuid4(),
        kind="provider_test",
        cache_key=uuid4().hex,
        actor_kind=actor.actor_kind,
        actor_token_id=actor.token_id,
        provider_config_id=getattr(llm, "provider_config_id", None),
        provider_identity=model_identity(llm),
        reasoning=reasoning,
        result={
            "submission": {
                **({"probe_levels": probe_levels} if probe_levels is not None else {}),
            },
            "warnings": warnings,
        },
    )
    session.add(job)
    await session.flush()
    audit(
        session,
        actor,
        "provider.test",
        job.id,
        {
            "provider_config_id": str(job.provider_config_id) if job.provider_config_id else None,
            "model": job.provider_identity,
            "reasoning": reasoning,
        },
    )
    return job


async def execute_test(execution: JobExecution, llm):
    async def authorized(session):
        job = await session.get(Job, execution.job_id)
        assert job is not None
        if job.actor_kind != "session" or job.actor_token_id is not None:
            raise ServiceError("forbidden", "A human session is required", 403, 4)

    execution.before_admit = authorized
    async with execution.db.transaction() as session:
        job = await execution.owned_job(session)
        llm, _, _ = with_reasoning(llm, job.reasoning)
        await authorized(session)
    # This is the contract's synthetic connectivity page, never a local tender record.
    probe = [
        {
            "id": UUID(int=1),
            "document_id": UUID(int=2),
            "page": 1,
            "text": "Connectivity test only. The delivery package must include a user guide.",
        }
    ]
    if not isinstance(llm, HTTPExtractor):
        await llm.extract(probe, Extraction.model_json_schema())
        raise ServiceError(
            "provider_unavailable", "Provider cannot run an accounted connection test", 503, 4
        )
    levels = job.result["submission"].get("probe_levels", [job.reasoning])
    execution.plan(len(levels))
    reports = []
    async with httpx.AsyncClient(
        transport=llm.transport, timeout=llm.settings.llm_timeout_seconds, follow_redirects=False
    ) as client:
        for name in levels:
            adapter = llm.at_reasoning(name) if name else llm
            _, usage = await adapter.call(client, probe)
            reports.append(
                {"reasoning": name, "passed": True, "usage": usage.model_dump(mode="json")}
            )
    async with execution.db.transaction() as session:
        job = await execution.owned_job(session)
        job.status, job.error, job.finished_at = "succeeded", None, datetime.now(UTC)
        job.result = {
            **job.result,
            "usage": usage.model_dump(mode="json"),
            "levels": reports,
            "cost": await job_cost(session, job.id),
        }


async def preview_test(session, actor, body, llm, settings):
    from app.services import preflight as preflight_service
    from app.services.drafts import digest

    await require_access(session, actor, write=True)
    llm, reasoning, warnings = with_reasoning(llm, body.reasoning)
    probe = [
        {
            "id": UUID(int=1),
            "document_id": UUID(int=2),
            "page": 1,
            "text": "Connectivity test only. The delivery package must include a user guide.",
        }
    ]
    quotes = (
        [lambda: llm.quote(llm.extraction_request(probe))] if isinstance(llm, HTTPExtractor) else []
    )
    data = await preflight_service.attach(
        session,
        {"dry_run": True, "reasoning": reasoning},
        command="provider test",
        task_id=None,
        input_hash=digest({"model": model_identity(llm), "reasoning": reasoning}),
        settings=settings,
        quote_sources=quotes,
        planned_calls=1,
    )
    return data, warnings
