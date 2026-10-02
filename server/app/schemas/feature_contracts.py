"""Approved shared contracts for declared software metadata and task selections."""

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.contracts import Contract


class FeatureData(Contract):
    product_id: UUID
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=10000)
    status: Literal["implemented", "developing", "planned"]

    @field_validator("name", "description")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a nonblank value is required")
        return value.strip()


class FeatureCreate(Contract):
    data: FeatureData


class FeatureUpdate(Contract):
    expected_revision: int = Field(ge=1)
    data: FeatureData


class FeatureRevision(Contract):
    id: UUID
    org_id: UUID
    feature_id: UUID
    revision: int = Field(ge=1)
    data: FeatureData


class TaskFeatureSelection(Contract):
    feature_id: UUID
    revision: int | None = Field(default=None, ge=1)
    lot: str | None = Field(default=None, max_length=100)


class TaskFeatureSnapshot(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    feature_revision_id: UUID
    revision: int = Field(ge=1)
    lot: str | None = None
    data: FeatureData
