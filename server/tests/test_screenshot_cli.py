"""Screenshot CLI transport, local file transaction and contract snapshots.

Failure modes covered here:
- prepare requires exactly one matching local upload, authorized certificate source or
  captured vendor page image;
- an existing path, symlink, missing parent, duplicate target, renderer mismatch, or failure
  while publishing either file leaves no partial PNG/receipt pair;
- successful local output and receipt are separate new mode-0600 files and only then emit
  the seven-key Result contract;
- certificate and rendition downloads accept only an exact same-origin signed route, disable
  redirects, bound bytes, and verify the PNG descriptor and hash before saving;
- add rejects bytes that differ from the reviewed receipt before multipart transport;
- malformed dry-run/retry combinations and invalid analyze/decision inputs never dispatch;
- every remote command uses the approved method, path, query and typed JSON/multipart shape;
- wait merges only the terminal job result and preserves partial-completion exit behavior.

All bytes and HTTP responses are synthetic. These tests do not build Rust, use PostgreSQL,
or call a real provider.
"""

import asyncio
import binascii
import hashlib
import json
import os
import stat
import struct
import sys
import types
import zlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from app.core.errors import ServiceError
from app.providers.screenshot_renderer import validate_png
from app.schemas.contracts import Result
from app.schemas.screenshot_contracts import (
    ContentMapping,
    ImagePlan,
    PixelRect,
    PreparedScreenshot,
    ScreenshotIngest,
    UploadSource,
)
from bid_cli import main as cli
from bid_cli import screenshots as screenshot_cli

IDENTIFIER = "11111111-1111-4111-8111-111111111111"
IDENTIFIER_2 = "22222222-2222-4222-8222-222222222222"
IDENTIFIER_3 = "33333333-3333-4333-8333-333333333333"
SHA = "a" * 64


def _chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def png(width: int = 4, height: int = 3, value: int = 127) -> bytes:
    rows = [b"\x00" + bytes([value, value, value]) * width for _ in range(height)]
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            _chunk(b"IDAT", zlib.compress(b"".join(rows))),
            _chunk(b"IEND", b""),
        )
    )


def source() -> UploadSource:
    return UploadSource(
        kind="upload",
        task_feature_id=UUID(IDENTIFIER),
        image_kind="screenshot",
        source_label="Synthetic reviewed screenshot",
        software_version="test-1",
        environment="test",
        captured_at=datetime(2026, 10, 2, tzinfo=UTC),
    )


def prepared(content: bytes, upload_source=None) -> PreparedScreenshot:
    descriptor = validate_png(content)
    plan = ImagePlan()
    return PreparedScreenshot(
        source=upload_source or source(),
        source_sha256=descriptor["sha256"],
        source_width=descriptor["width_px"],
        source_height=descriptor["height_px"],
        plan=plan,
        plan_sha256=hashlib.sha256(b"synthetic-plan").hexdigest(),
        preparation_profile="screenshot-privacy-v1",
        prepared_at=datetime(2026, 10, 2, tzinfo=UTC),
        image=descriptor,
        mapping=ContentMapping(
            crop=PixelRect(
                x=0,
                y=0,
                width=descriptor["width_px"],
                height=descriptor["height_px"],
            ),
            content_offset_x=0,
            content_offset_y=0,
            content_width=descriptor["width_px"],
            content_height=descriptor["height_px"],
            footer_height=0,
        ),
    )


def install_fake_prepare(monkeypatch, content: bytes, calls: list | None = None):
    async def fake_prepare(input_bytes, upload_source, plan):
        if calls is not None:
            calls.append((input_bytes, upload_source, plan))
        return content, prepared(content, upload_source)

    module = types.SimpleNamespace(prepare=fake_prepare)
    import app.services

    monkeypatch.setitem(sys.modules, "app.services.screenshots", module)
    monkeypatch.setattr(app.services, "screenshots", module, raising=False)


def write_prepare_input(path: Path):
    path.write_text(
        json.dumps({"source": source().model_dump(mode="json"), "plan": ImagePlan().model_dump()}),
        encoding="utf-8",
    )


def invoke(args: list[str], capsys) -> dict:
    cli.main([*args, "--json"])
    value = json.loads(capsys.readouterr().out)
    assert set(value) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
    return value


