"""Resolve encrypted local provider revisions and immutable job model identities."""

from uuid import UUID

from sqlalchemy import select, union_all

from app.core.errors import ServiceError
from app.core.provider_secrets import ProviderSecrets
from app.models.entities import Job
from app.models.provider_configs import ProviderConfig, ProviderSlot
from app.providers.base import ProviderFailure
from app.providers.disabled import DisabledLLM
from app.providers.llm import ADAPTER_VERSION, AnthropicExtractor, OpenAICompatibleExtractor


def model_identity(llm) -> dict:
    config_id = getattr(llm, "provider_config_id", None)
    return {
        "provider": llm.name,
        "model": llm.model,
        "adapter_version": llm.version,
        "model_revision": getattr(llm, "model_revision", None),
        **({"provider_config_id": str(config_id)} if config_id is not None else {}),
    }


def removed_slots():
    return select(ProviderSlot.slot).where(ProviderSlot.removed_at.is_not(None))


async def default_slot(session) -> UUID | None:
    marked = await session.scalar(
        select(ProviderSlot.slot).where(
            ProviderSlot.capability == "llm_extract", ProviderSlot.is_default
        )
    )
    if marked is not None:
        return marked
    # Without a marked default, the most recently saved model that was not removed.
    return await session.scalar(
        select(ProviderConfig.slot)
        .where(
            ProviderConfig.capability == "llm_extract",
            ProviderConfig.slot.not_in(removed_slots()),
        )
        .order_by(ProviderConfig.revision.desc())
        .limit(1)
    )


async def live_slots(session) -> tuple[list[UUID], UUID | None]:
    """Saved models that were not removed, and the default among them."""
    rows = (
        await session.execute(
            select(ProviderSlot.slot, ProviderSlot.is_default).where(
                ProviderSlot.capability == "llm_extract", ProviderSlot.removed_at.is_(None)
            )
        )
    ).all()
    default = next((row.slot for row in rows if row.is_default), None)
    if default is None:
        default = await default_slot(session)
    slots = [row.slot for row in rows]
    if default is not None and default not in slots:
        slots.append(default)
    return slots, default


def latest_revisions(columns, slots: list[UUID]):
    """One indexed LIMIT 1 lookup per model, so reads stay bounded by the model count."""
    return union_all(
        *(
            select(*columns)
            .where(ProviderConfig.capability == "llm_extract", ProviderConfig.slot == slot)
            .order_by(ProviderConfig.revision.desc())
            .limit(1)
            for slot in slots
        )
    )


async def current_config(session, slot: UUID | None = None):
    """The latest revision of `slot`, or of the default model when omitted."""
    if slot is None:
        slot = await default_slot(session)
        if slot is None:
            return None
    elif await session.scalar(removed_slots().where(ProviderSlot.slot == slot)) is not None:
        return None
    return await session.scalar(
        select(ProviderConfig)
        .where(ProviderConfig.capability == "llm_extract", ProviderConfig.slot == slot)
        .order_by(ProviderConfig.revision.desc())
        .limit(1)
    )


def configured_llm(settings, config: ProviderConfig, transport=None):
    if config.encrypted_key is None:
        raise ServiceError(
            "provider_secrets_unavailable", "Stored provider credential is missing", 503, 4
        )
    key = ProviderSecrets(settings).decrypt(config.encrypted_key, config.id)
    data = config.data
    configured = settings.model_copy(
        update={
            "llm_provider": data["provider"],
            "llm_model": data["model"],
            "llm_api_key": key,
            "llm_base_url": data["base_url"],
            "llm_json_mode": data["json_mode"],
            # Provider revisions never inherit deployment-specific vendor switches.
            "llm_request_options": None,
            "llm_effort": None,
        }
    )
    adapter = AnthropicExtractor if data["provider"] == "anthropic" else OpenAICompatibleExtractor
    llm = adapter(configured, transport, provider_config_id=config.id)
    llm.version = f"{ADAPTER_VERSION}:config:{config.id.hex}"
    llm.model_revision = config.revision
    llm.reasoning_levels = {level["name"]: level for level in data["reasoning"]}
    llm.default_reasoning = data["default_reasoning"]
    return llm


async def resolve_configured(
    session, settings, transport=None, job: Job | None = None, model: UUID | None = None
):
    """A queued job's pinned revision, else the chosen saved model or the default one."""
    identity = job.provider_identity if job is not None else None
    if job is not None and identity is not None:
        config = (
            await session.get(ProviderConfig, job.provider_config_id)
            if job.provider_config_id
            else None
        )
        if job.provider_config_id is not None and config is None:
            raise ProviderFailure(
                "Job provider revision is unavailable", code="provider_config_unavailable"
            )
    else:
        config = await current_config(session, model)
        if model is not None and config is None:
            raise ServiceError(
                "provider_model_unavailable", "The chosen model is not saved or was removed", 404, 2
            )
        if job is not None and config is not None:
            # A job without a pinned revision cannot silently adopt a newer credential.
            raise ProviderFailure(
                "Provider configuration changed since this job; submit a new job",
                code="provider_model_changed",
            )
    llm = configured_llm(settings, config, transport) if config is not None else DisabledLLM()
    if identity is not None and model_identity(llm) != identity:
        raise ProviderFailure(
            "Model configuration changed; submit a new job", code="provider_model_changed"
        )
    return llm
