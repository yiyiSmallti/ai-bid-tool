"""Whole-bid contracts: a tender's outline, and the receipt of an assembled whole bid."""

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


class OutlineScoringItem(Contract):
    """One row of the scoring chapter's rule table: the item and its score range."""

    title: str = Field(min_length=1)
    score_range: str
    rule: str = ""
    subjective: bool = False


type ScoringWarning = Literal[
    "no_items", "no_total", "sum_mismatch", "multiple_packages", "region_truncated"
]


class BidOutline(Contract):
    document_id: UUID
    chapters: list[OutlineChapter]
    format_chapter: str | None
    forms: list[OutlineForm]
    scoring_items: list[OutlineScoringItem]
    # Items come from the rule table parser, or from a verified model review.
    scoring_origin: Literal["rules", "model"] = "rules"
    scoring_total: float | None = None
    scoring_sum: float | None = None
    # Reasons the scoring items may be incomplete; any of them suggests a review.
    scoring_warnings: list[ScoringWarning] = []


class ScoringReviewRequest(Contract):
    model: UUID | None = None


class ScoringReviewItem(Contract):
    """One scored item the model found: the line it starts on, its title and top score."""

    line: int
    title: str
    max: float


class ScoringReviewOutput(Contract):
    total: float | None
    multiple_packages: bool
    items: list[ScoringReviewItem]


class ScoringReviewReceipt(Contract):
    job_id: UUID
    document_id: UUID


class WholeBidReceipt(Contract):
    """A downloaded whole bid and what still needs a human before submission."""

    document_id: UUID
    output_path: str
    size_bytes: int = Field(ge=1)
    draft: bool
    placeholders: int = Field(ge=0)
    unconfirmed_rows: int = Field(ge=0)
    unconfirmed_chapters: int = Field(default=0, ge=0)
    copies: int = Field(ge=0)