def test_prepare_publishes_both_mode_0600_files_then_result(tmp_path, monkeypatch, capsys):
    content = png()
    calls = []
    install_fake_prepare(monkeypatch, content, calls)
    original = tmp_path / "reviewed.png"
    original.write_bytes(content)
    request = tmp_path / "prepare.json"
    write_prepare_input(request)
    output, receipt = tmp_path / "SAFE.png", tmp_path / "SAFE.json"

    body = invoke(
        [
            "screenshot",
            "prepare",
            "--file",
            str(original),
            "--input",
            str(request),
            "--output",
            str(output),
            "--receipt",
            str(receipt),
        ],
        capsys,
    )

    assert body["ok"] is True and body["command"] == "screenshot prepare"
    assert output.read_bytes() == content
    assert (
        PreparedScreenshot.model_validate_json(receipt.read_text()).image.sha256
        == hashlib.sha256(content).hexdigest()
    )
    assert stat.S_IMODE(output.stat().st_mode) == stat.S_IMODE(receipt.stat().st_mode) == 0o600
    assert len(calls) == 1


def test_prepare_source_requires_matching_authorized_certificate_preview(
    tmp_path, monkeypatch, capsys
):
    content = png()
    source_id = UUID(IDENTIFIER_2)
    certificate_source = {"kind": "certificate_page", "evidence_source_id": str(source_id)}
    request = tmp_path / "prepare-source.json"
    request.write_text(json.dumps({"source": certificate_source, "plan": ImagePlan().model_dump()}))
    install_fake_prepare(monkeypatch, content)
    requested = []

    async def fake_preview(value):
        requested.append(value)
        return content

    monkeypatch.setattr(screenshot_cli, "_read_certificate_preview", fake_preview)
    output, receipt = tmp_path / "CERT.png", tmp_path / "CERT.json"

    body = invoke(
        [
            "screenshot",
            "prepare",
            "--source",
            str(source_id),
            "--input",
            str(request),
            "--output",
            str(output),
            "--receipt",
            str(receipt),
        ],
        capsys,
    )

    assert body["ok"] is True and requested == [source_id]
    assert (
        PreparedScreenshot.model_validate_json(receipt.read_text()).source.kind
        == "certificate_page"
    )


@pytest.mark.parametrize("artifact_kind", ["capture_png", "rendered_html"])
def test_prepare_sandbox_artifact_reads_only_the_named_vendor_page(
    tmp_path, monkeypatch, capsys, artifact_kind
):
    from app.schemas.sandbox_contracts import SandboxArtifactView
    from bid_cli import sandbox as sandbox_cli

    content = png()
    artifact_id = UUID(IDENTIFIER_2)
    request = tmp_path / "prepare-vendor.json"
    request.write_text(
        json.dumps(
            {
                "source": {"kind": "vendor_web", "sandbox_artifact_id": str(artifact_id)},
                "plan": ImagePlan().model_dump(),
            }
        )
    )
    install_fake_prepare(monkeypatch, content)
    requested = []

    async def fake_fetch(api, value):
        requested.append(value)
        view = SandboxArtifactView(
            id=value,
            run_id=UUID(IDENTIFIER_3),
            attempt_id=UUID(IDENTIFIER),
            kind=artifact_kind,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            media_type="image/png" if artifact_kind == "capture_png" else "text/html",
            width=4 if artifact_kind == "capture_png" else None,
            height=3 if artifact_kind == "capture_png" else None,
            provenance_manifest_hash=SHA,
        )
        return view, bytearray(content)

    monkeypatch.setattr(sandbox_cli, "fetch_artifact", fake_fetch)
    monkeypatch.setattr(cli, "client", lambda: object())
    arguments = ["--input", str(request), "--output", str(tmp_path / "V.png")]
    arguments += ["--receipt", str(tmp_path / "V.json")]

    with pytest.raises(SystemExit) as mismatched:
        cli.main(["screenshot", "prepare", "--sandbox-artifact", IDENTIFIER, *arguments, "--json"])
    body = json.loads(capsys.readouterr().out)
    assert mismatched.value.code == 2 and body["data"]["error"]["code"] == "invalid_input"
    assert requested == []

    if artifact_kind == "rendered_html":
        with pytest.raises(SystemExit) as refused:
            cli.main(
                ["screenshot", "prepare", "--sandbox-artifact", str(artifact_id), *arguments]
                + ["--json"]
            )
        body = json.loads(capsys.readouterr().out)
        assert refused.value.code == 2 and body["data"]["error"]["code"] == "invalid_input"
        assert not (tmp_path / "V.png").exists() and not (tmp_path / "V.json").exists()
        return
    body = invoke(
        ["screenshot", "prepare", "--sandbox-artifact", str(artifact_id), *arguments], capsys
    )
    assert body["ok"] is True and requested == [artifact_id]
    receipt = PreparedScreenshot.model_validate_json((tmp_path / "V.json").read_text())
    assert receipt.source.model_dump(mode="json") == {
        "kind": "vendor_web",
        "sandbox_artifact_id": str(artifact_id),
    }


