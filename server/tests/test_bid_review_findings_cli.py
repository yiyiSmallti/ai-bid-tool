"""CLI-to-ASGI acceptance for run-bound findings and append-only human requests."""

import json
import os
from pathlib import Path

import pytest
from app.schemas import bid_review_findings as models
from app.schemas.contracts import Result
from bid_cli import main as cli
from bid_cli.schema import command_schema
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from test_bid_review_cli import ID, NOW, SHA, InterfaceClient, invoke

REVIEW = "00000000-0000-4000-8000-000000000002"
FINDING = "00000000-0000-4000-8000-000000000003"
EVENT = "00000000-0000-4000-8000-000000000004"
PAGE = "00000000-0000-4000-8000-000000000005"


def interface():
    application = FastAPI()
    state = {"calls": [], "safe": False, "stale": False, "conflict": False, "malformed": False}
    finding = models.BidReviewFindingView(
        id=FINDING,
        review_id=REVIEW,
        task_id=ID,
        obligation_id=ID,
        code="bid_response_missing",
        title="Synthetic mandatory response missing",
        outcome="missing",
        severity="fatal",
        impact="rejection",
        basis={"kind": "rule", "rule_or_prompt_version": "synthetic-v1"},
        tender_support=[
            {
                "document_id": ID,
                "page_id": PAGE,
                "page": 1,
                "quote": "Synthetic tender obligation",
                "start_offset": 0,
                "end_offset": len("Synthetic tender obligation"),
            }
        ],
        absence_search={
            "kind": "locations",
            "searched_pages": [{"page_id": PAGE, "document_id": ID, "page": 2}],
            "coverage": "all_bid_pages",
            "method": "local_text",
        },
        explanation="Synthetic searched inventory has no response",
        remediation="Synthetic human follow-up",
        review_domain="commercial",
        revision=2,
        classification_id=EVENT,
        latest_decision_id=EVENT,
    ).model_dump(mode="json")
    event = models.BidReviewEventView(
        id=EVENT,
        review_id=REVIEW,
        task_id=ID,
        finding_id=FINDING,
        prior_decision_id=EVENT,
        revision=3,
        action="dismiss",
        review_domain="commercial",
        state="dismissed",
        reason="Synthetic human review reason",
        reason_sha256=SHA,
        decided_by=ID,
        decided_at=NOW,
    ).model_dump(mode="json")

    @application.get("/v4/bid-reviews/{review_id}/findings")
    async def findings(request: Request, review_id: str):
        assert request.headers["x-org-id"] == ID and review_id == REVIEW
        assert request.query_params["limit"] == "50"
        state["calls"].append({"method": "GET", "query": dict(request.query_params)})
        row = finding
        if state["safe"] or state["stale"]:
            row = {key: finding[key] for key in models.BidReviewSafeFinding.model_fields}
        if state["malformed"]:
            row = {**row, "revision": 0}
        return Result(
            ok=True,
            command="review findings",
            data={
                "review_id": REVIEW,
                "input_hash": SHA,
                "validity": "stale" if state["stale"] else "current",
                "next_cursor": "synthetic-next",
            },
            items=[row],
        )

    @application.api_route(
        "/v4/bid-reviews/{review_id}/findings/{finding_id}/{section}", methods=["GET", "POST"]
    )
    async def action(request: Request, review_id: str, finding_id: str, section: str):
        assert request.headers["x-org-id"] == ID and review_id == REVIEW and finding_id == FINDING
        assert section in {"decisions", "classification"}
        state["calls"].append({"method": request.method, "section": section})
        if request.method == "GET":
            assert request.query_params["limit"] == "50"
            return Result(
                ok=True,
                command="review history",
                data={
                    "review_id": REVIEW,
                    "input_hash": SHA,
                    "validity": "current",
                    "next_cursor": None,
                },
                items=[
                    event
                    if section == "decisions"
                    else {**event, "action": "classify", "state": "open"}
                ],
            )
        model = (
            models.BidReviewClassificationRequest
            if section == "classification"
            else models.BidReviewDecisionRequest
        )
        body = model.model_validate(await request.json())
        assert (
            body.expected_revision == 2
            and body.expected_input_hash == SHA
            and str(body.expected_decision_id) == EVENT
        )
        command = "review classify" if section == "classification" else "review decide"
        if state["conflict"]:
            return JSONResponse(
                status_code=409,
                content=Result(
                    ok=False,
                    command=command,
                    data={
                        "error": {
                            "code": "bid_finding_revision_conflict",
                            "message": "Synthetic revision conflict",
                            "exit_code": 2,
                        }
                    },
                ).model_dump(mode="json"),
            )
        return Result(
            ok=True,
            command=command,
            data={
                **event,
                "action": "classify" if section == "classification" else body.action,
                "state": "open"
                if section == "classification" or body.action == "reopen"
                else "confirmed"
                if body.action == "confirm"
                else "dismissed",
            },
        )

    return application, state


