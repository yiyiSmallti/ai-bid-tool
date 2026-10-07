"""Tenant provider revisions, safe views and accounted connectivity tests."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select, text

from app.core.errors import ServiceError, not_found
from app.core.provider_secrets import ProviderSecrets
from app.jobs.execution import JobExecution, job_cost
from app.models.entities import Job, PlatformModel, UsageRecord, User
from app.models.provider_configs import ProviderConfig
from app.providers.balance import provider_balance, supports_balance
from app.providers.configured import current_config, model_identity
from app.providers.llm import HTTPExtractor, billable, with_reasoning
from app.schemas.contracts import Extraction
from app.schemas.provider_contracts import ProviderConfigSet, ProviderConfigView, ProviderTest
from app.services import billing
from app.services.auth import Identity, membership, set_actor_context
from app.services.versioned import audit


async def require_access(session, actor: Identity, *, write=False):
    actor.require("provider:write" if write else "provider:read")
    member = await membership(session, actor.user_id, actor.org_id)
    if not await session.scalar(select(User.active).where(User.id == actor.user_id)):
        raise ServiceError("forbidden", "Provider access requires an active user", 403, 4)
    if write and (
        actor.actor_kind != "session" or actor.token_id is not None or member.role != "admin"
    ):
        raise ServiceError(
            "forbidden", "Only a human organization admin can configure or test providers", 403, 4
        )


def catalog_view(row: PlatformModel) -> dict:
    # Credentials, endpoint and wholesale prices are operator-only catalog metadata.
    return {
        "id": row.id,
        "model": row.model,
        "provider": row.provider,
        "sale_input_per_mtok": float(row.sale_input_per_mtok),
        "sale_output_per_mtok": float(row.sale_output_per_mtok),
        "default": row.is_default,
        "reasoning": [
            {"name": level["name"], "label": level.get("label")} for level in row.reasoning
        ],
        "default_reasoning": row.default_reasoning,
    }


def config_view(row: ProviderConfig) -> dict:
    return ProviderConfigView(
        id=row.id,
        org_id=row.org_id,
        revision=row.revision,
        capability=row.capability,
        source=row.source,
        platform_model_id=row.platform_model_id,
        key_last4=row.key_last4,
        updated_by=row.updated_by,
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
                "input_usd_per_mtok",
                "output_usd_per_mtok",
            )
        },
    ).model_dump(mode="json")


async def set_config(session, actor, body: ProviderConfigSet, settings):
    await require_access(session, actor, write=True)
    await set_actor_context(session, actor)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"provider:{actor.org_id}"},
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
    encrypted, last4 = None, None
    if body.source == "org":
        crypto = ProviderSecrets(settings)
        key = body.api_key
        if key is None:
            if (
                old is None
                or old.source != "org"
                or old.data["provider"] != body.provider
                or old.data["base_url"] != body.base_url
                or old.encrypted_key is None
            ):
                raise ServiceError(
                    "provider_key_required", "Supply a key for a new provider or endpoint", 400, 2
                )
            key = crypto.decrypt(old.encrypted_key, actor.org_id, old.id)
        encrypted = crypto.encrypt(key.get_secret_value(), actor.org_id, config_id)
        last4 = key.get_secret_value()[-4:]
        data = body.model_dump(
            mode="json",
            exclude={"capability", "source", "platform_model_id", "expected_revision", "api_key"},
        )
    else:
        entry = await session.scalar(
            select(PlatformModel).where(
                PlatformModel.id == body.platform_model_id,
                PlatformModel.enabled.is_(True),
                PlatformModel.capability == body.capability,
            )
        )
        if entry is None:
            raise not_found()
        data = {
            "provider": entry.provider,
            "model": entry.model,
            # The tenant receives neither operator endpoints nor wholesale prices.
            "base_url": None,
            "json_mode": settings.llm_json_mode,
            "reasoning": entry.reasoning,
            "default_reasoning": entry.default_reasoning,
            "input_usd_per_mtok": None,
            "output_usd_per_mtok": None,
            # Save published terms with this immutable choice; later catalog
            # changes must not replace an old revision's displayed identity.
            "catalog_revision": entry.revision,
            "sale_input_per_mtok": float(entry.sale_input_per_mtok),
            "sale_output_per_mtok": float(entry.sale_output_per_mtok),
        }
    config = ProviderConfig(
        id=config_id,
        org_id=actor.org_id,
        capability=body.capability,
        revision=old.revision + 1 if old else 1,
        source=body.source,
        platform_model_id=body.platform_model_id,
        data=data,
        encrypted_key=encrypted,
        key_last4=last4,
        updated_by=actor.user_id,
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
            "source": config.source,
            "previous_config_id": str(old.id) if old else None,
            "platform_model_id": config.platform_model_id,
        },
    )
    return config_view(config)


async def monthly_usage(session, config_id: UUID | None = None) -> dict:
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    statement = select(
        func.count(UsageRecord.id),
        func.coalesce(func.sum(UsageRecord.input_tokens), 0),
        func.coalesce(func.sum(UsageRecord.output_tokens), 0),
        func.sum(UsageRecord.usd),
        func.count(UsageRecord.usd),
        func.coalesce(func.sum(UsageRecord.charge), 0),
    ).where(UsageRecord.created_at >= start, UsageRecord.ocr_pages == 0)
    if config_id is not None:
        statement = statement.where(UsageRecord.provider_config_id == config_id)
    count, inputs, outputs, cost, priced, charge = (await session.execute(statement)).one()
    return {
        "month": start.strftime("%Y-%m"),
        "timezone": "UTC",
        "calls": count,
        "input_tokens": inputs,
        "output_tokens": outputs,
        "tokens": inputs + outputs,
        "usd": float(cost or 0) if count == priced else None,
        "unpriced_calls": count - priced,
        "charge": float(charge),
    }


async def balance_view(row, settings, transport=None):
    if (
        row is None
        or row.source != "org"
        or not supports_balance(row.data["provider"], row.data["base_url"])
    ):
        return {"status": "unsupported", "message": "该服务商不提供余量查询"}
    if row.encrypted_key is None:
        return {"status": "unavailable", "message": "暂时无法查询"}
    try:
        key = ProviderSecrets(settings).decrypt(row.encrypted_key, row.org_id, row.id)
    except ServiceError as exc:
        if exc.code != "provider_secrets_unavailable":
            raise
        return {"status": "unavailable", "message": "暂时无法查询"}
    return await provider_balance(row.data["provider"], row.data["base_url"], key, transport)


async def list_configs(session, actor, settings, *, history=False):
    await require_access(session, actor)
    statement = select(ProviderConfig).order_by(ProviderConfig.revision.desc())
    if not history:
        statement = statement.limit(1)
    rows = (await session.scalars(statement)).all()
    entries = (
        await session.scalars(
            select(PlatformModel)
            .where(PlatformModel.capability == "llm_extract", PlatformModel.enabled.is_(True))
            .order_by(PlatformModel.id)
        )
    ).all()
    items = []
    for row in rows:
        items.append({**config_view(row), "month_usage": await monthly_usage(session, row.id)})
    default = next((row for row in entries if row.is_default), None)
    return (
        {
            "catalog": [catalog_view(row) for row in entries],
            "currency": settings.billing_currency,
            "effective_source": rows[0].source
            if rows
            else "platform"
            if default
            else "unconfigured",
            "month_usage": await monthly_usage(session),
        },
        items,
        rows[0] if rows else None,
    )


async def submit_test(session, actor, body: ProviderTest, llm, settings, *, probe_levels=None):
    await require_access(session, actor, write=True)
    await set_actor_context(session, actor)
    llm, reasoning, warnings = with_reasoning(llm, body.reasoning)
    if billable(llm):
        await billing.require_funds(session, settings.billing_currency)
    job = Job(
        id=uuid4(),
        org_id=actor.org_id,
        kind="provider_test",
        cache_key=uuid4().hex,
        provider_config_id=getattr(llm, "provider_config_id", None),
        provider_identity=model_identity(llm),
        reasoning=reasoning,
        result={
            "submission": {
                "actor_user_id": str(actor.user_id),
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
        actor = Identity(
            UUID(job.result["submission"]["actor_user_id"]),
            execution.org_id,
            {"provider:write"},
            "admin",
        )
        await require_access(session, actor, write=True)

    execution.before_admit = authorized
    async with execution.db.transaction(execution.org_id) as session:
        job = await execution.owned_job(session)
        llm, _, _ = with_reasoning(llm, job.reasoning)
        await authorized(session)
    # This is the contract's synthetic connectivity page, never a tenant tender record.
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
    async with execution.db.transaction(execution.org_id) as session:
        job = await execution.owned_job(session)
        job.status, job.error, job.finished_at = "succeeded", None, datetime.now(UTC)
        job.result = {
            **job.result,
            "usage": usage.model_dump(mode="json"),
            "levels": reports,
            "cost": await job_cost(session, job.id),
        }


async def preview_test(session, actor, body, llm, settings):
    from app.services import budget_preflight
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
    data = await budget_preflight.attach(
        session,
        {"dry_run": True, "reasoning": reasoning},
        command="provider test",
        task_id=None,
        input_hash=digest({"model": model_identity(llm), "reasoning": reasoning}),
        currency=settings.billing_currency,
        settings=settings,
        quote_sources=quotes,
        planned_calls=1,
    )
    return data, warnings
