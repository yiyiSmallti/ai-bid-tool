"""DB-free CLI integration acceptance, specified before implementation.

Failure scenarios: malformed or oversized files reach the API; confirm/reopen
accepts the opposite action; paired rejected filters or paging bounds are ignored;
legacy discovery exposes new commands/enums; progress silently chooses an extraction;
mutations retry after a conflict or leak input text in errors; modes use distinct
routes/contracts; receipt/replay output changes the seven-key Result or zero Cost.
These transport snapshots do not establish PostgreSQL authorization or source validity.
"""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from app.schemas.contracts import Result
from bid_cli import main as cli
from bid_cli.client import Client
from bid_cli.schema import command_schema

ID = "00000000-0000-0000-0000-000000000001"
SHA = "a" * 64
SOURCE = {
    "document_id": ID,
    "chunk_id": ID,
    "page": 1,
    "quote": "Synthetic requirement",
}
CONTENT = {
    "category": "technical",
    "starred": False,
    "text": SOURCE["quote"],
    "condition": {},
    "source": SOURCE,
}
MANUAL = {"content": CONTENT, "reason": "Synthetic omitted item"}
CREATE = {**MANUAL, "request_id": ID, "expected_preview_hash": SHA}
DECISION = {
    "request_id": ID,
    "action": "confirm",
    "expected_revision": 1,
    "expected_review_hash": SHA,
    "reason": "Synthetic review",
}
BATCH = {
    "request_id": ID,
    "expected_set_revision": 1,
    "items": [{"requirement_id": ID, "expected_revision": 1, "expected_review_hash": SHA}],
    "reviewed_each": True,
    "reason": "Synthetic batch",
}
COMMANDS = {
    "req review-list": ["req", "review-list", "--task", ID, "--job", ID],
    "req show": ["req", "show", "--id", ID],
    "req review-history": ["req", "review-history", "--id", ID],
    "req rejected": ["req", "rejected", "--task", ID, "--job", ID],
    "req add preview": ["req", "add", "--task", ID, "--dry-run"],
    "req add": ["req", "add", "--task", ID],
    "req confirm": ["req", "confirm", "--id", ID],
    "req reopen": ["req", "reopen", "--id", ID],
    "req confirm-batch": ["req", "confirm-batch", "--task", ID, "--job", ID],
}
PATHS = {
    "req review-list": ("GET", f"/tasks/{ID}/extractions/{ID}/requirement-reviews"),
    "req show": ("GET", f"/requirements/{ID}/review"),
    "req review-history": ("GET", f"/requirements/{ID}/review-history"),
    "req rejected": ("GET", f"/tasks/{ID}/extractions/{ID}/rejected-items"),
    "req add preview": ("POST", f"/tasks/{ID}/requirements/manual-preview"),
    "req add": ("POST", f"/tasks/{ID}/requirements/manual"),
    "req confirm": ("POST", f"/requirements/{ID}/review-decisions"),
    "req reopen": ("POST", f"/requirements/{ID}/review-decisions"),
    "req confirm-batch": ("POST", f"/tasks/{ID}/extractions/{ID}/requirement-confirmations"),
}


def install(monkeypatch, handler):
    monkeypatch.setenv("BID_SESSION", "synthetic-session")
    monkeypatch.setenv("BID_ORG", ID)

    @asynccontextmanager
    async def transport(self):
        async with httpx.AsyncClient(
            base_url="http://local", transport=httpx.MockTransport(handler)
        ) as client:
            yield client

    monkeypatch.setattr(Client, "transport", transport)


def invoke(capsys, args, exit_code=0):
    if exit_code:
        with pytest.raises(SystemExit) as failure:
            cli.main([*args, "--json"])
        assert failure.value.code == exit_code
    else:
        cli.main([*args, "--json"])
    output = json.loads(capsys.readouterr().out)
    output["duration_ms"] = 0
    assert set(output) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
    return output


