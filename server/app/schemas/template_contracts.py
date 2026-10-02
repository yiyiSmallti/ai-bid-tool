"""Approved private DOCX templates and immutable task selection contracts."""

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.contracts import Contract


class TemplateChapter(Contract):
    title: str = Field(min_length=1, max_length=200)
    children: list["TemplateChapter"] = Field(default_factory=list, max_length=100)

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a nonblank chapter title is required")
        return value.strip()


class TemplateData(Contract):
    name: str = Field(min_length=1, max_length=200)
    project_types: list[str] | None = Field(default=None, max_length=100)
    chapters: list[TemplateChapter] | None = Field(default=None, max_length=100)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a nonblank name is required")
        return value.strip()

    @field_validator("project_types")
    @classmethod
    def bounded_project_types(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        if any(not entry.strip() or len(entry) > 200 for entry in value):
            raise ValueError("project types must be nonblank and at most 200 characters")
        return [entry.strip() for entry in value]

    @field_validator("chapters")
    @classmethod
    def bounded_chapter_tree(
        cls, value: list[TemplateChapter] | None
    ) -> list[TemplateChapter] | None:
        if value is None:
            return value
        pending = [(chapter, 1) for chapter in value]
        count = 0
        while pending:
            chapter, depth = pending.pop()
            count += 1
            if count > 200 or depth > 6:
                raise ValueError("chapter declarations are limited to 200 nodes and six levels")
            pending.extend((child, depth + 1) for child in chapter.children)
        return value


class TemplateCreate(Contract):
    data: TemplateData


class TemplateUpdate(Contract):
    expected_revision: int = Field(ge=1)
    data: TemplateData


class TemplateFile(Contract):
    name: str = Field(min_length=1, max_length=200)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0, le=40 * 1024 * 1024)
    media_type: Literal[
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ] = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class TemplateRevision(Contract):
    id: UUID
    org_id: UUID
    template_id: UUID
    revision: int = Field(ge=1)
    data: TemplateData
    file: TemplateFile


class TaskTemplateSelection(Contract):
    template_id: UUID
    revision: int | None = Field(default=None, ge=1)
    lot: str | None = Field(default=None, max_length=100)


class TaskTemplateSnapshot(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    template_revision_id: UUID
    revision: int = Field(ge=1)
    lot: str | None = None
    data: TemplateData
    file: TemplateFile


class TemplateDownloadReceipt(Contract):
    template_revision_id: UUID
    output_path: str = Field(min_length=1)
    file: TemplateFile
