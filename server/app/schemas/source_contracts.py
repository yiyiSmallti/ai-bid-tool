"""Read-only views of a parsed tender: keyword search and bounded reads.

Offsets count characters of the redacted text that these views return, so a
`next_offset` or `line_offset` is only meaningful for a later `source read`.
"""

from uuid import UUID

from pydantic import Field

from app.schemas.contracts import Contract

SEARCH_MATCH_LIMIT = 50
SEARCH_BYTE_LIMIT = 20_000
LINE_CHARS = 400
READ_CHARS = 8000


class SourceSearchArguments(Contract):
    document: UUID
    query: str = Field(min_length=1, max_length=200, pattern=r"\S")
    limit: int = Field(default=SEARCH_MATCH_LIMIT, strict=True, ge=1, le=SEARCH_MATCH_LIMIT)


class SourceReadArguments(Contract):
    chunk: UUID
    block: str | None = Field(default=None, min_length=2, max_length=200)
    offset: int = Field(default=0, strict=True, ge=0)
    limit: int = Field(default=READ_CHARS, strict=True, ge=1, le=READ_CHARS)


class SourcePosition(Contract):
    """A PDF page chunk has `page`; a Word chunk has `block_id` and its heading label."""

    chunk_id: UUID
    seq: int = Field(ge=1)
    page: int | None = Field(default=None, ge=1)
    block_id: str | None = None
    label: str | None = None


class SourceMatch(SourcePosition):
    line_offset: int = Field(ge=0)
    line: str
    cut: bool = Field(description="The line was shortened around the match; read it in full")


class SourceSearchData(Contract):
    document_id: UUID
    query: str
    matches: int = Field(ge=0)
    truncated: bool = Field(description="More matches exist than the match or byte limit allows")


class SourceReadData(SourcePosition):
    document_id: UUID
    offset: int = Field(ge=0)
    length: int = Field(ge=0, description="Characters in the whole chunk or block")
    text: str
    next_offset: int | None = Field(default=None, ge=1)
