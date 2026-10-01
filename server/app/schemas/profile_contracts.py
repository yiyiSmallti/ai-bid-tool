"""Approved organization declarations and immutable task selection contracts."""

from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.contracts import Contract


class OrgProfileData(Contract):
    name: str = Field(min_length=1, max_length=200)
    registration_details: str | None = Field(default=None, max_length=10000)
    performance_summary: str | None = Field(default=None, max_length=20000)
    standard_wording: str | None = Field(default=None, max_length=20000)

    @field_validator("name", "registration_details", "performance_summary", "standard_wording")
    @classmethod
    def nonblank_when_supplied(cls, value: str | None) -> str | None:
        if value is not None:
            if not value.strip():
                raise ValueError("a nonblank value is required when supplied")
            return value.strip()
        return value


class OrgProfileCreate(Contract):
    data: OrgProfileData


class OrgProfileUpdate(Contract):
    expected_revision: int = Field(ge=1)
    data: OrgProfileData


class OrgProfileRevision(Contract):
    id: UUID
    org_id: UUID
    profile_id: UUID
    revision: int = Field(ge=1)
    data: OrgProfileData


class TaskOrgProfileSelection(Contract):
    profile_id: UUID
    revision: int | None = Field(default=None, ge=1)
    lot: str | None = Field(default=None, max_length=100)


class TaskOrgProfileSnapshot(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    profile_revision_id: UUID
    revision: int = Field(ge=1)
    lot: str | None = None
    data: OrgProfileData
