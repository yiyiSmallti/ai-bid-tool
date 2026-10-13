"""Local provider revisions, safe views and accounted connectivity tests."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select, text

from app.core.errors import ServiceError
from app.core.provider_secrets import ProviderSecrets
from app.jobs.execution import JobExecution, job_cost
from app.models.entities import Job, UsageRecord
from app.models.provider_configs import ProviderConfig
from app.providers.configured import current_config, model_identity
from app.providers.llm import HTTPExtractor, with_reasoning
from app.schemas.contracts import Extraction
from app.schemas.provider_contracts import ProviderConfigSet, ProviderConfigView, ProviderTest
from app.services.auth import Identity, set_actor_context
from app.services.versioned import audit


async def require_access(session, actor: Identity, *, write=False):
    actor.require("provider:write" if write else "provider:read")


def config_view(row: ProviderConfig) -> dict:
    return ProviderConfigView(
        id=row.id,
        revision=row.revision,
        capability=row.capability,
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


async def set_config(session, actor, body: ProviderConfigSet, settings):
    await require_access(session, actor, write=True)
    await set_actor_context(session, actor)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": "provider:llm_extract"},
    )
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
        if (
            old is None
            or old.data["provider"] != body.provider
            or old.data["base_url"] != body.base_url
            or old.encrypted_key is None
        ):
            raise ServiceError(
                "provider_key_required", "Supply a key for a new provider or endpoint", 400, 2
            )
        key = crypto.decrypt(old.encrypted_key, old.id)
    encrypted = crypto.encrypt(key.get_secret_value(), config_id)
    last4 = key.get_secret_value()[-4:]
    data = body.model_dump(mode="json", exclude={"capability", "expected_revision", "api_key"})
    config = ProviderConfig(
        id=config_id,
        capability=body.capability,
        revision=old.revision + 1 if old else 1,
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
            "previous_config_id": str(old.id) if old else None,
        },
    )
    return config_view(config)


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
    statement = select(ProviderConfig).order_by(ProviderConfig.revision.desc())
    if not history:
        statement = statement.limit(1)
    rows = (await session.scalars(statement)).all()
    items = []
    for row in rows:
        items.append({**config_view(row), "month_usage": await monthly_usage(session, row.id)})
    return (
        {
            "configured": bool(rows),
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
