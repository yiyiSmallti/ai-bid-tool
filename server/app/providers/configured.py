"""Resolve tenant choices and immutable job model identities for both LLM capabilities."""

from sqlalchemy import select

from app.core.errors import ServiceError
from app.core.provider_secrets import ProviderSecrets
from app.models.entities import Job, PlatformModel
from app.models.provider_configs import ProviderConfig
from app.providers.base import ProviderFailure
from app.providers.disabled import DisabledLLM
from app.providers.llm import (
    ADAPTER_VERSION,
    AnthropicExtractor,
    OpenAICompatibleExtractor,
    UnavailablePlatformModel,
    platform_llm,
)


def model_identity(llm) -> dict:
    config_id = getattr(llm, "provider_config_id", None)
    return {
        "provider": llm.name,
        "model": llm.model,
        "adapter_version": llm.version,
        "platform_model_id": getattr(llm, "platform_model_id", None),
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


def org_llm(settings, config: ProviderConfig, transport=None):
    if config.encrypted_key is None:
        raise ServiceError(
            "provider_secrets_unavailable", "Stored provider credential is missing", 503, 4
        )
    key = ProviderSecrets(settings).decrypt(config.encrypted_key, config.org_id, config.id)
    data = config.data
    configured = settings.model_copy(
        update={
            "llm_provider": data["provider"],
            "llm_model": data["model"],
            "llm_api_key": key,
            "llm_base_url": data["base_url"],
            "llm_json_mode": data["json_mode"],
            "llm_input_usd_per_mtok": data["input_usd_per_mtok"],
            "llm_output_usd_per_mtok": data["output_usd_per_mtok"],
            # An org's declared options must not inherit deployment-specific vendor switches.
            "llm_request_options": None,
            "llm_effort": None,
        }
    )
    adapter = AnthropicExtractor if data["provider"] == "anthropic" else OpenAICompatibleExtractor
    llm = adapter(configured, transport, provider_config_id=config.id, org_owned=True)
    llm.version = f"{ADAPTER_VERSION}:org:{config.id.hex}"
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
            # Legacy jobs have no org revision in their cache or accounting identity.
            # They cannot silently adopt a newly authorized credential.
            raise ProviderFailure(
                "Provider configuration changed since this legacy job; submit a new job",
                code="provider_model_changed",
            )
    if config is not None and config.source == "org":
        llm = org_llm(settings, config, transport)
    else:
        catalog_id = (
            config.platform_model_id
            if config is not None
            else (identity.get("platform_model_id") if identity is not None else None)
        )
        entry = (
            await session.scalar(
                select(PlatformModel).where(
                    PlatformModel.id == catalog_id
                    if catalog_id is not None
                    else PlatformModel.is_default.is_(True),
                    PlatformModel.capability == "llm_extract",
                    PlatformModel.enabled.is_(True),
                )
            )
            if identity is None or catalog_id is not None
            else None
        )
        if catalog_id is not None and entry is None:
            raise ServiceError(
                "provider_unavailable", "Selected platform model is unavailable", 503, 4
            )
        if entry is None:
            llm = DisabledLLM()
        else:
            llm = platform_llm(settings, entry, transport)
            assert llm.credential_resolver is not None
            try:
                readiness = await llm.credential_resolver.readiness(llm.credential_target)
            except ServiceError as exc:
                raise ServiceError(
                    "provider_unavailable", "Selected provider is unavailable", 503, exc.exit_code
                ) from None
            if not readiness.configured:
                llm = UnavailablePlatformModel(entry)
        if config is not None:
            assert entry is not None
            llm.provider_config_id = config.id
            llm.version = f"{ADAPTER_VERSION}:{config.id.hex}:{entry.revision}"
    if identity is not None and model_identity(llm) != identity:
        raise ProviderFailure(
            "Model configuration changed; submit a new job", code="provider_model_changed"
        )
    return llm
