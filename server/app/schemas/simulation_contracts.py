"""Contracts for simulated product proposals (demonstration only, never deliverable)."""

from uuid import UUID

from pydantic import model_validator

from app.schemas.contracts import Contract
from app.schemas.export_contracts import Sha256


class ProductSimulationInput(Contract):
    extraction_job_id: UUID
    expected_input_hash: Sha256 | None = None
    dry_run: bool = False
    retry: bool = False

    @model_validator(mode="after")
    def preflight(self):
        if self.dry_run and self.retry:
            raise ValueError("dry-run cannot retry")
        if not self.dry_run and self.expected_input_hash is None:
            raise ValueError("preflight input hash required")
        return self
