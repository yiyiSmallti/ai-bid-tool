"""Solution chapter drafting and human review contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.contracts import Contract, ProviderUsage


class SolutionSection(Contract):
    heading: str = Field(min_length=1, max_length=300)
    paragraphs: list[str] = Field(min_length=1, max_length=100)

    @field_validator("heading")
    @classmethod
    def nonblank_heading(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("heading must contain text")
        return value

    @field_validator("paragraphs")
    @classmethod
    def nonblank_paragraphs(cls, value: list[str]) -> list[str]:
        if any(not paragraph.strip() for paragraph in value):
            raise ValueError("paragraphs must contain text")
        return value


class SolutionDraftRequest(Contract):
    document_id: UUID
    titles: list[str] | None = None
    model: UUID | None = None


class SolutionDraftReceipt(Contract):
    job_id: UUID
    task_id: UUID
    document_id: UUID
    titles: list[str]


class SolutionEdit(Contract):
    expected_revision: int = Field(ge=1)
    sections: list[SolutionSection] = Field(min_length=1, max_length=30)


class SolutionConfirm(Contract):
    expected_revision: int = Field(ge=1)


class SolutionRevision(Contract):
    id: UUID
    revision: int = Field(ge=1)
    state: Literal["draft", "confirmed"]
    origin: Literal["model", "human"]
    sections: list[SolutionSection]
    actor_kind: str
    actor_token_id: UUID | None
    agent_session_id: UUID | None
    job_id: UUID | None
    created_at: datetime
    confirmed_at: datetime | None


class SolutionChapter(Contract):
    id: UUID
    task_id: UUID
    document_id: UUID
    title: str
    score_range: str
    current: SolutionRevision
    placeholder_count: int = Field(ge=0)


class SolutionList(Contract):
    task_id: UUID
    document_id: UUID | None = None
    total: int = Field(ge=0)


class SolutionWireOutput(Contract):
    sections: list[SolutionSection] = Field(min_length=4, max_length=8)


class SolutionProviderRequest(Contract):
    title: str
    score_range: str
    rule: str
    needs_text: str
    project_name: str | None
    tenderee: str | None


class SolutionProviderResult(Contract):
    output: SolutionWireOutput
    usages: list[ProviderUsage]
