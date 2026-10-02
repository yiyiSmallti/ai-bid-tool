import hashlib
import json
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
import pytest
from app.core.errors import ServiceError
from app.schemas.contracts import Result
from app.schemas.sandbox_contracts import (
    PrototypeSpec,
    SandboxArtifactView,
    SandboxMetrics,
    SandboxSubmit,
    VendorSpec,
    Viewport,
)
from bid_cli.client import Client, State
from bid_cli.main import main
from bid_cli.sandbox import download_artifact
from pydantic import ValidationError

IDENTIFIER = "00000000-0000-0000-0000-000000000001"
RUN_ID = "00000000-0000-0000-0000-000000000002"
JOB_ID = "00000000-0000-0000-0000-000000000003"
ATTEMPT_ID = "00000000-0000-0000-0000-000000000004"
SHA256 = "a" * 64


class SyntheticState(State):
    def load(self):
        return {"session": "synthetic-only-session", "org_id": IDENTIFIER}


def prototype_spec(content: bytes = b"<html>synthetic</html>") -> dict:
    return {
        "purpose": "prototype_offline",
        "extraction_job_id": IDENTIFIER,
        "task_feature_id": IDENTIFIER,
        "expected_feature_revision_id": IDENTIFIER,
        "html_sha256": hashlib.sha256(content).hexdigest(),
        "html_size_bytes": len(content),
    }


def vendor_spec(**overrides) -> dict:
    value = {
        "purpose": "vendor_capture",
        "extraction_job_id": IDENTIFIER,
        "task_resource_id": IDENTIFIER,
        "expected_product_revision_id": IDENTIFIER,
        "source_field": "whitepaper_url",
        "expected_source_url_sha256": SHA256,
        "format": "pdf",
        "pdf_pages": [1, 3],
        "capture_key": IDENTIFIER,
    }
    value.update(overrides)
    return value


def artifact(content: bytes = b"synthetic-artifact") -> dict:
    return {
        "id": IDENTIFIER,
        "run_id": RUN_ID,
        "attempt_id": ATTEMPT_ID,
        "kind": "rendered_html",
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
        "media_type": "text/html",
        "width": None,
        "height": None,
        "page": None,
        "parent_artifact_id": None,
        "provenance_manifest_hash": SHA256,
    }


def run_view() -> dict:
    return {
        "id": RUN_ID,
        "org_id": IDENTIFIER,
        "task_id": IDENTIFIER,
        "job_id": JOB_ID,
        "purpose": "prototype_offline",
        "request_hash": SHA256,
        "profile": "prototype-v1",
        "policy_revision": "policy-v1",
        "selection_active": True,
        "state": "queued",
        "attempt_id": None,
        "cleanup_state": "not_started",
        "artifacts": [],
        "metrics": None,
        "issues": [],
        "usage_ids": [],
        "charge": None,
        "charge_currency": None,
    }


def preview() -> dict:
    return {
        "dry_run": True,
        "request_hash": SHA256,
        "purpose": "prototype_offline",
        "profile": "prototype-v1",
        "policy_revision": "policy-v1",
        "ready": True,
        "issues": [],
        "estimated_cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0.0},
        "estimate_basis": "no_vendor_call",
        "reserved_charge": None,
        "charge_currency": None,
        "estimated_duration_ms": None,
    }


@pytest.mark.parametrize(
    ("model", "field", "value"),
    [
        (Viewport, "width", True),
        (Viewport, "height", False),
        (PrototypeSpec, "html_size_bytes", True),
        (SandboxMetrics, "wall_ms", True),
        (SandboxMetrics, "request_count", False),
    ],
)
def test_sandbox_integer_fields_reject_bool(model, field, value):
    if model is Viewport:
        payload = {field: value}
    elif model is PrototypeSpec:
        payload = prototype_spec()
        payload[field] = value
    else:
        payload = {
            "wall_ms": 0,
            "cpu_ms": 0,
            "peak_memory_bytes": 0,
            "input_bytes": 0,
            "network_bytes": 0,
            "output_bytes": 0,
            "request_count": 0,
        }
        payload[field] = value
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(
    "pages",
    [[], [0], [True], [2, 1], [1, 1], list(range(1, 12))],
)
def test_pdf_capture_requires_bounded_ordered_unique_positive_pages(pages):
    with pytest.raises(ValidationError):
        VendorSpec.model_validate(vendor_spec(pdf_pages=pages))


def test_web_capture_rejects_pdf_pages_and_valid_defaults_are_bounded():
    with pytest.raises(ValidationError):
        VendorSpec.model_validate(vendor_spec(format="web", pdf_pages=[1]))
    spec = VendorSpec.model_validate(
        vendor_spec(format="web", source_field="official_url", pdf_pages=[])
    )
    assert spec.viewport == Viewport(width=1440, height=900, device_scale_factor=1)


