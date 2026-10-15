"""Keyword search and bounded reads over one parsed tender document.

Every text is redacted with the task's confidential library before it is
searched, so neither a match count nor an offset reveals a masked value.
"""

import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import not_found
from app.core.security import Secrets
from app.models.entities import Chunk
from app.schemas.source_contracts import (
    LINE_CHARS,
    SEARCH_BYTE_LIMIT,
    SourceMatch,
    SourceReadData,
    SourceSearchData,
)
from app.services import confidential, redaction
from app.services.auth import Identity
from app.services.task_authorization import task_authorized


async def masker(session: AsyncSession, task_id: UUID, settings):
    entries = await confidential.task_entries(session, task_id)
    library = confidential.library(entries, Secrets.for_data(settings))
    return lambda text: redaction.redact(text, True, library)[0]


def units(chunk: Chunk):
    """(block_id, label, text) per addressable unit: the page, or each Word block."""
    if chunk.blocks is None:
        return [(None, None, chunk.text)]
    return [(block["block_id"], block["label"], block["text"]) for block in chunk.blocks]


def cut_line(line: str, start: int) -> tuple[str, bool]:
    if len(line) <= LINE_CHARS:
        return line, False
    begin = max(0, min(start - LINE_CHARS // 3, len(line) - LINE_CHARS))
    end = begin + LINE_CHARS
    return ("…" if begin else "") + line[begin:end] + ("…" if end < len(line) else ""), True


@task_authorized("task:read", parent=("document_id", "documents"))
async def search(
    session: AsyncSession,
    identity: Identity,
    document_id: UUID,
    query: str,
    limit: int,
    settings,
) -> tuple[dict, list[dict]]:
    """Lines that contain every whitespace-separated term, in document order."""
    identity.require("task:read")
    terms = [term.casefold() for term in query.split()]
    chunks = (
        await session.scalars(
            select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.seq)
        )
    ).all()
    mask = await masker(session, chunks[0].task_id, settings) if chunks else None
    items: list[dict] = []
    size, truncated = 0, False
    for chunk in chunks:
        assert mask is not None
        for block_id, label, raw in units(chunk):
            offset = 0
            for line in mask(raw).split("\n"):
                folded = line.casefold()
                if terms and all(term in folded for term in terms):
                    if len(items) >= limit:
                        truncated = True
                        break
                    shown, cut = cut_line(line, folded.find(terms[0]))
                    item = SourceMatch(
                        chunk_id=chunk.id,
                        seq=chunk.seq,
                        page=chunk.page,
                        block_id=block_id,
                        label=mask(label) if label else None,
                        line_offset=offset,
                        line=shown,
                        cut=cut,
                    ).model_dump(mode="json")
                    weight = len(json.dumps(item, ensure_ascii=False).encode())
                    if size + weight > SEARCH_BYTE_LIMIT:
                        truncated = True
                        break
                    size += weight
                    items.append(item)
                offset += len(line) + 1
            if truncated:
                break
        if truncated:
            break
    data = SourceSearchData(
        document_id=document_id, query=query, matches=len(items), truncated=truncated
    )
    return data.model_dump(mode="json"), items


@task_authorized("task:read", parent=("chunk_id", "chunks"))
async def read(
    session: AsyncSession,
    identity: Identity,
    chunk_id: UUID,
    block: str | None,
    offset: int,
    limit: int,
    settings,
) -> dict:
    identity.require("task:read")
    chunk = await session.get(Chunk, chunk_id)
    if chunk is None:
        raise not_found()
    if block is None:
        block_id, label, raw = None, None, chunk.text
    else:
        found = [unit for unit in units(chunk) if unit[0] == block]
        if not found:
            raise not_found()
        block_id, label, raw = found[0]
    mask = await masker(session, chunk.task_id, settings)
    text = mask(raw)
    offset = min(offset, len(text))
    end = min(len(text), offset + limit)
    return SourceReadData(
        chunk_id=chunk.id,
        seq=chunk.seq,
        page=chunk.page,
        block_id=block_id,
        label=mask(label) if label else None,
        document_id=chunk.document_id,
        offset=offset,
        length=len(text),
        text=text[offset:end],
        next_offset=end if end < len(text) else None,
    ).model_dump(mode="json")
