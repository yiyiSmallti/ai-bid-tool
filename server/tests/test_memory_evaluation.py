"""Repeatable synthetic retrieval evaluation through the real PostgreSQL API."""

import asyncio
import hashlib
import json
import time
from pathlib import Path
from uuid import UUID

from app.schemas.memory_contracts import MemoryEvalCase, MemoryRetrievalRequest
from test_memory_api import approved_rule

SAMPLE_VERSION = "org-keyword-evaluation-v1"


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


async def approved_preference(api, header):
    reply = await api.post(
        "/memories",
        headers=header,
        json={
            "target": {"scope": "org"},
            "content": {
                "kind": "preference",
                "conflict_key": "evaluation.default",
                "text": "交货期",
                "tags": ["delivery"],
            },
        },
    )
    assert reply.status_code == 200, reply.text
    memory = reply.json()["data"]["memory"]
    reply = await api.post(
        f"/memories/{memory['id']}/decisions",
        headers=header,
        json={
            "expected_revision": 1,
            "action": "approve",
            "reason": "Synthetic preference approved",
        },
    )
    assert reply.status_code == 200, reply.text
    return memory


async def register_confidential_value_with_outbound_redaction_off(api, header):
    """A registered value has no regex signal and must still be rejected by memory."""
    task = await api.post("/tasks", headers=header, json={"name": "Synthetic memory evaluation"})
    assert task.status_code == 200, task.text
    task_id = task.json()["data"]["id"]
    changed = await api.put(
        f"/tasks/{task_id}/model-redaction",
        headers=header,
        json={"expected_revision": 1, "model_redaction_enabled": False},
    )
    assert changed.status_code == 200, changed.text
    field = await api.post(
        "/confidential-fields",
        headers=header,
        json={
            "key": "evaluation_private",
            "label": "Synthetic protected label",
            "kind": "other",
            "scope": "task",
        },
    )
    assert field.status_code == 200, field.text
    confidential_value = "SyntheticPrivatePhraseForMemoryEvaluation"
    saved = await api.post(
        f"/confidential-fields/{field.json()['data']['id']}/values",
        headers=header,
        json={"task_id": task_id, "value": confidential_value},
    )
    assert saved.status_code == 200, saved.text
    return task_id, confidential_value


