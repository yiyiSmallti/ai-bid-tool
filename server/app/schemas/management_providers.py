"""Explicit nonsecret projections for org extraction-model settings."""

from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.contracts import Contract
from app.schemas.management_pages import ActionHint, PageQuery, Revision, SearchText
from app.schemas.provider_contracts import ProviderConfigInput


class ProviderCatalogQuery(PageQuery):
    """Catalog identifiers support literal prefix search, never vendor endpoints."""

    q: SearchText | None = None


class CatalogReasoningChoice(Contract):
    name: str = Field(min_length=1, max_length=20)
    label: str | None = Field(default=None, max_length=100)


class PlatformModelChoice(Contract):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,39}$")
    revision: Revision
    model: str = Field(min_length=1, max_length=100)
    provider: Literal["anthropic", "openai"]
    sale_input_per_mtok: float = Field(ge=0, allow_inf_nan=False)
    sale_output_per_mtok: float = Field(ge=0, allow_inf_nan=False)
    default: bool
    reasoning: list[CatalogReasoningChoice] = Field(max_length=8)
    default_reasoning: str | None


class ProviderRevisionMetadata(Contract):
    id: UUID
    org_id: UUID
    revision: Revision
    configuration: ProviderConfigInput
    provider: Literal["anthropic", "openai"]
    model: str = Field(min_length=1, max_length=100)
    catalog_state: Literal["enabled", "unavailable", "not_applicable"]
    credential_state: Literal["configured", "platform_managed"]
    updated_by: UUID
    updated_at: AwareDatetime
    revised_at: AwareDatetime
    revised_by: UUID | None
    reasoning: list[CatalogReasoningChoice] = Field(max_length=8)
    default_reasoning: str | None
    catalog_revision: Revision | None = None
    sale_input_per_mtok: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    sale_output_per_mtok: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def read_only_configuration(self) -> Self:
        if self.configuration.expected_revision is not None:
            raise ValueError("read projection cannot supply a write precondition")
        expected = "configured" if self.configuration.source == "org" else "platform_managed"
        if self.credential_state != expected:
            raise ValueError("credential state must match the selected source")
        if self.configuration.source == "org":
            if (
                self.catalog_state != "not_applicable"
                or self.provider != self.configuration.provider
                or self.model != self.configuration.model
                or self.catalog_revision is not None
                or self.sale_input_per_mtok is not None
                or self.sale_output_per_mtok is not None
            ):
                raise ValueError("BYOK metadata must match its stored configuration")
        elif self.catalog_state == "not_applicable":
            raise ValueError("a platform selection requires catalog availability")
        return self


class ProviderSettingsData(Contract):
    org_id: UUID
    capability: Literal["llm_extract"] = "llm_extract"
    effective_source: Literal["org", "platform", "unconfigured"]
    current: ProviderRevisionMetadata | None
    default_model: PlatformModelChoice | None
    billing_currency: str = Field(pattern=r"^[A-Z]{3}$")
    reasoning: list[CatalogReasoningChoice] = Field(max_length=8)
    actions: list[ActionHint] = Field(max_length=16)
    connection_status: Literal["not_checked"] = "not_checked"

    @model_validator(mode="after")
    def effective_source_binding(self) -> Self:
        if self.current is not None and self.current.org_id != self.org_id:
            raise ValueError("configuration must belong to the authenticated org")
        expected = (
            self.current.configuration.source
            if self.current is not None
            else "platform"
            if self.default_model is not None
            else "unconfigured"
        )
        if self.effective_source != expected:
            raise ValueError("effective source must follow org revision then platform default")
        return self
