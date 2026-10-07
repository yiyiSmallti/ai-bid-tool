"""B05 DB-free transport acceptance, specified before implementation.

Failure cases: submit without explicit reviewed hashes; dry-run with submit flags;
malformed/oversized plans; unbounded paging; routes that differ between local/remote;
wait reports admission as publication; timeout drops job ID; failed/cancelled jobs
claim success; retries change the exact approval pin; discovery exposes B05 in v3.
Snapshots prove the CLI transport and Result envelope, never DB or renderer gates.
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

ID = "00000000-0000-4000-8000-000000000001"
SHA = "a" * 64
PLAN = {
    "extraction_job_id": ID,
    "card_id": ID,
    "expected_card_revision": 1,
    "source": {"kind": "certificate_page", "evidence_source_id": ID},
    "plan": {"crop": None, "boxes": []},
}
REVIEW = {
    "evidence_id": ID,
    "card_id": ID,
    "expected_card_revision": 2,
    "expected_approval_binding_hash": SHA,
    "request_id": ID,
    "job_id": ID,
    "retry": True,
}


def test_annotation_cli_transport_snapshots(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("BID_SESSION", "synthetic-session")
    monkeypatch.setenv("BID_ORG", ID)
    calls, snapshots = [], {}
    scenario = "queued"

    def handler(request):
        calls.append(
            (
                request.method,
                request.url.path,
                dict(request.url.params),
                json.loads(request.content) if request.content else None,
            )
        )
        if scenario == "error":
            return httpx.Response(
                409,
                json=Result(
                    ok=False,
                    command="evidence stamp",
                    data={
                        "error": {
                            "code": "annotation_input_changed",
                            "message": "Review again",
                            "exit_code": 2,
                        }
                    },
                ).model_dump(mode="json"),
            )
        if request.url.path.endswith("/jobs/" + ID + "/cancel"):
            return httpx.Response(
                200,
                json=Result(
                    ok=True,
                    command="job cancel",
                    data={"id": ID, "status": "cancelled"},
                    cost={"billing_currency": "CNY"},
                ).model_dump(mode="json"),
            )
        if request.url.path.endswith("/jobs/" + ID):
            status = (
                "running"
                if scenario == "wait-timeout"
                else "failed"
                if scenario == "retryable-failed"
                else scenario
                if scenario in {"failed", "cancelled"}
                else "succeeded"
            )
            data = {
                "id": ID,
                "kind": "annotation_render",
                "status": status,
                "result": {"annotation_id": ID},
                "error": None,
            }
            if status == "failed":
                data["error"] = {
                    "code": "renderer_timeout"
                    if scenario == "retryable-failed"
                    else "renderer_unavailable",
                    "message": "Unavailable",
                    "exit_code": 3 if scenario == "retryable-failed" else 4,
                }
            return httpx.Response(
                200,
                json=Result(
                    ok=True,
                    command="job status",
                    data=data,
                    cost={"basis": "actual", "billing_currency": "CNY"},
                ).model_dump(mode="json"),
            )
        if request.method == "POST":
            data = (
                {"dry_run": True, "input_hash": SHA, "blocker_codes": []}
                if calls[-1][3].get("dry_run")
                else {
                    "job_id": ID,
                    "task_id": ID,
                    "card_id": ID,
                    "kind": "annotation_release"
                    if "releases" in request.url.path
                    else "annotation_render",
                    "status": "queued",
                    "duplicate": scenario == "duplicate",
                }
            )
        elif request.url.path.endswith("/annotations/" + ID):
            data = {
                "candidate": {"id": ID, "status": "unconfirmed_material"},
                "current": {"validity": "current"},
            }
        elif request.url.path.endswith("/preview"):
            data = {
                "annotation_id": ID,
                "rendition_id": ID,
                "kind": "candidate",
                "url": "/annotations/" + ID + "/preview/download?signature=synthetic",
                "expires_in": 300,
            }
        else:
            data = {"task_id": ID, "returned": 0, "has_more": False, "next_cursor": None}
        return httpx.Response(
            200,
            json=Result(
                ok=True, command="evidence stamp", data=data, cost={"billing_currency": "CNY"}
            ).model_dump(mode="json"),
        )

    @asynccontextmanager
    async def transport(self):
        async with httpx.AsyncClient(
            base_url="http://synthetic", transport=httpx.MockTransport(handler)
        ) as client:
            yield client

    monkeypatch.setattr(Client, "transport", transport)
    plan, review = tmp_path / "plan.json", tmp_path / "review.json"
    plan.write_text(json.dumps(PLAN))
    review.write_text(json.dumps(REVIEW))
    stamp = ["evidence", "stamp", "--task", ID, "--input", str(plan)]
    submit = [
        *stamp,
        "--expected-input-hash",
        SHA,
        "--reviewed-source-png-sha256",
        SHA,
        "--request-id",
        ID,
    ]
    commands = {
        "job-status-id": (["job", "status", "--id", ID], "GET", f"/jobs/{ID}"),
        "job-wait-id": (["job", "wait", "--id", ID], "GET", f"/jobs/{ID}"),
        "job-cancel-id": (["job", "cancel", "--id", ID], "POST", f"/jobs/{ID}/cancel"),
        "dry-run": ([*stamp, "--dry-run"], "POST", f"/tasks/{ID}/annotations"),
        "queued": (submit, "POST", f"/tasks/{ID}/annotations"),
        "duplicate": (submit, "POST", f"/tasks/{ID}/annotations"),
        "retry": ([*submit, "--retry"], "POST", f"/tasks/{ID}/annotations"),
        "list": (
            ["evidence", "annotation", "list", "--task", ID, "--card", ID],
            "GET",
            f"/tasks/{ID}/annotations",
        ),
        "show": (["evidence", "annotation", "show", "--id", ID], "GET", f"/annotations/{ID}"),
        "preview": (
            ["evidence", "annotation", "preview", "--id", ID],
            "GET",
            f"/annotations/{ID}/preview",
        ),
        "releases": (
            ["evidence", "annotation", "releases", "--id", ID],
            "GET",
            f"/annotations/{ID}/releases",
        ),
        "release-preview": (
            ["evidence", "annotation", "release", "preview", "--id", ID],
            "GET",
            f"/annotation-releases/{ID}/preview",
        ),
        "release-retry": (
            ["evidence", "annotation", "release", "retry", "--id", ID, "--input", str(review)],
            "POST",
            f"/annotations/{ID}/releases",
        ),
    }
    for scenario, (args, method, path) in commands.items():
        before = len(calls)
        cli.main([*args, "--json"])
        body = json.loads(capsys.readouterr().out)
        body["duration_ms"] = 0
        snapshots[scenario] = body
        assert calls[before][:2] == (method, "/v4" + path)
        assert len(calls) == before + 1
        assert set(body) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
        assert body["cost"]["billing_currency"] == "CNY"
    for scenario, code in [
        ("succeeded", 0),
        ("failed", 4),
        ("cancelled", 4),
        ("retryable-failed", 3),
        ("wait-timeout", 3),
        ("error", 2),
    ]:
        if code:
            with pytest.raises(SystemExit) as exc:
                cli.main(
                    [
                        *submit,
                        "--wait",
                        "--timeout",
                        "0.01" if scenario == "wait-timeout" else "60",
                        "--json",
                    ]
                )
            assert exc.value.code == code
        else:
            cli.main(
                [
                    *submit,
                    "--wait",
                    "--timeout",
                    "0.01" if scenario == "wait-timeout" else "60",
                    "--json",
                ]
            )
        body = json.loads(capsys.readouterr().out)
        body["duration_ms"] = 0
        snapshots[scenario] = body
        if scenario == "succeeded":
            assert body["data"]["id"] == ID
            assert body["cost"]["basis"] == "actual"
        if scenario in {"failed", "cancelled", "retryable-failed", "wait-timeout"}:
            assert body["ok"] is False
    expected = Path(__file__).parent / "snapshots" / "annotation-cli.json"
    if os.environ.get("UPDATE_ANNOTATION_SNAPSHOTS") == "1":
        expected.write_text(json.dumps(snapshots, indent=2) + "\n")
    assert snapshots == json.loads(expected.read_text())


def test_annotation_cli_rejects_invalid_input_without_transport(monkeypatch, capsys, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid input reached the API")

    monkeypatch.setattr(cli, "call", forbidden)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(PLAN))
    stamp = ["evidence", "stamp", "--task", ID, "--input", str(plan)]
    for args in [
        stamp,
        ["job", "status", ID, "--id", ID],
        ["job", "wait"],
        [*stamp, "--dry-run", "--retry"],
        [*stamp, "--dry-run", "--wait"],
        ["evidence", "annotation", "list", "--task", ID, "--limit", "101"],
    ]:
        with pytest.raises(SystemExit) as exc:
            cli.main([*args, "--json"])
        assert exc.value.code == 2
        capsys.readouterr()
    schema = command_schema()
    assert schema["commands"]["evidence stamp"]["input"]
    assert "evidence annotation release retry" in schema["commands"]
    assert "evidence stamp" not in command_schema(version="3.0")["commands"]
