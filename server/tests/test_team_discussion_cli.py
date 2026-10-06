"""Assignment/discussion CLI acceptance specified before implementation.

Failure scenarios: omitted identifiers or malformed/bounded inputs send a request;
assignment routes lose their extraction binding; discussion routes fabricate a
task argument; local mode bypasses the authenticated transport; conflicts replay
writes automatically; schema loses typed page items; Result changes its seven
keys or records paid cost for metadata operations. Transport snapshots exercise
every command in both modes without a database or an external service.
"""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from app.schemas.contracts import Cost, Result
from bid_cli import main as cli
from bid_cli.client import Client

ID = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"
WHEN = "2026-10-05T00:00:00Z"
ASSIGN_INPUT = {"expected_assignment_revision": 0, "assignee_user_id": OTHER, "reason": "assign"}
COMMENT_INPUT = {
    "body": "Synthetic plain text <script> and @member",
    "mentioned_user_ids": [OTHER],
    "client_request_id": ID,
}
THREAD_INPUT = {**COMMENT_INPUT, "expected_card_revision": 1}
CASES = [
    (
        ["assign", "--task", ID, "--requirement", OTHER, "--job", ID],
        "PUT",
        f"/tasks/{ID}/requirements/{OTHER}/assignment",
        ASSIGN_INPUT,
    ),
    (["thread", "list", "--card", ID], "GET", f"/cards/{ID}/threads", None),
    (["thread", "create", "--card", ID], "POST", f"/cards/{ID}/threads", THREAD_INPUT),
    (
        ["comment", "list", "--card", ID, "--thread", OTHER],
        "GET",
        f"/cards/{ID}/threads/{OTHER}/comments",
        None,
    ),
    (
        ["comment", "add", "--card", ID, "--thread", OTHER],
        "POST",
        f"/cards/{ID}/threads/{OTHER}/comments",
        COMMENT_INPUT,
    ),
]


def response_data(method, path, *, empty=False):
    from app.schemas import team_workflow as schemas

    assignment = {
        "org_id": ID,
        "task_id": ID,
        "extraction_job_id": ID,
        "requirement_id": OTHER,
        "revision": 1,
        "assignee_user_id": OTHER,
        "changed_by_user_id": ID,
        "changed_at": WHEN,
    }
    thread = {
        "id": OTHER,
        "org_id": ID,
        "task_id": ID,
        "card_id": ID,
        "created_card_revision_id": ID,
        "revision": 1,
        "created_by_user_id": ID,
        "created_at": WHEN,
        "last_message_at": WHEN,
        "message_count": 1,
    }
    comment = {
        **COMMENT_INPUT,
        "id": ID,
        "org_id": ID,
        "task_id": ID,
        "card_id": ID,
        "thread_id": OTHER,
        "author_user_id": ID,
        "created_at": WHEN,
    }
    if path.endswith("assignment"):
        model, data, items = schemas.AssignmentData, {"assignment": assignment}, []
    elif method == "POST" and path.endswith("threads"):
        model, data, items = (
            schemas.ThreadCreatedData,
            {"thread": thread, "first_comment": comment},
            [],
        )
    elif method == "POST":
        model, data, items = schemas.CommentData, {"comment": comment}, []
    else:
        is_comments = path.endswith("comments")
        item_model = schemas.CommentMessageView if is_comments else schemas.CommentThreadView
        items = [] if empty else [item_model.model_validate(comment if is_comments else thread)]
        model, data = (
            schemas.PageData,
            {
                "org_id": ID,
                "task_id": ID,
                "card_id": ID,
                "thread_id": OTHER if is_comments else None,
                "next_cursor": None,
                "returned": len(items),
                "has_more": False,
            },
        )
        items = [item.model_dump(mode="json") for item in items]
    return model.model_validate(data).model_dump(mode="json"), items


