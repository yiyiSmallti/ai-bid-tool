"""Approved declared-certificate metadata and retained task-selection contracts."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.contracts import Contract


class CertificateData(Contract):
    kind: Literal["qualification", "personnel"]
    name: str = Field(min_length=1, max_length=200)
    number: str = Field(min_length=1, max_length=200)
    valid_from: date | None = None
    valid_until: date | None = None

    @field_validator("name", "number")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a nonblank value is required")
        return value.strip()

    @model_validator(mode="after")
    def coherent_dates(self):
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_from > self.valid_until
        ):
            raise ValueError("valid_from cannot be after valid_until")
        return self


class CertificateCreate(Contract):
    data: CertificateData


class CertificateUpdate(Contract):
    expected_revision: int = Field(ge=1)
    data: CertificateData


class CertificateRevision(Contract):
    id: UUID
    org_id: UUID
    certificate_id: UUID
    revision: int = Field(ge=1)
    data: CertificateData


class TaskCertificateSelection(Contract):
    certificate_id: UUID
    revision: int | None = Field(default=None, ge=1)
    lot: str | None = Field(default=None, max_length=100)


class TaskCertificateSnapshot(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    certificate_revision_id: UUID
    revision: int = Field(ge=1)
    lot: str | None = None
    data: CertificateData
