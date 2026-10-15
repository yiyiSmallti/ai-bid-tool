"""Explicit nonsecret projections for local extraction-model settings."""

from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.contracts import Contract
from app.schemas.management_pages import ActionHint, Revision
from app.schemas.provider_contracts import ProviderConfigInput


class ReasoningChoice(Contract):
    name: str = Field(min_length=1, max_length=20)
    label: str | None = Field(default=None, max_length=100)


class ProviderRevisionMetadata(Contract):
    id: UUID
    slot: UUID
    name: str | None = Field(default=None, max_length=60)
    revision: Revision
    configuration: ProviderConfigInput
    provider: Literal["anthropic", "openai"]
    model: str = Field(min_length=1, max_length=100)
    credential_state: Literal["configured"] = "configured"
    updated_at: AwareDatetime
    revised_at: AwareDatetime
    reasoning: list[ReasoningChoice] = Field(max_length=8)
    default_reasoning: str | None

    @model_validator(mode="after")
    def read_only_configuration(self) -> Self:
        if self.configuration.expected_revision is not None:
            raise ValueError("read projection cannot supply a write precondition")
        if self.provider != self.configuration.provider or self.model != self.configuration.model:
            raise ValueError("metadata must match its stored configuration")
        return self


class ProviderSettingsData(Contract):
    capability: Literal["llm_extract"] = "llm_extract"
    configured: bool
    current: ProviderRevisionMetadata | None
    # Every saved model's current revision, the default model first.
    models: list[ProviderRevisionMetadata] = Field(default_factory=list, max_length=20)
    default_slot: UUID | None = None
    reasoning: list[ReasoningChoice] = Field(max_length=8)
    actions: list[ActionHint] = Field(max_length=16)
    connection_status: Literal["not_checked"] = "not_checked"

    @model_validator(mode="after")
    def configuration_binding(self) -> Self:
        if self.configured != (self.current is not None):
            raise ValueError("configured state must follow the saved revision")
        if self.default_slot != (self.current.slot if self.current else None):
            raise ValueError("the default model must be the current configuration")
        return self
