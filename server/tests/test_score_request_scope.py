"""DB-free scoring request-to-HTTP regressions for realistic draft volume.

Failure inventory written before the request-scope/budget fix:

* unrelated gap rows can inflate every score request;
* scoping metadata can remove confirmed cross-Requirement support;
* mapped coverage can lose context while duplicate/excluded coverage leaks it;
* whole-request context can leak into split batches and invalidate acceptance;
* scoring can inherit the extraction budget or truncate oversized bid-side text;
* preview can estimate a different plan than accounted HTTP execution.

Only synthetic snapshots and MockTransport are used. The HTTP test writes a
deterministic, sanitized report artifact under the pytest temporary directory.
"""

import json
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from app.core.config import Settings
from app.providers.base import ProviderFailure
from app.providers.calls import current_accounting
from app.schemas.check_contracts import OutboundText
from app.services import score_execution, score_semantic
from test_check_provider import Accounting
from test_score_execution_provider import adapter, fixed_secret


def identifier(namespace: int, index: int) -> str:
    return str(UUID(int=namespace * 100_000 + index + 1))


def large_draft() -> dict:
    secret = fixed_secret()
    anchor = secret["items"][0]
    rubric = secret["rubric"]["items"][0]
    rows, items = [], []
    for index in range(1546):
        quote = f"Criterion {index + 1}: memory 64 GB earns 5 points."
        rows.append(
            {
                **deepcopy(anchor),
                "requirement_id": identifier(1, index),
                "response_item_id": identifier(2, index),
                "card_revision_id": identifier(3, index),
                "source": {**anchor["source"], "page": index + 1, "quote": quote},
                "tender_original": quote,
                "partition": "response" if index < 16 else "gap",
                "response_text": f"Confirmed row {index + 1} supplies 64 GB memory."
                if index < 16
                else None,
                "deviation_note": "No deviation." if index < 16 else None,
                "gap_reasons": [] if index < 16 else ["no_confirmed_response"],
            }
        )
        if index < 86:
            items.append(
                {
                    **deepcopy(rubric),
                    "id": identifier(4, index),
                    "requirement_id": identifier(1, index),
                    "key": f"criterion-{index + 1}",
                    "order": index + 1,
                    "source": rows[-1]["source"],
                }
            )
    secret["items"] = rows
    secret["rubric"]["items"] = items
    secret["rubric"]["coverage"] = []
    return secret


def build_request(secret):
    outbound = score_semantic.build_outbound(secret, [], [])
    request = score_semantic.provider_request(outbound)
    assert request is not None
    return outbound, request


def item_context(index):
    return {f"d{index + 1}.context", f"d{index + 1}.tender"}


def all_response_context():
    return {ref for index in range(16) for ref in item_context(index)}


def expected_refs(request):
    return {
        ref
        for item in request.items
        for ref in (item.tender_ref, item.rule_ref, *item.draft_refs, *item.context_only_refs)
    }


def test_large_draft_scopes_gaps_and_keeps_all_confirmed_response_candidates(tmp_path):
    secret = large_draft()
    outbound, request = build_request(secret)
    provider = adapter(tmp_path, [], [], batch_chars=64_000)
    assert len(secret["rubric"]["items"]) == len(request.items) == 86
    assert sum(row["partition"] == "response" for row in secret["items"]) == 16
    assert sum(row["partition"] == "gap" for row in secret["items"]) == 1530
    draft_refs = {
        f"d{index + 1}.{field}"
        for index in range(16)
        for field in ("response_text", "deviation_note")
    }
    for index, item in enumerate(request.items):
        assert set(item.draft_refs) == draft_refs
        assert set(item.context_only_refs) == all_response_context() | item_context(index)
        single = provider._request_for(request, [index])
        assert len(single.model_dump_json()) < 16_000
        assert {text.ref for text in single.context.texts} == expected_refs(single)
    assert set(request.context_only_refs) == {
        ref for item in request.items for ref in item.context_only_refs
    }
    assert {text.ref for text in request.context.texts} == expected_refs(request)
    assert "d1546.tender" not in request.context_only_refs
    assert "d1546.tender" not in {text.ref for text in request.context.texts}
    groups = provider._groups(request)
    assert 1 < len(groups) < len(request.items)
    assert all(len(group.model_dump_json()) <= 64_000 for group in groups)
    assert sum(len(group.items) for group in groups) == 86
    assert outbound["preflight"] == {}


