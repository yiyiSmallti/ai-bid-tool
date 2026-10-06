"""Co-sign transport contract specified before implementation.

Failure inventory: missing IDs/invalid JSON issue requests; review-purpose union
loses discrimination; local mode bypasses authentication; dry-run silently writes;
revision conflict automatically retries a signature; history lands in data rather
than items; JSON envelope/version/cost changes; schemas omit typed signatures.
Mock HTTP is used only for transport acceptance, never for production paths.
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
RULE = {
    "expected_revision": 1,
    "reason": "Synthetic rule",
    "co_sign_starred": True,
    "dry_run": False,
}
POLICY = {"expected_policy_revision": 0, "reason": "Synthetic policy", "co_sign_required": True}
OPEN = {
    "expected_revision": 1,
    "reason": "Synthetic disposition",
    "intended_disposition": "comply_only",
    "purpose": "disposition",
    "client_request_id": ID,
}
DISPOSITION = {
    "expected_revision": 1,
    "purpose": "disposition",
    "expected_round": 1,
    "selected_domain": "technical",
    "reviewed_warning_codes": [],
    "reason": "Synthetic acknowledgment",
    "client_request_id": ID,
}
CONFIRM = {
    "expected_revision": 1,
    "action": "confirm",
    "purpose": "response",
    "expected_round": 1,
    "selected_domain": "technical",
    "reviewed_evidence_ids": [],
    "reviewed_warning_codes": [],
    "reason": None,
    "client_request_id": ID,
}
POLICY_FLAGS = ["--task", ID, "--requirement", OTHER, "--job", ID]
CASES = [
    (["task", "review-rule", "show", "--task", ID], "GET", f"/tasks/{ID}/review-rule", None, {}),
    (["task", "review-rule", "set", "--task", ID], "PUT", f"/tasks/{ID}/review-rule", RULE, {}),
    (
        ["task", "review-rule", "set", "--task", ID, "--dry-run"],
        "PUT",
        f"/tasks/{ID}/review-rule",
        RULE,
        {},
    ),
    (
        ["card", "policy", "show", *POLICY_FLAGS],
        "GET",
        f"/tasks/{ID}/requirements/{OTHER}/review-policy",
        None,
        {"extraction_job_id": ID},
    ),
    (
        ["card", "policy", "set", *POLICY_FLAGS],
        "PUT",
        f"/tasks/{ID}/requirements/{OTHER}/review-policy",
        POLICY,
        {"extraction_job_id": ID},
    ),
    (
        ["card", "signoff", "list", "--card", ID],
        "GET",
        f"/cards/{ID}/signoffs",
        None,
        {"limit": "50"},
    ),
    (
        ["card", "review-round", "open", "--card", ID],
        "POST",
        f"/cards/{ID}/review-rounds",
        OPEN,
        {},
    ),
    (["card", "signoff", "add", "--card", ID], "POST", f"/cards/{ID}/signoffs", DISPOSITION, {}),
    (["card", "signoff", "add", "--card", ID], "POST", f"/cards/{ID}/signoffs", CONFIRM, {}),
]


def response_data(method, path, body, empty=False):
    from app.schemas import team_workflow as schemas

    round_data = {
        "id": ID,
        "org_id": ID,
        "task_id": ID,
        "card_id": ID,
        "extraction_job_id": ID,
        "requirement_id": OTHER,
        "round_revision": 1,
        "card_revision": 1,
        "card_revision_id": OTHER,
        "policy_revision": 1,
        "task_rule_revision": 1,
        "access_epoch": 1,
        "evidence_sha256": "a" * 64,
        "requirement_sha256": "b" * 64,
        "citation_sha256": "c" * 64,
        "content_sha256": "d" * 64,
        "required_domains": ["commercial", "technical"],
        "purpose": "disposition",
        "intended_disposition": "comply_only",
        "prior_card_state": "draft",
        "state": "open",
        "created_at": WHEN,
    }
    purpose = body.get("purpose", "disposition") if body else "disposition"
    signature = {
        "id": OTHER,
        "org_id": ID,
        "task_id": ID,
        "card_id": ID,
        "round_id": ID,
        "purpose": purpose,
        "domain": "technical",
        "signer_user_id": OTHER,
        "signer_org_role": "technical",
        "reviewed_evidence_ids": [],
        "reviewed_warning_codes": [],
        "reason_sha256": "a" * 64,
        "client_request_id": ID,
        "created_at": WHEN,
    }
    summary = {
        "status": "partial",
        "round_revision": 1,
        "required_domains": ["commercial", "technical"],
        "signed_domains": ["technical"],
        "pending_domains": ["commercial"],
    }
    items = []
    if path.endswith("review-rule"):
        data = {
            "rule": {
                "org_id": ID,
                "task_id": ID,
                "workflow_revision": 1,
                "rule_revision": 1,
                "co_sign_starred": True,
            },
            "dry_run": bool(body and body.get("dry_run")),
            "affected_requirements": 2,
        }
        model = schemas.TaskRuleData
    elif path.endswith("review-policy"):
        data = {
            "policy": {
                "org_id": ID,
                "task_id": ID,
                "extraction_job_id": ID,
                "requirement_id": OTHER,
                "revision": 1,
                "primary_domain": "technical",
                "co_sign_required": True,
                "starred": True,
                "co_sign_starred": False,
                "task_rule_revision": 1,
                "required_domains": ["technical", "commercial"],
            }
        }
        model = schemas.CoSignPolicyData
    elif path.endswith("review-rounds"):
        data, model = {"round": round_data}, schemas.ReviewRoundData
    elif method == "POST":
        data = {
            "summary": summary,
            "signature": signature,
            "current_card_revision": 1,
            "current_card_revision_id": OTHER,
        }
        model = schemas.CoSignData
    else:
        items = (
            []
            if empty
            else [schemas.CoSignSignatureView.model_validate(signature).model_dump(mode="json")]
        )
        data = {
            "org_id": ID,
            "task_id": ID,
            "card_id": ID,
            "thread_id": None,
            "next_cursor": None,
            "returned": len(items),
            "has_more": False,
            "round": round_data,
            "summary": summary,
        }
        model = schemas.SignoffsData
    return model.model_validate(data).model_dump(mode="json"), items


def test_cosign_transport_and_snapshots(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("BID_SESSION", "synthetic-only")
    monkeypatch.setenv("BID_ORG", ID)
    calls, snapshots = [], {}
    failure = None
    empty = False

    def handler(request):
        assert request.headers["authorization"] == "Bearer synthetic-only"
        assert request.headers["x-org-id"] == ID
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, dict(request.url.params), body))
        if failure:
            status, code, exit_code = failure
            return httpx.Response(
                status,
                json=Result(
                    ok=False,
                    command="card signoff add",
                    data={
                        "error": {
                            "code": code,
                            "message": "Synthetic failure",
                            "exit_code": exit_code,
                        }
                    },
                ).model_dump(mode="json"),
            )
        data, items = response_data(request.method, request.url.path, body, empty)
        return httpx.Response(
            200,
            json=Result(ok=True, command="card signoff add", data=data, items=items).model_dump(
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
    input_file = tmp_path / "cosign.json"
    for mode in ("remote", "local"):
        for index, (flags, method, path, input_body, params) in enumerate(CASES):
            extra = []
            if input_body is not None:
                input_file.write_text(json.dumps(input_body))
                extra = ["--input", str(input_file)]
            cli.main(["--mode", mode, *flags, *extra, "--json"])
            emitted = json.loads(capsys.readouterr().out)
            assert len(emitted) == 7 and emitted["cost"] == Cost().model_dump(mode="json")
            expected_body = {**input_body, "dry_run": True} if "--dry-run" in flags else input_body
            assert calls[-1] == (method, "/v4" + path, params, expected_body)
            assert emitted["command"] == " ".join(flags[:3])
            emitted["duration_ms"] = 0
            snapshots[f"{mode}:{index}:{emitted['command']}"] = emitted
        empty = True
        cli.main(
            [
                "--mode",
                mode,
                "card",
                "signoff",
                "list",
                "--card",
                ID,
                "--cursor",
                "opaque-page",
                "--limit",
                "100",
                "--json",
            ]
        )
        emitted = json.loads(capsys.readouterr().out)
        assert calls[-1][2] == {"cursor": "opaque-page", "limit": "100"}
        assert emitted["items"] == [] and emitted["data"]["returned"] == 0
        emitted["duration_ms"] = 0
        snapshots[f"{mode}:empty"] = emitted
        empty = False
        for status, code, exit_code in (
            (403, "forbidden", 4),
            (404, "not_found", 4),
            (409, "revision_conflict", 2),
            (409, "task_archived", 2),
            (409, "idempotency_conflict", 2),
            (503, "unavailable", 3),
        ):
            failure = status, code, exit_code
            input_file.write_text(json.dumps(DISPOSITION))
            before = len(calls)
            with pytest.raises(SystemExit) as error:
                cli.main(
                    [
                        "--mode",
                        mode,
                        "card",
                        "signoff",
                        "add",
                        "--card",
                        ID,
                        "--input",
                        str(input_file),
                        "--json",
                    ]
                )
            assert error.value.code == exit_code and len(calls) == before + 1
            emitted = json.loads(capsys.readouterr().out)
            assert len(emitted) == 7 and not emitted["ok"] and emitted["items"] == []
            emitted["duration_ms"] = 0
            snapshots[f"{mode}:{code}"] = emitted
        failure = None
    snapshot = Path(__file__).with_name("snapshots") / "team-cosign-cli.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(json.dumps(snapshots, ensure_ascii=False, indent=2) + "\n")
    assert snapshots == json.loads(snapshot.read_text())


def test_cosign_invalid_inputs_do_not_send(monkeypatch, capsys, tmp_path):
    calls = []
    monkeypatch.setattr(cli, "call", lambda *args, **kw: calls.append(args))
    for flags in (
        ["task", "review-rule", "show"],
        ["card", "policy", "show", "--task", ID],
        ["card", "signoff", "list", "--card", ID, "--limit", "101"],
        ["card", "signoff", "list", "--card", ID, "--cursor", "x" * 1025],
        ["card", "signoff", "add", "--card", ID],
    ):
        with pytest.raises(SystemExit) as error:
            cli.main([*flags, "--json"])
        assert error.value.code == 2
        capsys.readouterr()
    input_file = tmp_path / "invalid-cosign.json"
    for flags, invalid in (
        (CASES[1][0], {**RULE, "reason": " "}),
        (CASES[4][0], {**POLICY, "expected_policy_revision": -1}),
        (CASES[6][0], {**OPEN, "purpose": "response"}),
        (CASES[7][0], {**DISPOSITION, "reviewed_evidence_ids": [ID]}),
        (CASES[7][0], {**DISPOSITION, "selected_domain": "unknown"}),
        (CASES[8][0], {**CONFIRM, "purpose": "unknown"}),
        (CASES[8][0], {**CONFIRM, "expected_round": 0}),
        (CASES[8][0], {**CONFIRM, "action": "reject"}),
    ):
        input_file.write_text(json.dumps(invalid))
        with pytest.raises(SystemExit) as error:
            cli.main([*flags, "--input", str(input_file), "--json"])
        assert error.value.code == 2
        capsys.readouterr()
    assert calls == []


def test_cosign_schema_discovery(capsys):
    from app.schemas import team_workflow as schemas
    from pydantic import TypeAdapter

    cli.main(["schema", "--json"])
    schema = json.loads(capsys.readouterr().out)["data"]
    assert schema["version"] == "4.0"
    commands = schema["commands"]
    for name, input_model, output_model in (
        ("task review-rule show", None, schemas.TaskRuleData),
        ("task review-rule set", schemas.TaskRuleSet, schemas.TaskRuleData),
        ("card policy show", None, schemas.CoSignPolicyData),
        ("card policy set", schemas.RequirementCoSignPolicySet, schemas.CoSignPolicyData),
        ("card signoff list", schemas.PageQuery, schemas.SignoffsData),
        ("card review-round open", schemas.CoSignOpen, schemas.ReviewRoundData),
        ("card signoff add", schemas.CoSignSignRequest, schemas.CoSignData),
    ):
        assert commands[name]["input"] == (
            TypeAdapter(input_model).json_schema() if input_model else None
        )
        assert commands[name]["output"] == output_model.model_json_schema()
        assert commands[name]["cli_parameters"]
    assert commands["card signoff list"]["items"] == schemas.CoSignSignatureView.model_json_schema()
    assert commands["card signoff add"]["input"]["discriminator"]["propertyName"] == "purpose"
