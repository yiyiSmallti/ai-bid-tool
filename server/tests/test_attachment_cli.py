"""DB-free API-client failure acceptance, using synthetic immutable metadata.

Failure scenarios: missing parameters cause a request; legacy output accepts new
commands; metadata pages exceed server bounds; deterministic commands report billed
cost; local upload drops bytes or changes dry-run; signed link leaves exact lineage;
stream exceeds descriptor or hash; output overwrites a retained local artifact.
"""

import hashlib
import json
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
import pytest
from app.core.errors import ServiceError
from app.schemas.contracts import Result
from bid_cli import main as cli
from bid_cli.attachments import COMMAND_INPUTS, download, validated
from bid_cli.schema import command_schema
from pydantic import TypeAdapter

ID = "00000000-0000-4000-8000-000000000001"
RAW = b"%PDF-1.7\nsynthetic-only"
HASH = hashlib.sha256(RAW).hexdigest()
WHEN = "2026-10-06T00:00:00Z"
READINESS = {
    "blockers": ["archive_review_pending"],
    "next_action": "review_uploaded_file",
}
ROW = {
    "id": ID,
    "current_revision_id": ID,
    "current_revision": 1,
    "state_version": 1,
    "kind": "contract",
    "active": True,
    "review_state": "pending",
    "latest_review_id": None,
    "created_at": WHEN,
    "readiness": READINESS,
}
FILE = {
    "name": "attachment.pdf",
    "media_type": "application/pdf",
    "size_bytes": len(RAW),
    "sha256": HASH,
    "page_count": 1,
}
REVISION = {
    "id": ID,
    "attachment_id": ID,
    "revision": 1,
    "file_id": ID,
    "kind": "contract",
    "original_sha256": HASH,
    "metadata_sha256": "b" * 64,
    "page_count": 1,
    "size_bytes": len(RAW),
    "review_state": "pending",
    "latest_review_id": None,
    "created_at": WHEN,
    "metadata": {"kind": "contract", "label": "Synthetic"},
    "original": FILE,
    "parts": [],
}


def envelope(command, data, items=()):
    return Result(ok=True, command=command, data=data, items=list(items)).model_dump(mode="json")


def test_discovery_exact_result4_commands_and_metadata_schema():
    schema = command_schema(cli.app)
    assert len(COMMAND_INPUTS) == 24
    assert "resource attachment assign" not in schema["commands"]
    for name in COMMAND_INPUTS:
        model = COMMAND_INPUTS[name]
        assert schema["commands"][name]["input"] == (
            TypeAdapter(model).json_schema() if model else None
        )
        assert schema["commands"][name]["cli_parameters"]
        with pytest.raises(ValueError, match="Only contract 5.0"):
            command_schema(cli.app, "3.0")


@pytest.mark.parametrize(
    "command", [name for name in COMMAND_INPUTS if name != "resource attachment list"]
)
def test_required_arguments_fail_without_transport(monkeypatch, capsys, command):
    def fail(*args, **kwargs):
        raise AssertionError("Transport must not run")

    monkeypatch.setattr(cli, "call", fail)
    with pytest.raises(SystemExit) as exc:
        cli.main([*command.split(), "--json"])
    assert exc.value.code == 2
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is False
    assert body["command"] == command
    assert set(body) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}


