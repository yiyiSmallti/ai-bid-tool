"""Synthetic-only ContainerBackend utility; never imported by production providers.

The fake executes the real supervisor framing/lifecycle path with controlled bytes.
It does not decode media, launch a browser, or provide an isolation guarantee.
"""

import asyncio
import json
import struct
import sys
import zlib
from pathlib import Path

from app.providers.sandbox_runtime import MIB, RunDescriptor, RuntimeConfig, SandboxFailure


def synthetic_png(width: int, height: int) -> bytes:
    """Deterministic synthetic stripes, never used as business evidence."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload))
        )

    rows = b"".join(b"\x00" + bytes((40 + row % 2, 80, 140)) * width for row in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


class FakeContainerBackend:
    def __init__(
        self,
        *,
        failure_stage: str | None = None,
        fail_cleanup: bool = False,
        fail_metrics: bool = False,
    ):
        self.runner_path = str(Path(__file__).resolve())
        self.failure_stage = failure_stage
        self.fail_cleanup = fail_cleanup
        self.fail_metrics = fail_metrics
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.events: list[str] = []

    async def preflight(self) -> None:
        self.events.append("preflight")

    async def inventory(self) -> list[str]:
        return list(self.processes)

    async def start(
        self, name: str, descriptor: RunDescriptor, stage: str, deadline: float
    ) -> asyncio.subprocess.Process:
        if not descriptor.synthetic_input:
            raise SandboxFailure("sandbox_synthetic_only")
        if name in self.processes:
            raise SandboxFailure("sandbox_attempt_exists")
        self.events.append(f"start:{stage}")
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            self.runner_path,
            "--fake-runner",
            stage,
            "fail" if self.failure_stage == stage else "success",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.processes[name] = process
        return process

    async def stats(self, name: str) -> tuple[int, int]:
        if self.fail_metrics or name not in self.processes:
            raise SandboxFailure("sandbox_metrics_unavailable")
        self.events.append("stats")
        return 10, MIB

    async def remove(self, name: str) -> None:
        self.events.append("remove")
        if self.fail_cleanup:
            raise SandboxFailure("cleanup_pending")
        process = self.processes.pop(name, None)
        if process is not None:
            if process.returncode is None:
                process.kill()
            await process.wait()


def synthetic_runtime_config(state_path: Path) -> RuntimeConfig:
    return RuntimeConfig(
        enabled=True,
        accepted=True,
        synthetic_only=True,
        runtime="runc",
        image="synthetic-only@sha256:" + "0" * 64,
        seccomp_sha256="0" * 64,
        state_path=state_path,
    )


def _runner() -> None:
    # Pure fixed synthetic protocol peer, scoped to this test utility executable.
    def read() -> tuple[dict, bytes]:
        header = json.loads(sys.stdin.buffer.readline(65537))
        length = header["length"]
        if type(length) is not int or not 0 <= length <= 40 * MIB:
            raise SystemExit(2)
        return header, sys.stdin.buffer.read(length)

    def write(header: dict, payload: bytes = b"") -> None:
        sys.stdout.buffer.write(json.dumps({**header, "length": len(payload)}).encode() + b"\n")
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()

    initial, _ = read()
    if sys.argv[-1] == "fail":
        write({"type": "error", "code": "synthetic_canary_must_not_escape"})
        return
    if sys.argv[-2] == "render":
        width = initial["descriptor"]["viewport_width"]
        height = initial["descriptor"]["viewport_height"]
        png = synthetic_png(width, height)
        write(
            {
                "type": "artifact",
                "ordinal": 0,
                "kind": "prototype_png",
                "width": width,
                "height": height,
                "page": None,
            },
            png,
        )
        write(
            {
                "type": "artifact",
                "ordinal": 1,
                "kind": "rendered_html",
                "width": None,
                "height": None,
                "page": None,
            },
            b"<html>synthetic</html>",
        )
    else:
        while True:
            header, payload = read()
            if header["type"] == "end":
                break
            write({key: value for key, value in header.items() if key != "length"}, payload)
    write({"type": "done", "versions": {}, "issues": []})
    header, _ = read()
    if header["type"] != "finish":
        raise SystemExit(2)


if __name__ == "__main__" and sys.argv[1:2] == ["--fake-runner"]:
    _runner()
