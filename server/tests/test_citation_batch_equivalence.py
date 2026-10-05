"""Batch citation regressions against a frozen, independent original predicate.

Failure inventory recorded before optimization: PDF/document/task bindings may be
lost, unverified chunks may pass, Word locations may match only part of a block,
normalized-only quotes may lose the verbatim gate, compatibility expansions may
accept partial units, combining/Hangul sequences may split, duplicate/overlapping
matches may lose ambiguity or boundary preference, and source grouping may depend
on UUID order or the original 128-entry normalization cache.
"""

import random
import re
import unicodedata
from functools import lru_cache
from types import SimpleNamespace
from uuid import UUID

import pytest
from app.services import extraction, response_cards

# Frozen from extraction.py and response_cards.py before batch optimization. Do
# not import their helpers here: production and oracle must fail independently.
FROZEN_QUOTES = str.maketrans(
    {"“": '"', "”": '"', "„": '"', "‟": '"', "‘": "'", "’": "'", "‚": "'", "‛": "'"}
)
FROZEN_SEPARATORS = frozenset("；;，,。：:、！!？?（）()【】[]《》“”‘’\"'")


def frozen_normalize(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).translate(FROZEN_QUOTES))


@lru_cache(maxsize=128)
def frozen_normalized_spans(text):
    units = []
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
        part = frozen_normalize(text[start:end])
        parts.append(part)
        offsets.extend([(start, end)] * len(part))
    return "".join(parts), tuple(offsets)


def frozen_at_boundary(text, start, end):
    def separates(char):
        return char.isspace() or char in FROZEN_SEPARATORS

    return (start == 0 or separates(text[start - 1])) and (end == len(text) or separates(text[end]))


def frozen_locate_span(text, quote):
    needle = frozen_normalize(quote)
    if not needle:
        return None, "quote_not_at_position"
    haystack, offsets = frozen_normalized_spans(text)
    spans = []
    offset = haystack.find(needle)
    while offset != -1:
        start, end = offsets[offset][0], offsets[offset + len(needle) - 1][1]
        if frozen_normalize(text[start:end]) == needle:
            spans.append((start, end))
        offset = haystack.find(needle, offset + 1)
    if len(spans) > 1:
        spans = [span for span in spans if frozen_at_boundary(text, *span)] or spans
    if not spans:
        return None, "quote_not_at_position"
    if len(spans) > 1:
        return None, "ambiguous_quote"
    return spans[0], None


def frozen_citation_valid_in_chunk(requirement, chunk):
    if (
        chunk is None
        or not chunk.citation_verified
        or chunk.task_id != requirement.task_id
        or chunk.document_id != requirement.document_id
    ):
        return False
    if requirement.page is not None:
        return (
            chunk.page == requirement.page
            and requirement.location is None
            and requirement.quote in chunk.text
            and frozen_locate_span(chunk.text, requirement.quote)[0] is not None
        )
    if not requirement.location or not chunk.blocks:
        return False
    return any(
        {key: value for key, value in block.items() if key != "text"} == requirement.location
        and requirement.quote in block["text"]
        and frozen_locate_span(block["text"], requirement.quote)[0] is not None
        for block in chunk.blocks
    )


