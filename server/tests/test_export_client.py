import hashlib
import json
from contextlib import asynccontextmanager
from copy import deepcopy
from uuid import UUID

import httpx
import pytest
from app.core.errors import ServiceError
from app.schemas.contracts import Result
from app.schemas.export_contracts import (
    DOCX_MEDIA_TYPE,
    ExportBindingCreate,
    ExportPrepare,
    ExportRunView,
)
from bid_cli.client import Client, State
from bid_cli.export_client import download_export
from bid_cli.main import main
from pydantic import ValidationError

IDENTIFIER = "00000000-0000-0000-0000-000000000001"
IDENTIFIER_2 = "00000000-0000-0000-0000-000000000002"
SHA = "a" * 64


class SyntheticState(State):
    def load(self):
        return {"session": "synthetic-only-session", "org_id": IDENTIFIER}


def export_view(content, *, mode="final_section", validity="current", org_id=IDENTIFIER):
    return {
        "id": IDENTIFIER,
        "org_id": org_id,
        "task_id": IDENTIFIER,
        "run_id": IDENTIFIER,
        "draft_id": IDENTIFIER,
        "task_template_id": IDENTIFIER,
        "template_revision_id": IDENTIFIER,
        "binding_id": IDENTIFIER,
        "mode": mode,
        "completion": "partial" if mode == "review_copy" else "complete",
        "validity": validity,
        "input_hash": SHA,
        "manifest_hash": "b" * 64,
        "file": {
            "name": "synthetic-export.docx",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "media_type": DOCX_MEDIA_TYPE,
        },
        "released_by": IDENTIFIER,
        "released_at": "2026-10-02T00:00:00Z",
        "issues": [],
        "invalidated_requirement_ids": [],
    }


def download_client(
    tmp_path,
    content,
    *,
    mode="final_section",
    validity="current",
    org_id=IDENTIFIER,
    link=None,
    served=None,
    media_type=DOCX_MEDIA_TYPE,
    redirect=False,
    transport_error=None,
    on_download=None,
    download_error=None,
):
    client = Client(
        "remote", "https://synthetic.example.test", SyntheticState(tmp_path / "unused.enc")
    )
    path = f"/exports/{IDENTIFIER}/download"
    view = export_view(content, mode=mode, validity=validity, org_id=org_id)
    calls = []

    def handler(request):
        calls.append(request.url)
        assert request.headers["Authorization"] == "Bearer synthetic-only-session"
        assert request.headers["X-Org-Id"] == IDENTIFIER
        if request.url.path == f"/exports/{IDENTIFIER}":
            return httpx.Response(
                200,
                json=Result(ok=True, command="fixture", data=view).model_dump(mode="json"),
            )
        if request.url.path == path + "-link":
            descriptor = deepcopy(view["file"])
            return httpx.Response(
                200,
                json=Result(
                    ok=True,
                    command="fixture",
                    data={
                        "export_id": IDENTIFIER,
                        "file": descriptor,
                        "url": link or path + "?signature=synthetic-only",
                        "expires_in": 300,
                    },
                ).model_dump(mode="json"),
            )
        assert request.url.path == path and request.url.params["signature"] == "synthetic-only"
        if transport_error is not None:
            raise transport_error("Synthetic transfer failure", request=request)
        if on_download is not None:
            on_download()
        if download_error is not None:
            status, body = download_error
            if isinstance(body, dict):
                return httpx.Response(status, json=body)
            return httpx.Response(status, content=body)
        if redirect:
            return httpx.Response(302, headers={"Location": "https://foreign.example.test/file"})
        return httpx.Response(
            200,
            content=content if served is None else served,
            headers={"Content-Type": media_type},
        )

    @asynccontextmanager
    async def transport():
        async with httpx.AsyncClient(
            base_url="https://synthetic.example.test", transport=httpx.MockTransport(handler)
        ) as http:
            yield http

    client.transport = transport
    return client, calls


@pytest.mark.parametrize(
    ("mode", "ok", "completion"),
    [("final_section", True, "complete"), ("review_copy", False, "partial")],
)
async def test_export_download_verifies_and_atomically_saves_real_docx(
    tmp_path, docx_bytes, mode, ok, completion
):
    output = tmp_path.resolve() / f"{mode}.docx"
    client, calls = download_client(tmp_path, docx_bytes, mode=mode)
    result = await download_export(client, UUID(IDENTIFIER), output)
    assert result["ok"] is ok and result["data"]["completion"] == completion
    assert result["data"]["file"]["sha256"] == hashlib.sha256(docx_bytes).hexdigest()
    assert output.read_bytes() == docx_bytes and output.stat().st_mode & 0o777 == 0o600
    assert len(calls) == 3 and not list(tmp_path.glob(".bid-download-*"))
    with pytest.raises(ServiceError) as error:
        await download_export(client, UUID(IDENTIFIER), output)
    assert error.value.exit_code == 2 and len(calls) == 3


