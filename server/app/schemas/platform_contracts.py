"""Validated provider reasoning options."""

from typing import Any

from pydantic import Field, field_validator

from app.core.llm_options import validate_request_options
from app.schemas.contracts import Contract


class ReasoningLevel(Contract):
    """One of the vendor's official reasoning levels for a configured model."""

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
