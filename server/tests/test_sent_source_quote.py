"""DB-free sent-source failures, recorded before the shared matcher change.

Summary/position text can duplicate or invent tender quotations; only the redacted
source segment is eligible. Repeated or overlapping literals inside that segment
must fail even when extraction prefers one word boundary. Normalization must keep
the original span, and redaction must not be bypassed by an unredacted source.
"""

import pytest
from app.services.extraction import locate_sent_source_quote, locate_source_citation_span


def test_summary_repeat_does_not_change_sent_source_or_pinned_offsets():
    source = "评分标准：技术方案满分为五分。"
    citation = "技术方案满分为五分"
    summary = f"摘要：{citation}，按完整性评分。"
    sent = f"要求摘要：\n{summary}\n招标原文：\n{source}"
    assert sent.count(citation) == 2

    quote, reason = locate_sent_source_quote(source, citation)
    assert (quote, reason) == (citation, None)
    prefix = "前文：不属于固定评分条款。\n"
    original = prefix + source
    start = len(prefix) + len("评分标准：")
    assert locate_source_citation_span(original, source, quote) == (
        (start, start + len(citation)),
        None,
    )


@pytest.mark.parametrize(
    "source,citation,reason",
    [
        ("技术方案得五分。", "摘要才有的商务要求", "quote_not_at_position"),
        ("技术方案得五分。", "招标原文：", "quote_not_at_position"),
        ("技术方案得五分。另列五分。", "五分", "ambiguous_quote"),
        ("评分：3.5分；5分。", "5分", "ambiguous_quote"),
        ("评分：哈哈哈。", "哈哈", "ambiguous_quote"),
        ("联系人：{{secret.contact}}。技术方案得五分。", "合成人名", "quote_not_at_position"),
        ("", "五分", "quote_not_at_position"),
    ],
)
def test_sent_source_rejects_ineligible_or_ambiguous_quotes(source, citation, reason):
    assert locate_sent_source_quote(source, citation) == (None, reason)


def test_sent_source_returns_contiguous_original_after_normalization():
    assert locate_sent_source_quote("技术方案得５ 分。", "得5分") == ("得５ 分", None)
