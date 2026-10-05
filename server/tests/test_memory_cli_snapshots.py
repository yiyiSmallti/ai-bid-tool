"""Saved snapshots of every approved memory command at the CLI boundary."""

import json
import os
from pathlib import Path
from uuid import UUID

import pytest
from app.core.errors import ServiceError
from app.schemas import memory_contracts as models
from app.schemas.contracts import Result
from bid_cli import main as cli

ID = "00000000-0000-0000-0000-000000000001"
RID = "00000000-0000-0000-0000-000000000002"
NOW = "2026-10-05T00:00:00Z"
SHA = "a" * 64
CONTENT = {
    "kind": "rule",
    "conflict_key": "delivery.source",
    "text": "交货期以答疑为准",
    "tags": ["delivery"],
}
SOURCE = {"origin": "human"}


def fixtures():
    revision = models.MemoryRevisionView.model_validate(
        {
            "id": RID,
            "org_id": ID,
            "memory_id": ID,
            "revision": 1,
            "target": {"scope": "org"},
            "content": CONTENT,
            "status": "candidate",
            "source": SOURCE,
            "content_sha256": SHA,
            "created_at": NOW,
            "created_by": ID,
            "actor_kind": "session",
        }
    )
    memory = models.MemoryView(
        id=UUID(ID), org_id=UUID(ID), current=revision, effective_status="candidate"
    )
    data = models.MemoryData(memory=memory)
    page = models.MemoryPageData(returned=1)
    retrieval = models.MemoryRetrievalData.model_validate(
        {
            "retrieval_id": None,
            "org_id": ID,
            "mode": "keyword",
            "retrieval_version": "keyword-v1",
            "priority_version": "memory-priority-v1",
            "query_sha256": SHA,
            "manifest_sha256": SHA,
            "scopes": ["org"],
            "epochs": [{"org_id": ID, "scope": "org", "owner_id": ID, "epoch": 1}],
            "valid_until": None,
            "context_chars": len(CONTENT["text"]),
            "preview": True,
            "currently_valid": True,
        }
    )
    hit = models.MemoryHit.model_validate(
        {
            "memory": {
                "memory_id": ID,
                "revision_id": RID,
                "revision": 2,
                "scope": "org",
                "content_sha256": SHA,
                "sent_sha256": SHA,
            },
            "kind": "rule",
            "conflict_key": CONTENT["conflict_key"],
            "text": CONTENT["text"],
            "rank": 1,
            "priority": 2,
            "relevance": 110,
            "matched_by": ["exact", "keyword"],
        }
    )
    feedback = models.MemoryFeedbackView.model_validate(
        {
            "id": ID,
            "org_id": ID,
            "task_id": ID,
            "card_id": ID,
            "before_revision_id": ID,
            "after_revision_id": RID,
            "actor_user_id": ID,
            "kind": "card_rejected",
            "review_domain": "commercial",
            "created_at": NOW,
            "sanitized_sha256": SHA,
            "sanitizer_version": "memory-sanitize-v1:redaction-v1",
        }
    )
    sample = models.MemoryEvalSampleView.model_validate(
        {
            "id": ID,
            "org_id": ID,
            "task_id": ID,
            "feedback_event_id": ID,
            "card_id": ID,
            "before_revision_id": ID,
            "after_revision_id": RID,
            "label": "card_rejected",
            "actor_user_id": ID,
            "generator_version": "feedback-copy-v1",
            "sanitized_sha256": SHA,
            "created_at": NOW,
            "review_state": "unreviewed",
            "review_revision": 1,
        }
    )
    detail = models.MemoryEvalDetailData(
        sample=sample, sanitized_summary="人工驳回：交货期应采用答疑。"
    )
    reviewed = sample.model_copy(
        update={
            "review_state": "accepted",
            "review_revision": 2,
            "reviewed_by": UUID(ID),
            "reviewed_at": revision.created_at,
        }
    )
    submission = models.MemoryJobSubmissionData(
        job_id=UUID(ID), task_id=UUID(ID), reused=False, dry_run=False, event_count=1
    )
    active_revision = revision.model_copy(
        update={
            "revision": 2,
            "status": "active",
            "confirmed_by": UUID(ID),
            "confirmed_at": revision.created_at,
            "decision": "approve",
            "decision_reason_sha256": SHA,
        }
    )
    active = models.MemoryView(
        id=UUID(ID), org_id=UUID(ID), current=active_revision, effective_status="active"
    )
    disabled_revision = revision.model_copy(
        update={
            "revision": 2,
            "status": "disabled",
            "decision": "disable",
            "decision_reason_sha256": SHA,
        }
    )
    disabled = models.MemoryView(
        id=UUID(ID), org_id=UUID(ID), current=disabled_revision, effective_status="disabled"
    )
    outputs = {}
    for action in ("add", "show", "update"):
        outputs[f"memory {action}"] = (data, [])
    outputs["memory approve"] = (models.MemoryData(memory=active), [])
    outputs["memory reject"] = (
        models.MemoryData(
            memory=disabled.model_copy(
                update={"current": disabled_revision.model_copy(update={"decision": "reject"})}
            )
        ),
        [],
    )
    outputs["memory disable"] = (models.MemoryData(memory=disabled), [])
    deleted = models.MemoryView(
        id=UUID(ID),
        org_id=UUID(ID),
        current=disabled_revision.model_copy(update={"decision": "delete"}),
        effective_status="deleted",
        deleted_at=revision.created_at,
    )
    outputs["memory delete"] = (models.MemoryData(memory=deleted), [])
    outputs["memory list"] = (page, [memory])
    outputs["memory history"] = (page, [revision])
    outputs["memory retrieve"] = (retrieval, [hit])
    outputs["memory retrieval show"] = (
        retrieval.model_copy(update={"retrieval_id": UUID(ID), "preview": False}),
        [hit],
    )
    outputs["memory used"] = (models.MemoryCallData(job_id=UUID(ID), calls=[]), [])
    outputs["memory feedback list"] = (page, [feedback])
    outputs["memory candidates run"] = (submission, [])
    outputs["memory samples list"] = (page, [sample])
    outputs["memory samples show"] = (detail, [])
    outputs["memory samples review"] = (models.MemoryEvalData(sample=reviewed), [])
    return outputs


