"""Whole-bid contracts: the outline of a tender's chapters and its format chapter forms."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.contracts import Contract

type ChapterRole = Literal["format", "scoring", "needs", "contract", "instructions", "other"]
type FormKind = Literal[
    "cover",
    "letter",
    "price",
    "declaration",
    "copy",
    "deviation",
    "scoring_index",
    "roster",
    "solution",
    "form",
]


class OutlinePlace(Contract):
    """Where a chapter or form starts and ends: PDF pages, or Word block labels."""

    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    block_start: str | None = None
    block_end: str | None = None


class OutlineChapter(OutlinePlace):
    title: str = Field(min_length=1)
    role: ChapterRole


class OutlineForm(OutlinePlace):
    """One numbered form of the format chapter, kept verbatim.

    `text` holds the form's lines in source order; a table row is one line with
    tab-separated cells, as parsing stores it.
    """

    index: int = Field(ge=1)
    label: str
    title: str = Field(min_length=1)
    kind: FormKind
    text: str


class BidOutline(Contract):
    document_id: UUID
    chapters: list[OutlineChapter]
    format_chapter: str | None
    forms: list[OutlineForm]
