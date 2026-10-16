"""Resolve encrypted local provider revisions and immutable job model identities."""

from uuid import UUID

from sqlalchemy import select, union_all
from sqlalchemy.orm import aliased

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


def acting_member(session) -> UUID | None:
    """The gateway member this transaction acts for; None in a personal installation."""
    actor = session.info.get("actor")
    return actor.member_id if actor is not None else None


def removed_slots():
    return select(ProviderSlot.slot).where(ProviderSlot.removed_at.is_not(None))


def hidden_slots(member: UUID | None):
    """Other members' personal models, which nobody else sees, administrators included."""
    # An alias, so the subquery never correlates with an outer `provider_slots`.
    other = aliased(ProviderSlot)
    query = select(other.slot).where(other.owner_member_id.is_not(None))
    if member is not None:
        query = query.where(other.owner_member_id != member)
    return query


async def default_slot(session) -> UUID | None:
    """The company default model."""
    marked = await session.scalar(
        select(ProviderSlot.slot).where(
            ProviderSlot.capability == "llm_extract", ProviderSlot.is_default
        )
    )
    if marked is not None:
        return marked
    # Without a marked default, the most recently saved company model that was not removed.
    return await session.scalar(
        select(ProviderConfig.slot)
        .where(
            ProviderConfig.capability == "llm_extract",
            ProviderConfig.slot.not_in(removed_slots()),
            ProviderConfig.slot.not_in(hidden_slots(None)),
        )
        .order_by(ProviderConfig.revision.desc())
        .limit(1)
    )


async def member_default(session, member: UUID | None) -> UUID | None:
    """The member's own default model while it is still visible and saved."""
    if member is None:
        return None
    from app.models.members import MemberSetting

    return await session.scalar(
        select(MemberSetting.default_slot)
        .join(ProviderSlot, ProviderSlot.slot == MemberSetting.default_slot)
        .where(
            MemberSetting.member_id == member,
            ProviderSlot.removed_at.is_(None),
            ProviderSlot.slot.not_in(hidden_slots(member)),
        )
    )


async def effective_default(session) -> UUID | None:
    """What a run without a model uses: the member's default, else the company one."""
    return await member_default(session, acting_member(session)) or await default_slot(session)


async def slot_owner(session, slot: UUID) -> UUID | None:
    return await session.scalar(
        select(ProviderSlot.owner_member_id).where(ProviderSlot.slot == slot)
    )


async def live_slots(session) -> tuple[list[UUID], UUID | None]:
    """Saved models the acting member sees that were not removed, and the company default."""
    rows = (
        await session.execute(
            select(ProviderSlot.slot, ProviderSlot.is_default).where(
                ProviderSlot.capability == "llm_extract",
                ProviderSlot.removed_at.is_(None),
                ProviderSlot.slot.not_in(hidden_slots(acting_member(session))),
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
    """The latest revision of `slot`, or of the acting member's default model when omitted."""
    if slot is None:
        slot = await effective_default(session)
        if slot is None:
            return None
    elif (
        await session.scalar(
            select(ProviderSlot.slot).where(
                ProviderSlot.slot == slot,
                (ProviderSlot.removed_at.is_not(None))
                | ProviderSlot.slot.in_(hidden_slots(acting_member(session))),
            )
        )
        is not None
    ):
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