def synthetic_page_sources(count, chunk_count, *, characters=6000):
    """Shared synthetic page workload, with each quote present on exactly one page."""
    if chunk_count < 1:
        raise ValueError("chunk_count must be positive")
    quotes = [f"Synthetic located requirement {index:05d}." for index in range(count)]
    pages = []
    for page in range(chunk_count):
        body = "\n".join(quotes[page::chunk_count]) or "Synthetic empty extraction source."
        padding = f"\n第 {page + 1} 页采购范围、交付安排、实施步骤及验收说明。"
        if len(body) < characters:
            body += (padding * ((characters - len(body)) // len(padding) + 1))[
                : characters - len(body)
            ]
        pages.append(body)
    return quotes, pages


def span_corpus():
    cases = [
        ("", ["", "quote", " \t\n\u3000"]),
        ("source", ["", " \t", "missing", "source"]),
        (" \t\n\u3000", [" \t", ""]),
        ("3.5mm插孔：≥2个；5mm插孔：≥2个", ["5mm插孔：≥2个", "插孔", "≥2个"]),
        ("A-1;B/1;C.1;1", ["1", "A-1", "B/1", "C.1"]),
        ("Memory:16 GB;Memory:16GB", ["Memory:16GB", "Memory:16 GB", "16GB"]),
        ("fi;ﬁ;xﬁ;f;ﬃ", ["f", "i", "fi", "ffi", "ﬁ", "ﬃ"]),
        ("ﬁ", ["fi", "f", "i", "ﬁ"]),
        ("㍍;メートル", ["メートル", "メート", "㍍"]),
        ("e\u0301;e", ["e", "é", "e\u0301"]),
        ("q\u0307\u0323", ["q\u0323\u0307", "q\u0307\u0323", "q", "\u0323"]),
        ("\u1100\u1161\u11a8;각", ["각", "가", "\u1100\u1161\u11a8"]),
        ("\u1100\u1161\u11a8", ["각", "가", "\u1100"]),
        ("①;1", ["1", "①"]),
        ("aaaa", ["a", "aa", "aaa", "aaaa"]),
        ("xABY; A B", ["AB", "A B", "xABY"]),
        ("前缀“ＡＢＣ”后缀；‘ＡＢＣ’", ['"ABC"', "'ABC'", "ＡＢＣ", "ABC"]),
        ("甲 \n乙", ["甲乙", "甲 \n乙", " \n甲乙\t "]),
        ("甲；中间；乙", ["甲；乙", "甲乙"]),
        ("a\x1cb\x1dc\x1ed\x1fe", ["abcde", "a", "b", "c", "d", "e"]),
        ("a \u0301b;ab", ["ab", "a \u0301b", "a", "\u0301"]),
    ]
    tokens = ("内存：≥16 GB", "ＡＢＣ１２３", "e\u0301", "\u1100\u1161\u11a8", "ﬃ", "㍍")
    separators = sorted(FROZEN_SEPARATORS) + [
        "\t",
        "\n",
        "\r",
        "\v",
        "\f",
        "\x1c",
        "\x1d",
        "\x1e",
        "\x1f",
        "\u0085",
        "\u00a0",
        "\u1680",
        "\u2000",
        "\u2001",
        "\u2002",
        "\u2003",
        "\u2004",
        "\u2005",
        "\u2006",
        "\u2007",
        "\u2008",
        "\u2009",
        "\u200a",
        "\u2028",
        "\u2029",
        "\u202f",
        "\u205f",
        "\u3000",
    ]
    for token in tokens:
        quotes = [token, frozen_normalize(token), "missing", "", token]
        for separator in separators:
            cases.append(("前" + token + "后" + separator + token + separator + "末", quotes))
    for index in range(48):
        ordinary = f"参数{index:03d}:16GB"
        width = ordinary.translate(str.maketrans("0123456789:GB", "０１２３４５６７８９：ＧＢ"))
        cases.extend(
            [
                (ordinary + "；" + width, [ordinary, width]),
                ("前" + width + "；" + ordinary, [ordinary, width]),
            ]
        )
    for exceptional in ("\u0f73", "\u0f75", "\u0f81", "\uff9e", "\uff9f"):
        for base in ("a", "e", "q"):
            for accent in ("\u0301", "\u0323"):
                token = base + exceptional + accent
                cases.append((token, [token, frozen_normalize(token), base, exceptional]))
    for token in ("ｶﾞ", "ﾊﾟ", "ｶﾞ\u0301", "ﾊﾟ\u0323"):
        cases.append((token, [token, frozen_normalize(token), token[0]]))
    return cases


@pytest.mark.parametrize("text,quotes", span_corpus())
def test_bulk_spans_match_frozen_original(text, quotes):
    expected = {quote: frozen_locate_span(text, quote) for quote in quotes}
    assert extraction.locate_spans(text, iter(quotes)) == expected
    assert extraction.locate_spans(text, quotes, require_verbatim=True) == {
        quote: result if quote in text else (None, "quote_not_at_position")
        for quote, result in expected.items()
    }
    assert {quote: extraction.locate_span(text, quote) for quote in quotes} == expected
    assert extraction.normalized_spans(text) == frozen_normalized_spans(text)


def requirement(chunk, quote, **changes):
    values = {
        "id": UUID(int=random.Random(f"{chunk.id}:{quote}:{changes}").getrandbits(128)),
        "chunk_id": chunk.id,
        "task_id": chunk.task_id,
        "document_id": chunk.document_id,
        "page": chunk.page,
        "location": None,
        "quote": quote,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def chunk(identifier, text, **changes):
    values = {
        "id": UUID(int=identifier),
        "task_id": UUID(int=1),
        "document_id": UUID(int=2),
        "page": 1,
        "text": text,
        "citation_verified": True,
        "blocks": None,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_batch_predicate_retains_pdf_and_word_rejections():
    pdf = chunk(100, "Memory:16 GB;Memory:16GB；ﬁ；f； \t")
    location = {
        "block_id": "p-1",
        "section_path": ["Technical"],
        "paragraph_index": 1,
        "label": "Technical paragraph 1",
    }
    word = chunk(101, "", page=None, blocks=[{**location, "text": "提供原厂证明；ﬁ；f； \t"}])
    chunks = {item.id: item for item in (pdf, word)}
    requirements = [
        requirement(pdf, "Memory:16GB"),  # Exact spelling still has normalized ambiguity.
        requirement(pdf, "f"),
        requirement(pdf, "ﬁ"),
        requirement(pdf, ""),
        requirement(pdf, " \t"),
        requirement(pdf, "absent"),
        requirement(pdf, "f", page=2),
        requirement(pdf, "f", location=location),
        requirement(pdf, "f", task_id=UUID(int=3)),
        requirement(pdf, "f", document_id=UUID(int=4)),
        requirement(pdf, "f", chunk_id=UUID(int=404)),
        requirement(word, "提供原厂证明", location=location),
        requirement(word, "ﬁ", location=location),
        requirement(word, "f", location=location),
        requirement(word, "fi", location=location),  # Normalized-only text is not verbatim.
        requirement(word, "", location=location),
        requirement(word, " \t", location=location),
        requirement(word, "提供原厂证明", location={**location, "paragraph_index": 2}),
        requirement(word, "提供原厂证明", location={"block_id": "p-1"}),
        requirement(word, "提供原厂证明", location={**location, "extra": True}),
        requirement(word, "提供原厂证明", location=None),
    ]
    unverified = chunk(102, "visible", citation_verified=False)
    no_blocks = chunk(103, "visible", page=None)
    empty_blocks = chunk(104, "visible", page=None, blocks=[])
    for source in (unverified, no_blocks, empty_blocks):
        chunks[source.id] = source
        requirements.append(
            requirement(source, "visible", location=None if source.page else location)
        )
    expected = {
        item.id: frozen_citation_valid_in_chunk(item, chunks.get(item.chunk_id))
        for item in requirements
    }
    assert any(expected.values()) and not all(expected.values())
    assert response_cards.citation_validity_batch(requirements, chunks) == expected
    assert all(
        response_cards.citation_valid_in_chunk(item, chunks.get(item.chunk_id)) == expected[item.id]
        for item in requirements
    )


@pytest.mark.parametrize(
    "text,quote,valid",
    [
        ("xABY; A B", "AB", True),
        ("ﬁ", "fi", False),
        ("ﬁ", "ﬁ", True),
        ("ﬁ", "f", False),
        ("fi;ﬁ", "fi", False),
        ("Memory:16 GB;Memory:16GB", "Memory:16GB", False),
        ("3.5mm插孔：≥2个；5mm插孔：≥2个", "5mm插孔：≥2个", True),
        ("aaaa", "aa", False),
        ("e\u0301", "e\u0301", True),
        ("e\u0301", "é", False),
        ("q\u0307\u0323", "q\u0307\u0323", True),
        ("\u1100\u1161\u11a8", "\u1100\u1161\u11a8", True),
        ("\u1100\u1161\u11a8", "가", False),
        ("ＡＢＣ", "ＡＢＣ", True),
        ("“ＡＢＣ”", '"ABC"', False),
        ("", "", False),
        (" \t", " \t", False),
    ],
)
def test_explicit_verbatim_and_uniqueness_acceptance(text, quote, valid):
    source = chunk(200, text)
    item = requirement(source, quote)
    assert frozen_citation_valid_in_chunk(item, source) is valid
    assert response_cards.citation_validity_batch([item], {source.id: source}) == {item.id: valid}


def test_batch_predicate_is_equivalent_above_cache_capacity_in_shuffled_uuid_order():
    rng = random.Random(1729)
    chunks = {}
    requirements = []
    for index in range(257):
        source = chunk(1000 + index, f"参数{index}:１６ＧＢ；精确要求{index}；ﬁ；f")
        source.id = UUID(int=rng.getrandbits(128))
        chunks[source.id] = source
        requirements.extend(
            [
                requirement(source, f"精确要求{index}"),
                requirement(source, f"参数{index}:16GB"),
                requirement(source, f"参数{index}:１６ＧＢ"),
                requirement(source, "f"),
                requirement(source, "ﬁ"),
                requirement(source, "", id=UUID(int=rng.getrandbits(128))),
            ]
        )
    rng.shuffle(requirements)
    expected = {
        item.id: frozen_citation_valid_in_chunk(item, chunks.get(item.chunk_id))
        for item in requirements
    }
    assert response_cards.citation_validity_batch(requirements, chunks) == expected
    rng.shuffle(requirements)
    assert (
        response_cards.citation_validity_batch(requirements, dict(reversed(list(chunks.items()))))
        == expected
    )


def test_empty_batches_and_many_prefix_patterns_match_original():
    assert response_cards.citation_validity_batch([], {}) == {}
    assert extraction.locate_spans("", []) == {}
    text = "Source context. " * 3000 + ";".join(f"prefix-{index:03d}-suffix" for index in range(96))
    quotes = ["prefix", "suffix", "", " ", *[f"prefix-{index:03d}-suffix" for index in range(96)]]
    assert extraction.locate_spans(text, quotes) == {
        quote: frozen_locate_span(text, quote) for quote in quotes
    }


def test_many_unicode_patterns_preserve_normalized_aliases_and_partial_units():
    text = "采购实施范围说明。" * 4500 + "；".join(
        f"参数{index:03d}:１６ＧＢ；e\u0301；ﬃ" for index in range(48)
    )
    quotes = ["e", "é", "e\u0301", "f", "fi", "ffi", "ﬃ", "", " "]
    for index in range(48):
        quotes.extend([f"参数{index:03d}:１６ＧＢ", f"参数{index:03d}:16GB"])
    expected = {quote: frozen_locate_span(text, quote) for quote in quotes}
    assert extraction.locate_spans(text, quotes) == expected
    assert extraction.locate_spans(text, quotes, require_verbatim=True) == {
        quote: result if quote in text else (None, "quote_not_at_position")
        for quote, result in expected.items()
    }
