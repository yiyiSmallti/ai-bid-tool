"""Readonly invocation origin shared by public business and agent contracts."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.contracts import Contract


class AgentProvenance(Contract):
    """Business origin survives worker publication; actor_kind remains the actual executor."""

    initiated_by: Literal["builtin_agent", "external_agent"]
    on_behalf_of_user_id: UUID
    principal_id: UUID | None = None
    session_id: UUID | None = None
    step_id: UUID | None = None
    token_id: UUID | None = None
    invocation_id: UUID
    command: str = Field(min_length=1, max_length=100)
    job_id: UUID | None = None
    run_id: UUID | None = None

    @model_validator(mode="after")
    def origin_binding(self):
        if self.initiated_by == "builtin_agent":
            if self.principal_id is None or self.session_id is None or self.token_id is not None:
                raise ValueError(
                    "built-in origin requires a principal and session, without a token"
                )
        elif self.token_id is None or self.principal_id is not None or self.session_id is not None:
            raise ValueError("external origin requires an authenticated API token")
        return self
