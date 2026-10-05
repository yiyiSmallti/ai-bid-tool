"""CLI transport acceptance specified before implementation.

Failure scenarios: invalid filters/bounds or member input sends a request; revision
conflict triggers an automatic retry; task events emits JSON-lines; local mode
changes the authenticated route; schema omits slice-one commands or advertises
slice-two/three writes. Every Result retains its seven keys and actual zero Cost.
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

ID = "00000000-0000-0000-0000-000000000001"
WHEN = "2026-10-05T00:00:00Z"
WORKFLOW = {
    "org_id": ID,
    "task_id": ID,
    "owner_user_id": ID,
    "state": "active",
    "revision": 1,
    "access_epoch": 1,
    "co_sign_starred": False,
    "rule_revision": 1,
    "last_event_cursor": "opaque-snapshot",
    "archived_at": None,
    "archived_by_user_id": None,
}
MEMBER = {
    "org_id": ID,
    "task_id": ID,
    "user_id": ID,
    "display_label": "Synthetic member",
    "role": "observer",
    "review_domains": [],
    "active": True,
    "revision": 1,
}
JOB = {
    "job_id": ID,
    "extraction_job_id": ID,
    "state": "running",
    "run_id": ID,
    "attempts": 1,
    "progress": {"completed": 1, "total": None},
    "updated_at": WHEN,
}


def response_page(path, empty=False):
    from app.schemas import team_workflow as schemas

    data, items, model = {"workflow": WORKFLOW}, [], schemas.TaskWorkflowData
    page = {
        "org_id": ID,
        "task_id": ID,
        "card_id": None,
        "thread_id": None,
        "next_cursor": None,
        "returned": 0 if empty else 1,
        "has_more": False,
    }
    if path == "progress":
        data, items, model = (
            {
                "org_id": ID,
                "task_id": ID,
                "workflow": WORKFLOW,
                "event_cursor": "opaque-snapshot",
                "as_of": WHEN,
                "next_cursor": None,
                "returned": 0 if empty else 1,
            },
            [] if empty else [JOB],
            schemas.TaskProgressData,
        )
    elif path == "members":
        data, items, model = page, [] if empty else [MEMBER], schemas.PageData
    elif path == "member-candidates":
        data, items, model = (
            page,
            []
            if empty
            else [
                {
                    "org_id": ID,
                    "user_id": ID,
                    "display_label": "Synthetic candidate",
                    "org_role": "technical",
                    "active": True,
                }
            ],
            schemas.PageData,
        )
    elif path == "members/" + ID:
        data, model = (
            {"workflow": {**WORKFLOW, "revision": 2}, "member": MEMBER},
            schemas.TaskMemberData,
        )
    elif path == "activity":
        data, items, model = (
            page,
            []
            if empty
            else [{"id": ID, "actor_user_id": ID, "action": "task_archived", "created_at": WHEN}],
            schemas.PageData,
        )
    elif path == "events/poll":
        data, model = (
            {
                "org_id": ID,
                "task_id": ID,
                "next_cursor": None,
                "head_cursor": "opaque-snapshot",
                "has_more": False,
                "reset_required": {
                    "type": "reset_required",
                    "reason": "initial_snapshot_required",
                    "head_cursor": "opaque-snapshot",
                },
            },
            schemas.EventReplayData,
        )
    elif path == "archive":
        data = {
            "workflow": {
                **WORKFLOW,
                "state": "archived",
                "revision": 2,
                "archived_at": WHEN,
                "archived_by_user_id": ID,
            }
        }
    elif path == "board":
        counts = {
            key: 0
            for key in (
                "total",
                "gap",
                "draft_card",
                "pending_review",
                "needs_material",
                "confirmed",
                "comply_only",
            )
        }
        if not empty:
            counts.update(total=1, gap=1)
        budget = {
            "org_id": ID,
            "task_id": ID,
            "revision": 1,
            "limit": "10",
            "currency": "USD",
            "state": "active",
            "spent": "0",
            "reserved": "0",
            "available": "10",
            "unpriced_calls": 0,
            "unresolved_calls": 0,
            "history_complete": True,
            "as_of": WHEN,
        }
        data, model = (
            {
                "org_id": ID,
                "task_id": ID,
                "extraction_job_id": ID,
                "workflow": WORKFLOW,
                "counts": counts,
                "matching": 0 if empty else 1,
                "returned": 0 if empty else 1,
                "budget": budget,
                "next_cursor": None,
                "event_cursor": "opaque-snapshot",
                "as_of": WHEN,
                "refresh_by": "2026-10-05T00:00:25Z",
                "jobs": [],
                "activity": [],
            },
            schemas.BoardData,
        )
        items = (
            []
            if empty
            else [
                {
                    "requirement_id": ID,
                    "title": "Synthetic requirement",
                    "category": "technical",
                    "starred": False,
                    "bucket": "gap",
                    "card_id": None,
                    "card_revision": None,
                    "card_state": None,
                    "eligibility": None,
                    "review_domain": "technical",
                    "owner_user_id": None,
                    "assignment_revision": 0,
                    "co_sign": None,
                    "flags": {
                        "human_needs_material": False,
                        "model_needs_material_hint": False,
                        "certificate_date_advisory": False,
                        "final_export_prototype_blocked": False,
                    },
                    "blockers": ["missing_card"],
                    "next_actions": [
                        {
                            "code": "view",
                            "target": {"kind": "requirement", "id": ID},
                            "eligible_user_ids": [],
                            "eligible_domains": [],
                        }
                    ],
                    "comment_thread_count": 0,
                }
            ]
        )
    item_model = {
        "board": schemas.BoardRow,
        "progress": schemas.BoardJobView,
        "members": schemas.TaskMemberView,
        "member-candidates": schemas.MemberCandidateView,
        "activity": schemas.BoardActivityView,
    }.get(path)
    if item_model:
        items = [item_model.model_validate(item).model_dump(mode="json") for item in items]
    return model.model_validate(data).model_dump(mode="json"), items


CASES = [
    (["workflow"], "GET", "workflow"),
    (["progress", "--limit", "20"], "GET", "progress"),
    (["member", "list"], "GET", "members"),
    (["member", "candidates"], "GET", "member-candidates"),
    (["member", "set", "--user", ID], "PUT", "members/" + ID),
    (
        ["member", "remove", "--user", ID, "--expected-revision", "1", "--reason", "remove"],
        "POST",
        "members/" + ID + "/remove",
    ),
    (
        [
            "handover",
            "--to-user",
            ID,
            "--previous-owner-role",
            "observer",
            "--expected-revision",
            "1",
            "--reason",
            "handover",
        ],
        "POST",
        "handover",
    ),
    (["archive", "--expected-revision", "1", "--reason", "archive"], "POST", "archive"),
    (["unarchive", "--expected-revision", "1", "--reason", "unarchive"], "POST", "unarchive"),
    (["board", "--job", ID, "--mine", "--unassigned"], "GET", "board"),
    (["activity"], "GET", "activity"),
    (["events", "--wait-seconds", "25"], "GET", "events/poll"),
]


def test_workflow_transport_and_snapshots(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("BID_SESSION", "synthetic-only")
    monkeypatch.setenv("BID_ORG", ID)
    calls, snapshots = [], {}
    failure = None
    empty = False

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
                    command="task archive",
                    data={
                        "error": {
                            "code": code,
                            "message": "Synthetic failure",
                            "exit_code": exit_code,
                        }
                    },
                ).model_dump(mode="json"),
            )
        path = request.url.path.split(f"/tasks/{ID}/", 1)[1]
        data, items = response_page(path, empty)
        return httpx.Response(
            200,
            json=Result(ok=True, command="task workflow", data=data, items=items).model_dump(
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
    input_file = tmp_path / "member.json"
    input_file.write_text(
        json.dumps(
            {"role": "observer", "review_domains": [], "expected_revision": 1, "reason": "add"}
        )
    )
    for mode in ("remote", "local"):
        for flags, method, path in CASES:
            flags = flags + (["--input", str(input_file)] if path == "members/" + ID else [])
            cli.main(["--mode", mode, "task", *flags, "--task", ID, "--json"])
            body = json.loads(capsys.readouterr().out)
            assert len(body) == 7 and body["cost"]["usd"] == 0
            assert calls[-1][:2] == (method, f"/v4/tasks/{ID}/{path}")
            body["duration_ms"] = 0
            snapshots[mode + ":" + " ".join(flags[:2])] = body
        empty = True
        for flags, method, path in CASES:
            if method != "GET" or path == "workflow":
                continue
            cli.main(["--mode", mode, "task", *flags, "--task", ID, "--json"])
            body = json.loads(capsys.readouterr().out)
            body["duration_ms"] = 0
            assert body["items"] == []
            snapshots[mode + ":empty:" + " ".join(flags[:2])] = body
        empty = False
        for status, code, exit_code in (
            (404, "not_found", 4),
            (409, "revision_conflict", 2),
            (429, "stream_limit", 3),
            (503, "unavailable", 3),
        ):
            failure = (status, code, exit_code)
            before = len(calls)
            with pytest.raises(SystemExit) as error:
                cli.main(
                    [
                        "--mode",
                        mode,
                        "task",
                        "archive",
                        "--task",
                        ID,
                        "--expected-revision",
                        "1",
                        "--reason",
                        "synthetic",
                        "--json",
                    ]
                )
            assert error.value.code == exit_code and len(calls) == before + 1
            body = json.loads(capsys.readouterr().out)
            assert len(body) == 7 and not body["ok"] and body["items"] == []
            body["duration_ms"] = 0
            snapshots[f"{mode}:HTTP{status}"] = body
        failure = None
    snapshot = Path(__file__).with_name("snapshots") / "team-workflow-cli.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(json.dumps(snapshots, ensure_ascii=False, indent=2) + "\n")
    assert snapshots == json.loads(snapshot.read_text())


def test_workflow_invalid_inputs_do_not_send(monkeypatch, capsys, tmp_path):
    calls = []
    monkeypatch.setattr(cli, "call", lambda *a, **kw: calls.append(a))
    for flags in (
        ["progress", "--limit", "21"],
        ["events", "--wait-seconds", "26"],
        ["archive", "--expected-revision", "0", "--reason", "x"],
        ["board", "--job", ID, "--owner", ID, "--unassigned"],
    ):
        with pytest.raises(SystemExit) as error:
            cli.main(["task", *flags, "--task", ID, "--json"])
        assert error.value.code == 2
        capsys.readouterr()
    invalid_file = tmp_path / "invalid.json"
    invalid_file.write_text(
        '{"role":"owner","review_domains":[],"expected_revision":1,"reason":"invalid"}'
    )
    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                "task",
                "member",
                "set",
                "--task",
                ID,
                "--user",
                ID,
                "--input",
                str(invalid_file),
                "--json",
            ]
        )
    assert error.value.code == 2
    capsys.readouterr()
    assert not calls


def test_workflow_schema_discovery(capsys):
    cli.main(["schema", "--json"])
    body = json.loads(capsys.readouterr().out)
    names = body["data"]["commands"]
    assert all(
        "task " + name in names
        for name in (
            "workflow",
            "progress",
            "member list",
            "member candidates",
            "member set",
            "member remove",
            "handover",
            "archive",
            "unarchive",
            "board",
            "activity",
            "events",
        )
    )
    assert "card assign" not in names and "card signoff add" not in names

    assert all(
        "items" in names["task " + name]
        for name in ("board", "progress", "activity", "events", "member list", "member candidates")
    )
