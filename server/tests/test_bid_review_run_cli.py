"""CLI-to-ASGI transport acceptance for exact text grants and slice 3a review jobs."""

import json
import os
from pathlib import Path

import pytest
from app.schemas import bid_review_privacy as privacy
from app.schemas import bid_review_run as runs
from app.schemas.contracts import Result
from bid_cli import main as cli
from bid_cli.schema import command_schema
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from test_bid_review_cli import ID, NOW, SHA, SUBMISSION, InterfaceClient, invoke

PAGE = "00000000-0000-4000-8000-000000000003"
REVIEW = "00000000-0000-4000-8000-000000000004"


def interface():
    application = FastAPI()
    state = {"calls": [], "partial": False, "malformed": False, "queue_failure": False}
    pages = [{"page_id": PAGE, "sanitized_text_sha256": SHA}]
    grant = {
        "id": ID,
        "org_id": ID,
        "task_id": ID,
        "submission_id": SUBMISSION,
        "submission_manifest_sha256": SHA,
        "preparation_input_hash": SHA,
        "redaction_manifest_sha256": SHA,
        "authorized_sanitized_context_sha256": privacy.sanitized_context_sha256(
            [privacy.SanitizedPageRef.model_validate(item) for item in pages]
        ),
        "provider_bindings_sha256": SHA,
        "revision": 1,
        "prior_authorization_id": None,
        "allow_external": True,
        "purposes": ["bid_review_text"],
        "allowed_capabilities": ["llm"],
        "pages": pages,
        "authorized_by": ID,
        "authorized_at": NOW,
        "reason_sha256": SHA,
        "current": True,
    }
    run = runs.BidReviewRunView(
        id=REVIEW,
        task_id=ID,
        submission_id=SUBMISSION,
        job_id=ID,
        status="succeeded",
        completion="complete",
        input_hash=SHA,
        created_at=NOW,
        coverage={"tender_pages": 1, "processed_pages": 1},
    ).model_dump(mode="json")

    @application.api_route(
        "/v4/bid-submissions/{submission_id}/outbound-authorizations", methods=["GET", "POST"]
    )
    async def authorizations(request: Request, submission_id: str):
        assert request.headers["x-org-id"] == ID and submission_id == SUBMISSION
        if request.method == "GET":
            state["calls"].append("outbound-list")
            return Result(
                ok=True,
                command="review outbound list",
                data={"total": 1, "next_cursor": None},
                items=[grant],
            )
        body = privacy.OutboundAuthorizationRequest.model_validate(await request.json())
        assert (
            body.authorized_sanitized_context_sha256 == grant["authorized_sanitized_context_sha256"]
        )
        state["calls"].append("authorize")
        return Result(ok=True, command="review outbound authorize", data=grant)

    @application.post("/v4/bid-submissions/{submission_id}/outbound-authorizations/revoke")
    async def revoke(request: Request, submission_id: str):
        assert submission_id == SUBMISSION
        body = privacy.OutboundRevokeRequest.model_validate(await request.json())
        assert str(body.expected_authorization_id) == ID and body.expected_revision == 1
        state["calls"].append("revoke")
        return Result(
            ok=True, command="review outbound revoke", data={**grant, "allow_external": False}
        )

    @application.api_route("/v4/tasks/{task_id}/bid-reviews", methods=["POST", "GET"])
    async def reviews(request: Request, task_id: str):
        assert request.headers["x-org-id"] == ID and task_id == ID
        if request.method == "GET":
            state["calls"].append("list")
            return Result(
                ok=True,
                command="review list",
                data={"task_id": ID, "next_cursor": None},
                items=[run],
            )
        body = runs.BidReviewRequest.model_validate(await request.json())
        if not body.dry_run:
            assert (
                body.expected_input_hash == SHA
                and body.preflight_token == "synthetic-review-receipt"
            )
            state["calls"].append("submit")
            if state["queue_failure"]:
                return JSONResponse(
                    status_code=503,
                    content=Result(
                        ok=False,
                        command="review run",
                        data={
                            "job_id": ID,
                            "error": {
                                "code": "queue_unavailable",
                                "message": "Synthetic queue failure",
                                "exit_code": 3,
                            },
                        },
                    ).model_dump(mode="json"),
                )
            return Result(
                ok=True,
                command="review run",
                data={"job_id": ID, "status": "queued", "cached": False},
            )
        state["calls"].append("preview")
        zero = Result(ok=True, command="zero").cost.model_dump(mode="json")
        preview = runs.BidReviewPreview(
            input={"task_id": ID, "submission_id": SUBMISSION, "input_hash": SHA},
            expires_at="2026-10-08T00:15:00Z",
            preflight_token="synthetic-review-receipt",
            budget={
                "dry_run": True,
                "command": "review run",
                "task_id": ID,
                "input_hash": SHA,
                "as_of": NOW,
                "task_budget": None,
                "planned_calls": 1,
                "maximum_calls": 1,
                "estimate": zero,
                "next_call": None,
                "admission_blocker": None,
                "first_pass_fits": True,
                "full_run_guaranteed": False,
            },
        )
        return Result(ok=True, command="review run", data=preview.model_dump(mode="json"))

    @application.get("/v4/bid-reviews/{review_id}")
    async def show(request: Request, review_id: str):
        assert review_id == REVIEW
        assert request.query_params["section"] in {"obligations", "signing_requirements"}
        state["calls"].append("show")
        return Result(
            ok=not state["partial"],
            command="review show",
            data={
                "run": {**run, "completion": "partial" if state["partial"] else "complete"},
                "obligations": [],
                "signing_requirements": [],
                "next_cursor": None,
            },
        )

    @application.get("/v4/jobs/{job_id}")
    async def job(job_id: str):
        assert job_id == ID
        state["calls"].append("job")
        output = {
            "review_id": REVIEW,
            "completion": "partial" if state["partial"] else "complete",
            "coverage": run["coverage"],
            "uncovered_codes": ["signing_unresolved"] if state["partial"] else [],
            "stop_reason": None,
            "usage_record_ids": [],
        }
        if state["malformed"]:
            output.pop("review_id")
        return Result(
            ok=True, command="job status", data={"id": ID, "status": "succeeded", "result": output}
        )

    return application, state, grant


