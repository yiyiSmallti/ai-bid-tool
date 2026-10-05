"""Versioned outbound-only masking. Neither matches nor original text are logged."""

import re
import unicodedata
from collections import Counter

RULE_VERSION = "bid-redaction-v2"
KINDS = ("amount", "contact", "identity", "bank_account")
PLACEHOLDER = re.compile(r"\[REDACTED_[A-Z_]+\]")

# Match on NFKC text, mapping offsets back to the untouched original. A label
# alone is not a hit: identity, account and phone values must be number-shaped,
# and contact names need an explicit separator, so tender wording such as
# "刷身份证登录" or "管理员账号" stays readable. Labelled amounts accept free text
# after a separator and written-out or numeric values without one.
SEPARATOR = r"\s*[:：=]\s*"
OPTIONAL_SEPARATOR = r"\s*[:：=]?\s*"
CURRENCY = r"(?:[¥￥$€£]|人民币|(?<![A-Za-z])(?:RMB|CNY|USD|EUR|GBP)(?![A-Za-z]))"
CURRENCY_UNIT = (
    r"(?:万?元|亿?元|人民币|美元|美金|欧元|(?<![A-Za-z])(?:RMB|CNY|USD|EUR|GBP)(?![A-Za-z]))"
)
NUMBER = r"\d[\d,]*(?:\.\d+)?(?:\s*[万亿])?"
AMOUNT_VALUE = (
    rf"(?:{CURRENCY}\s*{NUMBER}(?:\s*{CURRENCY_UNIT})?|{NUMBER}\s*{CURRENCY_UNIT}"
    r"|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{3,}(?:\.\d+)?|\d+\.\d+"
    rf"|{CURRENCY}?\s*[零壹贰叁肆伍陆柒捌玖拾佰仟][零壹贰叁肆伍陆柒捌玖拾佰仟万亿圆元角分整]*)"
)
AMOUNT_LABEL = (
    r"(?:报价|投标价|投标总价|总报价|金额|价格|单价|总价|合同价|预算"
    r"|(?<![A-Za-z])(?:quote\s*price|quoted?\s*(?:price|amount)|amount|price|budget)(?![A-Za-z]))"
)


def labelled(label: str, separator: str, value: str) -> re.Pattern[str]:
    return re.compile(rf"{label}{separator}(?P<value>{value})", re.I)


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
        labelled(
            r"(?:身份证(?:号码?|号)?|(?<![A-Za-z])identity\s*(?:number|no\.?|id)(?![A-Za-z]))",
            OPTIONAL_SEPARATOR,
            r"\d(?:[\dXx*]|[ -](?=[\dXx*])){5,}",
        ),
    ),
    (
        "bank_account",
        labelled(
            r"(?:银行账号|银行账户|银行帐号|银行卡号|收款账号|账号|帐号"
            r"|(?<![A-Za-z])bank\s*account(?:\s*(?:number|no\.?))?(?![A-Za-z]))",
            OPTIONAL_SEPARATOR,
            r"\d(?:[\d*]|[ -](?=[\d*])){7,}",
        ),
    ),
    (
        "bank_account",
        labelled(
            r"(?<![A-Za-z])IBAN(?![A-Za-z])",
            OPTIONAL_SEPARATOR,
            r"[A-Z]{2}\d{2}(?: ?[A-Z\d]){10,30}",
        ),
    ),
    (
        "contact",
        labelled(
            r"(?:联系人|联系人员|联络人|项目联系人"
            r"|(?<![A-Za-z])contact(?:\s*(?:person|name))?(?![A-Za-z]))",
            SEPARATOR,
            r"[^\n\r;；,，。:：]+",
        ),
    ),
    (
        "contact",
        labelled(
            r"(?:联系电话|手机号码?|手机号|电话|手机"
            r"|(?<![A-Za-z])(?:telephone|phone|mobile|tel\.?)(?![A-Za-z]))",
            OPTIONAL_SEPARATOR,
            r"[+(]?\d[\d() .\-]{5,}\d(?:\s*(?:转|分机|ext\.?)\s*\d+)?",
        ),
    ),
    ("amount", labelled(AMOUNT_LABEL, SEPARATOR, r"[^\n\r;；。]+")),
    ("amount", labelled(AMOUNT_LABEL, r"\s*(?:为|是)?\s*", AMOUNT_VALUE)),
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