def setup(monkeypatch, tmp_path):
    application, state = interface()
    monkeypatch.setenv("BID_SESSION", "synthetic-findings-session")
    monkeypatch.setenv("BID_ORG", ID)
    monkeypatch.setattr(
        cli, "Client", lambda mode, server, saved: InterfaceClient(mode, server, saved, application)
    )
    paths = {}
    for action in ("dismiss", "reopen", "confirm", "classify"):
        path = tmp_path / f"{action}.json"
        body = {
            "request_id": ID,
            "reason": "Synthetic human review reason",
            "expected_revision": 2,
            "expected_input_hash": SHA,
            "expected_decision_id": EVENT,
        }
        body.update({"review_domain": "commercial"} if action == "classify" else {"action": action})
        path.write_text(json.dumps(body))
        paths[action] = path
    return state, paths


def test_findings_cli_asgi_snapshots_both_modes(monkeypatch, tmp_path, capsys):
    state, paths = setup(monkeypatch, tmp_path)
    commands = {
        "findings": [
            "findings",
            "--severity",
            "fatal",
            "--state",
            "open",
            "--outcome",
            "missing",
            "--cursor",
            "synthetic-cursor",
        ],
        "history": ["history", "--finding", FINDING],
        "classification history": ["history", "--finding", FINDING, "--classification"],
    }
    for action, path in paths.items():
        commands[action] = [
            "classify" if action == "classify" else "decide",
            "--finding",
            FINDING,
            "--input",
            str(path),
        ]
    actual = {}
    for mode in ("local", "remote"):
        for name, flags in commands.items():
            code, body = invoke(["--mode", mode, "review", *flags, "--id", REVIEW], capsys)
            assert code == 0
            actual[f"{mode}: review {name}"] = body
        for projection in ("safe", "stale"):
            state[projection] = True
            code, body = invoke(["--mode", mode, "review", "findings", "--id", REVIEW], capsys)
            assert code == 0 and "Synthetic tender obligation" not in json.dumps(body)
            assert "reason" not in body["items"][0] and "tender_support" not in body["items"][0]
            actual[f"{mode}: review findings {projection}"] = body
            state[projection] = False
    snapshot = Path(__file__).with_name("snapshots") / "bid-review-findings-cli-v4.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
    assert actual == json.loads(snapshot.read_text())


@pytest.mark.parametrize(
    "field,value",
    [
        ("reason", "  "),
        ("expected_revision", 0),
        ("expected_input_hash", "bad"),
        ("action", "classify"),
    ],
)
def test_invalid_findings_mutation_does_not_dispatch(monkeypatch, tmp_path, capsys, field, value):
    state, paths = setup(monkeypatch, tmp_path)
    body = json.loads(paths["dismiss"].read_text())
    body[field] = value
    paths["dismiss"].write_text(json.dumps(body))
    code, result = invoke(
        [
            "review",
            "decide",
            "--id",
            REVIEW,
            "--finding",
            FINDING,
            "--input",
            str(paths["dismiss"]),
        ],
        capsys,
    )
    assert code == 2 and not result["ok"] and not state["calls"]


def test_conflict_and_invalid_response_are_visible(monkeypatch, tmp_path, capsys):
    state, paths = setup(monkeypatch, tmp_path)
    state["conflict"] = True
    code, body = invoke(
        [
            "review",
            "decide",
            "--id",
            REVIEW,
            "--finding",
            FINDING,
            "--input",
            str(paths["dismiss"]),
        ],
        capsys,
    )
    assert code == 2 and body["data"]["error"]["code"] == "bid_finding_revision_conflict"
    state["malformed"] = True
    code, body = invoke(["review", "findings", "--id", REVIEW], capsys)
    assert code == 4 and body["data"]["error"]["code"] == "invalid_server_response"


def test_findings_discovery_matches_models():
    schema = command_schema(cli.app)
    for name in ("review findings", "review decide", "review classify", "review history"):
        assert name in schema["commands"] and name not in command_schema(cli.app, "3.0")["commands"]
    assert schema["commands"]["review decide"]["input"]["title"] == "BidReviewDecisionRequest"
    assert schema["commands"]["review classify"]["output"]["title"] == "BidReviewEventView"
