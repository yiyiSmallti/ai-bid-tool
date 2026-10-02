import hashlib
import re
import unicodedata

from app.core.errors import ServiceError
from app.schemas.contracts import Category, ExtractedRequirement, Extraction, Location, Source

PROMPT_VERSION = "req-v2"


QUOTES = str.maketrans(
    {"“": '"', "”": '"', "„": '"', "‟": '"', "‘": "'", "’": "'", "‚": "'", "‛": "'"}
)


def normalize(text: str) -> str:
    # Typographic differences only: width forms, curly quotes and whitespace. Applied
    # to quote and source alike, so a different word still fails the comparison.
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).translate(QUOTES))


def location_of(block: dict) -> Location:
    return Location.model_validate({key: block.get(key) for key in Location.model_fields})


def block_of(chunk: dict, block_id: str) -> dict | None:
    return next((b for b in chunk.get("blocks") or [] if b["block_id"] == block_id), None)


def cited(item: ExtractedRequirement, chunk: dict | None) -> bool:
    source = item.source
    if not chunk or str(source.document_id) != str(chunk["document_id"]):
        return False
    if not chunk["citation_verified"]:
        return False
    if source.location is not None:
        block = block_of(chunk, source.location.block_id)
        # The whole location must match the parsed block, and the quote must sit inside it.
        return (
            block is not None
            and source.location == location_of(block)
            and normalize(source.quote) in normalize(block["text"])
        )
    return (
        not chunk.get("blocks")
        and source.page == chunk["page"]
        and normalize(source.quote) in normalize(chunk["text"])
    )


def position_label(item: ExtractedRequirement) -> str:
    location = item.source.location
    return location.label if location else f"第 {item.source.page} 页"


def split_cited(
    extraction: Extraction, chunks: list[dict]
) -> tuple[Extraction, list[dict[str, str]]]:
    """Keep items whose quote sits at the cited position; report the rest instead of saving them."""
    available = {str(chunk["id"]): chunk for chunk in chunks}
    kept, rejected = [], []
    for item in extraction.items:
        if cited(item, available.get(str(item.source.chunk_id))):
            kept.append(item)
        else:
            rejected.append(
                {
                    "position": position_label(item),
                    "quote": item.source.quote[:200],
                    "reason": "quote_not_at_position",
                }
            )
    if extraction.items and not kept:
        raise ServiceError(
            "invalid_citation",
            "No extracted requirement cited its source verbatim; nothing was saved",
            400,
            4,
        )
    return Extraction(items=kept), rejected


def validate_extraction(extraction: Extraction, chunks: list[dict]) -> None:
    available = {str(chunk["id"]): chunk for chunk in chunks}
    for item in extraction.items:
        if not cited(item, available.get(str(item.source.chunk_id))):
            raise ServiceError(
                "invalid_citation",
                "Provider returned an unverified source citation; no requirements were saved",
                400,
                4,
            )


def merge_starred(extraction: Extraction, chunks: list[dict]) -> Extraction:
    items = list(extraction.items)
    for chunk in chunks:
        units = (
            [(block["text"], block) for block in chunk["blocks"]]
            if chunk.get("blocks")
            else [(chunk["text"], None)]
        )
        for text, block in units:
            for line in text.splitlines():
                quote = line.strip()
                if not quote or not re.search(r"[★☆]|实质性要求|否决投标|废标", quote):
                    continue
                matching = next(
                    (
                        item
                        for item in items
                        if item.source.chunk_id == chunk["id"]
                        and (
                            block is None
                            or (
                                item.source.location is not None
                                and item.source.location.block_id == block["block_id"]
                            )
                        )
                        # Same requirement when either quote contains the other.
                        and (
                            normalize(quote) in normalize(item.source.quote)
                            or normalize(item.source.quote) in normalize(quote)
                        )
                    ),
                    None,
                )
                if matching:
                    matching.starred = True
                    continue
                location = location_of(block) if block else None
                items.append(
                    ExtractedRequirement(
                        category=Category.substantive,
                        starred=True,
                        text=quote,
                        source=Source(
                            document_id=chunk["document_id"],
                            chunk_id=chunk["id"],
                            quote=quote,
                            page=None if block else chunk["page"],
                            location=location,
                        ),
                    )
                )
    return Extraction(items=items)


def fingerprint(item: ExtractedRequirement) -> str:
    position = item.source.location.block_id if item.source.location else item.source.page
    return hashlib.sha256(
        f"{item.source.document_id}:{position}:{normalize(item.source.quote)}".encode()
    ).hexdigest()
