"""Inputs for the platform operator console."""

import re
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.core.llm_options import validate_request_options
from app.schemas.contracts import Contract


class PlatformLogin(Contract):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)
    totp: str = Field(pattern=r"^\d{6}$")


class PasswordSetup(Contract):
    token: str = Field(min_length=1, max_length=4096)
    password: str = Field(min_length=1, max_length=1024)


class PlatformOrgCreate(Contract):
    name: str = Field(min_length=1, max_length=200)
    admin_email: str = Field(min_length=3, max_length=254)

    @field_validator("name")
    @classmethod
    def meaningful_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must contain non-whitespace characters")
        return value.strip()

    @field_validator("admin_email")
    @classmethod
    def plausible_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+", value):
            raise ValueError("admin_email must be an email address")
        return value


class PlatformOrgActive(Contract):
    active: bool


class ReasoningLevel(Contract):
    """One of the vendor's official reasoning levels for a catalog model."""

    name: str = Field(pattern=r"^[a-z0-9_-]{1,20}$")
    label: str | None = Field(default=None, max_length=60)
    # Output limits are reserved; other adapter fields win on conflict.
    request_options: dict[str, Any] = Field(default_factory=dict)
    # Anthropic only: written to output_config.effort.
    effort: str | None = Field(default=None, pattern=r"^[a-z]{1,10}$")
    batch_chars: int = Field(default=8000, ge=1000, le=200000)

    @field_validator("request_options")
    @classmethod
    def adapter_owned_limits(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_request_options(value)


class PlatformModelSet(Contract):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,39}$")
    capability: Literal["llm_extract"]
    provider: Literal["anthropic", "openai"]
    model: str = Field(min_length=1, max_length=100)
    base_url: str | None = Field(default=None, max_length=300)
    credential: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    default: bool = False
    enabled: bool = True
    reasoning: list[ReasoningLevel] = Field(default_factory=list, max_length=8)
    default_reasoning: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)

    @field_validator("base_url")
    @classmethod
    def https_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        from app.schemas.platform_credentials import CredentialSpec

        return CredentialSpec.safe_endpoint(value)

    @model_validator(mode="after")
    def default_is_enabled(self):
        if self.default and not self.enabled:
            raise ValueError("a default model must be enabled")
        names = [level.name for level in self.reasoning]
        if len(names) != len(set(names)):
            raise ValueError("reasoning level names must be unique")
        if names and self.default_reasoning not in names:
            raise ValueError("default_reasoning must name one of the reasoning levels")
        if not names and self.default_reasoning is not None:
            raise ValueError("default_reasoning needs reasoning levels")
        return self


class OrgLookup(Contract):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class PlatformModelTest(Contract):
    dry_run: bool = False
    test_org_id: UUID | None = None

    @model_validator(mode="after")
    def live_probe_has_tenant(self):
        if not self.dry_run and self.test_org_id is None:
            raise ValueError(
                "live platform probes require an authorized internal test organization"
            )
        return self
