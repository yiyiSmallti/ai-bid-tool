"""CLI task board transport acceptance.

Failure scenarios: invalid filters or bounds send a request; failure triggers an
automatic retry; task events emit JSON-lines; local mode changes the authenticated
route; schema omits board commands. Every Result retains its seven keys and zero Cost.
"""

import json
from contextlib import asynccontextmanager

import httpx
import pytest
from app.schemas.contracts import Result
from bid_cli import main as cli
from bid_cli.client import Client

ID = "00000000-0000-0000-0000-000000000001"
WHEN = "2026-10-05T00:00:00Z"
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
    from app.schemas import task_board as schemas

    items = []
    page = {
        "task_id": ID,
        "next_cursor": None,
        "returned": 0 if empty else 1,
        "has_more": False,
    }
    if path == "progress":
        data, items, model = (
            {
                "task_id": ID,
                "event_cursor": "opaque-snapshot",
                "as_of": WHEN,
                "next_cursor": None,
                "returned": 0 if empty else 1,
            },
            [] if empty else [JOB],
            schemas.TaskProgressData,
        )
    elif path == "activity":
        data, items, model = (
            page,
            [] if empty else [{"id": ID, "action": "card_changed", "created_at": WHEN}],
            schemas.PageData,
        )
    elif path == "events/poll":
        data, model = (
            {
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
        data, model = (
            {
                "task_id": ID,
                "extraction_job_id": ID,
                "counts": counts,
                "matching": 0 if empty else 1,
                "returned": 0 if empty else 1,
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
                        }
                    ],
                }
            ]
        )
    item_model = {
        "board": schemas.BoardRow,
        "progress": schemas.BoardJobView,
        "activity": schemas.BoardActivityView,
    }.get(path)
    if item_model:
        items = [item_model.model_validate(item).model_dump(mode="json") for item in items]
    return model.model_validate(data).model_dump(mode="json"), items


CASES = [
    (["progress", "--limit", "20"], "GET", "progress"),
    (["board", "--job", ID, "--mine"], "GET", "board"),
    (["activity"], "GET", "activity"),
    (["events", "--wait-seconds", "25"], "GET", "events/poll"),
]


def test_task_board_transport_and_snapshots(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("BID_ACCESS_KEY", "synthetic-only")
    calls, snapshots = [], {}
    failure = None
    empty = False

    def handler(request):
        assert request.headers["authorization"] == "Bearer synthetic-only"
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
                    command="task activity",
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
            json=Result(
                ok=True,
                command="task " + {"events/poll": "events"}.get(path, path),
                data=data,
                items=items,
            ).model_dump(mode="json"),
        )

    @asynccontextmanager
    async def transport(self):
        async with httpx.AsyncClient(
            base_url="http://local", transport=httpx.MockTransport(handler)
        ) as client:
            yield client

    monkeypatch.setattr(Client, "transport", transport)
    for mode in ("remote", "local"):
        for flags, method, path in CASES:
            cli.main(["--mode", mode, "task", *flags, "--task", ID, "--json"])
            body = json.loads(capsys.readouterr().out)
            assert len(body) == 7
            assert body["cost"] == {
                "calls": 0,
                "llm_tokens": 0,
                "ocr_pages": 0,
                "search_requests": 0,
                "browser_units": 0,
                "unresolved_calls": 0,
            }
            assert calls[-1][:2] == (method, f"/v4/tasks/{ID}/{path}")
            body["duration_ms"] = 0
            snapshots[mode + ":" + " ".join(flags[:2])] = body
        empty = True
        for flags, _method, _path in CASES:
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
                        "activity",
                        "--task",
                        ID,
                        "--json",
                    ]
                )
            assert error.value.code == exit_code and len(calls) == before + 1
            body = json.loads(capsys.readouterr().out)
            assert len(body) == 7 and not body["ok"] and body["items"] == []
            body["duration_ms"] = 0
            snapshots[f"{mode}:HTTP{status}"] = body
        failure = None
    (tmp_path / "task-board-cli.json").write_text(
        json.dumps(snapshots, ensure_ascii=False, indent=2) + "\n"
    )


def test_task_board_invalid_inputs_do_not_send(monkeypatch, capsys, tmp_path):
    calls = []
    monkeypatch.setattr(cli, "call", lambda *a, **kw: calls.append(a))
    for flags in (
        ["progress", "--limit", "21"],
        ["events", "--wait-seconds", "26"],
        ["board", "--job", ID, "--bucket", "unsupported"],
    ):
        with pytest.raises(SystemExit) as error:
            cli.main(["task", *flags, "--task", ID, "--json"])
        assert error.value.code == 2
        capsys.readouterr()
    assert not calls


def test_task_board_schema_discovery(capsys):
    from app.schemas.console_assessments import PageData as AssessmentPageData
    from app.schemas.task_board import PageData as BoardPageData

    cli.main(["schema", "--json"])
    body = json.loads(capsys.readouterr().out)
    names = body["data"]["commands"]
    assert all(
        "task " + name in names
        for name in (
            "progress",
            "board",
            "activity",
            "events",
        )
    )
    # Both slices call their envelope PageData; discovery must keep each contract.
    assert names["task activity"]["output"] == BoardPageData.model_json_schema()
    assert (
        names["check show"]["variants"]["console"]["coverage"]["output"]
        == AssessmentPageData.model_json_schema()
    )
    assert (
        names["score rubric show"]["variants"]["console"]["sections"]["output"]
        == AssessmentPageData.model_json_schema()
    )
    assert (
        names["score show"]["variants"]["console"]["items"]["output"]
        == AssessmentPageData.model_json_schema()
    )

    assert all(
        "items" in names["task " + name] for name in ("board", "progress", "activity", "events")
    )