def test_coverage_preserves_own_mapped_context_and_excludes_unrelated_gaps(tmp_path):
    secret = large_draft()
    secret["items"][16]["partition"] = "comply_only"
    secret["rubric"]["coverage"] = [
        {
            "requirement_id": identifier(1, index),
            "disposition": disposition,
            "rubric_item_ids": [identifier(4, linked)] if disposition == "mapped" else [],
            "canonical_requirement_id": identifier(1, 16) if disposition == "duplicate" else None,
            "reason": "Human coverage decision.",
        }
        for index, disposition, linked in (
            (16, "mapped", 16),
            (1501, "duplicate", 1),
            (1502, "excluded", 2),
        )
    ]
    _, request = build_request(secret)
    assert set(request.items[0].context_only_refs) == all_response_context()
    assert set(request.items[1].context_only_refs) == all_response_context()
    assert set(request.items[2].context_only_refs) == all_response_context()
    assert set(request.items[16].context_only_refs) == all_response_context() | item_context(16)
    assert not (item_context(1500) | item_context(1501) | item_context(1502)) & set(
        request.context_only_refs
    )
    single = adapter(tmp_path, [], [])._request_for(request, [1])
    assert not item_context(1500) & {text.ref for text in single.context.texts}
    # An invalid foreign coverage binding must not widen this local request.
    secret["rubric"]["coverage"].append(
        {
            "requirement_id": identifier(1, 1500),
            "disposition": "mapped",
            "rubric_item_ids": [identifier(4, 0)],
        }
    )
    _, invalid_coverage_request = build_request(secret)
    assert invalid_coverage_request.model_dump() == request.model_dump()


@pytest.mark.parametrize("mutation", ["extra_top_context", "missing_top_context", "extra_text"])
def test_adapter_rejects_context_refs_outside_exact_item_union(tmp_path, mutation):
    _, request = build_request(large_draft())
    invalid = request.model_copy(deep=True)
    if mutation == "extra_top_context":
        invalid.context_only_refs.append("unrelated.context")
        invalid.context.texts.append(OutboundText(ref="unrelated.context", text="Unrelated gap."))
    elif mutation == "missing_top_context":
        invalid.context_only_refs.pop()
    else:
        invalid.context.texts.append(OutboundText(ref="unrelated.context", text="Unrelated gap."))
    with pytest.raises(ProviderFailure) as failure:
        adapter(tmp_path, [], [])._groups(invalid)
    assert failure.value.code == "invalid_score_request"


async def test_oversized_confirmed_bid_text_is_blocked_without_truncation_or_calls(tmp_path):
    secret = large_draft()
    large_text = "Beginning of confirmed text. " + "正文" * 40_000 + " End of confirmed text."
    secret["items"][15]["response_text"] = large_text
    _, request = build_request(secret)
    assert (
        next(text.text for text in request.context.texts if text.ref == "d16.response_text")
        == large_text
    )
    sent = []
    provider = adapter(tmp_path, [], sent, batch_chars=64_000)
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        with pytest.raises(ProviderFailure) as failure:
            await provider.score(request)
    finally:
        current_accounting.reset(token)
    assert failure.value.code == "score_context_limit"
    assert sent == accounting.planned == accounting.completed == []


async def test_preview_estimates_actual_scoring_groups_without_database_or_calls(tmp_path):
    outbound, request = build_request(large_draft())
    provider = adapter(tmp_path, [], [], batch_chars=64_000)
    llm = provider.llm
    llm.settings.llm_input_usd_per_mtok = Decimal("1.25")
    llm.settings.llm_output_usd_per_mtok = Decimal("5")
    fixed = SimpleNamespace(
        manifest={"model_redaction_enabled": True}, secret={"outbound": outbound}
    )

    class NoDatabase:
        async def scalar(self, statement):
            pytest.fail("Org-owned scoring preview must not query PostgreSQL")

    preview = await score_execution.preview_cost(
        NoDatabase(), fixed, llm, SimpleNamespace(max_charge=None), llm.settings
    )
    groups = provider._groups(request)
    bodies = [provider.request_body(group) for group in groups]
    inputs = sum(len(json.dumps(body, ensure_ascii=False).encode()) + 4096 for body in bodies)
    outputs = sum(llm.output_token_bound(body) for body in bodies)
    assert preview["admission_blocker"] is None
    assert preview["cost_basis"] == "known"
    assert preview["estimated_cost"]["llm_tokens"] == inputs + outputs
    assert (
        preview["estimated_cost"]["usd"]
        == (inputs * Decimal("1.25") + outputs * Decimal("5")) / 1_000_000
    )
    assert preview["estimated_charge"] == sum(
        (provider.reservation(group) for group in groups), Decimal(0)
    )
    llm.settings.llm_batch_chars = 1000
    assert [group.model_dump() for group in provider._groups(request)] == [
        group.model_dump() for group in groups
    ]
    assert Settings.model_fields["score_batch_chars"].default == 64_000