@pytest.mark.parametrize(
    "link",
    [
        "https://foreign.example.test/file",
        "//foreign.example.test/file",
        "/arbitrary?signature=synthetic-only",
        f"/exports/{IDENTIFIER}/download?signature=one&signature=two",
        f"/exports/{IDENTIFIER}/download?signature=synthetic-only&extra=true",
        f"/exports/{IDENTIFIER}/download?signature=synthetic-only#fragment",
    ],
)
async def test_export_download_rejects_external_or_malformed_links(tmp_path, docx_bytes, link):
    client, calls = download_client(tmp_path, docx_bytes, link=link)
    output = tmp_path.resolve() / "new.docx"
    with pytest.raises(ServiceError) as error:
        await download_export(client, UUID(IDENTIFIER), output)
    assert error.value.exit_code == 4 and len(calls) == 2
    assert not output.exists() and not list(tmp_path.glob(".bid-download-*"))


@pytest.mark.parametrize(
    "failure",
    ["hash", "short", "long", "redirect", "media_type", "corrupt", "foreign", "stale"],
)
async def test_export_download_never_writes_untrusted_or_stale_content(
    tmp_path, docx_bytes, failure
):
    served = {
        "hash": b"x" * len(docx_bytes),
        "short": docx_bytes[:-1],
        "long": docx_bytes + b"x",
        "corrupt": b"x" * len(docx_bytes),
    }.get(failure)
    if failure == "corrupt":
        content = served
        served = None
    else:
        content = docx_bytes
    client, calls = download_client(
        tmp_path,
        content,
        served=served,
        redirect=failure == "redirect",
        media_type="application/octet-stream" if failure == "media_type" else DOCX_MEDIA_TYPE,
        org_id=IDENTIFIER_2 if failure == "foreign" else IDENTIFIER,
        validity="stale" if failure == "stale" else "current",
    )
    output = tmp_path.resolve() / "new.docx"
    with pytest.raises(ServiceError) as error:
        await download_export(client, UUID(IDENTIFIER), output)
    assert error.value.exit_code == 4
    assert not output.exists() and not list(tmp_path.glob(".bid-download-*"))
    assert all(url.host == "synthetic.example.test" for url in calls)


@pytest.mark.parametrize(
    "transport_error", [httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError]
)
async def test_export_download_interrupted_transfer_is_retryable(
    tmp_path, docx_bytes, transport_error
):
    client, _ = download_client(tmp_path, docx_bytes, transport_error=transport_error)
    output = tmp_path.resolve() / "new.docx"
    with pytest.raises(ServiceError) as error:
        await download_export(client, UUID(IDENTIFIER), output)
    assert error.value.code == "network_unavailable" and error.value.exit_code == 3
    assert not output.exists() and not list(tmp_path.glob(".bid-download-*"))


async def test_export_download_preserves_bounded_server_error_result(tmp_path, docx_bytes):
    failure = Result(
        ok=False,
        command="export download",
        data={
            "error": {
                "code": "export_input_changed",
                "message": "Export inputs changed",
                "exit_code": 3,
            }
        },
    ).model_dump(mode="json")
    client, _ = download_client(tmp_path, docx_bytes, download_error=(409, failure))
    output = tmp_path.resolve() / "new.docx"
    with pytest.raises(ServiceError) as error:
        await download_export(client, UUID(IDENTIFIER), output)
    assert (
        error.value.code == "export_input_changed"
        and error.value.message == "Export inputs changed"
        and error.value.status == 409
        and error.value.exit_code == 3
    )
    assert not output.exists() and not list(tmp_path.glob(".bid-download-*"))


@pytest.mark.parametrize(
    "body",
    [b"not-json", b"{}", b"x" * (64 * 1024 + 1)],
)
async def test_export_download_invalid_error_protocol_is_fatal(tmp_path, docx_bytes, body):
    client, _ = download_client(tmp_path, docx_bytes, download_error=(502, body))
    output = tmp_path.resolve() / "new.docx"
    with pytest.raises(ServiceError) as error:
        await download_export(client, UUID(IDENTIFIER), output)
    assert error.value.code == "invalid_server_response" and error.value.exit_code == 4
    assert not output.exists() and not list(tmp_path.glob(".bid-download-*"))


