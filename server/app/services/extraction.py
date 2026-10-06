import hashlib
import re
import unicodedata
from collections import deque
from collections.abc import Iterable, Iterator
from functools import lru_cache

from app.core.errors import ServiceError
from app.schemas.contracts import Category, ExtractedRequirement, Extraction, Location, Source

PROMPT_VERSION = "req-v3"
EXTRACTION_VERSION = "exact-spans-v2"


QUOTES = str.maketrans(
    {"“": '"', "”": '"', "„": '"', "‟": '"', "‘": "'", "’": "'", "‚": "'", "‛": "'"}
)


def normalize(text: str) -> str:
    # Typographic differences only: width forms, curly quotes and whitespace. Applied
    # to quote and source alike, so a different word still fails the comparison.
    if text.isascii():
        return "".join(text.split())
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).translate(QUOTES))


@lru_cache(maxsize=128)
def normalized_spans(text: str) -> tuple[str, tuple[tuple[int, int], ...]]:
    """Map normalized characters back to whole original normalization units.

    Combining sequences and Hangul composition must stay together; compatibility
    expansions may map several output characters to the same original interval.
    """
    singleton_text: str | None = None
    if text.isascii():
        singleton_text = text
    elif (
        all(
            not unicodedata.combining(char) and unicodedata.normalize("NFKC", char) == char
            for char in set(text)
        )
        and unicodedata.normalize("NFKC", text) == text
    ):
        # Normalization is closed under substrings. With no combining characters,
        # an already-normalized source cannot join any adjacent original units.
        # Checking the whole source also excludes CCC=0 composition (e.g. Hangul).
        singleton_text = text.translate(QUOTES)
    if singleton_text is not None:
        return "".join(singleton_text.split()), tuple(
            (index, index + 1) for index, char in enumerate(text) if not char.isspace()
        )
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
    return locate_spans(text, (quote,))[quote]


def locate_source_citation_span(
    text: str, source_quote: str, citation_quote: str
) -> tuple[tuple[int, int] | None, str | None]:
    """Locate a citation inside its pinned Source, returning page/block offsets.

    Resolve the full Source with extraction's normalization and boundary preference.
    Resolve the citation only in that original slice, retaining the exact-uniqueness
    gate even when a repeated literal is inside a longer token. Never search outside
    the Source to repair a missing or ambiguous citation.
    """
    source_span, reason = locate_span(text, source_quote)
    if source_span is None:
        return None, reason
    source_start, source_end = source_span
    original = text[source_start:source_end]
    citation_span, reason = locate_span(original, citation_quote)
    if citation_span is None:
        return None, reason
    start, end = citation_span
    original_quote = original[start:end]
    first = original.find(original_quote)
    if original.find(original_quote, first + 1) >= 0:
        return None, "ambiguous_quote"
    return (source_start + start, source_start + end), None


class _SpanCandidates:
    """Keep only the counts and spans needed for the legacy ambiguity rule."""

    __slots__ = ("count", "first", "boundary_count", "boundary")

    def __init__(self) -> None:
        self.count = 0
        self.first: tuple[int, int] | None = None
        self.boundary_count = 0
        self.boundary: tuple[int, int] | None = None

    def add(self, text: str, start: int, end: int) -> None:
        if self.count < 2:
            self.count += 1
            self.first = (start, end)
        if self.boundary_count < 2 and at_boundary(text, start, end):
            self.boundary_count += 1
            self.boundary = (start, end)

    def result(self) -> tuple[tuple[int, int] | None, str | None]:
        if not self.count:
            return None, "quote_not_at_position"
        if self.count == 1:
            return self.first, None
        # Boundary preference applies only after multiple valid original spans.
        if self.boundary_count == 1:
            return self.boundary, None
        return None, "ambiguous_quote"


def _matching_offsets(haystack: str, needles: list[str]) -> Iterator[tuple[int, int]]:
    """Yield every (pattern index, start), including overlapping occurrences."""
    if len(needles) <= 16 or len(haystack) < 32_768:
        # C-level find wins on small sources or query sets; trie construction is
        # worthwhile when many quotes would repeatedly scan a long source.
        for pattern, needle in enumerate(needles):
            offset = haystack.find(needle)
            while offset != -1:
                yield pattern, offset
                offset = haystack.find(needle, offset + 1)
        return

    # Aho-Corasick shares one source scan across all distinct normalized quotes.
    transitions: list[dict[str, int]] = [{}]
    failures = [0]
    outputs: list[list[int]] = [[]]
    for pattern, needle in enumerate(needles):
        state = 0
        for char in needle:
            child = transitions[state].get(char)
            if child is None:
                child = len(transitions)
                transitions[state][char] = child
                transitions.append({})
                failures.append(0)
                outputs.append([])
            state = child
        outputs[state].append(pattern)
    pending = deque(transitions[0].values())
    while pending:
        state = pending.popleft()
        for char, child in transitions[state].items():
            pending.append(child)
            fallback = failures[state]
            while fallback and char not in transitions[fallback]:
                fallback = failures[fallback]
            failures[child] = transitions[fallback].get(char, 0)
            outputs[child].extend(outputs[failures[child]])
    state = 0
    for offset, char in enumerate(haystack):
        while state and char not in transitions[state]:
            state = failures[state]
        state = transitions[state].get(char, 0)
        for pattern in outputs[state]:
            yield pattern, offset - len(needles[pattern]) + 1


def locate_spans(
    text: str, quotes: Iterable[str], *, require_verbatim: bool = False
) -> dict[str, tuple[tuple[int, int] | None, str | None]]:
    """Locate a batch in one source, preserving scalar offsets and rejection reasons.

    The index lives for this call only. Normalized aliases share a search, while
    results retain each original quote as a key. Optional verbatim presence is
    checked independently of the span chosen by normalized boundary preference.
    """
    normalized = {quote: normalize(quote) for quote in dict.fromkeys(quotes)}
    if require_verbatim:
        raw_quotes = [quote for quote, needle in normalized.items() if needle]
        present = {raw_quotes[pattern] for pattern, _ in _matching_offsets(text, raw_quotes)}
        normalized = {
            quote: needle if quote in present else "" for quote, needle in normalized.items()
        }
    candidates = {needle: _SpanCandidates() for needle in normalized.values() if needle}
    if candidates:
        haystack, offsets = normalized_spans(text)
        needles = list(candidates)
        matches = list(candidates.values())
        ascii_text = text.isascii()
        for pattern, offset in _matching_offsets(haystack, needles):
            candidate = matches[pattern]
            if candidate.boundary_count == 2:
                continue
            needle = needles[pattern]
            start, end = offsets[offset][0], offsets[offset + len(needle) - 1][1]
            # A partial compatibility expansion has no corresponding original span.
            # ASCII units cannot expand or combine, so their offsets suffice.
            if ascii_text or normalize(text[start:end]) == needle:
                candidate.add(text, start, end)
    resolved = {needle: candidate.result() for needle, candidate in candidates.items()}
    return {
        quote: resolved.get(needle, (None, "quote_not_at_position"))
        for quote, needle in normalized.items()
    }


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
            for segment in re.finditer(r"[^；;\r\n]+", text):
                quote = segment.group().strip()
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
                    and (span := locate_span(text, item.source.quote)[0]) is not None
                    and segment.start() <= span[0] < span[1] <= segment.end()
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
