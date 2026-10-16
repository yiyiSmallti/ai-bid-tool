"""Local provider revisions, safe views and accounted connectivity tests."""

from datetime import UTC, datetime
from urllib.parse import urlsplit
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
    acting_member,
    current_config,
    default_slot,
    hidden_slots,
    latest_revisions,
    live_slots,
    member_default,
    model_identity,
    slot_owner,
)
from app.providers.llm import HTTPExtractor, with_reasoning
from app.schemas.contracts import Extraction
from app.schemas.provider_contracts import ProviderConfigSet, ProviderConfigView, ProviderTest
from app.services.auth import Identity, set_actor_context
from app.services.versioned import audit


async def require_access(session, actor: Identity, *, write=False):
    actor.require("provider:write" if write else "provider:read")


def company_writer(actor: Identity) -> None:
    """On a gateway only an administrator changes or tests a company model."""
    if actor.member_id is not None and actor.member_role != "admin":
        raise ServiceError("forbidden", "Only an administrator can change company models", 403, 4)


# Per owner: the company's models, or one member's personal models.
MAX_MODELS = 20
DEFAULT_ENDPOINTS = {"anthropic": "https://api.anthropic.com", "openai": "https://api.openai.com"}


def config_view(
    row: ProviderConfig,
    default: UUID | None = None,
    owner: UUID | None = None,
    mine: UUID | None = None,
) -> dict:
    return ProviderConfigView(
        id=row.id,
        revision=row.revision,
        capability=row.capability,
        slot=row.slot,
        name=row.data.get("name"),
        owner="personal" if owner is not None else "company",
        is_default=row.slot == default,
        is_member_default=row.slot == (mine or default),
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


async def owners(session, slots) -> dict[UUID, UUID | None]:
    """Each model's owning member; a company model, or one without a slot row, has none."""
    rows = await session.execute(
        select(ProviderSlot.slot, ProviderSlot.owner_member_id).where(
            ProviderSlot.slot.in_(list(slots))
        )
    )
    return dict(rows.tuples().all())


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


def personal_endpoint(body: ProviderConfigSet, settings) -> None:
    """Tender text leaves through a personal model only to an allowed endpoint."""
    url = urlsplit(body.base_url or DEFAULT_ENDPOINTS[body.provider])
    if f"https://{url.netloc.lower()}" not in settings.personal_model_endpoints:
        raise ServiceError(
            "provider_endpoint_not_allowed",
            "Personal models may use only the endpoints the gateway allows",
            400,
            2,
        )


async def set_config(session, actor, body: ProviderConfigSet, settings):
    await lock(session, actor)
    if body.owner == "personal":
        owner = actor.member_id
        if owner is None or actor.actor_kind != "session":
            raise ServiceError(
                "provider_owner_invalid", "Personal models belong to a signed-in member", 400, 2
            )
        personal_endpoint(body, settings)
    else:
        owner = None
        company_writer(actor)
    default = await default_slot(session)
    models = await live_models(session)
    owned = await owners(session, [row.slot for row in models])
    models = [row for row in models if owned.get(row.slot) == owner]
    if body.new:
        old = None
        if len(models) >= MAX_MODELS:
            raise ServiceError(
                "provider_model_limit", f"At most {MAX_MODELS} models can be saved", 409, 2
            )
    elif body.slot is not None:
        old = await require_model(session, body.slot)
        if await slot_owner(session, old.slot) != owner:
            raise ServiceError("provider_owner_mismatch", "A saved model keeps its owner", 409, 2)
    elif owner is None:
        old = await current_config(session, default) if default is not None else None
    else:
        raise ServiceError(
            "provider_model_required",
            "Name the personal model to revise, or save a new one",
            400,
            2,
        )
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
        # A key is reused only for the same owner, provider and endpoint: this
        # model's previous revision first, else another of the owner's models there.
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
        mode="json",
        exclude={"capability", "expected_revision", "api_key", "slot", "new", "owner"},
    )
    previous = await session.scalar(
        select(func.max(ProviderConfig.revision)).where(
            ProviderConfig.capability == body.capability
        )
    )
    slot = old.slot if old else uuid4()
    await session.execute(
        insert(ProviderSlot)
        .values(slot=slot, owner_member_id=owner)
        .on_conflict_do_nothing(index_elements=["slot"])
    )
    if owner is None and (default is None or default == slot):
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
            "owner": "personal" if owner else "company",
            "previous_config_id": str(old.id) if old else None,
        },
    )
    return config_view(config, default, owner, await member_default(session, owner))