async def test_export_download_rejects_parent_directory_replacement(tmp_path, docx_bytes):
    original = tmp_path / "target"
    moved = tmp_path / "moved-target"
    original.mkdir()

    def replace_parent():
        original.rename(moved)
        original.mkdir()

    client, _ = download_client(tmp_path, docx_bytes, on_download=replace_parent)
    output = original / "new.docx"
    with pytest.raises(ServiceError) as error:
        await download_export(client, UUID(IDENTIFIER), output)
    assert error.value.code == "invalid_output_path" and error.value.exit_code == 2
    assert not output.exists() and not list(original.iterdir()) and not list(moved.iterdir())


def section_bindings():
    columns = [
        {"key": key, "width_percent": width}
        for key, width in zip(
            (
                "ordinal",
                "requirement",
                "response",
                "compliance",
            ),
            (6, 36, 44, 14),
            strict=True,
        )
    ]
    return [
        {
            "section": section,
            "heading_style_id": "Heading 1",
            "table_style_id": "Table Grid",
            "columns": deepcopy(columns)
            if section in {"substantive", "commercial", "technical"}
            else [],
        }
        for section in (
            "substantive",
            "commercial",
            "technical",
            "comply_only",
            "gaps",
            "evidence_appendix",
        )
    ]


@pytest.mark.parametrize("failure", ["order", "columns", "width", "style", "static_hash"])
def test_binding_contract_rejects_incomplete_or_ambiguous_mapping(failure):
    value = {
        "template_revision_id": IDENTIFIER,
        "expected_template_sha256": SHA,
        "expected_static_content_hash": "b" * 64,
        "sections": section_bindings(),
    }
    if failure == "order":
        value["sections"][0], value["sections"][1] = value["sections"][1], value["sections"][0]
    elif failure == "columns":
        value["sections"][0]["columns"].pop()
    elif failure == "width":
        value["sections"][0]["columns"][0]["width_percent"] = 7
    elif failure == "style":
        value["sections"][0]["heading_style_id"] = " "
    else:
        value["expected_static_content_hash"] = None
    with pytest.raises(ValidationError):
        ExportBindingCreate.model_validate(value)


@pytest.mark.parametrize("failure", ["missing_hash", "dry_retry", "dry_ack", "duplicate_ack"])
def test_prepare_contract_rejects_gate_bypass_inputs(failure):
    value = {
        "draft_id": IDENTIFIER,
        "task_template_id": IDENTIFIER,
        "binding_id": IDENTIFIER,
        "mode": "final_section",
        "expected_input_hash": SHA,
    }
    if failure == "missing_hash":
        value["expected_input_hash"] = None
    elif failure == "dry_retry":
        value.update({"dry_run": True, "retry": True, "expected_input_hash": None})
    elif failure == "dry_ack":
        value.update(
            {"dry_run": True, "acknowledged_issue_ids": [SHA], "expected_input_hash": None}
        )
    else:
        value["acknowledged_issue_ids"] = [SHA, SHA]
    with pytest.raises(ValidationError):
        ExportPrepare.model_validate(value)


def test_invalidated_run_retains_only_existing_export_identifier():
    value = {
        "id": IDENTIFIER,
        "org_id": IDENTIFIER,
        "task_id": IDENTIFIER,
        "draft_id": IDENTIFIER,
        "render_job_id": IDENTIFIER_2,
        "mode": "final_section",
        "input_hash": SHA,
        "state": "invalidated",
        "candidate_sha256": None,
        "export_id": IDENTIFIER,
        "issues": [],
    }
    assert ExportRunView.model_validate(value).export_id == UUID(IDENTIFIER)
    with pytest.raises(ValidationError):
        ExportRunView.model_validate({**value, "candidate_sha256": "b" * 64})


def test_cli_rejects_conflicting_input_flags_before_network(monkeypatch, tmp_path, capsys):
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("conflicting input reached the API")

    monkeypatch.setattr(Client, "request", forbidden)
    metadata = tmp_path / "prepare.json"
    metadata.write_text(
        json.dumps(
            {
                "draft_id": IDENTIFIER,
                "task_template_id": IDENTIFIER,
                "binding_id": IDENTIFIER,
                "mode": "final_section",
                "expected_input_hash": SHA,
                "dry_run": False,
            }
        )
    )
    with pytest.raises(SystemExit) as error:
        main(
            [
                "export",
                "prepare",
                "--task",
                IDENTIFIER,
                "--input",
                str(metadata),
                "--dry-run",
                "--json",
            ]
        )
    body = json.loads(capsys.readouterr().out)
    assert error.value.code == 2 and body["data"]["error"]["code"] == "conflicting_input_option"
    assert not calls