def test_prepare_second_publish_failure_rolls_back_both_files(tmp_path, monkeypatch, capsys):
    content = png()
    install_fake_prepare(monkeypatch, content)
    original = tmp_path / "reviewed.png"
    original.write_bytes(content)
    request = tmp_path / "prepare.json"
    write_prepare_input(request)
    output, receipt = tmp_path / "SAFE.png", tmp_path / "SAFE.json"
    real_link = screenshot_cli.os.link
    linked = 0

    def fail_second(source_path, target_path, **kwargs):
        nonlocal linked
        linked += 1
        if linked == 2:
            raise OSError("synthetic second publish failure")
        return real_link(source_path, target_path, **kwargs)

    monkeypatch.setattr(screenshot_cli.os, "link", fail_second)
    with pytest.raises(SystemExit) as stopped:
        cli.main(
            [
                "screenshot",
                "prepare",
                "--file",
                str(original),
                "--input",
                str(request),
                "--output",
                str(output),
                "--receipt",
                str(receipt),
                "--json",
            ]
        )

    body = json.loads(capsys.readouterr().out)
    assert stopped.value.code == 2 and body["data"]["error"]["code"] == "invalid_output_path"
    assert not output.exists() and not receipt.exists()


@pytest.mark.parametrize("unsafe", ["existing", "symlink", "same"])
def test_prepare_rejects_unsafe_targets_before_render(tmp_path, monkeypatch, capsys, unsafe):
    content = png()
    calls = []
    install_fake_prepare(monkeypatch, content, calls)
    original = tmp_path / "reviewed.png"
    original.write_bytes(content)
    request = tmp_path / "prepare.json"
    write_prepare_input(request)
    output, receipt = tmp_path / "SAFE.png", tmp_path / "SAFE.json"
    if unsafe == "existing":
        output.write_bytes(b"existing")
    elif unsafe == "symlink":
        output.symlink_to(original)
    else:
        receipt = output

    with pytest.raises(SystemExit) as stopped:
        cli.main(
            [
                "screenshot",
                "prepare",
                "--file",
                str(original),
                "--input",
                str(request),
                "--output",
                str(output),
                "--receipt",
                str(receipt),
                "--json",
            ]
        )

    body = json.loads(capsys.readouterr().out)
    assert stopped.value.code == 2 and body["ok"] is False
    assert calls == []


class FakeState:
    def __init__(self, org_id: str):
        self.org_id = org_id

    def load(self):
        return {"session": "synthetic-session", "org_id": self.org_id}


class SignedClient:
    def __init__(self, link: dict, handler, org_id: str):
        self.link = link
        self.handler = handler
        self.state = FakeState(org_id)
        self.requests = []

    async def request(self, method, path, **kwargs):
        self.requests.append((method, path, kwargs))
        return self.link

    @asynccontextmanager
    async def transport(self):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(self.handler), base_url="http://local"
        ) as client:
            yield client


