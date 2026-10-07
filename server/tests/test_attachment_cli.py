"""DB-free API-client failure acceptance, using synthetic immutable metadata.

Failure scenarios: missing parameters cause a request; legacy output accepts new
commands; metadata pages exceed server bounds; deterministic commands report billed
cost; local upload drops bytes or changes dry-run; signed link leaves exact lineage;
stream exceeds descriptor or hash; output overwrites a retained local artifact.
"""

import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from app.core.errors import ServiceError
from app.schemas.contracts import Result
from bid_cli import main as cli
from bid_cli.attachments import COMMAND_INPUTS, download, validated
from bid_cli.schema import command_schema

ID = "00000000-0000-4000-8000-000000000001"
RAW = b"%PDF-1.7\nsynthetic-only"
HASH = hashlib.sha256(RAW).hexdigest()
WHEN = "2026-10-06T00:00:00Z"
READINESS = {
    "blockers": ["archive_review_pending"],
    "next_action": "review_uploaded_file",
    "responsible_user_id": ID,
}
ROW = {
    "id": ID,
    "org_id": ID,
    "current_revision_id": ID,
    "current_revision": 1,
    "state_version": 1,
    "kind": "contract",
    "active": True,
    "custodian_user_id": ID,
    "reviewer_user_id": ID,
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
    "org_id": ID,
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
    "created_by": ID,
    "created_at": WHEN,
    "metadata": {"kind": "contract", "label": "Synthetic"},
    "original": FILE,
    "parts": [],
}


def envelope(command, data, items=()):
    return Result(ok=True, command=command, data=data, items=list(items)).model_dump(mode="json")


def test_discovery_exact_result4_commands_and_schema_snapshot():
    schema = command_schema(cli.app)
    snapshot = json.loads(Path("server/tests/snapshots/cli-v1.json").read_text())["schema"]["data"][
        "commands"
    ]
    assert len(COMMAND_INPUTS) == 25
    for name in COMMAND_INPUTS:
        assert schema["commands"][name] == snapshot[name]
        assert schema["commands"][name]["cli_parameters"]
        assert name not in command_schema(cli.app, "3.0")["commands"]


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


def test_upload_transmits_unchanged_pdf_and_explicit_reviewer(monkeypatch, capsys, tmp_path):
    content = tmp_path / "original.pdf"
    content.write_bytes(RAW)
    meta = tmp_path / "input.json"
    meta.write_text(
        json.dumps(
            {
                "request_id": ID,
                "metadata": {"kind": "contract", "label": "Synthetic"},
                "reviewer_user_id": ID,
            }
        )
    )

    def call(method, path, **kwargs):
        assert method == "POST" and path == "/resources/attachments"
        assert kwargs["files"] == [("files", ("original.pdf", RAW, "application/pdf"))]
        assert json.loads(kwargs["data"]["metadata"])["reviewer_user_id"] == ID
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
        self.state = self
        self.requests = []

    def load(self):
        return {"session": "synthetic", "org_id": ID}

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
            body["cost"]["billing_currency"] = "CNY"
            return body
        return envelope("resource attachment revision show", REVISION)

    @asynccontextmanager
    async def transport(self):
        def handle(request):
            assert request.url.path == f"/v4/resources/attachments/revisions/{ID}/file/download"
            assert request.headers["X-Org-Id"] == ID
            return httpx.Response(self.status, content=self.content)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle), base_url="http://synthetic"
        ) as client:
            yield client


async def test_authenticated_checksum_download_preserves_exact_bytes(tmp_path):
    output = tmp_path / "download.pdf"
    body = await download(FakeDownload(), UUID(ID), output, None)
    assert output.read_bytes() == RAW
    assert body["data"]["file"]["sha256"] == HASH
    assert body["data"]["file_id"] == ID
    assert body["cost"]["basis"] == "zero"
    assert body["cost"]["billing_currency"] == "CNY"


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
async def test_download_failure_writes_no_local_file(tmp_path, kwargs, code):
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
