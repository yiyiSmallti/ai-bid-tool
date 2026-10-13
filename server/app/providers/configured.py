"""Resolve encrypted local provider revisions and immutable job model identities."""

from sqlalchemy import select

from app.core.errors import ServiceError
from app.core.provider_secrets import ProviderSecrets
from app.models.entities import Job
from app.models.provider_configs import ProviderConfig
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


async def current_config(session):
    return await session.scalar(
        select(ProviderConfig)
        .where(ProviderConfig.capability == "llm_extract")
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


async def resolve_configured(session, settings, transport=None, job: Job | None = None):
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
        config = await current_config(session)
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