def archive(source_id: UUID, org_id: UUID, content: bytes) -> dict:
    descriptor = validate_png(content)
    return {
        "id": str(source_id),
        "org_id": str(org_id),
        "task_id": IDENTIFIER_2,
        "task_certificate_id": IDENTIFIER_2,
        "certificate_id": IDENTIFIER_2,
        "certificate_revision_id": IDENTIFIER_2,
        "certificate_file_id": IDENTIFIER_2,
        "source_kind": "user_supplied_certificate_pdf",
        "original": {
            "name": "synthetic.pdf",
            "sha256": "b" * 64,
            "size_bytes": 100,
            "media_type": "application/pdf",
            "page_count": 1,
        },
        "page": 1,
        "render_profile": "pdf-page-preview-v1",
        "dpi": 150,
        "preview": {"name": "page-1.png", **descriptor},
        "rendered_at": "2026-10-02T00:00:00+00:00",
        "created_by": IDENTIFIER_3,
        "active_selection": True,
        "status": "unconfirmed_source",
        "confirmed_by": None,
        "eligible_for_draft_export": False,
    }


async def test_certificate_source_reads_only_exact_signed_preview(monkeypatch):
    source_id, org_id = uuid4(), uuid4()
    content = png()
    path = f"/evidence-sources/{source_id}/preview/download"
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.path == path
        assert request.url.params.get("signature") == "fixed-signature"
        return httpx.Response(200, content=content, headers={"content-type": "image/png"})

    link = Result(
        ok=True,
        command="fixture",
        data={"url": path + "?signature=fixed-signature", "expires_in": 300},
        items=[archive(source_id, org_id, content)],
    ).model_dump(mode="json")
    client = SignedClient(link, handler, str(org_id))
    monkeypatch.setattr(cli, "runtime", client)

    assert await screenshot_cli._read_certificate_preview(source_id) == content
    assert len(seen) == 1


async def test_certificate_source_rejects_cross_origin_link_before_download(monkeypatch):
    source_id, org_id = uuid4(), uuid4()
    content = png()
    link = Result(
        ok=True,
        command="fixture",
        data={"url": "https://evil.example.test/private.png?signature=secret"},
        items=[archive(source_id, org_id, content)],
    ).model_dump(mode="json")

    def forbidden(_request):
        raise AssertionError("unsafe link must not be requested")

    monkeypatch.setattr(cli, "runtime", SignedClient(link, forbidden, str(org_id)))
    with pytest.raises(ServiceError) as stopped:
        await screenshot_cli._read_certificate_preview(source_id)
    assert stopped.value.code == "invalid_download_link"


async def test_rendition_preview_rejects_hash_mismatch_without_output(tmp_path, monkeypatch):
    rendition_id, org_id = uuid4(), uuid4()
    expected, changed = png(value=127), png(value=10)
    descriptor = validate_png(expected)
    path = f"/screenshot-renditions/{rendition_id}/content"

    def handler(request):
        assert request.url.path == path
        assert request.extensions.get("follow_redirects") is None
        return httpx.Response(200, content=changed, headers={"content-type": "image/png"})

    link = Result(
        ok=True,
        command="fixture",
        data={
            "url": path + "?signature=fixed-signature",
            "expires_in": 300,
            "rendition_id": str(rendition_id),
            "image": descriptor,
        },
    ).model_dump(mode="json")
    monkeypatch.setattr(cli, "runtime", SignedClient(link, handler, str(org_id)))
    output = tmp_path / "preview.png"

    with pytest.raises(ServiceError) as stopped:
        await screenshot_cli._download_rendition(rendition_id, output)

    assert stopped.value.code == "screenshot_integrity"
    assert not output.exists()


