"""Versioned outbound-only masking. Neither matches nor original text are logged."""

import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

RULE_VERSION = "bid-redaction-v3"
KINDS = ("amount", "contact", "identity", "bank_account")
PLACEHOLDER = re.compile(r"\[REDACTED_[A-Z_]+\]")
# A confidential field reference. Cards keep it; only an export fills in the value.
SECRET_PLACEHOLDER = re.compile(r"\{\{secret\.([a-z][a-z0-9_]{1,47})\}\}")
NUMERIC_KINDS = {"identity", "bank_account", "contact"}


def secret_placeholder(key: str) -> str:
    return "{{secret." + key + "}}"


def secret_keys(text: str | None) -> list[str]:
    return SECRET_PLACEHOLDER.findall(text or "")


def fill_secrets(text: str, values: Mapping[str, str], labels: Mapping[str, str]) -> str:
    """Replace each reference with its value, or a visible 【label】 when it has none."""
    return SECRET_PLACEHOLDER.sub(
        lambda match: values.get(match[1]) or "【" + labels.get(match[1], match[1]) + "】", text
    )


@dataclass(frozen=True)
class LibraryValue:
    key: str
    pattern: re.Pattern[str]


def library_value(key: str, kind: str, value: str) -> LibraryValue | None:
    """How a stored value is found in outbound text, or None when it is too short to
    find without also replacing unrelated text (for example "100" or a single character)."""
    text = unicodedata.normalize("NFKC", value).strip()
    compact = re.sub(r"[\s\-]", "", text)
    if kind in NUMERIC_KINDS and re.fullmatch(r"\+?[\dXx*()]+", compact):
        if sum(char.isdigit() for char in compact) < 4:
            return None
        body = r"[ \-]?".join(re.escape(char) for char in compact)
        return LibraryValue(key, re.compile(rf"(?<![\dA-Za-z]){body}(?![\dA-Za-z])", re.I))
    if kind == "amount" and re.fullmatch(r"[\d,]+(?:\.\d+)?", compact):
        whole, _, fraction = compact.replace(",", "").partition(".")
        if len(whole + fraction) < 4:
            return None
        body = "[,]?".join(re.escape(char) for char in whole)
        body += r"\." + re.escape(fraction) if fraction else ""
        return LibraryValue(key, re.compile(rf"(?<![\d.]){body}(?!\d|\.\d)"))
    if len(text) < 2:
        return None
    return LibraryValue(key, re.compile(re.escape(text)))


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


def redact(
    value: str, enabled: bool, library: Sequence[LibraryValue] = ()
) -> tuple[str, dict[str, int]]:
    """Mask `value` for a vendor. Registered confidential values become their
    `{{secret.key}}` placeholder; the pattern rules mask the rest as `[REDACTED_…]`.
    Counts cover both, the placeholders under "confidential"."""
    counts = Counter({kind: 0 for kind in (*KINDS, "confidential")})
    normalized, offsets = [], []
    for index, char in enumerate(value):
        part = unicodedata.normalize("NFKC", char)
        normalized.append(part)
        offsets.extend([index] * len(part))
    text = "".join(normalized)

    def original(start: int, end: int) -> tuple[int, int]:
        return offsets[start], offsets[end - 1] + 1

    # Longest registered value first, so a stored account number wins over a stored
    # suffix of it; a later value never overlaps one already placed.
    secrets: list[tuple[int, int, str]] = []
    candidates = sorted(
        (
            (match.start(), match.end(), item.key)
            for item in library
            for match in item.pattern.finditer(text)
            if match.end() > match.start()
        ),
        key=lambda found: (found[0] - found[1], found[0]),
    )
    for start, end, key in candidates:
        if all(end <= other_start or start >= other_end for other_start, other_end, _ in secrets):
            secrets.append((start, end, key))
            counts["confidential"] += 1
    secrets = [(*original(start, end), key) for start, end, key in sorted(secrets)]
    matches: list[tuple[int, int, str]] = []
    for kind, pattern in RULES:
        for match in pattern.finditer(text):
            start, end = match.span("value") if "value" in pattern.groupindex else match.span()
            if start == end:
                continue
            start, end = original(start, end)
            # Union overlapping detections: a broad labelled value must not leave
            # a sensitive suffix exposed because a numeric sub-pattern matched first.
            counts[kind] += 1
            # A rule hit on a registered value keeps only the parts outside it, so a
            # label rule cannot mask the placeholder or expose what surrounds it.
            for secret_start, secret_end, _ in secrets:
                if start < secret_end and secret_start < end:
                    if start < secret_start:
                        matches.append((start, secret_start, kind))
                    start = max(start, secret_end)
            if start < end:
                matches.append((start, end, kind))
    if not enabled:
        return value, dict(counts)
    merged: list[tuple[int, int, set[str]]] = []
    for start, end, kind in sorted(matches):
        while start < end and value[start].isspace():
            start += 1
        while end > start and value[end - 1].isspace():
            end -= 1
        if start == end:
            continue
        if merged and start < merged[-1][1]:
            old_start, old_end, kinds = merged[-1]
            merged[-1] = old_start, max(end, old_end), kinds | {kind}
        else:
            merged.append((start, end, {kind}))
    spans = [
        (start, end, "[REDACTED_" + "_".join(sorted(kinds)).upper() + "]")
        for start, end, kinds in merged
    ] + [(start, end, secret_placeholder(key)) for start, end, key in secrets]
    parts, cursor = [], 0
    for start, end, replacement in sorted(spans):
        parts.extend((value[cursor:start], replacement))
        cursor = end
    parts.append(value[cursor:])
    return "".join(parts), dict(counts)


def redact_tree(value, enabled: bool, library: Sequence[LibraryValue] = ()):
    """Mask every string leaf, including document location labels and section paths."""
    counts = Counter({kind: 0 for kind in (*KINDS, "confidential")})

    def visit(item):
        if isinstance(item, str):
            sent, hits = redact(item, enabled, library)
            counts.update(hits)
            return sent
        if isinstance(item, dict):
            return {key: visit(child) for key, child in item.items()}
        if isinstance(item, list):
            return [visit(child) for child in item]
        return item

    sent = visit(value)
    return sent, dict(counts)
