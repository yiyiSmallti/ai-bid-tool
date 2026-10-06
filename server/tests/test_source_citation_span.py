"""DB-free pinned-source location failures, using synthetic Chinese text.

Failure inventory: another page/block sentence shadows the cited occurrence;
the citation repeats inside its Source (including overlapping/larger-token hits);
only out-of-Source text matches; the Source itself is missing or ambiguous; or
normalization loses original offsets. Source boundary preference must survive.
"""

import pytest
from app.services.extraction import locate_source_citation_span


@pytest.mark.parametrize("separator", ["。", "\n"], ids=["pdf-page", "docx-block"])
def test_repeated_sentence_outside_source_maps_to_pinned_occurrence(separator):
    citation = "技术方案满分为五分"
    source = f"评分标准{separator}{citation}{separator}按完整性评分"
    prefix = f"前文说明{separator}{citation}{separator}"
    original = prefix + source + separator + "后续说明"

    span, reason = locate_source_citation_span(original, source, citation)

    start = len(prefix) + len(f"评分标准{separator}")
    assert (span, reason) == ((start, start + len(citation)), None)
    assert span is not None and original[slice(*span)] == citation


@pytest.mark.parametrize(
    "original,source,citation,reason",
    [
        ("评分标准：五分；五分。", "评分标准：五分；五分。", "五分", "ambiguous_quote"),
        ("评分标准：3.5分；5分。", "评分标准：3.5分；5分。", "5分", "ambiguous_quote"),
        ("评分标准：哈哈哈。", "评分标准：哈哈哈。", "哈哈", "ambiguous_quote"),
        ("技术得五分。商务得十分。", "技术得五分。", "商务得十分", "quote_not_at_position"),
        ("评分：五分。\n评分：五分。", "评分：五分。", "五分", "ambiguous_quote"),
        ("技术得五分。", "技术得十分。", "技术", "quote_not_at_position"),
        ("评分：五分。", "评分：五分。", "", "quote_not_at_position"),
        ("评分：五分。", "", "五分", "quote_not_at_position"),
    ],
    ids=[
        "citation-repeated",
        "citation-repeated-inside-token",
        "citation-overlapping",
        "citation-outside-source",
        "source-ambiguous",
        "source-missing",
        "citation-empty",
        "source-empty",
    ],
)
def test_invalid_source_or_citation_preserves_reason(original, source, citation, reason):
    assert locate_source_citation_span(original, source, citation) == (None, reason)


def test_source_uses_extraction_boundary_preference():
    source = "技术评分：五分"
    prefix = f"补充{source}说明；"
    original = prefix + source + "。"

    start = len(prefix) + len("技术评分：")
    assert locate_source_citation_span(original, source, "五分") == (
        (start, start + len("五分")),
        None,
    )


def test_offsets_use_contiguous_original_units_after_normalization():
    original = "前文：５分。\n评分标准：技术５ 分；说明。"
    source = "评分标准：技术5分；说明。"
    start = original.index("５ 分")

    span, reason = locate_source_citation_span(original, source, "5分")

    assert (span, reason) == ((start, start + len("５ 分")), None)
    assert span is not None and original[slice(*span)] == "５ 分"
