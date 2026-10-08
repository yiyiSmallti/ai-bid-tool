"""Bounded, literal tender signing-clause candidates; no applicability inference."""

import re

from app.core.errors import ServiceError

SCAN_VERSION = "signing-clauses-v1"
CANDIDATE_LIMIT = 2000
QUOTE_LIMIT = 2000
KEYWORDS = re.compile(
    r"公章|盖章|加盖|签字|签章|法定代表人|授权代表|委托代理人|日期|骑缝章|每页|电子签章|私章|个人印章|数字签名|[★▲]"
)


def scan(text: str) -> list[dict]:
    """Offsets are Unicode codepoint offsets in the immutable native page text."""
    candidates = []
    for match in re.finditer(r"[^\n。；;]+[。；;]?", text):
        raw = match.group()
        if not KEYWORDS.search(raw):
            continue
        quote = raw.strip()
        if not quote:
            continue
        if len(quote) > QUOTE_LIMIT or len(candidates) >= CANDIDATE_LIMIT:
            raise ServiceError(
                "bid_signing_clause_limit", "Signing clause scan exceeds bounds", 400, 2
            )
        start = match.start() + len(raw) - len(raw.lstrip())
        marks = []
        for kind, pattern in (
            ("company_seal", r"公章|盖章|加盖"),
            (
                "legal_representative_signature",
                r"法定代表人.*(?:签字|签章)|(?:签字|签章).*法定代表人",
            ),
            (
                "authorized_agent_signature",
                r"(?:授权代表|委托代理人).*(?:签字|签章)|(?:签字|签章).*(?:授权代表|委托代理人)",
            ),
            ("personal_seal", r"私章|个人印章"),
            ("date", r"日期"),
            ("seam_seal", r"骑缝章"),
            ("every_page_electronic_seal", r"每页.*电子签章|电子签章.*每页"),
            ("pdf_digital_signature", r"电子签章|数字签名"),
        ):
            if re.search(pattern, quote):
                marks.append(kind)
        owners = [
            role
            for role, pattern in (
                ("company", r"公章"),
                ("legal_representative", r"法定代表人"),
                ("authorized_agent", r"授权代表|委托代理人"),
            )
            if re.search(pattern, quote)
        ]
        location = re.search(r"每页|骑缝|签[字章]处|盖章处|指定位置|本页|末页|首页", quote)
        date_required = None
        if re.search(
            r"(?:无需|不需|不必|不得|不用).{0,8}(?:填写)?日期|日期.{0,8}(?:无需|不需|不必|不得|不用)",
            quote,
        ):
            date_required = False
        elif re.search(
            r"(?:须|应|需要|填写|注明|填).{0,12}日期|日期.{0,12}(?:必填|填写|注明)", quote
        ):
            date_required = True
        candidates.append(
            {
                "quote": quote,
                "start_offset": start,
                "end_offset": start + len(quote),
                "mark_types": marks,
                "owner_roles": owners,
                "date_required": date_required,
                "location_hint": location.group() if location else None,
                "applicability": "unknown",
                "scan_version": SCAN_VERSION,
            }
        )
    return candidates
