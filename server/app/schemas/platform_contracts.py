"""Inputs for the platform operator console."""

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

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


class PlatformModelSet(Contract):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,39}$")
    capability: Literal["llm_extract"]
    provider: Literal["anthropic", "openai"]
    model: str = Field(min_length=1, max_length=100)
    base_url: str | None = Field(default=None, max_length=300)
    credential: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    vendor_input_usd_per_mtok: float = Field(ge=0)
    vendor_output_usd_per_mtok: float = Field(ge=0)
    sale_input_per_mtok: float = Field(ge=0)
    sale_output_per_mtok: float = Field(ge=0)
    default: bool = False
    enabled: bool = True
    expected_revision: int | None = Field(default=None, ge=1)

    @field_validator("base_url")
    @classmethod
    def https_url(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"https://[^\s/]+(/\S*)?", value):
            raise ValueError("base_url must be an https URL")
        return value

    @model_validator(mode="after")
    def default_is_enabled(self):
        if self.default and not self.enabled:
            raise ValueError("a default model must be enabled")
        return self
