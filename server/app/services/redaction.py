"""Versioned outbound-only masking. Neither matches nor original text are logged."""

import re
import unicodedata
from collections import Counter

RULE_VERSION = "bid-redaction-v1"
KINDS = ("amount", "contact", "identity", "bank_account")
PLACEHOLDER = re.compile(r"\[REDACTED_[A-Z_]+\]")

# Match on NFKC text, mapping offsets back to the untouched original. Labelled
# amounts also cover written-out prices; unlabelled numeric currency is explicit.
RULES = (
    ("identity", re.compile(r"(?<!\d)\d{17}[\dXx](?!\w)|(?<!\d)\d{15}(?!\d)")),
    ("bank_account", re.compile(r"(?<!\d)(?:\d[ -]?){15,18}\d(?!\d)")),
    (
        "contact",
        re.compile(
            r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)|(?<!\d)(?:\+?\d{1,3}[- ])?0\d{2,3}[- ]?\d{7,8}(?:[- ](?:转|ext\.?)[- ]?\d+)?(?!\d)",
            re.I,
        ),
    ),
    ("contact", re.compile(r"(?<!\w)\+?\d{1,3}[- .]?\(?\d{3}\)?[- .]\d{3}[- .]\d{4}(?!\d)")),
    (
        "identity",
        re.compile(
            r"(?:身份证(?:号码?|号)?|identity\s*(?:number|no\.?|id))\s*[:：=]?\s*(?P<value>[^\n\r;；,，。]+)",
            re.I,
        ),
    ),
    (
        "bank_account",
        re.compile(
            r"(?:银行账号|银行账户|银行帐号|银行卡号|收款账号|账号|帐号|bank\s*account(?:\s*(?:number|no\.?))?|IBAN)\s*[:：=]?\s*(?P<value>[^\n\r;；,，。]+)",
            re.I,
        ),
    ),
    (
        "contact",
        re.compile(
            r"(?:联系人|联系人员|联络人|项目联系人|contact(?:\s*(?:person|name))?)\s*[:：=]?\s*(?P<value>[^\n\r;；,，。:：]+)",
            re.I,
        ),
    ),
    (
        "contact",
        re.compile(
            r"(?:联系电话|手机号码?|手机号|电话|手机|telephone|phone|mobile|tel\.?)\s*[:：=]?\s*(?P<value>[+\d(][\d() +.\-转extEXT分机]*)",
            re.I,
        ),
    ),
    (
        "amount",
        re.compile(
            r"(?:报价|投标价|投标总价|总报价|金额|价格|单价|总价|合同价|预算|quote\s*price|quoted?\s*(?:price|amount)|amount|price|budget)\s*[:：=]?\s*(?P<value>[^\n\r;；。]+)",
            re.I,
        ),
    ),
    (
        "amount",
        re.compile(
            r"(?:[¥￥$€£]|\b(?:RMB|CNY|USD|EUR|GBP)\s*)\s*[\d,]+(?:\.\d+)?(?:\s*[万亿])?|[\d,]+(?:\.\d+)?\s*(?:万?元|亿?元|人民币|美元|美金|欧元|RMB\b|CNY\b|USD\b|EUR\b|GBP\b)",
            re.I,
        ),
    ),
)


def redact(value: str, enabled: bool) -> tuple[str, dict[str, int]]:
    counts = Counter({kind: 0 for kind in KINDS})
    normalized, offsets = [], []
    for index, char in enumerate(value):
        part = unicodedata.normalize("NFKC", char)
        normalized.append(part)
        offsets.extend([index] * len(part))
    text = "".join(normalized)
    matches: list[tuple[int, int, str]] = []
    for kind, pattern in RULES:
        for match in pattern.finditer(text):
            start, end = match.span("value") if "value" in pattern.groupindex else match.span()
            if start == end:
                continue
            start, end = offsets[start], offsets[end - 1] + 1
            # Union overlapping detections: a broad labelled value must not leave
            # a sensitive suffix exposed because a numeric sub-pattern matched first.
            counts[kind] += 1
            matches.append((start, end, kind))
    if not enabled:
        return value, dict(counts)
    merged: list[tuple[int, int, set[str]]] = []
    for start, end, kind in sorted(matches):
        if merged and start < merged[-1][1]:
            old_start, old_end, kinds = merged[-1]
            merged[-1] = old_start, max(end, old_end), kinds | {kind}
        else:
            merged.append((start, end, {kind}))
    parts, cursor = [], 0
    for start, end, kinds in merged:
        parts.extend((value[cursor:start], "[REDACTED_" + "_".join(sorted(kinds)).upper() + "]"))
        cursor = end
    parts.append(value[cursor:])
    return "".join(parts), dict(counts)


def redact_tree(value, enabled: bool):
    """Mask every string leaf, including document location labels and section paths."""
    counts = Counter({kind: 0 for kind in KINDS})

    def visit(item):
        if isinstance(item, str):
            sent, hits = redact(item, enabled)
            counts.update(hits)
            return sent
        if isinstance(item, dict):
            return {key: visit(child) for key, child in item.items()}
        if isinstance(item, list):
            return [visit(child) for child in item]
        return item

    sent = visit(value)
    return sent, dict(counts)