def test_requirement_cli_transport_snapshots(monkeypatch, capsys, tmp_path):
    from app.schemas import requirement_confirmation as schemas

    snapshots, calls = {}, []
    expected_name = ""
    failure = None
    replayed = False
    empty = False
    pin = {
        "source": SOURCE,
        "document_sha256": SHA,
        "chunk_sha256": SHA,
        "location_sha256": SHA,
        "quote_sha256": SHA,
        "start": 0,
        "end": len(SOURCE["quote"]),
        "verifier_version": "literal-v1",
        "binding_sha256": SHA,
    }
    review = {
        "org_id": ID,
        "task_id": ID,
        "extraction_job_id": ID,
        "requirement_id": ID,
        "origin": "manual_missing",
        "content": CONTENT,
        "revision": 1,
        "review_hash": SHA,
        "state": "unconfirmed",
        "citation_valid": True,
        "source_pin": pin,
        "next_actor": {
            "user_id": ID,
            "basis": "owner",
            "action": "confirm_requirement",
            "can_current_actor_act": True,
        },
    }
    scope = {
        "org_id": ID,
        "task_id": ID,
        "extraction_job_id": ID,
        "document_id": ID,
        "origin": "manual",
        "revision": 1,
        "membership_sha256": SHA,
        "confirmation_sha256": SHA,
        "summary": {
            "total": 1,
            "confirmed": 0,
            "unconfirmed": 1,
            "legacy_unconfirmed": 0,
            "invalidated": 0,
            "invalid_citations": 0,
            "manual_added": 1,
        },
        "last_event_cursor": "synthetic-cursor",
    }

    def handler(request):
        assert request.headers["authorization"] == "Bearer synthetic-session"
        assert request.headers["x-org-id"] == ID
        expected_method, expected_path = PATHS[expected_name]
        assert request.method == expected_method and request.url.path == "/v4" + expected_path
        calls.append(request)
        if failure:
            status, code, exit_code = failure
            return httpx.Response(
                status,
                json=Result(
                    ok=False,
                    command="req show",
                    data={
                        "error": {
                            "code": code,
                            "message": "Synthetic failure",
                            "exit_code": exit_code,
                        }
                    },
                ).model_dump(mode="json"),
            )
        items = []
        if expected_name == "req review-list":
            data = schemas.ReviewPageData(
                task_id=ID, extraction_job_id=ID, total=1, scope=scope
            ).model_dump(mode="json")
            items = (
                []
                if empty
                else [schemas.RequirementReviewView.model_validate(review).model_dump(mode="json")]
            )
        elif expected_name in {"req rejected", "req review-history"}:
            data = schemas.PageData(
                task_id=ID, extraction_job_id=ID, total=0 if empty else 1
            ).model_dump(mode="json")
            if not empty:
                if expected_name == "req rejected":
                    items = [
                        schemas.RejectedItemView(
                            job_id=ID,
                            index=0,
                            summary_sha256=SHA,
                            position="page 1",
                            quote=SOURCE["quote"],
                            reason="unverified_location",
                            entered_requirement_ids=[ID],
                            entered_requirement_count=1,
                        ).model_dump(mode="json")
                    ]
                else:
                    items = [
                        schemas.RequirementReviewEvent(
                            id=ID,
                            org_id=ID,
                            task_id=ID,
                            extraction_job_id=ID,
                            requirement_id=ID,
                            revision=1,
                            action="manual_add",
                            state_after="unconfirmed",
                            content=CONTENT,
                            review_hash=SHA,
                            source_pin=pin,
                            actor_kind="session",
                            actor_user_id=ID,
                            reason_sha256=SHA,
                            request_id=ID,
                            occurred_at="2026-10-05T00:00:00Z",
                        ).model_dump(mode="json")
                    ]
        elif expected_name == "req add preview":
            data = schemas.ManualEntryPreview(
                task_id=ID,
                extraction_job_id=None,
                creates_manual_scope=True,
                expected_set_revision=None,
                preview_hash=SHA,
                verified_source=pin,
            ).model_dump(mode="json")
        elif expected_name == "req confirm-batch":
            data = schemas.ConfirmationBatchData(
                scope={
                    **scope,
                    "revision": 2,
                    "summary": {**scope["summary"], "confirmed": 1, "unconfirmed": 0},
                },
                request_id=ID,
                event_ids=[ID],
                changed=1,
                replayed=replayed,
            ).model_dump(mode="json")
            items = [
                schemas.ConfirmationReceiptItem(
                    requirement_id=ID,
                    event_id=ID,
                    confirmed_revision=2,
                    current_revision=2,
                    current_state="confirmed",
                ).model_dump(mode="json")
            ]
        else:
            model = (
                schemas.ManualEntryData
                if expected_name == "req add"
                else schemas.RequirementReviewData
            )
            values = {"requirement": review, "scope": scope, "replayed": replayed}
            if expected_name == "req confirm":
                values.update(
                    requirement={
                        **review,
                        "revision": 2,
                        "state": "confirmed",
                        "confirmed_by_user_id": ID,
                        "confirmed_at": "2026-10-05T00:00:00Z",
                        "next_actor": None,
                    },
                    scope={
                        **scope,
                        "revision": 2,
                        "summary": {**scope["summary"], "confirmed": 1, "unconfirmed": 0},
                    },
                )
            elif expected_name == "req reopen":
                values.update(requirement={**review, "revision": 3}, scope={**scope, "revision": 3})
            if expected_name != "req show":
                values.update(request_id=ID, event_ids=[ID])
            if expected_name == "req add":
                values["created_scope"] = True
            data = model.model_validate(values).model_dump(mode="json")
        return httpx.Response(
            201 if expected_name == "req add" and not replayed else 200,
            json=Result(ok=True, command="req show", data=data, items=items).model_dump(
                mode="json"
            ),
        )

    install(monkeypatch, handler)
    for mode in ("local", "remote"):
        for name, flags in COMMANDS.items():
            expected_name = name
            args = list(flags)
            if name in {
                "req add preview",
                "req add",
                "req confirm",
                "req reopen",
                "req confirm-batch",
            }:
                value = (
                    MANUAL
                    if name == "req add preview"
                    else CREATE
                    if name == "req add"
                    else BATCH
                    if name == "req confirm-batch"
                    else {**DECISION, "action": name.split()[-1]}
                )
                path = tmp_path / "input.json"
                path.write_text(json.dumps(value))
                args.extend(["--input", str(path)])
            snapshots[mode + ":" + name] = invoke(capsys, ["--mode", mode, *args])
            assert (
                snapshots[mode + ":" + name]["cost"]
                == Result(ok=True, command="x").model_dump(mode="json")["cost"]
            )
        expected_name = "req add"
        path.write_text(json.dumps(CREATE))
        replayed = True
        snapshots[mode + ":manual-replay"] = invoke(
            capsys, ["--mode", mode, *COMMANDS["req add"], "--input", str(path)]
        )
        replayed = False
        empty = True
        for expected_name in ("req review-list", "req review-history", "req rejected"):
            snapshots[mode + ":empty:" + expected_name] = invoke(
                capsys, ["--mode", mode, *COMMANDS[expected_name]]
            )
        empty = False
        for status, code, exit_code in (
            (422, "nonverbatim_quote", 2),
            (404, "not_found", 4),
            (409, "review_changed", 2),
            (503, "service_unavailable", 3),
        ):
            expected_name = "req show"
            failure = status, code, exit_code
            before = len(calls)
            snapshots[f"{mode}:HTTP{status}"] = invoke(
                capsys, ["--mode", mode, *COMMANDS[expected_name]], exit_code
            )
            assert len(calls) == before + 1
        failure = None
    snapshot = Path(__file__).with_name("snapshots") / "requirement-confirmation-cli.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(
            json.dumps(snapshots, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
    assert snapshots == json.loads(snapshot.read_text())


def test_requirement_cli_validation_prevents_transport(monkeypatch, capsys, tmp_path):
    calls = []
    install(monkeypatch, lambda request: calls.append(request))
    invalid = [
        [*COMMANDS["req review-list"], "--state", "invented"],
        [*COMMANDS["req review-list"], "--rejected-job", ID],
        [*COMMANDS["req review-list"], "--rejected-index", "0"],
        [*COMMANDS["req rejected"], "--limit", "101"],
        [*COMMANDS["req rejected"], "--category", "technical"],
        ["task", "progress", "--task", ID, "--view", "requirement-review"],
        ["task", "progress", "--task", ID, "--job", ID],
        ["task", "board", "--task", ID, "--job", ID, "--view", "invented"],
    ]
    path = tmp_path / "invalid.json"
    for value in (
        {**DECISION, "action": "reopen"},
        {**DECISION, "reason": "  "},
        {**DECISION, "actor": ID},
        [],
        "Synthetic sensitive reason",
    ):
        path.write_text(json.dumps(value))
        output = invoke(capsys, [*COMMANDS["req confirm"], "--input", str(path)], 2)
        assert "Synthetic sensitive reason" not in json.dumps(output)
    path.write_text("x" * (256 * 1024 + 1))
    invalid.append([*COMMANDS["req add"], "--input", str(path)])
    invalid.append([*COMMANDS["req add"], "--input", str(tmp_path / "absent.json")])
    for flags in invalid:
        invoke(capsys, flags, 2)
    assert not calls


def test_requirement_schema_and_legacy_boundaries(capsys):
    from app.schemas import requirement_confirmation as schemas
    from app.schemas.team_workflow import BoardData, BoardRow, TaskProgressData

    current = command_schema(cli.app)
    legacy = command_schema(cli.app, "3.0")
    for name in PATHS:
        name = "req add" if name == "req add preview" else name
        assert name in current["commands"] and name not in legacy["commands"]
        invoke(capsys, ["--contract-version", "3.0", *COMMANDS[name]], 2)
    board = current["commands"]["task board"]
    assert board["output"] == BoardData.model_json_schema()
    assert board["items"] == BoardRow.model_json_schema()
    assert (
        board["variants"]["requirement-review"]["output"]
        == schemas.RequirementBoardData.model_json_schema()
    )
    assert (
        board["variants"]["requirement-review"]["items"]
        == schemas.RequirementBoardItem.model_json_schema()
    )
    progress = current["commands"]["task progress"]
    assert progress["output"] == TaskProgressData.model_json_schema()
    assert (
        progress["variants"]["requirement-review"]["output"]
        == schemas.RequirementProgressData.model_json_schema()
    )
    assert "requirement-review" not in json.dumps(legacy)
    assert '"requirement_review"' not in json.dumps(legacy)
    assert (
        current["commands"]["req add"]["variants"]["dry_run"]["input"]
        == schemas.ManualRequirementInput.model_json_schema()
    )


def test_requirement_cli_filters_and_optin_transport(monkeypatch, capsys):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200, json=Result(ok=True, command="req review-list").model_dump(mode="json")
        )

    install(monkeypatch, handler)
    for mode in ("local", "remote"):
        invoke(
            capsys,
            [
                "--mode",
                mode,
                *COMMANDS["req review-list"],
                "--state",
                "invalidated",
                "--category",
                "technical",
                "--no-starred",
                "--origin",
                "manual_rejected",
                "--rejected-job",
                ID,
                "--rejected-index",
                "0",
                "--limit",
                "100",
                "--cursor",
                "synthetic-page",
            ],
        )
        assert dict(requests[-1].url.params) == {
            "limit": "100",
            "cursor": "synthetic-page",
            "state": "invalidated",
            "category": "technical",
            "starred": "false",
            "origin": "manual_rejected",
            "rejected_job_id": ID,
            "rejected_index": "0",
        }
        invoke(
            capsys,
            [
                "--mode",
                mode,
                "task",
                "board",
                "--task",
                ID,
                "--job",
                ID,
                "--view",
                "requirement-review",
                "--bucket",
                "requirement_review",
                "--blocker",
                "requirement_unconfirmed",
            ],
        )
        assert requests[-1].url.path == f"/v4/tasks/{ID}/board"
        assert requests[-1].url.params["view"] == "requirement-review"
        assert requests[-1].url.params["extraction_job_id"] == ID
        assert requests[-1].url.params["bucket"] == "requirement_review"
        invoke(
            capsys,
            [
                "--mode",
                mode,
                "task",
                "progress",
                "--task",
                ID,
                "--job",
                ID,
                "--view",
                "requirement-review",
            ],
        )
        assert requests[-1].url.path == f"/v4/tasks/{ID}/progress"
        assert dict(requests[-1].url.params) == {
            "limit": "20",
            "view": "requirement-review",
            "extraction_job_id": ID,
        }
    before = len(requests)
    invoke(
        capsys, ["task", "board", "--task", ID, "--job", ID, "--bucket", "requirement_review"], 2
    )
    assert len(requests) == before
