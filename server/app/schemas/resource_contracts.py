"""Shared contracts for versioned metadata and task selections."""

from uuid import UUID

from pydantic import Field, HttpUrl, field_validator

from app.schemas.contracts import Contract


class ProductData(Contract):
    name: str = Field(min_length=1, max_length=200)
    vendor: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    model_version: str | None = Field(default=None, max_length=100)
    official_url: HttpUrl | None = None
    whitepaper_url: HttpUrl | None = None

    @field_validator("name", "vendor", "model")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a nonblank value is required")
        return value.strip()

    @field_validator("official_url", "whitepaper_url")
    @classmethod
    def no_embedded_credentials(cls, value: HttpUrl | None) -> HttpUrl | None:
        if value is not None and (value.username or value.password):
            raise ValueError("source URLs cannot contain credentials")
        return value


class ProductCreate(Contract):
    data: ProductData


class ProductUpdate(Contract):
    expected_revision: int = Field(ge=1)
    data: ProductData


class ProductRevision(Contract):
    id: UUID
    org_id: UUID
    product_id: UUID
    revision: int = Field(ge=1)
    data: ProductData


class TaskProductSelection(Contract):
    product_id: UUID
    revision: int | None = Field(default=None, ge=1)
    lot: str | None = Field(default=None, max_length=100)


class TaskProductSnapshot(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    product_revision_id: UUID
    revision: int = Field(ge=1)
    lot: str | None = None
    data: ProductData
