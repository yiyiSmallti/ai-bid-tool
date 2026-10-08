"""CLI → HTTP contract acceptance for independent image privacy clearance."""

import json
import os
from pathlib import Path

from app.schemas import bid_review_presence as models
from app.schemas.contracts import Result
from bid_cli import main as cli
from bid_cli.schema import command_schema
from fastapi import FastAPI, Request
from test_bid_review_cli import ID, SHA, SUBMISSION, InterfaceClient, invoke


def test_presence_http_transport_snapshots(monkeypatch, tmp_path, capsys):
    application = FastAPI()
    received = []
    preview = models.PresencePreview(
        submission_id=SUBMISSION,
        source_review_id=ID,
        manifest_sha256=SHA,
        expected_revision=1,
        images=[
            models.PresenceImage(
                id=ID,
                page_id=ID,
                document_id=ID,
                page=2,
                sha256=SHA,
                source_sha256="b" * 64,
                width_px=192,
                height_px=256,
                size_bytes=2400,
                blur={"version": "presence-gaussian-v1", "sigma_px": 4.0},
                privacy_receipt_sha256=SHA,
            )
        ],
    )

    @application.api_route("/v4/bid-submissions/{submission_id}/{action}", methods=["GET", "POST"])
    async def endpoint(request: Request, submission_id: str, action: str):
        assert request.headers["x-org-id"] == ID and submission_id == SUBMISSION
        if action == "presence-preview":
            assert request.method == "GET"
            command, data = "review presence preview", preview
        elif action == "presence-preparations":
            body = models.PresencePrepareRequest.model_validate(await request.json())
            assert str(body.review_id) == ID
            command, data = "review presence prepare", preview
        else:
            assert action == "presence-authorizations"
            grant = models.PresenceAuthorizationRequest.model_validate(await request.json())
            assert grant.privacy_reviewed and grant.manifest_sha256 == SHA
            command = "review presence authorize"
            data = models.PresenceAuthorizationView(
                id=ID,
                revision=1,
                manifest_sha256=SHA,
                image_ids=grant.image_ids,
                allow_external=grant.allow_external,
            )
        received.append(action)
        return Result(ok=True, command=command, data=data.model_dump(mode="json"))

    monkeypatch.setenv("BID_SESSION", "synthetic-presence-session")
    monkeypatch.setenv("BID_ORG", ID)
    monkeypatch.setattr(
        cli, "Client", lambda mode, server, saved: InterfaceClient(mode, server, saved, application)
    )
    request = tmp_path / "presence.json"
    request.write_text(
        json.dumps(
            {
                "request_id": ID,
                "expected_revision": 1,
                "manifest_sha256": SHA,
                "image_ids": [ID],
                "privacy_reviewed": True,
                "reason": "Reviewed exact derivative pixels",
            }
        )
    )
    commands = {
        "review presence preview": ["review", "presence", "preview", "--submission", SUBMISSION],
        "review presence prepare": [
            "review",
            "presence",
            "prepare",
            "--submission",
            SUBMISSION,
            "--review",
            ID,
        ],
        "review presence authorize": [
            "review",
            "presence",
            "authorize",
            "--submission",
            SUBMISSION,
            "--input",
            str(request),
        ],
    }
    actual = {}
    schemas = command_schema(cli.app)["commands"]
    for mode in ("local", "remote"):
        for name, command in commands.items():
            assert name in schemas
            code, value = invoke(["--mode", mode, *command], capsys)
            assert code == 0
            assert "Reviewed exact" not in json.dumps(value)
            actual[f"{mode}: {name}"] = value
    assert len(received) == 6
    snapshot = Path(__file__).with_name("snapshots") / "bid-review-presence-cli-v4.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(json.dumps(actual, sort_keys=True, indent=2) + "\n")
    assert actual == json.loads(snapshot.read_text())