def setup(monkeypatch, tmp_path):
    application, state, grant = interface()
    monkeypatch.setenv("BID_SESSION", "synthetic-review-session")
    monkeypatch.setenv("BID_ORG", ID)
    monkeypatch.setattr(
        cli, "Client", lambda mode, server, saved: InterfaceClient(mode, server, saved, application)
    )
    paths = {name: tmp_path / f"{name}.json" for name in ("authorize", "revoke", "run")}
    paths["authorize"].write_text(
        json.dumps(
            {
                "request_id": ID,
                "expected_revision": 1,
                "expected_authorization_id": None,
                "expected_submission_manifest_sha256": SHA,
                "expected_preparation_input_hash": SHA,
                "expected_redaction_manifest_sha256": SHA,
                "authorized_sanitized_context_sha256": grant["authorized_sanitized_context_sha256"],
                "provider_bindings_sha256": SHA,
                "pages": grant["pages"],
                "privacy_reviewed": True,
                "reason": "Synthetic exact text review",
            }
        )
    )
    paths["revoke"].write_text(
        json.dumps(
            {
                "request_id": ID,
                "expected_revision": 1,
                "expected_authorization_id": ID,
                "reason": "Synthetic withdrawal",
            }
        )
    )
    paths["run"].write_text(
        json.dumps({"request_id": ID, "submission_id": SUBMISSION, "assessment_date": "2026-10-08"})
    )
    return state, paths


