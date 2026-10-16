"""Solution chapter drafting and human review contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.contracts import Contract, ProviderUsage


def _nonblank(values: list[str], name: str) -> list[str]:
    if any(not value.strip() for value in values):
        raise ValueError(f"{name} must contain text")
    return values


class SolutionTable(Contract):
    """A plain grid: a header row and body rows of the same width."""

    columns: list[str] = Field(min_length=2, max_length=8)
    rows: list[list[str]] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def rectangular(self) -> "SolutionTable":
        _nonblank(self.columns, "columns")
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("every row must have one cell per column")
        return self


class SolutionSubsection(Contract):
    heading: str = Field(min_length=1, max_length=300)
    paragraphs: list[str] = Field(min_length=1, max_length=40)
    table: SolutionTable | None = None

    @field_validator("heading")
    @classmethod
    def nonblank_heading(cls, value: str) -> str:
        return _nonblank([value], "heading")[0]

    @field_validator("paragraphs")
    @classmethod
    def nonblank_paragraphs(cls, value: list[str]) -> list[str]:
        return _nonblank(value, "paragraphs")


class SolutionSection(Contract):
    """A chapter section: lead paragraphs, then numbered subsections."""

    heading: str = Field(min_length=1, max_length=300)
    paragraphs: list[str] = Field(default_factory=list, max_length=100)
    subsections: list[SolutionSubsection] = Field(default_factory=list, max_length=12)

    @field_validator("heading")
    @classmethod
    def nonblank_heading(cls, value: str) -> str:
        return _nonblank([value], "heading")[0]

    @field_validator("paragraphs")
    @classmethod
    def nonblank_paragraphs(cls, value: list[str]) -> list[str]:
        return _nonblank(value, "paragraphs")

    @model_validator(mode="after")
    def has_body(self) -> "SolutionSection":
        if not self.paragraphs and not self.subsections:
            raise ValueError("a section needs paragraphs or subsections")
        return self

    def texts(self) -> list[str]:
        """Every text of the section, for placeholder counting."""
        out = [self.heading, *self.paragraphs]
        for sub in self.subsections:
            out += [sub.heading, *sub.paragraphs]
            if sub.table:
                out += sub.table.columns + [cell for row in sub.table.rows for cell in row]
        return out


class SolutionDraftRequest(Contract):
    document_id: UUID
    titles: list[str] | None = None
    model: UUID | None = None
    reasoning: str | None = None


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


class SolutionPlanSection(Contract):
    heading: str = Field(min_length=1, max_length=120)
    points: list[str] = Field(min_length=2, max_length=6)


class SolutionPlanOutput(Contract):
    """Stage one: the chapter's section headings and each section's subsection headings."""

    sections: list[SolutionPlanSection] = Field(min_length=4, max_length=10)


class SolutionSectionOutput(Contract):
    """Stage two: one planned section written out, one subsection per planned point."""

    paragraphs: list[str] = Field(min_length=1, max_length=3)
    subsections: list[SolutionSubsection] = Field(min_length=2, max_length=6)


class SolutionProviderRequest(Contract):
    title: str
    score_range: str
    rule: str
    needs_text: str
    project_name: str | None
    tenderee: str | None


class SolutionSectionRequest(Contract):
    """Stage-two input: the item and the whole plan, with one section to write."""

    item: SolutionProviderRequest
    plan: list[SolutionPlanSection]
    section: SolutionPlanSection


class SolutionProviderResult(Contract):
    sections: list[SolutionSection]
    usages: list[ProviderUsage]