def screenshot_files(tmp_path: Path) -> dict[str, Path]:
    content = png()
    fixed = prepared(content)
    safe = tmp_path / "SAFE.png"
    safe.write_bytes(content)
    ingest = ScreenshotIngest(
        extraction_job_id=UUID(IDENTIFIER),
        prepared=fixed,
        reviewed_upload_sha256=fixed.image.sha256,
        idempotency_key=UUID(IDENTIFIER_2),
    )
    values = {
        "ingest": ingest.model_dump(mode="json"),
        "annotate": {
            "parent_rendition_id": IDENTIFIER,
            "expected_image_sha256": fixed.image.sha256,
            "plan": ImagePlan().model_dump(mode="json"),
        },
        "withdraw": {"reason": "Synthetic privacy withdrawal"},
        "analyze": {
            "extraction_job_id": IDENTIFIER,
            "requirement_ids": [IDENTIFIER],
            "images": [{"rendition_id": IDENTIFIER, "expected_image_sha256": fixed.image.sha256}],
            "purposes": ["match_requirements"],
            "dry_run": True,
        },
        "ui-mock": {
            "extraction_job_id": IDENTIFIER,
            "requirement_id": IDENTIFIER_2,
            "task_feature_id": IDENTIFIER_3,
        },
        "evidence-search": {
            "extraction_job_id": IDENTIFIER,
            "task_resource_id": IDENTIFIER_2,
        },
        "evidence-adopt": {"field": "whitepaper_url", "expected_product_revision": 1},
        "decision-preview": {
            "extraction_job_id": IDENTIFIER,
            "module_label": "Synthetic module",
            "task_feature_ids": [IDENTIFIER],
        },
        "decision-apply": {
            "extraction_job_id": IDENTIFIER,
            "module_label": "Synthetic module",
            "task_feature_ids": [IDENTIFIER],
            "expected_input_hash": fixed.image.sha256,
            "items": [
                {
                    "evidence_id": IDENTIFIER,
                    "card_revision_id": IDENTIFIER,
                    "rendition_id": IDENTIFIER,
                    "image_sha256": fixed.image.sha256,
                    "html_sha256": "b" * 64,
                    "task_feature_id": IDENTIFIER,
                    "expected_previous_decision_id": None,
                    "decision": "keep",
                    "keep_basis": "will_deliver",
                    "reason": None,
                }
            ],
            "idempotency_key": IDENTIFIER_2,
        },
    }
    paths = {"safe": safe}
    for name, value in values.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        paths[name] = path
    return paths