def test_list_cursor_history_kind_are_bounded_and_validated(monkeypatch, capsys):
    calls = []

    def call(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return envelope(
            "resource attachment list", {"limit": 50, "history": True, "next_cursor": None}, [ROW]
        )

    monkeypatch.setattr(cli, "call", call)
    cli.main(
        [
            "resource",
            "attachment",
            "list",
            "--kind",
            "contract",
            "--history",
            "--cursor",
            "synthetic-cursor",
            "--json",
        ]
    )
    assert calls == [
        (
            "GET",
            "/resources/attachments",
            {
                "params": {
                    "kind": "contract",
                    "history": True,
                    "cursor": "synthetic-cursor",
                    "limit": 50,
                }
            },
        )
    ]
    assert json.loads(capsys.readouterr().out)["items"][0]["id"] == ID


@pytest.mark.parametrize("change", ["oversize", "cost", "items", "command"])
def test_invalid_success_response_is_rejected(change):
    body = envelope(
        "resource attachment list", {"limit": 1, "history": False, "next_cursor": None}, [ROW]
    )
    if change == "oversize":
        body["items"].append(ROW)
    if change == "cost":
        body["cost"]["llm_tokens"] = 1
    if change == "items":
        body["items"][0] = {"id": ID}
    if change == "command":
        body["command"] = "other"
    with pytest.raises(ServiceError) as exc:
        validated(body, "resource attachment list")
    assert exc.value.code == "invalid_server_response"


def test_legacy_attachment_rejected_before_transport(monkeypatch, capsys):
    monkeypatch.setattr(cli, "call", lambda *args, **kwargs: pytest.fail("No v3 attachment route"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["--contract-version", "3.0", "resource", "attachment", "list", "--json"])
    assert exc.value.code == 2
    assert json.loads(capsys.readouterr().out)["data"]["code"] == "invalid_input"


def test_upload_transmits_unchanged_pdf_and_explicit_metadata(monkeypatch, capsys, tmp_path):
    content = tmp_path / "original.pdf"
    content.write_bytes(RAW)
    meta = tmp_path / "input.json"
    meta.write_text(
        json.dumps(
            {
                "request_id": ID,
                "metadata": {"kind": "contract", "label": "Synthetic"},
            }
        )
    )

    def call(method, path, **kwargs):
        assert method == "POST" and path == "/resources/attachments"
        assert kwargs["files"] == [("files", ("original.pdf", RAW, "application/pdf"))]
        assert json.loads(kwargs["data"]["metadata"]) == {
            "request_id": ID,
            "metadata": {"kind": "contract", "label": "Synthetic"},
            "parts": [],
            "dry_run": False,
        }
        return envelope(
            "resource attachment upload",
            {
                "attachment_id": ID,
                "revision_id": ID,
                "file_id": ID,
                "revision": 1,
                "state_version": 1,
                "review_state": "pending",
                "duplicate": False,
                "next_action": "review_uploaded_file",
            },
        )

    monkeypatch.setattr(cli, "call", call)
    cli.main(
        ["resource", "attachment", "upload", "--input", str(meta), "--file", str(content), "--json"]
    )
    assert json.loads(capsys.readouterr().out)["data"]["review_state"] == "pending"


class FakeDownload:
    def __init__(self, content=RAW, url=None, status=200):
        self.content, self.url, self.status = content, url, status
        self.requests = []

    async def request(self, method, path):
        self.requests.append(path)
        if path.endswith("/file/download-link"):
            body = envelope(
                "resource attachment file download",
                {
                    "url": self.url
                    or f"/resources/attachments/revisions/{ID}/file/download?signature=synthetic",
                    "expires_in": 300,
                },
            )
            return body
        return envelope("resource attachment revision show", REVISION)

    @asynccontextmanager
    async def transport(self):
        def handle(request):
            assert request.url.path == f"/v4/resources/attachments/revisions/{ID}/file/download"
            assert request.headers["Authorization"] == "Bearer synthetic-installation-access-key"
            assert "X-Org-Id" not in request.headers
            return httpx.Response(self.status, content=self.content)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle), base_url="http://synthetic"
        ) as client:
            yield client


async def test_authenticated_checksum_download_preserves_exact_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("BID_ACCESS_KEY", "synthetic-installation-access-key")
    output = tmp_path / "download.pdf"
    body = await download(FakeDownload(), UUID(ID), output, None)
    assert output.read_bytes() == RAW
    assert body["data"]["file"]["sha256"] == HASH
    assert body["data"]["file_id"] == ID


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"content": RAW + b"overflow"}, "attachment_integrity"),
        ({"content": b"x" * len(RAW)}, "attachment_integrity"),
        ({"url": "https://foreign.invalid/original"}, "invalid_download_link"),
        (
            {
                "url": f"/resources/attachments/revisions/{ID}/file/download?signature=synthetic&other=1"
            },
            "invalid_download_link",
        ),
        ({"status": 302}, "download_unavailable"),
    ],
)
async def test_download_failure_writes_no_local_file(tmp_path, kwargs, code, monkeypatch):
    monkeypatch.setenv("BID_ACCESS_KEY", "synthetic-installation-access-key")
    output = tmp_path / "download.pdf"
    with pytest.raises(ServiceError) as exc:
        await download(FakeDownload(**kwargs), UUID(ID), output, None)
    assert exc.value.code == code
    assert not output.exists()


async def test_download_never_overwrites_existing_output(tmp_path):
    output = tmp_path / "download.pdf"
    output.write_bytes(b"retained")
    with pytest.raises(ServiceError) as exc:
        await download(FakeDownload(), UUID(ID), output, None)
    assert exc.value.code == "invalid_output_path"
    assert output.read_bytes() == b"retained"