def test_discussion_transport_and_snapshots(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("BID_SESSION", "synthetic-only")
    monkeypatch.setenv("BID_ORG", ID)
    calls, snapshots = [], {}
    empty, failure = False, None

    def handler(request):
        assert request.headers["authorization"] == "Bearer synthetic-only"
        assert request.headers["x-org-id"] == ID
        calls.append(
            (
                request.method,
                request.url.path,
                dict(request.url.params),
                json.loads(request.content) if request.content else None,
            )
        )
        if failure:
            status, code, exit_code = failure
            return httpx.Response(
                status,
                json=Result(
                    ok=False,
                    command="card comment add",
                    data={
                        "error": {
                            "code": code,
                            "message": "Synthetic failure",
                            "exit_code": exit_code,
                        }
                    },
                ).model_dump(mode="json"),
            )
        data, items = response_data(request.method, request.url.path, empty=empty)
        return httpx.Response(
            200,
            json=Result(ok=True, command="card assign", data=data, items=items).model_dump(
                mode="json"
            ),
        )

    @asynccontextmanager
    async def transport(self):
        async with httpx.AsyncClient(
            base_url="http://local", transport=httpx.MockTransport(handler)
        ) as client:
            yield client

    monkeypatch.setattr(Client, "transport", transport)
    input_file = tmp_path / "discussion.json"
    for mode in ("remote", "local"):
        for flags, method, path, input_body in CASES:
            extra = []
            if input_body is not None:
                input_file.write_text(json.dumps(input_body))
                extra = ["--input", str(input_file)]
            cli.main(["--mode", mode, "card", *flags, *extra, "--json"])
            body = json.loads(capsys.readouterr().out)
            assert len(body) == 7 and body["cost"] == Cost().model_dump(mode="json")
            assert calls[-1][:2] == (method, "/v4" + path)
            assert calls[-1][3] == input_body
            assert calls[-1][2] == (
                {"extraction_job_id": ID}
                if method == "PUT"
                else {"limit": "50"}
                if method == "GET"
                else {}
            )
            body["duration_ms"] = 0
            command = " ".join(flags[:1] if flags[0] == "assign" else flags[:2])
            assert body["command"] == "card " + command
            snapshots[f"{mode}:{command}"] = body
        empty = True
        for flags, method, _path, _ in CASES:
            if method != "GET":
                continue
            cli.main(
                [
                    "--mode",
                    mode,
                    "card",
                    *flags,
                    "--cursor",
                    "opaque-page",
                    "--limit",
                    "100",
                    "--json",
                ]
            )
            body = json.loads(capsys.readouterr().out)
            assert calls[-1][2] == {"cursor": "opaque-page", "limit": "100"}
            assert body["items"] == [] and body["data"]["returned"] == 0
            body["duration_ms"] = 0
            snapshots[f"{mode}:empty:{' '.join(flags[:2])}"] = body
        empty = False
        for status, code, exit_code in (
            (401, "invalid_session", 4),
            (403, "forbidden", 4),
            (404, "not_found", 4),
            (409, "revision_conflict", 2),
            (409, "idempotency_conflict", 2),
            (409, "task_archived", 2),
            (429, "rate_limited", 3),
            (503, "unavailable", 3),
        ):
            failure = status, code, exit_code
            input_file.write_text(json.dumps(COMMENT_INPUT))
            before = len(calls)
            with pytest.raises(SystemExit) as error:
                cli.main(
                    [
                        "--mode",
                        mode,
                        "card",
                        "comment",
                        "add",
                        "--card",
                        ID,
                        "--thread",
                        OTHER,
                        "--input",
                        str(input_file),
                        "--json",
                    ]
                )
            assert error.value.code == exit_code and len(calls) == before + 1
            body = json.loads(capsys.readouterr().out)
            assert len(body) == 7 and not body["ok"] and body["items"] == []
            assert body["cost"] == Cost().model_dump(mode="json")
            body["duration_ms"] = 0
            snapshots[f"{mode}:{code}"] = body
        failure = None
    snapshot = Path(__file__).with_name("snapshots") / "team-discussion-cli.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(json.dumps(snapshots, ensure_ascii=False, indent=2) + "\n")
    assert snapshots == json.loads(snapshot.read_text())


def test_discussion_invalid_inputs_do_not_send(monkeypatch, capsys, tmp_path):
    calls = []
    monkeypatch.setattr(cli, "call", lambda *a, **kw: calls.append(a))
    for flags in (
        ["assign", "--task", ID, "--requirement", OTHER, "--job", ID],
        ["thread", "list", "--card", "invalid"],
        ["thread", "list", "--card", ID, "--limit", "101"],
        ["thread", "list", "--card", ID, "--cursor", "x" * 1025],
        ["comment", "list", "--card", ID],
        ["comment", "list", "--card", ID, "--thread", OTHER, "--limit", "0"],
        ["thread", "list", "--card", ID, "--task", ID],
    ):
        with pytest.raises(SystemExit) as error:
            cli.main(["card", *flags, "--json"])
        assert error.value.code == 2
        capsys.readouterr()
    invalid_file = tmp_path / "invalid-discussion.json"
    for flags, invalid in (
        (CASES[0][0], {**ASSIGN_INPUT, "expected_assignment_revision": -1}),
        (CASES[0][0], {**ASSIGN_INPUT, "assignee_user_id": "invalid"}),
        (CASES[0][0], {**ASSIGN_INPUT, "reason": " "}),
        (CASES[2][0], {**THREAD_INPUT, "expected_card_revision": 0}),
        (CASES[2][0], {**THREAD_INPUT, "body": " "}),
        (CASES[2][0], {**THREAD_INPUT, "body": "x" * 4001}),
        (CASES[4][0], {**COMMENT_INPUT, "mentioned_user_ids": [OTHER, OTHER]}),
        (CASES[4][0], {"body": "missing request ID"}),
        (CASES[4][0], {**COMMENT_INPUT, "author_user_id": OTHER}),
    ):
        invalid_file.write_text(json.dumps(invalid))
        with pytest.raises(SystemExit) as error:
            cli.main(["card", *flags, "--input", str(invalid_file), "--json"])
        assert error.value.code == 2
        body = json.loads(capsys.readouterr().out)
        assert not body["ok"] and "Synthetic plain text" not in json.dumps(body)
    assert calls == []


def test_discussion_schema_discovery(capsys):
    from app.schemas import team_workflow as schemas

    cli.main(["schema", "--json"])
    names = json.loads(capsys.readouterr().out)["data"]["commands"]
    for command, input_model, output_model in (
        ("card assign", schemas.RequirementAssignmentSet, schemas.AssignmentData),
        ("card thread list", schemas.PageQuery, schemas.PageData),
        ("card thread create", schemas.CommentThreadCreate, schemas.ThreadCreatedData),
        ("card comment list", schemas.PageQuery, schemas.PageData),
        ("card comment add", schemas.CommentReplyCreate, schemas.CommentData),
    ):
        assert names[command]["input"] == input_model.model_json_schema()
        assert names[command]["output"] == output_model.model_json_schema()
        assert names[command]["cli_parameters"]
    assert names["card thread list"]["items"] == schemas.CommentThreadView.model_json_schema()
    assert names["card comment list"]["items"] == schemas.CommentMessageView.model_json_schema()
    assert "card signoff add" not in names and "card policy set" not in names