async def test_memory_synthetic_retrieval_evaluation(api, headers, tenants):
    started = time.monotonic()
    exact = await approved_rule(api, headers[0], "evaluation.exact", "交货期", ["delivery"])
    keyword_tag = await approved_rule(
        api, headers[0], "evaluation.keyword-tag", "交货期由答疑明确", ["delivery"]
    )
    keyword = await approved_rule(api, headers[0], "evaluation.keyword", "交货期按答疑")
    tie_a = await approved_rule(api, headers[0], "evaluation.tie-a", "交货期应逐项注明A")
    tie_b = await approved_rule(api, headers[0], "evaluation.tie-b", "交货期应逐项注明B")
    normalized = await approved_rule(api, headers[0], "evaluation.normalized", "ＦＯＯ   Bar")
    preference = await approved_preference(api, headers[0])
    foreign = await approved_rule(api, headers[1], "evaluation.foreign", "交货期")
    ids = {
        name: UUID(memory["id"])
        for name, memory in (
            ("exact", exact),
            ("keyword_tag", keyword_tag),
            ("keyword", keyword),
            ("tie_a", tie_a),
            ("tie_b", tie_b),
            ("normalized", normalized),
            ("preference", preference),
            ("foreign", foreign),
        )
    }
    ties = sorted([ids["tie_a"], ids["tie_b"]], key=str)
    expected_order = [ids["exact"], ids["keyword_tag"], ids["keyword"], *ties, ids["preference"]]
    org = tenants["orgs"][0]
    cases = [
        MemoryEvalCase(
            case_id="rules-before-preferences",
            request=MemoryRetrievalRequest(
                org_id=org, scopes=["org"], query="交货期", keywords=["答疑"], tags=["delivery"]
            ),
            expected_memory_ids=expected_order,
            forbidden_memory_ids=[ids["foreign"]],
            expected_order=expected_order,
        ),
        MemoryEvalCase(
            case_id="top-k-priority",
            request=MemoryRetrievalRequest(
                org_id=org,
                scopes=["org"],
                query="交货期",
                keywords=["答疑"],
                tags=["delivery"],
                top_k=3,
            ),
            expected_memory_ids=expected_order[:3],
            forbidden_memory_ids=[ids["foreign"]],
            expected_order=expected_order[:3],
        ),
        MemoryEvalCase(
            case_id="nfkc-casefold",
            request=MemoryRetrievalRequest(org_id=org, scopes=["org"], query="foo bar"),
            expected_memory_ids=[ids["normalized"]],
            forbidden_memory_ids=[ids["foreign"]],
            expected_order=[ids["normalized"]],
        ),
        MemoryEvalCase(
            case_id="literal-like-metacharacters",
            request=MemoryRetrievalRequest(org_id=org, scopes=["org"], query="%_"),
            expected_memory_ids=[],
            forbidden_memory_ids=list(ids.values()),
            expected_order=[],
        ),
        MemoryEvalCase(
            case_id="foreign-org-hidden",
            request=MemoryRetrievalRequest(
                org_id=tenants["orgs"][1], scopes=["org"], query="交货期"
            ),
            expected_memory_ids=[],
            forbidden_memory_ids=list(ids.values()),
            expected_order=[],
            expected_error="not_found",
        ),
        MemoryEvalCase(
            case_id="global-disabled",
            request=MemoryRetrievalRequest(org_id=org, scopes=["global"], query="交货期"),
            expected_memory_ids=[],
            forbidden_memory_ids=list(ids.values()),
            expected_order=[],
            expected_error="memory_scope_unavailable",
        ),
        MemoryEvalCase(
            case_id="user-disabled",
            request=MemoryRetrievalRequest(
                org_id=org, scopes=["user"], user_id=tenants["users"][0], query="交货期"
            ),
            expected_memory_ids=[],
            forbidden_memory_ids=list(ids.values()),
            expected_order=[],
            expected_error="memory_scope_unavailable",
        ),
        MemoryEvalCase(
            case_id="project-disabled",
            request=MemoryRetrievalRequest(
                org_id=org, scopes=["project"], task_id=UUID(int=1), query="交货期"
            ),
            expected_memory_ids=[],
            forbidden_memory_ids=list(ids.values()),
            expected_order=[],
            expected_error="memory_scope_unavailable",
        ),
        MemoryEvalCase(
            case_id="vector-unconfigured",
            request=MemoryRetrievalRequest(
                org_id=org, scopes=["org"], query="交货期", mode="vector"
            ),
            expected_memory_ids=[],
            forbidden_memory_ids=list(ids.values()),
            expected_order=[],
            expected_error="embedding_unconfigured",
        ),
        MemoryEvalCase(
            case_id="hybrid-unconfigured",
            request=MemoryRetrievalRequest(
                org_id=org, scopes=["org"], query="交货期", mode="hybrid"
            ),
            expected_memory_ids=[],
            forbidden_memory_ids=list(ids.values()),
            expected_order=[],
            expected_error="embedding_unconfigured",
        ),
    ]
    results = []
    recalls, reciprocal_ranks = [], []
    priority_violations = isolation_violations = 0
    for case in cases:
        case_started = time.monotonic()
        response = await api.post(
            "/memories/retrieve?preview=true",
            headers=headers[0],
            json=case.request.model_dump(mode="json"),
        )
        output = response.json()
        selected = [UUID(item["memory"]["memory_id"]) for item in output["items"]]
        isolation_violations += len(set(selected) & set(case.forbidden_memory_ids))
        if case.expected_error:
            assert response.status_code in {403, 404}, response.text
            assert output["data"]["error"]["code"] == case.expected_error
            assert output["data"]["error"]["exit_code"] == 4
            assert selected == []
            recall = mrr = None
        else:
            assert response.status_code == 200, response.text
            assert selected == case.expected_order
            relevant = set(case.expected_memory_ids)
            recall = len(set(selected) & relevant) / len(relevant) if relevant else 1.0
            mrr = next(
                (
                    1 / (rank + 1)
                    for rank, identifier in enumerate(selected)
                    if identifier in relevant
                ),
                1.0 if not relevant else 0.0,
            )
            recalls.append(recall)
            reciprocal_ranks.append(mrr)
            saw_preference = False
            for item in output["items"]:
                if item["kind"] == "preference":
                    saw_preference = True
                elif saw_preference:
                    priority_violations += 1
            if case.case_id == "rules-before-preferences":
                assert [item["relevance"] for item in output["items"]] == [115, 25, 20, 10, 10, 115]
            if case.case_id == "top-k-priority":
                assert {item["reason"] for item in output["data"]["omitted"]} == {"top_k_limit"}
            assert output["cost"] == {"llm_tokens": 0, "ocr_pages": 0, "usd": 0.0}
        results.append(
            {
                "case": case.model_dump(mode="json"),
                "command": "POST /memories/retrieve?preview=true",
                "http_status": response.status_code,
                "result_sha256": digest(output),
                "selected_ids": [str(identifier) for identifier in selected],
                "recall_at_k": recall,
                "mrr": mrr,
                "duration_ms": round((time.monotonic() - case_started) * 1000),
                "cost": output["cost"],
            }
        )
    task_id, confidential_value = await register_confidential_value_with_outbound_redaction_off(
        api, headers[0]
    )
    sensitive = await api.post(
        "/memories",
        headers=headers[0],
        json={
            "target": {"scope": "org"},
            "content": {
                "kind": "rule",
                "conflict_key": "evaluation.protected",
                "text": confidential_value,
            },
            "source": {"task_id": task_id},
        },
    )
    assert sensitive.status_code == 422, sensitive.text
    assert sensitive.json()["data"]["error"]["code"] == "memory_sensitive_value"
    assert confidential_value not in sensitive.text
    assert priority_violations == isolation_violations == 0
    report = {
        "sample_version": SAMPLE_VERSION,
        "resources": {key: str(value) for key, value in ids.items()},
        "cases": results,
        "metrics": {
            "recall_at_k": sum(recalls) / len(recalls),
            "mrr": sum(reciprocal_ranks) / len(reciprocal_ranks),
            "priority_violations": priority_violations,
            "isolation_violations": isolation_violations,
            "sensitive_rejection_count": 1,
            "candidate_acceptance_rate": None,
            "candidate_acceptance_reason": "Retrieval evaluation has no candidate review cohort",
            "cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0.0},
            "duration_ms": round((time.monotonic() - started) * 1000),
        },
        "sensitive_rejection": {
            "command": "POST /memories with task outbound redaction disabled",
            "http_status": sensitive.status_code,
            "result_sha256": digest(sensitive.json()),
            "error_code": "memory_sensitive_value",
        },
    }
    persisted = await asyncio.to_thread(write_report, report, confidential_value)
    assert persisted["metrics"]["recall_at_k"] == persisted["metrics"]["mrr"] == 1
    assert (
        persisted["metrics"]["isolation_violations"]
        == persisted["metrics"]["priority_violations"]
        == 0
    )


def write_report(report, confidential_value):
    artifact = (
        Path(__file__).resolve().parents[2]
        / "data/work/memory-validation/retrieval-evaluation.json"
    )
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    persisted = json.loads(artifact.read_text())
    assert confidential_value not in artifact.read_text()
    return persisted