def successful_reply(payload):
    texts = {text["ref"]: text["text"] for text in payload["context"]["texts"]}
    return {
        "items": [
            {
                "rubric_item_id": item["rubric_item_id"],
                "outcome": "assessed",
                "estimated_score": "5",
                "reason_code": "supported",
                "reason": "A confirmed response supports 64 GB memory.",
                "deduction_reasons": [],
                "strengthening_actions": [],
                "tender_citations": [
                    {"ref": item["tender_ref"], "quote": texts[item["tender_ref"]]}
                ],
                "draft_citations": [
                    {"ref": "d16.response_text", "quote": texts["d16.response_text"]}
                ],
            }
            for item in payload["items"]
        ]
    }


def http_reply(payload, *, truncated=False):
    return httpx.Response(
        200,
        json={
            "model": "synthetic-score",
            "choices": [
                {
                    "finish_reason": "length" if truncated else "stop",
                    "message": {
                        "content": "{" if truncated else json.dumps(successful_reply(payload))
                    },
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10},
        },
    )


async def execute(provider, request):
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        result = await provider.score(request)
    finally:
        current_accounting.reset(token)
    return result, accounting


async def test_large_http_batches_accept_cross_requirement_citations_and_write_artifact(tmp_path):
    secret = large_draft()
    outbound, request = build_request(secret)
    sent = []
    provider = adapter(tmp_path, [], [], batch_chars=64_000)
    expected_groups = provider._groups(request)

    def respond(http_request):
        payload = json.loads(json.loads(http_request.content)["messages"][1]["content"])
        sent.append(payload)
        assert set(payload["context_only_refs"]) == {
            ref for item in payload["items"] for ref in item["context_only_refs"]
        }
        assert {text["ref"] for text in payload["context"]["texts"]} == {
            ref
            for item in payload["items"]
            for ref in (
                item["tender_ref"],
                item["rule_ref"],
                *item["draft_refs"],
                *item["context_only_refs"],
            )
        }
        return http_reply(payload)

    provider.llm.transport = httpx.MockTransport(respond)
    result, accounting = await execute(provider, request)
    evaluated = score_semantic.accept_batches(secret, outbound, result)
    assert result.failure is None
    assert accounting.planned == [len(expected_groups)]
    assert len(sent) == len(result.batches) == len(accounting.completed) == len(expected_groups)
    assert len(evaluated) == 86
    assert all(row["outcome"] == "assessed" for row in evaluated)
    assert all(row["response_item_ids"] == [UUID(identifier(2, 15))] for row in evaluated)
    corrupted = result.model_copy(deep=True)
    corrupted.batches[0].sent_refs.append("d1546.tender")
    rejected = score_semantic.accept_batches(secret, outbound, corrupted)
    rejected_ids = set(corrupted.batches[0].requested_rubric_item_ids)
    assert all(
        row["reason_code"] == "invalid_batch_refs"
        if row["rubric_item_id"] in rejected_ids
        else row["outcome"] == "assessed"
        for row in rejected
    )
    artifact = tmp_path / "score-request-scope-report.json"
    artifact.write_text(
        json.dumps({"batch_count": len(result.batches), "items": evaluated}, default=str, indent=2)
        + "\n"
    )
    assert len(json.loads(artifact.read_text())["items"]) == 86


async def test_truncated_model_output_splits_context_per_item_before_acceptance(tmp_path):
    secret = large_draft()
    secret["rubric"]["items"] = secret["rubric"]["items"][16:18]
    outbound, request = build_request(secret)
    sent = []
    provider = adapter(tmp_path, [], [], batch_chars=64_000)

    def respond(http_request):
        payload = json.loads(json.loads(http_request.content)["messages"][1]["content"])
        sent.append(payload)
        return http_reply(payload, truncated=len(sent) == 1)

    provider.llm.transport = httpx.MockTransport(respond)
    result, accounting = await execute(provider, request)
    assert len(sent) == len(accounting.completed) == 3
    assert len(result.batches) == 2
    assert [len(payload["items"]) for payload in sent] == [2, 1, 1]
    for payload in sent[1:]:
        assert set(payload["context_only_refs"]) == set(payload["items"][0]["context_only_refs"])
    assert all(
        row["outcome"] == "assessed"
        for row in score_semantic.accept_batches(secret, outbound, result)
    )
