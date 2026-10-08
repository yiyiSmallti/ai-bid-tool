"""Additive submission signature output through the CLI HTTP transport."""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from app.schemas.contracts import Result
from bid_cli import main as cli
from bid_cli.client import Client

ID = "00000000-0000-4000-8000-000000000001"
SUBMISSION = "00000000-0000-4000-8000-000000000002"
SHA = "a" * 64
NOW = "2026-10-08T00:00:00Z"


def detail(restricted):
    document = "00000000-0000-4000-8000-000000000010"
    files = [
        {
            "id": f"00000000-0000-4000-8000-{10 + index:012}",
            "file_id": f"00000000-0000-4000-8000-{20 + index:012}",
            "org_id": ID,
            "task_id": ID,
            "submission_id": SUBMISSION,
            "role": role,
            "kind": "tender" if role == "tender" else "qualification",
            "media_type": "application/pdf",
            "sha256": SHA,
            "size_bytes": 100,
        }
        for index, role in enumerate(("tender", "bid"))
    ]
    certificate = {
        "fingerprint_sha256": SHA,
        "not_before": NOW,
        "not_after": "2027-10-08T00:00:00Z",
    }
    candidate = {
        "id": ID,
        "document_id": document,
        "page_id": ID,
        "page": 1,
        "ordinal": 1,
        "candidate_kind": "signing_clause",
        "applicability": "unknown",
    }
    if not restricted:
        certificate |= {"subject": "CN=Synthetic protected signer", "issuer": "CN=Synthetic issuer"}
        candidate |= {
            "quote": "投标文件须逐页加盖公章。",
            "start_offset": 0,
            "end_offset": len("投标文件须逐页加盖公章。"),
            "mark_types": ["company_seal"],
            "owner_roles": ["bidder"],
            "date_required": False,
            "location_hint": "every_page",
        }
    signature = {
        "signature_index": 1,
        "field_name_sha256": SHA,
        "coverage_status": "whole_revision",
        "crypto_status": "valid",
        "certificate_validity_status": "valid",
        "trust_status": "unknown",
        "revocation_status": "unknown",
        "timestamp_status": "absent",
        "modified_after_signing": True,
        "final_revision_covered": False,
        "certificate": certificate,
    }
    return {
        "submission": {
            "id": SUBMISSION,
            "org_id": ID,
            "task_id": ID,
            "revision": 1,
            "manifest_sha256": SHA,
            "documents": [
                file
                | {
                    "page_count": 1,
                    "rendered_pdf_sha256": SHA,
                    "render_profile": "synthetic-local-v1",
                    "renderer_identity": "synthetic-local-v1",
                    "citation_mode": "page",
                    "parsing_warnings": [],
                }
                for file in files
            ],
            "created_by": ID,
            "created_at": NOW,
            "state": "prepared",
            "preparation_job_id": ID,
            "preparation_input_hash": SHA,
        },
        "preparation": None,
        "inventory": [],
        "signature_validations": [
            {
                "document_id": document,
                "original_sha256": SHA,
                "trust_store_sha256": SHA,
                "validator_identity": "synthetic-local-v1",
                "validation_time": NOW,
                "status": "unknown",
                "signatures": [signature],
                "final_revision": {
                    "status": "modified_after_signing",
                    "covered_by_signature_indices": [],
                    "modified_after_last_signature": True,
                },
            }
        ],
        "signing_candidates": [candidate],
        "signing_candidate_count": 1,
        "signing_candidates_next_cursor": None,
    }


def test_signature_detail_snapshot_protected_and_restricted_both_modes(monkeypatch, capsys):
    monkeypatch.setenv("BID_SESSION", "synthetic-org-session")
    monkeypatch.setenv("BID_ORG", ID)
    current = {"restricted": False}

    def handler(request):
        assert request.url.path == f"/v4/bid-submissions/{SUBMISSION}"
        assert request.headers["x-org-id"] == ID
        return httpx.Response(
            200,
            json=Result(
                ok=True, command="review submission show", data=detail(current["restricted"])
            ).model_dump(mode="json"),
        )

    @asynccontextmanager
    async def transport(self):
        async with httpx.AsyncClient(
            base_url="https://console.example.test", transport=httpx.MockTransport(handler)
        ) as client:
            yield client

    monkeypatch.setattr(Client, "transport", transport)
    actual = {}
    for mode in ("remote", "local"):
        for restricted in (False, True):
            current["restricted"] = restricted
            cli.main(["--mode", mode, "review", "submission", "show", "--id", SUBMISSION, "--json"])
            output = capsys.readouterr().out
            assert "synthetic-org-session" not in output
            if restricted:
                assert "protected signer" not in output and "投标文件须" not in output
            body = json.loads(output)
            body["duration_ms"] = 0
            actual[f"{mode}: {'restricted' if restricted else 'protected'}"] = body
    snapshot = Path(__file__).with_name("snapshots") / "bid-review-signatures-cli-v4.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
    assert actual == json.loads(snapshot.read_text())
