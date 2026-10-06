"""DB-free outbound -> fake HTTP Provider -> pinned-Source semantic acceptance.

Failure inventory, recorded before adding these regressions:
* a short quote repeated outside its unique pinned Source may be falsely rejected;
* a quote repeated inside the Source, including embedded literal repeats, may pass;
* a quote found only outside the Source may borrow another clause's support;
* an ambiguous or absent full Source may be mistaken for a valid fixed location;
* valid full-Source boundary preference may be lost while restricting short quotes;
* PDF pages or Word structural positions may be rewritten in accepted citations.
* duplicate or invented quotations in metadata/rule refs may affect tender citations.

All text is synthetic. These cases exercise the existing production outbound,
accounted MockTransport Provider, and acceptance paths without database fixtures.
"""

import json
from decimal import Decimal

import pytest
from app.schemas.contracts import Source
from test_check_semantic_boundary import answer as check_answer
from test_check_semantic_boundary import execute_boundary as execute_check
from test_check_semantic_boundary import input_case
from test_score_execution_provider import execute_boundary as execute_score
from test_score_execution_provider import fixed_secret
from test_score_execution_provider import wire as score_answer

SOURCE = "技术评分条款：投标设备应配备64GB内存。符合条件得5分。"
QUOTE = "64GB内存"
RESPONSE = "我方投标设备配置64GB内存，型号参数表列明了该配置。"


def source_case(case):
    if case == "repeat_outside":
        return SOURCE, f"其他条款也提及{QUOTE}。\n{SOURCE}\n附件另列{QUOTE}。", QUOTE
    if case == "repeat_inside":
        source = f"技术评分：{QUOTE}；配置要求再次列明{QUOTE}。满足要求得5分。"
        return source, source, QUOTE
    if case == "outside_only":
        source = "技术评分条款：投标设备应配备32GB内存。符合条件得5分。"
        return source, f"{source}\n其他条款：{QUOTE}。", QUOTE
    if case == "ambiguous_source":
        return SOURCE, f"{SOURCE}\n重复条款：{SOURCE}", QUOTE
    if case == "missing_source":
        return SOURCE, f"另一个评分条款仅提及{QUOTE}，未包含固定条款。", QUOTE
    if case == "source_boundary_preference":
        source = "5mm插孔，符合条件得5分。"
        return source, f"其他规格：3.{source}\n{source}", "5mm插孔"
    if case == "literal_repeat_inside":
        source = "技术评分：3.5mm插孔；5mm插孔。符合条件得5分。"
        return source, source, "5mm插孔"
    raise AssertionError(f"Unknown synthetic case: {case}")


def pinned_source(source, document_format, quote):
    position = (
        {"page": 1, "location": None}
        if document_format == "pdf"
        else {
            "page": None,
            "location": {
                "block_id": "p:3",
                "kind": "paragraph",
                "section_path": ["技术评分"],
                "paragraph": 3,
                "label": "技术评分 / 第3段",
            },
        }
    )
    return Source.model_validate({**source, **position, "quote": quote}).model_dump(mode="json")


