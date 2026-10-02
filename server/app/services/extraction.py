import hashlib
import re
import unicodedata
from functools import lru_cache

from app.core.errors import ServiceError
from app.schemas.contracts import Category, ExtractedRequirement, Extraction, Location, Source

PROMPT_VERSION = "req-v3"
EXTRACTION_VERSION = "exact-spans-v1"


QUOTES = str.maketrans(
    {"“": '"', "”": '"', "„": '"', "‟": '"', "‘": "'", "’": "'", "‚": "'", "‛": "'"}
)


def normalize(text: str) -> str:
    # Typographic differences only: width forms, curly quotes and whitespace. Applied
    # to quote and source alike, so a different word still fails the comparison.
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).translate(QUOTES))


@lru_cache(maxsize=128)
def normalized_spans(text: str) -> tuple[str, tuple[tuple[int, int], ...]]:
    """Map normalized characters back to whole original normalization units.

    Combining sequences and Hangul composition must stay together; compatibility
    expansions may map several output characters to the same original interval.
    """
    units: list[tuple[int, int]] = []
    start = 0
    for index in range(1, len(text)):
        previous, char = text[start:index], text[index]
        if unicodedata.combining(char) or unicodedata.normalize(
            "NFKC", previous + char
        ) != unicodedata.normalize("NFKC", previous) + unicodedata.normalize("NFKC", char):
            continue
        units.append((start, index))
        start = index
    if text:
        units.append((start, len(text)))
    parts, offsets = [], []
    for start, end in units:
        part = normalize(text[start:end])
        parts.append(part)
        offsets.extend([(start, end)] * len(part))
    return "".join(parts), tuple(offsets)


def locate_quote(text: str, quote: str) -> tuple[str | None, str | None]:
    """Return the unique contiguous original span, never a normalized substitute."""
    span, reason = locate_span(text, quote)
    return (text[span[0] : span[1]], None) if span else (None, reason)


def locate_span(text: str, quote: str) -> tuple[tuple[int, int] | None, str | None]:
    """Offsets of the one original span the quote cites, or the rejection reason."""
    needle = normalize(quote)
    if not needle:
        return None, "quote_not_at_position"
    haystack, offsets = normalized_spans(text)
    spans = []
    offset = haystack.find(needle)
    while offset != -1:
        start, end = offsets[offset][0], offsets[offset + len(needle) - 1][1]
        # A match inside a compatibility expansion (e.g. f inside ﬁ) has no
        # corresponding original span and must not be accepted.
        if normalize(text[start:end]) == needle:
            spans.append((start, end))
        offset = haystack.find(needle, offset + 1)
    if len(spans) > 1:
        # "5mm插孔" also occurs inside "3.5mm插孔"; the occurrence that starts and ends at a
        # segment boundary is the one cited. Several such occurrences stay ambiguous.
        spans = [span for span in spans if at_boundary(text, *span)] or spans
    if not spans:
        return None, "quote_not_at_position"
    if len(spans) > 1:
        return None, "ambiguous_quote"
    return spans[0], None


SEGMENT_SEPARATORS = frozenset("；;，,。：:、！!？?（）()【】[]《》“”‘’\"'")


def at_boundary(text: str, start: int, end: int) -> bool:
    # "." "-" and "/" also occur inside numbers and model names (3.5mm, A-1), so only
    # whitespace and list or sentence punctuation separate segments.
    def separates(char: str) -> bool:
        return char.isspace() or char in SEGMENT_SEPARATORS

    return (start == 0 or separates(text[start - 1])) and (end == len(text) or separates(text[end]))


def location_of(block: dict) -> Location:
    return Location.model_validate({key: block.get(key) for key in Location.model_fields})


def block_of(chunk: dict, block_id: str) -> dict | None:
    return next((b for b in chunk.get("blocks") or [] if b["block_id"] == block_id), None)


def source_text(source: Source, chunk: dict | None) -> str | None:
    if not chunk or str(source.document_id) != str(chunk["document_id"]):
        return None
    if not chunk["citation_verified"]:
        return None
    if source.location is not None:
        block = block_of(chunk, source.location.block_id)
        # The whole location must match the parsed block, and the quote must sit inside it.
        return block["text"] if block and source.location == location_of(block) else None
    return chunk["text"] if (not chunk.get("blocks") and source.page == chunk["page"]) else None


def cited(item: ExtractedRequirement, chunk: dict | None) -> bool:
    text = source_text(item.source, chunk)
    return text is not None and locate_quote(text, item.source.quote)[0] is not None


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
        text = source_text(item.source, available.get(str(item.source.chunk_id)))
        quote, reason = (
            locate_quote(text, item.source.quote)
            if text is not None
            else (None, "quote_not_at_position")
        )
        if quote is not None:
            kept.append(
                item.model_copy(
                    update={
                        "source": item.source.model_copy(update={"quote": quote}),
                        "model_quote": item.model_quote
                        if item.model_quote is not None
                        else item.source.quote,
                    }
                )
            )
        else:
            assert reason is not None
            rejected.append(
                {
                    "position": position_label(item),
                    "quote": item.source.quote[:200],
                    "reason": reason,
                }
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
            for line in re.split(r"[；;\r\n]+", text):
                quote = line.strip()
                if (
                    not quote
                    or not re.search(r"[★☆]|实质性要求|否决投标|废标", quote)
                    # A colon with no following content introduces requirements, but is
                    # not itself one (for example, ★3.合同的终止：).
                    or re.fullmatch(r"[^:：；;。！？]+[:：]", quote)
                ):
                    continue
                matching = [
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
                    and normalize(item.source.quote) in normalize(quote)
                ]
                if matching:
                    for item in matching:
                        item.starred = True
                    continue
                # Repeated identical source clauses have no unique citation span.
                if locate_quote(text, quote)[0] is None:
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
