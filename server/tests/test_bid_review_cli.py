"""CLI transport snapshots for upload and call-free local-preparation admission."""

import hashlib
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from app.schemas import bid_review as models
from app.schemas.contracts import Result
from bid_cli import main as cli
from bid_cli.client import Client
from bid_cli.schema import command_schema
from fastapi import FastAPI, Request

ID = "00000000-0000-4000-8000-000000000001"
SUBMISSION = "00000000-0000-4000-8000-000000000002"
SHA = "a" * 64
NOW = "2026-10-08T00:00:00Z"


class InterfaceClient(Client):
    def __init__(self, mode, server, state, application):
        super().__init__(mode, server, state)
        self.application = application

    @asynccontextmanager
    async def transport(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.application), base_url="http://interface"
        ) as client:
            yield client


def invoke(arguments, capsys):
    code = 0
    try:
        cli.main([*arguments, "--json"])
    except SystemExit as exc:
        code = exc.code
    body = json.loads(capsys.readouterr().out)
    body["duration_ms"] = 0
    return code, body


def interface():
    application = FastAPI()
    calls, uploaded = [], {}
    ready = {"prepared": False}

    @application.api_route("/v4/tasks/{task_id}/bid-submissions", methods=["POST", "GET"])
    async def uploads(request: Request, task_id: str):
        assert task_id == ID
        assert request.headers["x-org-id"] == ID
        if request.method == "GET":
            calls.append("list")
            assert request.query_params["limit"] == "50"
            return Result(
                ok=True,
                command="review submission list",
                data={"task_id": ID, "total": 1, "next_cursor": None},
                items=[uploaded],
            )
        form = await request.form()
        body = models.BidSubmissionCreate.model_validate_json(str(form["metadata"]))
        files = form.getlist("files")
        assert len(files) == len(body.files)
        for file, descriptor in zip(files, body.files, strict=True):
            content = await file.read()
            assert len(content) == descriptor.size_bytes
            assert hashlib.sha256(content).hexdigest() == descriptor.sha256
        calls.append("preview" if body.dry_run else "upload")
        if body.dry_run:
            data = models.BidUploadPreview(
                files=body.files,
                payload_sha256=SHA,
                limits={
                    "files": 20,
                    "file_bytes": 40 * 1024 * 1024,
                    "submission_bytes": 40 * 1024 * 1024,
                },
                cost=Result(ok=True, command="zero").cost,
            ).model_dump(mode="json")
        else:
            ready["prepared"] = False
            uploaded.update(
                id=SUBMISSION,
                org_id=ID,
                task_id=ID,
                revision=1,
                manifest_sha256=SHA,
                files=[
                    {
                        **file.model_dump(mode="json"),
                        "id": f"00000000-0000-4000-8000-{index:012}",
                        "file_id": f"00000000-0000-4000-8000-{index + 100:012}",
                        "org_id": ID,
                        "task_id": ID,
                        "submission_id": SUBMISSION,
                    }
                    for index, file in enumerate(body.files, 10)
                ],
                created_by=ID,
                created_at=NOW,
                state="uploaded",
            )
            data = uploaded.copy()
        return Result(ok=True, command="review upload", data=data)

    @application.get("/v4/bid-submissions/{submission_id}")
    async def show(submission_id: str):
        assert submission_id == SUBMISSION
        calls.append("show")
        submission = uploaded
        if ready["prepared"]:
            submission = models.BidSubmissionView.model_validate(
                {
                    **{
                        key: value
                        for key, value in uploaded.items()
                        if key not in {"files", "state"}
                    },
                    "state": "prepared",
                    "preparation_job_id": ID,
                    "preparation_input_hash": SHA,
                    "documents": [
                        {
                            **file,
                            "page_count": 1,
                            "rendered_pdf_sha256": file["sha256"],
                            "render_profile": "bid-pages-v1",
                            "renderer_identity": "synthetic-renderer",
                            "citation_mode": "page",
                            "parsing_warnings": [],
                        }
                        for file in uploaded["files"]
                    ],
                }
            ).model_dump(mode="json")
        return Result(
            ok=True,
            command="review submission show",
            data={"submission": submission, "preparation": None, "inventory": []},
        )

    @application.post("/v4/tasks/{task_id}/bid-submissions/{submission_id}/prepare")
    async def prepare(request: Request, task_id: str, submission_id: str):
        assert task_id == ID and submission_id == SUBMISSION
        body = models.BidPrepareRequest.model_validate(await request.json())
        calls.append("prepare-preview" if body.dry_run else "prepare")
        if not body.dry_run:
            assert body.expected_input_hash == SHA and body.preflight_token == "synthetic-receipt"
            return Result(
                ok=True,
                command="review prepare",
                data={"job_id": ID, "status": "queued", "cached": False},
            )
        zero = Result(ok=True, command="zero").cost.model_dump(mode="json")
        preview = {
            "dry_run": True,
            "submission_id": SUBMISSION,
            "input_hash": SHA,
            "expires_at": "2026-10-08T00:15:00Z",
            "preflight_token": "synthetic-receipt",
            "local_only": True,
            "admission_blockers": [],
            "budget": {
                "dry_run": True,
                "command": "review prepare",
                "task_id": ID,
                "input_hash": SHA,
                "as_of": NOW,
                "task_budget": None,
                "planned_calls": 0,
                "maximum_calls": 0,
                "estimate": zero,
                "next_call": None,
                "admission_blocker": None,
                "first_pass_fits": True,
                "full_run_guaranteed": False,
            },
        }
        return Result(
            ok=True,
            command="review prepare",
            data=models.BidPreparePreview.model_validate(preview).model_dump(mode="json"),
        )

    @application.get("/v4/jobs/{job_id}")
    async def job(job_id: str):
        assert job_id == ID
        calls.append("job")
        ready["prepared"] = True
        return Result(ok=True, command="job status", data={"id": ID, "status": "succeeded"})

    return application, calls