def test_saved_memory_cli_snapshots(monkeypatch, tmp_path, capsys):
    outputs = fixtures()
    calls = []
    selected = None
    failure = None

    def call(method, path, **kwargs):
        calls.append({"method": method, "path": path, **kwargs})
        if failure is not None:
            raise ServiceError(*failure)
        data, items = outputs[selected]
        # Validate synthesized outputs against their public Pydantic type before transport.
        data = type(data).model_validate(data.model_dump(mode="json"))
        items = [type(item).model_validate(item.model_dump(mode="json")) for item in items]
        return Result(
            ok=True,
            command=selected,
            data=data.model_dump(mode="json"),
            items=[item.model_dump(mode="json") for item in items],
        ).model_dump(mode="json")

    monkeypatch.setattr(cli, "call", call)
    create = tmp_path / "create.json"
    update = tmp_path / "update.json"
    approve = tmp_path / "approve.json"
    reject = tmp_path / "reject.json"
    manage = tmp_path / "manage.json"
    retrieve = tmp_path / "retrieve.json"
    candidates = tmp_path / "candidates.json"
    review = tmp_path / "review.json"
    for path, body in (
        (create, {"target": {"scope": "org"}, "content": CONTENT}),
        (update, {"expected_revision": 1, "content": CONTENT}),
        (approve, {"expected_revision": 1, "action": "approve", "reason": "Reviewed"}),
        (reject, {"expected_revision": 1, "action": "reject", "reason": "Reviewed"}),
        (manage, {"expected_revision": 1, "reason": "Reviewed"}),
        (retrieve, {"org_id": ID, "scopes": ["org"], "query": CONTENT["text"]}),
        (candidates, {"event_ids": [ID]}),
        (review, {"expected_revision": 1, "action": "accept", "reason": "Reviewed"}),
    ):
        path.write_text(json.dumps(body, ensure_ascii=False))
    commands = {
        "memory add": ["memory", "add", "--input", str(create)],
        "memory list": ["memory", "list", "--scope", "org"],
        "memory show": ["memory", "show", "--id", ID],
        "memory update": ["memory", "update", "--id", ID, "--input", str(update)],
        "memory history": ["memory", "history", "--id", ID],
        "memory approve": ["memory", "approve", "--id", ID, "--input", str(approve)],
        "memory reject": ["memory", "reject", "--id", ID, "--input", str(reject)],
        "memory disable": ["memory", "disable", "--id", ID, "--input", str(manage)],
        "memory delete": ["memory", "delete", "--id", ID, "--input", str(manage)],
        "memory retrieve": ["memory", "retrieve", "--input", str(retrieve), "--dry-run"],
        "memory retrieval show": ["memory", "retrieval", "show", "--id", ID],
        "memory used": ["memory", "used", "--job", ID],
        "memory feedback list": ["memory", "feedback", "list", "--task", ID],
        "memory candidates run": [
            "memory",
            "candidates",
            "run",
            "--task",
            ID,
            "--input",
            str(candidates),
        ],
        "memory samples list": ["memory", "samples", "list", "--task", ID],
        "memory samples show": ["memory", "samples", "show", "--id", ID],
        "memory samples review": [
            "memory",
            "samples",
            "review",
            "--id",
            ID,
            "--input",
            str(review),
        ],
    }
    actual = {}
    for selected, args in commands.items():
        calls.clear()
        cli.main([*args, "--json"])
        body = json.loads(capsys.readouterr().out)
        body["duration_ms"] = 0
        actual[selected] = {"exit_code": 0, "output": body, "requests": calls.copy()}
    selected = "memory show"
    for exit_code, failure_case in [
        (2, ("memory_revision_conflict", "Refresh exact revision", 409, 2)),
        (3, ("queue_unavailable", "Job saved for retry", 503, 3)),
        (4, ("not_found", "Resource not found", 404, 4)),
    ]:
        failure = failure_case
        calls.clear()
        with pytest.raises(SystemExit) as stopped:
            cli.main([*commands[selected], "--json"])
        assert stopped.value.code == exit_code
        body = json.loads(capsys.readouterr().out)
        body["duration_ms"] = 0
        actual[f"error exit {exit_code}"] = {
            "exit_code": exit_code,
            "output": body,
            "requests": calls.copy(),
        }
    failure = None
    selected = "memory candidates run"

    async def waiting(job_id, wait_timeout):
        result = models.MemoryCandidateJobResult.model_validate(
            {
                "job_id": ID,
                "run_id": RID,
                "completion": "partial",
                "items": [
                    {"event_id": ID, "outcome": "failed", "error_code": "synthetic_event_failure"}
                ],
                "generator_version": "feedback-copy-v1",
                "stop_reason": "event_failure",
            }
        )
        return Result(
            ok=True,
            command="job wait",
            data={"result": result.model_dump(mode="json")},
            warnings=["memory_candidates_partial"],
        ).model_dump(mode="json")

    monkeypatch.setattr(cli, "wait_for_job", waiting)
    calls.clear()
    with pytest.raises(SystemExit) as stopped:
        cli.main([*commands[selected], "--wait", "--json"])
    assert stopped.value.code == 5
    body = json.loads(capsys.readouterr().out)
    body["duration_ms"] = 0
    actual["partial exit 5"] = {"exit_code": 5, "output": body, "requests": calls.copy()}
    snapshot = Path(__file__).with_name("snapshots") / "memory-cli.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(json.dumps(actual, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    assert actual == json.loads(snapshot.read_text())
