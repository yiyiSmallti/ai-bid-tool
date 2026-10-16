"""Project package requests (docs/plan/enterprise-gateway.md#packages)."""

from uuid import UUID

from app.schemas.contracts import Contract


class PackageExport(Contract):
    task_id: UUID