def test_review_run_transport_snapshots_both_modes(monkeypatch, tmp_path, capsys):
    state, paths = setup(monkeypatch, tmp_path)
    run = ["review", "run", "--task", ID, "--input", str(paths["run"])]
    receipt = ["--expected-input-hash", SHA, "--preflight-token", "synthetic-review-receipt"]
    commands = {
        "review outbound authorize": [
            "review",
            "outbound",
            "authorize",
            "--submission",
            SUBMISSION,
            "--input",
            str(paths["authorize"]),
        ],
        "review outbound revoke": [
            "review",
            "outbound",
            "revoke",
            "--submission",
            SUBMISSION,
            "--input",
            str(paths["revoke"]),
        ],
        "review outbound list": ["review", "outbound", "list", "--submission", SUBMISSION],
        "review run --dry-run": [*run, "--dry-run"],
        "review run": [*run, *receipt],
        "review run --wait": [*run, *receipt, "--wait"],
        "review list": ["review", "list", "--task", ID],
        "review show": ["review", "show", "--id", REVIEW],
    }
    actual = {}
    for mode in ("remote", "local"):
        for name, flags in commands.items():
            code, body = invoke(["--mode", mode, *flags], capsys)
            assert code == 0
            actual[f"{mode}: {name}"] = body
            assert "Synthetic exact text review" not in json.dumps(body)
        state["partial"] = True
        code, body = invoke(["--mode", mode, *run, *receipt, "--wait"], capsys)
        assert code == 5 and not body["ok"] and body["data"]["completion"] == "partial"
        actual[f"{mode}: review run --wait partial"] = body
        code, body = invoke(["--mode", mode, *commands["review show"]], capsys)
        assert code == 5 and not body["ok"] and body["data"]["run"]["completion"] == "partial"
        actual[f"{mode}: review show partial"] = body
        state["partial"] = False
    snapshot = Path(__file__).with_name("snapshots") / "bid-review-run-cli-v4.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
    assert actual == json.loads(snapshot.read_text())


@pytest.mark.parametrize(
    "flags", [[], ["--dry-run", "--wait"], ["--dry-run", "--retry"], ["--expected-input-hash", SHA]]
)
def test_review_missing_or_mixed_receipts_do_not_dispatch(monkeypatch, tmp_path, capsys, flags):
    state, paths = setup(monkeypatch, tmp_path)
    code, body = invoke(
        ["review", "run", "--task", ID, "--input", str(paths["run"]), *flags], capsys
    )
    assert code == 2 and not body["ok"] and not state["calls"]


def test_review_malformed_completion_preserves_job_id(monkeypatch, tmp_path, capsys):
    state, paths = setup(monkeypatch, tmp_path)
    state["malformed"] = True
    code, body = invoke(
        [
            "review",
            "run",
            "--task",
            ID,
            "--input",
            str(paths["run"]),
            "--expected-input-hash",
            SHA,
            "--preflight-token",
            "synthetic-review-receipt",
            "--wait",
        ],
        capsys,
    )
    assert code == 4 and body["data"]["job_id"] == ID


def test_review_queue_failure_preserves_accepted_job_id(monkeypatch, tmp_path, capsys):
    state, paths = setup(monkeypatch, tmp_path)
    state["queue_failure"] = True
    code, body = invoke(
        [
            "review",
            "run",
            "--task",
            ID,
            "--input",
            str(paths["run"]),
            "--expected-input-hash",
            SHA,
            "--preflight-token",
            "synthetic-review-receipt",
        ],
        capsys,
    )
    assert code == 3 and not body["ok"] and body["data"]["job_id"] == ID


def test_review_discovery_matches_models_and_rejects_legacy():
    commands = command_schema(cli.app)["commands"]
    for name in (
        "review outbound authorize",
        "review outbound revoke",
        "review outbound list",
        "review run",
        "review list",
        "review show",
    ):
        assert name in commands and name not in command_schema(cli.app, "3.0")["commands"]
    assert commands["review run"]["preflight"]["title"] == "BidReviewPreview"
    assert commands["review run"]["wait_output"]["title"] == "BidReviewJobResult"