@pytest.mark.parametrize("pipeline", ["check", "score"])
@pytest.mark.parametrize("document_format", ["pdf", "docx"])
@pytest.mark.parametrize(
    "case,reason",
    [
        ("repeat_outside", None),
        ("repeat_inside", "ambiguous_quote"),
        ("outside_only", "quote_not_at_position"),
        ("ambiguous_source", "ambiguous_quote"),
        ("missing_source", "quote_not_at_position"),
        ("source_boundary_preference", None),
        ("literal_repeat_inside", "ambiguous_quote"),
    ],
)
async def test_semantic_citation_stays_within_pinned_source(
    tmp_path, pipeline, document_format, case, reason
):
    source_text, full_location, citation_quote = source_case(case)
    response = RESPONSE if "5mm" not in citation_quote else "我方设备配备5mm插孔，参数表已列明。"

    if pipeline == "check":
        secret = input_case(response)
        source = pinned_source(secret["items"][0]["source"], document_format, source_text)
        secret["items"][0] |= {"source": source, "tender_original": full_location}
        candidate = check_answer()
        candidate["citations"] = [
            {"ref": "r1.tender", "quote": citation_quote},
            {"ref": "r1.response_text", "quote": citation_quote},
        ]
        wire = {"items": [candidate]}
        evaluated, outbound, sent = await execute_check(tmp_path, secret, wire)
        row = evaluated[0]
        assert row["semantic_reason_code"] == reason
        assert row["semantic_status"] == ("assessed" if reason is None else "unassessed")
        assert row["semantic_outcome"] == ("no_risk_found" if reason is None else None)
        citations = row["semantic_citations"]
        tender_ref = "r1.tender"
    else:
        secret = fixed_secret(response=response)
        source = pinned_source(secret["items"][0]["source"], document_format, source_text)
        secret["items"][0] |= {"source": source, "tender_original": full_location}
        secret["rubric"]["items"][0] |= {
            "source": source,
            "rule_text": "设备满足指定配置得5分。",
        }
        wire = score_answer()
        wire["items"][0]["tender_citations"] = [{"ref": "s1.tender", "quote": citation_quote}]
        wire["items"][0]["draft_citations"] = [{"ref": "d1.response_text", "quote": citation_quote}]
        evaluated, outbound, result, sent = await execute_score(tmp_path, secret, wire)
        assert result.failure is None
        assert len(result.usages) == 1
        row = evaluated[0]
        assert row["reason_code"] == ("supported" if reason is None else reason)
        assert row["outcome"] == ("assessed" if reason is None else "unassessable")
        assert row["estimated_score"] == (Decimal("5") if reason is None else None)
        assert evaluated[1]["outcome"] == "assessed"
        citations = row["citations"]
        tender_ref = "s1.tender"

    assert len(sent) == 1
    payload = json.loads(sent[0]["messages"][1]["content"])
    tender_text = next(text for text in payload["context"]["texts"] if text["ref"] == tender_ref)
    assert tender_text["text"] == source_text
    assert outbound["refs"][tender_ref]["location_original"] == full_location
    if reason is None:
        assert {citation["kind"] for citation in citations} == {"tender", "draft"}
        tender = next(citation for citation in citations if citation["kind"] == "tender")
        assert tender["source"] == {**source, "quote": citation_quote}
    else:
        assert citations == []

    artifact = tmp_path / "pinned-source-semantic.json"
    artifact.write_text(
        json.dumps(
            {
                "pipeline": pipeline,
                "document_format": document_format,
                "case": case,
                "pinned_source": source,
                "fixed_location_text": full_location,
                "provider_request": payload,
                "provider_response": wire,
                "outcome": row,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    assert json.loads(artifact.read_text(encoding="utf-8"))["case"] == case


@pytest.mark.parametrize("pipeline", ["check", "score"])
@pytest.mark.parametrize(
    "case,reason",
    [
        ("metadata_contains_quote", None),
        ("metadata_only_quote", "quote_not_at_position"),
        ("source_repeats_quote", "ambiguous_quote"),
    ],
)
async def test_semantic_tender_ref_excludes_summary_and_location(tmp_path, pipeline, case, reason):
    """Only tender-source text can support a citation, regardless of other sent refs."""
    source_text = {
        "metadata_contains_quote": SOURCE,
        "metadata_only_quote": SOURCE.replace(QUOTE, "32GB内存"),
        "source_repeats_quote": f"{SOURCE}再次要求{QUOTE}。",
    }[case]
    secret = input_case(RESPONSE) if pipeline == "check" else fixed_secret(response=RESPONSE)
    source = pinned_source(secret["items"][0]["source"], "docx", source_text)
    source["location"]["label"] = f"技术评分摘要：{QUOTE}"
    secret["items"][0] |= {"source": source, "tender_original": source_text}

    if pipeline == "check":
        candidate = check_answer()
        candidate["citations"] = [
            {"ref": "r1.tender", "quote": QUOTE},
            {"ref": "r1.response_text", "quote": QUOTE},
        ]
        wire = {"items": [candidate]}
        evaluated, outbound, sent = await execute_check(tmp_path, secret, wire)
        row = evaluated[0]
        assert row["semantic_reason_code"] == reason
        assert row["semantic_status"] == ("assessed" if reason is None else "unassessed")
        citations = row["semantic_citations"]
        tender_ref, metadata_ref = "r1.tender", "r1.location"
    else:
        secret["rubric"]["items"][0] |= {
            "source": source,
            "rule_text": f"技术评分摘要：设备配置{QUOTE}得5分。",
        }
        wire = score_answer()
        wire["items"][0]["tender_citations"] = [{"ref": "s1.tender", "quote": QUOTE}]
        wire["items"][0]["draft_citations"] = [{"ref": "d1.response_text", "quote": QUOTE}]
        evaluated, outbound, result, sent = await execute_score(tmp_path, secret, wire)
        assert result.failure is None
        row = evaluated[0]
        assert row["reason_code"] == ("supported" if reason is None else reason)
        assert row["outcome"] == ("assessed" if reason is None else "unassessable")
        citations = row["citations"]
        tender_ref, metadata_ref = "s1.tender", "s1.rule"

    payload = json.loads(sent[0]["messages"][1]["content"])
    texts = {text["ref"]: text["text"] for text in payload["context"]["texts"]}
    assert texts[tender_ref] == source_text
    assert QUOTE in texts[metadata_ref]
    assert outbound["refs"][tender_ref]["sent_source"] == source_text
    if reason is None:
        tender = next(citation for citation in citations if citation["kind"] == "tender")
        assert tender["source"] == {**source, "quote": QUOTE}
    else:
        assert citations == []

    artifact = tmp_path / "sent-source-semantic.json"
    artifact.write_text(
        json.dumps(
            {"pipeline": pipeline, "case": case, "request": payload, "wire": wire, "outcome": row},
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    assert json.loads(artifact.read_text(encoding="utf-8"))["case"] == case
