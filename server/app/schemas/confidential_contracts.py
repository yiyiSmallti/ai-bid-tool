"""Confidential fields: placeholders the model and cards use, values only exports fill."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.contracts import Contract

ConfidentialKind = Literal["amount", "contact", "identity", "bank_account", "other"]
ConfidentialScope = Literal["org", "task"]
KEY_PATTERN = r"^[a-z][a-z0-9_]{1,47}$"


def _nonblank(value: str | None) -> str | None:
    if value is not None:
        if not value.strip():
            raise ValueError("a nonblank value is required")
        return value.strip()
    return value


class ConfidentialFieldCreate(Contract):
    key: str = Field(pattern=KEY_PATTERN)
    label: str = Field(min_length=1, max_length=100)
    kind: ConfidentialKind
    scope: ConfidentialScope

    _label = field_validator("label")(_nonblank)


class ConfidentialFieldUpdate(Contract):
    expected_revision: int = Field(ge=1)
    label: str | None = Field(default=None, min_length=1, max_length=100)
    archived: bool | None = None

    _label = field_validator("label")(_nonblank)

    @model_validator(mode="after")
    def changes_something(self):
        if self.label is None and self.archived is None:
            raise ValueError("label or archived is required")
        return self


class ConfidentialValueSet(Contract):
    value: str = Field(min_length=1, max_length=2000)
    # Required for a task-scope field, refused for an org-scope one.
    task_id: UUID | None = None

    _value = field_validator("value")(_nonblank)


class ConfidentialFieldView(Contract):
    id: UUID
    key: str
    placeholder: str
    label: str
    kind: ConfidentialKind
    scope: ConfidentialScope
    archived: bool
    revision: int
    created_at: datetime


class ConfidentialValueView(Contract):
    """One field's current value for the org or a task, never the value itself."""

    field_id: UUID
    key: str
    placeholder: str
    label: str
    kind: ConfidentialKind
    scope: ConfidentialScope
    task_id: UUID | None
    status: Literal["filled", "missing"]
    value_id: UUID | None
    version: int | None
    tail: str | None
    set_by: UUID | None
    set_at: datetime | None


class ConfidentialReveal(Contract):
    field_id: UUID
    value_id: UUID
    key: str
    value: str