def test_bid_review_commands_snapshot_both_modes(monkeypatch, tmp_path, capsys):
    application, calls = interface()
    monkeypatch.setenv("BID_SESSION", "synthetic-review-session")
    monkeypatch.setenv("BID_ORG", ID)
    monkeypatch.setattr(
        cli, "Client", lambda mode, server, state: InterfaceClient(mode, server, state, application)
    )
    paths, descriptors = [], []
    for name, role, kind in (
        ("tender.pdf", "tender", "tender"),
        ("bid.pdf", "bid", "qualification"),
    ):
        path = tmp_path / name
        content = b"%PDF-1.4 synthetic transport " + role.encode()
        path.write_bytes(content)
        paths.append(path)
        descriptors.append(
            {
                "role": role,
                "kind": kind,
                "media_type": "application/pdf",
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        )
    upload = tmp_path / "upload.json"
    upload.write_text(json.dumps({"request_id": ID, "files": descriptors}))
    prepare = tmp_path / "prepare.json"
    prepare.write_text(json.dumps({"request_id": ID, "submission_id": SUBMISSION}))
    upload_flags = [
        "review",
        "upload",
        "--task",
        ID,
        "--input",
        str(upload),
        "--file",
        str(paths[0]),
        "--file",
        str(paths[1]),
    ]
    prepare_flags = ["review", "prepare", "--task", ID, "--input", str(prepare)]
    commands = {
        "review upload --dry-run": [*upload_flags, "--dry-run"],
        "review upload": upload_flags,
        "review prepare --dry-run": [*prepare_flags, "--dry-run"],
        "review prepare": [
            *prepare_flags,
            "--expected-input-hash",
            SHA,
            "--preflight-token",
            "synthetic-receipt",
        ],
        "review submission list": ["review", "submission", "list", "--task", ID],
        "review submission show": ["review", "submission", "show", "--id", SUBMISSION],
        "review prepare --wait": [
            *prepare_flags,
            "--expected-input-hash",
            SHA,
            "--preflight-token",
            "synthetic-receipt",
            "--wait",
        ],
    }
    actual = {}
    for mode in ("remote", "local"):
        for name, flags in commands.items():
            code, body = invoke(["--mode", mode, *flags], capsys)
            assert code == 0
            assert set(body) == {
                "ok",
                "command",
                "data",
                "items",
                "warnings",
                "cost",
                "duration_ms",
            }
            assert "synthetic-review-session" not in json.dumps(body)
            actual[f"{mode}: {name}"] = body
    snapshot = Path(__file__).with_name("snapshots") / "bid-review-cli-v4.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
    assert actual == json.loads(snapshot.read_text())
    assert (
        calls
        == [
            "preview",
            "upload",
            "prepare-preview",
            "prepare",
            "list",
            "show",
            "prepare",
            "job",
            "show",
        ]
        * 2
    )


@pytest.mark.parametrize(
    "flags", [[], ["--dry-run", "--wait"], ["--dry-run", "--retry"], ["--expected-input-hash", SHA]]
)
def test_prepare_rejects_missing_or_mixed_receipt_before_http(monkeypatch, tmp_path, capsys, flags):
    path = tmp_path / "prepare.json"
    path.write_text(json.dumps({"request_id": ID, "submission_id": SUBMISSION}))
    requests = []
    monkeypatch.setattr(Client, "request", lambda *args, **kwargs: requests.append(args))
    code, body = invoke(["review", "prepare", "--task", ID, "--input", str(path), *flags], capsys)
    assert code == 2 and not body["ok"]
    assert not requests


def test_schema_exposes_slice_one_and_legacy_rejects_it(capsys):
    commands = command_schema(cli.app)["commands"]
    for name in (
        "review upload",
        "review prepare",
        "review submission list",
        "review submission show",
    ):
        assert name in commands
        assert name not in command_schema(cli.app, "3.0")["commands"]
    assert commands["review prepare"]["preflight"]["title"] == "BidPreparePreview"
    assert commands["review submission show"]["output"]["title"] == "BidSubmissionDetail"
    code, body = invoke(
        ["--contract-version", "3.0", "review", "submission", "show", "--id", SUBMISSION], capsys
    )
    assert code == 2 and not body["ok"]


@pytest.mark.parametrize(
    "code,status,exit_code", [("queue_unavailable", 503, 3), ("not_found", 404, 4)]
)
def test_http_failures_keep_retryable_and_terminal_exit_codes(
    monkeypatch, capsys, code, status, exit_code
):
    from app.core.errors import ServiceError

    async def failure(*args, **kwargs):
        raise ServiceError(code, "Synthetic failure", status, exit_code)

    monkeypatch.setattr(Client, "request", failure)
    observed, body = invoke(["review", "submission", "show", "--id", SUBMISSION], capsys)
    assert observed == exit_code and not body["ok"]
    assert body["data"]["error"]["code"] == code
