"""Tenant model configuration inputs; credential values have no response representation."""

import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator

from app.schemas.contracts import Contract
from app.schemas.platform_contracts import ReasoningLevel


class ProviderConfigInput(Contract):
    capability: Literal["llm_extract"] = "llm_extract"
    source: Literal["platform", "org"]
    platform_model_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,39}$")
    provider: Literal["anthropic", "openai"] | None = None
    model: str | None = Field(default=None, min_length=1, max_length=100)
    base_url: str | None = Field(default=None, max_length=300)
    json_mode: Literal["json_schema", "json_object"] = "json_schema"
    reasoning: list[ReasoningLevel] = Field(default_factory=list, max_length=8)
    default_reasoning: str | None = None
    input_usd_per_mtok: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    output_usd_per_mtok: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    expected_revision: int | None = Field(default=None, ge=1)

    @field_validator("base_url")
    @classmethod
    def credential_free_https(cls, value: str | None) -> str | None:
        if value is None:
            return None
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or re.search(r"[\s\\\x00-\x1f]", value)
        ):
            raise ValueError("base_url must be an HTTPS URL without credentials, query or fragment")
        return value.rstrip("/")

    @model_validator(mode="after")
    def source_fields(self):
        if self.source == "platform":
            if not self.platform_model_id:
                raise ValueError("platform_model_id is required")
            if (
                any(
                    value is not None
                    for value in (
                        self.provider,
                        self.model,
                        self.base_url,
                        self.input_usd_per_mtok,
                        self.output_usd_per_mtok,
                        self.default_reasoning,
                    )
                )
                or self.reasoning
                or self.json_mode != "json_schema"
            ):
                raise ValueError("platform configuration uses catalog fields")
        elif not self.provider or not self.model or self.platform_model_id is not None:
            raise ValueError("org configuration requires provider/model and no platform_model_id")
        names = [level.name for level in self.reasoning]
        if len(names) != len(set(names)):
            raise ValueError("reasoning level names must be unique")
        if (names and self.default_reasoning not in names) or (
            not names and self.default_reasoning is not None
        ):
            raise ValueError("default_reasoning must name a configured reasoning level")
        return self


class ProviderConfigSet(ProviderConfigInput):
    api_key: SecretStr | None = Field(default=None, exclude=True, min_length=5, max_length=4096)

    @model_validator(mode="after")
    def key_source(self):
        if self.source == "platform" and self.api_key is not None:
            raise ValueError("platform configuration cannot accept a key")
        if self.api_key is not None and re.search(
            r"[\s\x00-\x1f\x7f]", self.api_key.get_secret_value()
        ):
            raise ValueError("api_key cannot contain whitespace or control characters")
        return self


class ProviderTest(Contract):
    capability: Literal["llm_extract"] = "llm_extract"
    reasoning: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,20}$")


class ProviderConfigView(Contract):
    id: UUID
    org_id: UUID
    revision: int
    capability: str
    source: str
    platform_model_id: str | None
    provider: str
    model: str
    base_url: str | None
    json_mode: str
    reasoning: list[ReasoningLevel]
    default_reasoning: str | None
    input_usd_per_mtok: float | None
    output_usd_per_mtok: float | None
    key_last4: str | None
    updated_by: UUID
    updated_at: datetime