def test_submit_cross_field_guards():
    spec = VendorSpec.model_validate(vendor_spec())
    with pytest.raises(ValidationError):
        SandboxSubmit(spec=spec, dry_run=True, retry=True)
    with pytest.raises(ValidationError):
        SandboxSubmit(spec=spec, dry_run=False)
    assert SandboxSubmit(spec=spec, dry_run=True).expected_request_hash is None
    assert SandboxSubmit(spec=spec, expected_request_hash=SHA256).dry_run is False


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "prototype_png", "width": None, "height": None},
        {"kind": "capture_png", "width": 8193, "height": 1},
        {"kind": "capture_png", "width": 5000, "height": 5000},
        {"kind": "pdf_page_png", "width": 100, "height": 100, "page": None},
        {"kind": "rendered_html", "width": 100, "height": 100},
        {"kind": "rendered_html", "page": 1},
    ],
)
def test_artifact_dimensions_and_pdf_page_are_kind_bound(changes):
    payload = artifact()
    payload.update(changes)
    with pytest.raises(ValidationError):
        SandboxArtifactView.model_validate(payload)


def test_render_cli_preflights_submits_same_hash_waits_and_refreshes(monkeypatch, tmp_path, capsys):
    html = b"<html>synthetic</html>"
    html_path = tmp_path / "prototype.html"
    html_path.write_bytes(html)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(prototype_spec(html)))
    calls = []

    async def fake_request(self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == f"/tasks/{IDENTIFIER}/sandbox-runs":
            if len(calls) == 1:
                submit = json.loads(kwargs["data"]["submit"])
                assert submit == {
                    "spec": {
                        **prototype_spec(html),
                        "viewport": {
                            "width": 1440,
                            "height": 900,
                            "device_scale_factor": 1,
                        },
                    },
                    "expected_request_hash": None,
                    "dry_run": True,
                    "retry": False,
                }
                assert kwargs["files"]["html"][1] == html
                data = preview()
            else:
                submit = json.loads(kwargs["data"]["submit"])
                assert submit["expected_request_hash"] == SHA256
                assert submit["dry_run"] is False and submit["retry"] is True
                data = run_view()
        elif path == f"/jobs/{JOB_ID}":
            data = {"id": JOB_ID, "status": "succeeded", "result": {}}
        else:
            assert path == f"/sandbox-runs/{RUN_ID}"
            png = {
                **artifact(b"synthetic-png"),
                "kind": "prototype_png",
                "media_type": "image/png",
                "width": 1,
                "height": 1,
            }
            provenance = {
                **artifact(b"synthetic-provenance"),
                "kind": "provenance_manifest",
                "media_type": "application/json",
            }
            data = {
                **run_view(),
                "state": "succeeded",
                "attempt_id": ATTEMPT_ID,
                "cleanup_state": "complete",
                "artifacts": [png, artifact(), provenance],
                "metrics": {
                    "wall_ms": 1,
                    "cpu_ms": 1,
                    "peak_memory_bytes": 1,
                    "input_bytes": 1,
                    "network_bytes": 0,
                    "output_bytes": 1,
                    "request_count": 0,
                },
            }
        return Result(ok=True, command="fixture", data=data).model_dump(mode="json")

    monkeypatch.setattr(Client, "request", fake_request)
    main(
        [
            "sandbox",
            "render",
            "--task",
            IDENTIFIER,
            "--html",
            str(html_path),
            "--input",
            str(plan),
            "--retry",
            "--wait",
            "--json",
        ]
    )
    body = json.loads(capsys.readouterr().out)
    assert set(body) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
    assert body["data"]["state"] == "succeeded" and body["command"] == "sandbox render"
    assert [call[1] for call in calls] == [
        f"/tasks/{IDENTIFIER}/sandbox-runs",
        f"/tasks/{IDENTIFIER}/sandbox-runs",
        f"/jobs/{JOB_ID}",
        f"/sandbox-runs/{RUN_ID}",
    ]


@pytest.mark.parametrize(
    "failure",
    ["oversized_json", "oversized_html", "invalid_utf8", "hash", "wrong_purpose"],
)
def test_render_cli_rejects_bad_local_input_before_network(failure, monkeypatch, tmp_path, capsys):
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("invalid local input reached network")

    monkeypatch.setattr(Client, "request", forbidden)
    html = tmp_path / "prototype.html"
    html.write_bytes(b"<html>synthetic</html>")
    plan = tmp_path / "plan.json"
    payload = prototype_spec(html.read_bytes())
    if failure == "oversized_json":
        plan.write_text(" " * (64 * 1024 + 1))
    elif failure == "oversized_html":
        html.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
        plan.write_text(json.dumps(payload))
    elif failure == "invalid_utf8":
        html.write_bytes(b"\xff")
        payload = prototype_spec(html.read_bytes())
        plan.write_text(json.dumps(payload))
    elif failure == "hash":
        payload["html_sha256"] = SHA256
        plan.write_text(json.dumps(payload))
    else:
        plan.write_text(json.dumps(vendor_spec()))
    with pytest.raises(SystemExit) as error:
        main(
            [
                "sandbox",
                "render",
                "--task",
                IDENTIFIER,
                "--html",
                str(html),
                "--input",
                str(plan),
                "--json",
            ]
        )
    body = json.loads(capsys.readouterr().out)
    assert error.value.code == 2 and body["command"] == "sandbox render" and not calls


def sandbox_download_client(tmp_path, content, *, link=None, served=None):
    client = Client(
        "remote", "https://synthetic.example.test", SyntheticState(tmp_path / "unused.enc")
    )
    path = f"/sandbox-artifacts/{IDENTIFIER}/download"
    calls = []

    def handler(request):
        calls.append(request.url)
        assert request.headers["Authorization"] == "Bearer synthetic-only-session"
        assert request.headers["X-Org-Id"] == IDENTIFIER
        if request.url.path == path + "-link":
            return httpx.Response(
                200,
                json=Result(
                    ok=True,
                    command="fixture",
                    data={
                        "artifact": artifact(content),
                        "url": link or path + "?token=synthetic-only",
                        "expires_in": 300,
                    },
                ).model_dump(mode="json"),
            )
        assert request.url.path == path and request.url.params["token"] == "synthetic-only"
        return httpx.Response(200, content=content if served is None else served)

    @asynccontextmanager
    async def transport():
        async with httpx.AsyncClient(
            base_url="https://synthetic.example.test", transport=httpx.MockTransport(handler)
        ) as http:
            yield http

    client.transport = transport
    return client, calls


async def test_download_is_verified_atomic_private_and_does_not_decode_on_host(tmp_path):
    content = b"not-decoded-on-host"
    output = tmp_path.resolve() / "artifact.bin"
    client, calls = sandbox_download_client(tmp_path, content)
    result = await download_artifact(client, UUID(IDENTIFIER), output)
    assert result["data"]["artifact"]["sha256"] == hashlib.sha256(content).hexdigest()
    assert result["data"]["output_path"] == str(output)
    assert output.read_bytes() == content and output.stat().st_mode & 0o777 == 0o600
    assert len(calls) == 2 and not list(tmp_path.glob(".bid-download-*"))
    with pytest.raises(ServiceError) as error:
        await download_artifact(client, UUID(IDENTIFIER), output)
    assert error.value.exit_code == 2 and len(calls) == 2


@pytest.mark.parametrize(
    ("link", "served"),
    [
        ("https://foreign.example.test/secret", None),
        (f"/sandbox-artifacts/{IDENTIFIER}/download?token=one&token=two", None),
        (f"/sandbox-artifacts/{IDENTIFIER}/download?token=synthetic-only&extra=true", None),
        (None, b"short"),
        (None, b"x" * 1024),
    ],
)
async def test_download_rejects_unsafe_link_or_bad_bytes(tmp_path, link, served):
    content = b"synthetic-artifact"
    output = tmp_path.resolve() / "artifact.bin"
    client, _ = sandbox_download_client(tmp_path, content, link=link, served=served)
    with pytest.raises(ServiceError) as error:
        await download_artifact(client, UUID(IDENTIFIER), output)
    assert error.value.exit_code == 4 and not output.exists()


def test_capture_dry_run_retry_fails_before_network(monkeypatch, tmp_path, capsys):
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(True)

    monkeypatch.setattr(Client, "request", forbidden)
    plan = tmp_path / "capture.json"
    plan.write_text(json.dumps(vendor_spec()))
    with pytest.raises(SystemExit) as error:
        main(
            [
                "sandbox",
                "capture",
                "--task",
                IDENTIFIER,
                "--input",
                str(plan),
                "--dry-run",
                "--retry",
                "--json",
            ]
        )
    assert error.value.code == 2 and not calls
    assert json.loads(capsys.readouterr().out)["command"] == "sandbox capture"


def test_sandbox_list_and_show_validate_and_preserve_result_shape(monkeypatch, capsys):
    async def fake_request(self, method, path, **kwargs):
        if path.startswith("/tasks/"):
            return Result(
                ok=True,
                command="fixture",
                data={"offset": 0, "limit": 20},
                items=[run_view()],
            ).model_dump(mode="json")
        return Result(ok=True, command="fixture", data=run_view()).model_dump(mode="json")

    monkeypatch.setattr(Client, "request", fake_request)
    for arguments in (
        ["sandbox", "list", "--task", IDENTIFIER],
        ["sandbox", "show", "--id", RUN_ID],
    ):
        main([*arguments, "--json"])
        body = json.loads(capsys.readouterr().out)
        assert set(body) == {
            "ok",
            "command",
            "data",
            "items",
            "warnings",
            "cost",
            "duration_ms",
        }
