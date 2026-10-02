"""Failure modes (written before implementation):

- Disabled or unapproved runtime starts a host browser or ordinary container.
- Caller substitutes image, command, mounts, dimensions, input hash or PDF pages.
- Renderer forges headers, output kinds, lengths, duplicate ordinals or dimensions.
- Input/output/diagnostic streams exceed their bound before the receiver stops.
- Cancellation, caller death or supervisor restart leaves containers running.
- Validation reuses the renderer or publishes before cleanup confirmation.
- Duplicate attempts execute again, or a cleanup failure admits another tenant.
- Production PNG/PDF decode occurs on the trusted business host.

The opt-in test traverses the actual client/supervisor/container/validator path.
It writes a repeatable artifact; a skipped runtime test is not isolation acceptance.
"""

import asyncio
import hashlib
import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from app.providers.browser import SocketBrowserProvider
from app.providers.sandbox_runtime import RunDescriptor, RuntimeConfig, SandboxFailure


def descriptor(html: bytes) -> RunDescriptor:
    return RunDescriptor(
        uuid4(), uuid4(), uuid4(), hashlib.sha256(html).hexdigest(), "prototype_offline"
    )


@pytest.mark.asyncio
async def test_disabled_provider_refuses_before_connection(tmp_path):
    provider = SocketBrowserProvider(RuntimeConfig(socket_path=tmp_path / "absent.sock"))
    with pytest.raises(SandboxFailure, match="sandbox_disabled"):
        await provider.render_prototype(descriptor(b"<h1>x</h1>"), b"<h1>x</h1>")


@pytest.mark.asyncio
async def test_bad_hash_refuses_before_connection(tmp_path):
    provider = SocketBrowserProvider(
        RuntimeConfig(enabled=True, socket_path=tmp_path / "absent.sock")
    )
    with pytest.raises(SandboxFailure, match="input_hash_mismatch"):
        await provider.render_prototype(descriptor(b"original"), b"changed")


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("BID_SANDBOX_RUNTIME_TEST") != "1",
    reason="requires independently provisioned accepted runsc supervisor",
)
async def test_real_prototype_pipeline():
    config = RuntimeConfig.from_env()
    provider = SocketBrowserProvider(config)
    html = b"<!doctype html><html><body><h1>Sandbox synthetic acceptance</h1></body></html>"
    run = replace(descriptor(html), synthetic_input=config.synthetic_only)
    result = await provider.render_prototype(run, html)
    assert result.cleanup_state == "complete"
    assert {x.kind for x in result.artifacts} == {"prototype_png", "rendered_html"}
    target = Path(
        os.environ.get("BID_SANDBOX_ARTIFACT_DIR", "data/work/sandbox-verification/runtime")
    )
    await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)
    for artifact in result.artifacts:
        (target / artifact.kind).write_bytes(artifact.data)
    assert result.metrics["output_bytes"] > 0
    assert result.runtime_versions["runtime"] == config.runtime