def test_screenshot_remote_commands_snapshot(tmp_path, monkeypatch, capsys):
    paths = screenshot_files(tmp_path)
    install_fake_prepare(monkeypatch, paths["safe"].read_bytes())
    prepare_input = tmp_path / "prepare.json"
    write_prepare_input(prepare_input)
    calls = []

    def fake_call(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path.endswith(
            (
                "/renditions",
                "/screenshot-analyses",
                "/prototype-generations",
                "/screenshot-searches",
            )
        ):
            data = {"dry_run": True, "input_hash": SHA}
        else:
            data = {"path": path, "method": method}
        return Result(ok=True, command="fixture", data=data).model_dump(mode="json")

    async def fake_preview(rendition_id, output):
        return Result(
            ok=True,
            command="fixture",
            data={"rendition_id": str(rendition_id), "output_path": str(output)},
        ).model_dump(mode="json")

    monkeypatch.setattr(cli, "call", fake_call)
    monkeypatch.setattr(screenshot_cli, "_download_rendition", fake_preview)
    commands = {
        "screenshot prepare": [
            "screenshot",
            "prepare",
            "--file",
            str(paths["safe"]),
            "--input",
            str(prepare_input),
            "--output",
            str(tmp_path / "prepared.png"),
            "--receipt",
            str(tmp_path / "prepared.json"),
        ],
        "screenshot add": [
            "screenshot",
            "add",
            "--task",
            IDENTIFIER,
            "--file",
            str(paths["safe"]),
            "--input",
            str(paths["ingest"]),
        ],
        "screenshot list": [
            "screenshot",
            "list",
            "--task",
            IDENTIFIER,
            "--job",
            IDENTIFIER_2,
            "--history",
            "--cursor",
            IDENTIFIER_3,
        ],
        "screenshot show": ["screenshot", "show", "--id", IDENTIFIER],
        "screenshot annotate": [
            "screenshot",
            "annotate",
            "--id",
            IDENTIFIER,
            "--input",
            str(paths["annotate"]),
            "--dry-run",
        ],
        "screenshot preview": [
            "screenshot",
            "preview",
            "--id",
            IDENTIFIER,
            "--output",
            str(tmp_path / "preview.png"),
        ],
        "screenshot withdraw": [
            "screenshot",
            "withdraw",
            "--id",
            IDENTIFIER,
            "--input",
            str(paths["withdraw"]),
        ],
        "screenshot analyze": [
            "screenshot",
            "analyze",
            "--task",
            IDENTIFIER,
            "--input",
            str(paths["analyze"]),
        ],
        "screenshot suggestions": [
            "screenshot",
            "suggestions",
            "--analysis",
            IDENTIFIER,
            "--cursor",
            IDENTIFIER_2,
        ],
        "ui mock": [
            "ui",
            "mock",
            "--task",
            IDENTIFIER,
            "--input",
            str(paths["ui-mock"]),
            "--dry-run",
        ],
        "evidence search": [
            "evidence",
            "search",
            "--task",
            IDENTIFIER,
            "--input",
            str(paths["evidence-search"]),
            "--dry-run",
        ],
        "evidence candidates": ["evidence", "candidates", "--search", IDENTIFIER_3],
        "evidence adopt": [
            "evidence",
            "adopt",
            "--candidate",
            IDENTIFIER_3,
            "--input",
            str(paths["evidence-adopt"]),
        ],
        "screenshot prototype-decisions preview": [
            "screenshot",
            "prototype-decisions",
            "preview",
            "--task",
            IDENTIFIER,
            "--input",
            str(paths["decision-preview"]),
        ],
        "screenshot prototype-decisions apply": [
            "screenshot",
            "prototype-decisions",
            "apply",
            "--task",
            IDENTIFIER,
            "--input",
            str(paths["decision-apply"]),
        ],
        "screenshot prototype-decisions list": [
            "screenshot",
            "prototype-decisions",
            "list",
            "--task",
            IDENTIFIER,
            "--job",
            IDENTIFIER_2,
        ],
    }
    actual = {}
    for name, arguments in commands.items():
        body = invoke(arguments, capsys)
        body["duration_ms"] = 0
        if "output_path" in body["data"]:
            body["data"]["output_path"] = "<screenshot-output>"
        if "receipt_path" in body["data"]:
            body["data"]["receipt_path"] = "<screenshot-receipt>"
        actual[name] = body

    snapshot = Path(__file__).with_name("snapshots") / "screenshot-cli-v2.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.parent.mkdir(exist_ok=True)
        snapshot.write_text(json.dumps(actual, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    assert actual == json.loads(snapshot.read_text())
    by_path = {(method, path): kwargs for method, path, kwargs in calls}
    assert by_path[("GET", f"/tasks/{IDENTIFIER}/screenshots")]["params"] == {
        "job": IDENTIFIER_2,
        "history": "true",
        "cursor": IDENTIFIER_3,
    }
    assert by_path[("POST", f"/tasks/{IDENTIFIER}/screenshots")]["files"]["file"][2] == "image/png"
    assert by_path[("POST", f"/tasks/{IDENTIFIER}/screenshot-analyses")]["json"]["dry_run"] is True
    assert by_path[("POST", f"/tasks/{IDENTIFIER}/screenshot-searches")]["json"] == {
        "extraction_job_id": IDENTIFIER,
        "task_resource_id": IDENTIFIER_2,
        "expected_input_hash": None,
        "dry_run": True,
        "retry": False,
    }
    assert by_path[("POST", f"/screenshot-search-candidates/{IDENTIFIER_3}/adopt")]["json"] == {
        "field": "whitepaper_url",
        "expected_product_revision": 1,
    }
    assert ("GET", f"/screenshot-searches/{IDENTIFIER_3}") in by_path
    assert by_path[("POST", f"/tasks/{IDENTIFIER}/prototype-generations")]["json"] == {
        "extraction_job_id": IDENTIFIER,
        "requirement_id": IDENTIFIER_2,
        "task_feature_id": IDENTIFIER_3,
        "expected_input_hash": None,
        "reasoning": None,
        "dry_run": True,
        "retry": False,
    }


def test_add_mismatch_and_annotate_dry_retry_do_not_dispatch(tmp_path, monkeypatch, capsys):
    paths = screenshot_files(tmp_path)
    paths["safe"].write_bytes(png(value=10))

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid local input must not dispatch")

    monkeypatch.setattr(cli, "call", forbidden)
    with pytest.raises(SystemExit) as add_stopped:
        cli.main(
            [
                "screenshot",
                "add",
                "--task",
                IDENTIFIER,
                "--file",
                str(paths["safe"]),
                "--input",
                str(paths["ingest"]),
                "--json",
            ]
        )
    assert add_stopped.value.code == 2
    capsys.readouterr()

    with pytest.raises(SystemExit) as annotate_stopped:
        cli.main(
            [
                "screenshot",
                "annotate",
                "--id",
                IDENTIFIER,
                "--input",
                str(paths["annotate"]),
                "--dry-run",
                "--retry",
                "--json",
            ]
        )
    body = json.loads(capsys.readouterr().out)
    assert annotate_stopped.value.code == 2
    assert body["data"]["error"]["code"] == "invalid_input"


def test_analyze_wait_merges_terminal_result(tmp_path, monkeypatch, capsys):
    paths = screenshot_files(tmp_path)
    analyze = json.loads(paths["analyze"].read_text())
    analyze.update(dry_run=False, expected_input_hash=SHA)
    paths["analyze"].write_text(json.dumps(analyze))

    def fake_call(method, path, **kwargs):
        return Result(
            ok=True, command="fixture", data={"job_id": IDENTIFIER_2, "status": "queued"}
        ).model_dump(mode="json")

    async def fake_wait(job_id, limit_seconds):
        assert str(job_id) == IDENTIFIER_2 and limit_seconds == 2
        return {
            "data": {
                "status": "succeeded",
                "result": {
                    "analysis_id": IDENTIFIER_3,
                    "completion": "complete",
                    "warnings": ["synthetic-warning"],
                    "cost": {"llm_tokens": 12, "ocr_pages": 0, "usd": 0.01},
                },
            }
        }

    monkeypatch.setattr(cli, "call", fake_call)
    monkeypatch.setattr(cli, "wait_for_job", fake_wait)
    body = invoke(
        [
            "screenshot",
            "analyze",
            "--task",
            IDENTIFIER,
            "--input",
            str(paths["analyze"]),
            "--wait",
            "--timeout",
            "2",
        ],
        capsys,
    )
    assert body["data"]["analysis_id"] == IDENTIFIER_3
    assert body["warnings"] == ["synthetic-warning"]
    assert body["cost"]["llm_tokens"] == 12


async def test_rendition_preview_saves_verified_new_file(tmp_path, monkeypatch):
    rendition_id, org_id = uuid4(), uuid4()
    content = png()
    descriptor = validate_png(content)
    path = f"/screenshot-renditions/{rendition_id}/content"
    link = Result(
        ok=True,
        command="fixture",
        data={
            "url": path + "?signature=fixed-signature",
            "expires_in": 300,
            "rendition_id": str(rendition_id),
            "image": descriptor,
        },
    ).model_dump(mode="json")

    def handler(request):
        assert request.url.path == path
        return httpx.Response(200, content=content, headers={"content-type": "image/png"})

    monkeypatch.setattr(cli, "runtime", SignedClient(link, handler, str(org_id)))
    target = tmp_path / "verified.png"
    result = await screenshot_cli._download_rendition(rendition_id, target)
    assert await asyncio.to_thread(target.read_bytes) == content
    assert result["data"]["image"]["sha256"] == descriptor["sha256"]
    assert "url" not in result["data"]
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_prepare_rejects_parent_replacement_without_writing_replacement(
    tmp_path, monkeypatch, capsys
):
    content = png()
    install_fake_prepare(monkeypatch, content)
    original = tmp_path / "source.png"
    original.write_bytes(content)
    request = tmp_path / "prepare.json"
    write_prepare_input(request)
    parent = tmp_path / "output"
    parent.mkdir()
    moved = tmp_path / "moved-output"
    actual_link = screenshot_cli.os.link
    replaced = False

    def change_parent(source, target, **kwargs):
        nonlocal replaced
        if not replaced:
            parent.rename(moved)
            parent.mkdir()
            replaced = True
        return actual_link(source, target, **kwargs)

    monkeypatch.setattr(screenshot_cli.os, "link", change_parent)
    with pytest.raises(SystemExit) as stopped:
        cli.main(
            [
                "screenshot",
                "prepare",
                "--file",
                str(original),
                "--input",
                str(request),
                "--output",
                str(parent / "SAFE.png"),
                "--receipt",
                str(parent / "SAFE.json"),
                "--json",
            ]
        )
    result = json.loads(capsys.readouterr().out)
    assert stopped.value.code == 2 and result["data"]["error"]["code"] == "invalid_output_path"
    assert list(parent.iterdir()) == [] and list(moved.iterdir()) == []
    assert original.read_bytes() == content