def test_cli_preserves_blocked_dry_run_preview_from_http_400(monkeypatch, tmp_path, capsys):
    issue = {
        "issue_id": SHA,
        "code": "export_gaps_present",
        "severity": "block",
        "requirement_ids": [IDENTIFIER],
        "evidence_ids": [],
    }
    preview = {
        "dry_run": True,
        "task_id": IDENTIFIER,
        "draft_id": IDENTIFIER,
        "mode": "final_section",
        "input_hash": "b" * 64,
        "ready": False,
        "requirement_count": 1,
        "table_rows": {"substantive": 0, "commercial": 0, "technical": 0},
        "comply_only_count": 0,
        "gap_count": 1,
        "negative_count": 0,
        "attachment_pages": 0,
        "issues": [issue],
        "estimated_output_bytes": None,
        "estimated_duration_ms": None,
        "estimated_cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0},
        "error": {
            "code": "export_blocked",
            "message": "Export preview contains blocking issues",
            "exit_code": 2,
        },
    }

    def handler(request):
        assert request.method == "POST" and request.url.path.endswith("/export-runs")
        return httpx.Response(
            400,
            json=Result(ok=False, command="export prepare", data=preview).model_dump(mode="json"),
        )

    @asynccontextmanager
    async def transport(self):
        async with httpx.AsyncClient(
            base_url="https://synthetic.example.test", transport=httpx.MockTransport(handler)
        ) as http:
            yield http

    monkeypatch.setattr(Client, "transport", transport)
    monkeypatch.setenv("BID_SESSION", "synthetic-only-session")
    monkeypatch.setenv("BID_ORG", IDENTIFIER)
    metadata = tmp_path / "preview.json"
    metadata.write_text(
        json.dumps(
            {
                "draft_id": IDENTIFIER,
                "task_template_id": IDENTIFIER,
                "binding_id": IDENTIFIER,
                "mode": "final_section",
                "dry_run": True,
            }
        )
    )
    with pytest.raises(SystemExit) as error:
        main(
            [
                "--server",
                "https://synthetic.example.test",
                "export",
                "prepare",
                "--task",
                IDENTIFIER,
                "--input",
                str(metadata),
                "--json",
            ]
        )
    body = json.loads(capsys.readouterr().out)
    assert error.value.code == 2 and body["ok"] is False
    assert body["data"]["error"]["code"] == "export_blocked"
    assert body["data"]["issues"] == [issue]


def test_prepare_wait_stops_at_candidate_without_releasing(monkeypatch, tmp_path, capsys):
    calls = []
    queued = {
        "id": IDENTIFIER,
        "org_id": IDENTIFIER,
        "task_id": IDENTIFIER,
        "draft_id": IDENTIFIER,
        "render_job_id": IDENTIFIER_2,
        "mode": "final_section",
        "input_hash": SHA,
        "state": "queued",
        "candidate_sha256": None,
        "export_id": None,
        "issues": [],
    }
    ready = {**queued, "state": "awaiting_release", "candidate_sha256": "b" * 64}

    async def request(self, method, path, **kwargs):
        calls.append((method, path))
        data = queued if method == "POST" else ready
        return Result(ok=True, command="fixture", data=data).model_dump(mode="json")

    monkeypatch.setattr(Client, "request", request)
    metadata = tmp_path / "prepare.json"
    metadata.write_text(
        json.dumps(
            {
                "draft_id": IDENTIFIER,
                "task_template_id": IDENTIFIER,
                "binding_id": IDENTIFIER,
                "mode": "final_section",
                "expected_input_hash": SHA,
            }
        )
    )
    main(
        [
            "export",
            "prepare",
            "--task",
            IDENTIFIER,
            "--input",
            str(metadata),
            "--wait",
            "--json",
        ]
    )
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is True and body["data"]["state"] == "awaiting_release"
    assert calls == [
        ("POST", f"/tasks/{IDENTIFIER}/export-runs"),
        ("GET", f"/export-runs/{IDENTIFIER}"),
    ]


def test_release_invalid_success_payload_is_fatal_result(monkeypatch, tmp_path, capsys):
    async def malformed(self, method, path, **kwargs):
        return Result(ok=True, command="fixture").model_dump(mode="json")

    monkeypatch.setattr(Client, "request", malformed)
    metadata = tmp_path / "release.json"
    metadata.write_text(
        json.dumps(
            {
                "expected_input_hash": SHA,
                "expected_candidate_sha256": "b" * 64,
            }
        )
    )
    with pytest.raises(SystemExit) as error:
        main(
            [
                "export",
                "release",
                "--run",
                IDENTIFIER,
                "--input",
                str(metadata),
                "--json",
            ]
        )
    body = json.loads(capsys.readouterr().out)
    assert error.value.code == 4 and body["ok"] is False
    assert body["data"]["error"]["code"] == "invalid_server_response"