async def set_default(session, actor, slot: UUID) -> dict:
    await lock(session, actor)
    company_writer(actor)
    config = await require_model(session, slot)
    if await slot_owner(session, slot) is not None:
        raise ServiceError(
            "provider_default_personal",
            "Only a company model can be the company default; set your own default model instead",
            409,
            2,
        )
    await mark_default(session, slot)
    audit(session, actor, "provider.default", config.id, {"slot": str(slot)})
    return config_view(config, slot)


async def remove_model(session, actor, slot: UUID) -> dict:
    """Removes a model from choice; jobs already pinned to its revisions still resolve them."""
    await lock(session, actor)
    config = await require_model(session, slot)
    owner = await slot_owner(session, slot)
    if owner is None:
        company_writer(actor)
    default = await default_slot(session)
    await session.execute(
        insert(ProviderSlot)
        .values(slot=slot, removed_at=func.now(), owner_member_id=owner)
        .on_conflict_do_update(
            index_elements=["slot"], set_={"removed_at": func.now(), "is_default": False}
        )
    )
    if default == slot:
        # The most recently saved remaining company model becomes the default.
        remaining = await live_models(session)
        owned = await owners(session, [row.slot for row in remaining])
        company = [row for row in remaining if owned.get(row.slot) is None]
        await mark_default(session, company[0].slot if company else None)
    audit(session, actor, "provider.remove", config.id, {"slot": str(slot)})
    default = await default_slot(session)
    return {"slot": str(slot), "removed": True, "default": str(default) if default else None}


async def monthly_usage(session, config_id: UUID | None = None, member: UUID | None = None) -> dict:
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    statement = select(
        func.count(UsageRecord.id),
        func.coalesce(func.sum(UsageRecord.input_tokens), 0),
        func.coalesce(func.sum(UsageRecord.output_tokens), 0),
    ).where(UsageRecord.created_at >= start, UsageRecord.ocr_pages == 0)
    if config_id is not None:
        statement = statement.where(UsageRecord.provider_config_id == config_id)
    if member is not None:
        statement = statement.where(UsageRecord.member_id == member)
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
    member = acting_member(session)
    mine = await member_default(session, member)
    if history:
        rows = list(
            (
                await session.scalars(
                    select(ProviderConfig)
                    .where(ProviderConfig.slot.not_in(hidden_slots(member)))
                    .order_by(ProviderConfig.revision.desc())
                )
            ).all()
        )
    else:
        # Every saved model's current revision, the default model first.
        rows = sorted(await live_models(session), key=lambda row: row.slot != default)
    owned = await owners(session, {row.slot for row in rows})
    # A member reads only their own usage; an administrator reads the company's.
    reader = member if actor.member_role not in {None, "admin"} else None
    items = []
    for row in rows:
        items.append(
            {
                **config_view(row, default, owned.get(row.slot), mine),
                "month_usage": await monthly_usage(session, row.id, reader),
            }
        )
    return (
        {
            "configured": (mine or default) is not None,
            "month_usage": await monthly_usage(session, member=reader),
        },
        items,
        rows[0] if rows else None,
    )


async def require_tester(session, actor, llm) -> None:
    """A company model's test spends company tokens, so it is an administrator's."""
    config_id = getattr(llm, "provider_config_id", None)
    config = await session.get(ProviderConfig, config_id) if config_id is not None else None
    if config is None or await slot_owner(session, config.slot) is None:
        company_writer(actor)


async def submit_test(session, actor, body: ProviderTest, llm, settings, *, probe_levels=None):
    await require_access(session, actor, write=True)
    await require_tester(session, actor, llm)
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
    await require_tester(session, actor, llm)
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
