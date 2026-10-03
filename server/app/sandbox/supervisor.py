"""Execution-node process with durable instance inventory and independent deadlines."""

import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
import socket
import sqlite3
import struct
import time
from dataclasses import asdict
from typing import Any, Protocol

import httpx

from app.providers.sandbox_runtime import (
    CONTROL_LIMIT,
    MIB,
    PROTOCOL_VERSION,
    ArtifactPayload,
    RunDescriptor,
    RuntimeConfig,
    SandboxFailure,
    read_frame,
    send_frame,
    validate_artifact,
)
from app.sandbox.runner import artifact_header

RUNNER_ERROR_CODES = frozenset(
    {
        "invalid_frame",
        "stream_limit",
        "control_limit",
        "image_limit",
        "network_denied",
        "source_http_error",
        "invalid_pdf",
        "encrypted_pdf",
        "pdf_page_missing",
        "popup_denied",
        "download_denied",
        "source_login_or_challenge",
        "invalid_png",
        "artifact_sequence",
        "input_hash_mismatch",
        "invalid_stage",
        "output_limit",
        "artifact_limit",
        "sandbox_parser_failure",
        "invalid_artifact",
        "image_dimensions_mismatch",
        "artifact_hash_mismatch",
        "invalid_html",
        "blank_capture",
        "browser_launch_failed",
    }
)


def runner_error_code(value: object) -> str:
    return (
        value
        if isinstance(value, str) and value in RUNNER_ERROR_CODES
        else "sandbox_execution_failed"
    )


async def bounded_read(stream: asyncio.StreamReader, limit: int) -> bytes:
    result = bytearray()
    while True:
        chunk = await stream.read(min(8192, limit - len(result) + 1))
        if not chunk:
            return bytes(result)
        result.extend(chunk)
        if len(result) > limit:
            raise SandboxFailure("diagnostic_limit")


class ContainerBackend(Protocol):
    async def preflight(self) -> None: ...
    async def inventory(self) -> list[str]: ...
    async def start(
        self, name: str, descriptor: RunDescriptor, stage: str, deadline: float
    ) -> asyncio.subprocess.Process: ...
    async def remove(self, name: str) -> None: ...
    async def stats(self, name: str) -> tuple[int, int]: ...


class DockerBackend:
    def __init__(self, config: RuntimeConfig):
        self.config = config
        self.base = ["docker", "--host", f"unix://{config.docker_socket}"]
        self.env = {"PATH": "/usr/local/bin:/usr/bin:/bin"}

    async def command(self, *arguments: str, allow_failure: bool = False) -> bytes:
        process = await asyncio.create_subprocess_exec(
            *self.base,
            *arguments,
            env=self.env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout and process.stderr
        try:
            async with asyncio.timeout(8):
                stdout, _ = await asyncio.gather(
                    bounded_read(process.stdout, CONTROL_LIMIT),
                    bounded_read(process.stderr, CONTROL_LIMIT),
                )
                code = await process.wait()
            if code and not allow_failure:
                raise SandboxFailure("sandbox_runtime_failure")
            return stdout
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def inventory(self) -> list[str]:
        return (
            (
                await self.command(
                    "ps", "--all", "--format", "{{.Names}}", "--filter", "label=bid.sandbox=1"
                )
            )
            .decode()
            .splitlines()
        )

    async def preflight(self) -> None:
        self.config.validate()
        if self.config.transport == "unix":
            if self.config.client_uid < 0 or self.config.control_gid < 0:
                raise SandboxFailure("sandbox_peer_configuration_required")
            if self.config.client_uid == os.getuid() and not self.config.synthetic_only:
                raise SandboxFailure("sandbox_separate_service_identity_required")
        if (
            hashlib.sha256(self.config.seccomp_path.read_bytes()).hexdigest()
            != self.config.seccomp_sha256
        ):
            raise SandboxFailure("sandbox_seccomp_mismatch")
        info = json.loads(await self.command("info", "--format", "{{json .}}"))
        if not any("rootless" in str(option) for option in info.get("SecurityOptions", [])):
            raise SandboxFailure("sandbox_rootless_required")
        if info.get("CgroupVersion") != "2" or not all(
            info.get(field) for field in ("MemoryLimit", "SwapLimit", "CpuCfsQuota", "PidsLimit")
        ):
            raise SandboxFailure("sandbox_cgroup_limits_unavailable")
        if self.config.runtime not in info.get("Runtimes", {}):
            raise SandboxFailure("sandbox_runtime_unavailable")
        # Missing images fail; admission must never pull from an external registry.
        await self.command("image", "inspect", "--format", "{{.Id}}", self.config.image)

    def create_arguments(
        self, name: str, descriptor: RunDescriptor, stage: str, deadline: float
    ) -> list[str]:
        memory = "1g" if descriptor.purpose == "prototype_offline" else "2g"
        temporary = "128m" if descriptor.purpose == "prototype_offline" else "256m"
        return [
            "create",
            "--name",
            name,
            "--label",
            "bid.sandbox=1",
            "--label",
            f"bid.deadline={deadline}",
            "--runtime",
            self.config.runtime,
            "--network",
            "none",
            "--ipc",
            "private",
            "--user",
            "10001:10001",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--security-opt",
            f"seccomp={self.config.seccomp_path}",
            "--pids-limit",
            "128",
            "--memory",
            memory,
            "--memory-swap",
            memory,
            "--cpu-period",
            "10000",
            "--cpu-quota",
            "10000",
            "--ulimit",
            "core=0:0",
            "--ulimit",
            "nofile=1024:1024",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,nodev,size={temporary},mode=1777",
            "--shm-size",
            "128m",
            "--log-driver",
            "none",
            "--stop-timeout",
            "1",
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTHONUNBUFFERED=1",
            "--interactive",
            self.config.image,
            "python",
            "-m",
            "app.sandbox.runner",
            stage,
        ]

    async def start(
        self, name: str, descriptor: RunDescriptor, stage: str, deadline: float
    ) -> asyncio.subprocess.Process:
        await self.command(*self.create_arguments(name, descriptor, stage, deadline))
        return await asyncio.create_subprocess_exec(
            *self.base,
            "start",
            "--attach",
            "--interactive",
            name,
            env=self.env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def remove(self, name: str) -> None:
        await self.command("rm", "--force", "--volumes", name, allow_failure=True)
        remaining = await self.command("ps", "--all", "--quiet", "--filter", f"name=^/{name}$")
        if remaining.strip():
            raise SandboxFailure("cleanup_pending")

    async def stats(self, name: str) -> tuple[int, int]:
        transport = httpx.AsyncHTTPTransport(uds=str(self.config.docker_socket))
        async with httpx.AsyncClient(transport=transport, timeout=2) as client:
            async with client.stream(
                "GET", f"http://localhost/containers/{name}/stats?stream=false&one-shot=true"
            ) as response:
                if response.status_code != 200:
                    raise SandboxFailure("sandbox_metrics_unavailable")
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    body.extend(chunk)
                    if len(body) > CONTROL_LIMIT:
                        raise SandboxFailure("sandbox_metrics_unavailable")
        values = json.loads(body)
        cpu = int(values["cpu_stats"]["cpu_usage"]["total_usage"]) // 1_000_000
        memory = int(values["memory_stats"].get("max_usage", values["memory_stats"]["usage"]))
        return cpu, memory


class Supervisor:
    def __init__(self, config: RuntimeConfig, backend: ContainerBackend | None = None):
        config.validate()
        self.config = config
        self.backend: ContainerBackend = backend if backend is not None else DockerBackend(config)
        config.state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_file = config.state_path.with_suffix(".lock").open("a+b")
        os.chmod(config.state_path.with_suffix(".lock"), 0o600)
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.lock_file.close()
            raise SandboxFailure("sandbox_supervisor_already_running") from exc
        self.db = sqlite3.connect(config.state_path)
        os.chmod(config.state_path, 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS groups (
            group_id TEXT PRIMARY KEY, binding TEXT NOT NULL, deadline REAL NOT NULL,
            state TEXT NOT NULL, org_id TEXT NOT NULL, memory_bytes INTEGER NOT NULL,
            metrics TEXT NOT NULL DEFAULT '{}')""")
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(groups)")}
        if "metrics" not in columns:
            self.db.execute("ALTER TABLE groups ADD COLUMN metrics TEXT NOT NULL DEFAULT '{}'")
        self.db.commit()
        self.quarantined = False
        self.active: dict[str, asyncio.Task[Any]] = {}
        self.connections = 0

    async def cleanup(self, group: str) -> None:
        try:
            async with asyncio.timeout(10):
                for stage in ("render", "validate"):
                    await self.backend.remove(f"bid-sbx-{group}-{stage}")
        except (OSError, TimeoutError, SandboxFailure):
            self.quarantined = True
            self.db.execute("UPDATE groups SET state='cleanup_pending' WHERE group_id=?", (group,))
            self.db.commit()
            raise SandboxFailure("cleanup_pending") from None
        self.db.execute("UPDATE groups SET state='complete' WHERE group_id=?", (group,))
        self.db.commit()

    async def recover(self) -> None:
        # Called before opening the socket. Inventory includes containers created before a crash.
        self.quarantined = True
        names = await self.backend.inventory()
        for name in names:
            if not name.startswith("bid-sbx-"):
                raise SandboxFailure("sandbox_inventory_mismatch")
            await self.backend.remove(name)
        for (group,) in self.db.execute(
            "SELECT group_id FROM groups WHERE state != 'complete'"
        ).fetchall():
            await self.cleanup(group)
        self.quarantined = False

    def register(self, descriptor: RunDescriptor, deadline: float) -> str:
        if self.quarantined:
            raise SandboxFailure("sandbox_node_quarantined")
        group = hashlib.sha256(
            f"{descriptor.org_id}:{descriptor.job_id}:{descriptor.attempt_id}".encode()
        ).hexdigest()[:32]
        memory = (1024 if descriptor.purpose == "prototype_offline" else 2048) * MIB
        self.db.execute("BEGIN IMMEDIATE")
        try:
            previous = self.db.execute(
                "SELECT binding FROM groups WHERE group_id=?", (group,)
            ).fetchone()
            if previous:
                raise SandboxFailure("sandbox_attempt_exists")
            active = self.db.execute(
                "SELECT org_id,memory_bytes FROM groups WHERE state != 'complete'"
            ).fetchall()
            if (
                len(active) >= self.config.node_capacity
                or sum(row[1] for row in active) + memory > self.config.memory_capacity_bytes
            ):
                raise SandboxFailure("sandbox_node_capacity")
            if sum(row[0] == str(descriptor.org_id) for row in active) >= 2:
                raise SandboxFailure("sandbox_org_capacity")
            self.db.execute(
                "INSERT INTO groups (group_id,binding,deadline,state,org_id,memory_bytes) VALUES (?,?,?,?,?,?)",
                (group, descriptor.digest, deadline, "running", str(descriptor.org_id), memory),
            )
            self.db.commit()
        except (sqlite3.Error, SandboxFailure):
            self.db.rollback()
            raise
        return group

    async def reaper(self) -> None:
        last_inventory = time.monotonic()
        while True:
            await asyncio.sleep(1)
            expired = self.db.execute(
                "SELECT group_id FROM groups WHERE state='running' AND deadline <= ?",
                (time.time(),),
            ).fetchall()
            for (group,) in expired:
                task = self.active.get(group)
                if task is not None:
                    task.cancel()
                else:
                    await self.cleanup(group)
            if time.monotonic() - last_inventory >= 60:
                names = await self.backend.inventory()
                expected = {
                    f"bid-sbx-{group}-{stage}"
                    for group in self.active
                    for stage in ("render", "validate")
                }
                for name in names:
                    if name not in expected:
                        await self.backend.remove(name)
                last_inventory = time.monotonic()
            # An unconfirmed cleanup blocks all new admissions until restart/recovery.
            if self.quarantined:
                for (group,) in self.db.execute(
                    "SELECT group_id FROM groups WHERE state='cleanup_pending'"
                ).fetchall():
                    with contextlib.suppress(SandboxFailure):
                        await self.cleanup(group)

    async def stage(
        self,
        group: str,
        stage: str,
        descriptor: RunDescriptor,
        source: bytes,
        artifacts: list[ArtifactPayload],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        metrics: dict[str, int],
        deadline: float,
        issues: list[str] | None = None,
    ) -> list[ArtifactPayload]:
        name = f"bid-sbx-{group}-{stage}"
        process = await self.backend.start(name, descriptor, stage, deadline)
        assert process.stdin and process.stdout and process.stderr
        stdin, stdout, stderr = process.stdin, process.stdout, process.stderr
        prior_cpu = metrics["cpu_ms"]
        output: list[ArtifactPayload] = []
        terminal_sample = asyncio.Event()

        async def monitor() -> None:
            while process.returncode is None and not terminal_sample.is_set():
                if reader.at_eof():
                    raise SandboxFailure("sandbox_caller_disconnected")
                cpu, memory = await self.backend.stats(name)
                metrics["cpu_ms"] = prior_cpu + cpu
                metrics["peak_memory_bytes"] = max(metrics["peak_memory_bytes"], memory)
                metrics["wall_ms"] = max(
                    0, int((descriptor.wall_seconds - 1 - (deadline - time.time())) * 1000)
                )
                self.persist_metrics(group, metrics)
                if metrics["cpu_ms"] >= descriptor.wall_seconds * 1000:
                    raise SandboxFailure("cpu_limit")
                await asyncio.sleep(0.2)

        async def exchange() -> None:
            await send_frame(stdin, {"type": "input", "descriptor": asdict(descriptor)}, source)
            if stage == "validate":
                for ordinal, artifact in enumerate(artifacts):
                    await send_frame(stdin, artifact_header(artifact, ordinal), artifact.data)
                await send_frame(stdin, {"type": "end"})
            while True:
                header, data = await read_frame(
                    stdout, min(40 * MIB, descriptor.output_limit - metrics["output_bytes"])
                )
                message = header.get("type")
                if message == "fetch":
                    if stage != "render" or descriptor.purpose != "vendor_capture" or data:
                        raise SandboxFailure("unexpected_fetch")
                    if set(header) != {"type", "url", "method", "length"} or header[
                        "method"
                    ] not in ("GET", "HEAD"):
                        raise SandboxFailure("invalid_fetch")
                    if metrics["request_count"] >= 200:
                        raise SandboxFailure("request_limit")
                    metrics["request_count"] += 1
                    await send_frame(writer, header)
                    reply, body = await read_frame(
                        reader, min(32 * MIB, 64 * MIB - metrics["network_bytes"])
                    )
                    if reply.get("type") == "error":
                        raise SandboxFailure("network_denied")
                    if reply.get("type") != "fetch_result":
                        raise SandboxFailure("invalid_fetch")
                    metrics["network_bytes"] += len(body)
                    await send_frame(stdin, reply, body)
                elif message == "artifact":
                    if len(output) >= 32 or header.get("ordinal") != len(output):
                        raise SandboxFailure("artifact_sequence")
                    artifact = validate_artifact(header, data)
                    output.append(artifact)
                    metrics["output_bytes"] += len(data)
                elif message == "done":
                    if data or set(header) != {"type", "versions", "issues", "length"}:
                        raise SandboxFailure("invalid_frame")
                    reported_issues = header["issues"]
                    if reported_issues not in ([], ["offline_resources_blocked"]):
                        raise SandboxFailure("invalid_frame")
                    if reported_issues and (
                        stage != "render" or descriptor.purpose != "prototype_offline"
                    ):
                        raise SandboxFailure("invalid_frame")
                    if issues is not None:
                        issues.extend(reported_issues)
                    terminal_sample.set()
                    await monitor_task
                    cpu, memory = await self.backend.stats(name)
                    metrics["cpu_ms"] = max(metrics["cpu_ms"], prior_cpu + cpu)
                    metrics["peak_memory_bytes"] = max(metrics["peak_memory_bytes"], memory)
                    if metrics["cpu_ms"] >= descriptor.wall_seconds * 1000:
                        raise SandboxFailure("cpu_limit")
                    self.persist_metrics(group, metrics)
                    await send_frame(stdin, {"type": "finish"})
                    if await stdout.read(1):
                        raise SandboxFailure("unexpected_output")
                    if await process.wait() != 0:
                        raise SandboxFailure("sandbox_execution_failed")
                    return
                elif message == "error":
                    if data or set(header) != {"type", "code", "length"}:
                        raise SandboxFailure("invalid_frame")
                    raise SandboxFailure(runner_error_code(header.get("code")))
                else:
                    raise SandboxFailure("invalid_frame")

        io_task = asyncio.create_task(exchange())
        monitor_task = asyncio.create_task(monitor())
        diagnostic_task = asyncio.create_task(bounded_read(stderr, CONTROL_LIMIT))
        tasks = (io_task, monitor_task, diagnostic_task)
        try:
            while not io_task.done():
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    if task.exception() is not None:
                        raise task.exception()  # type: ignore[misc]
                if diagnostic_task in done and not io_task.done():
                    await io_task
                if monitor_task in done and not io_task.done():
                    await io_task
            await io_task
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            process.stdin.close()
            await self.backend.remove(name)
            if process.returncode is None:
                process.kill()
            await process.wait()
        return output

    @staticmethod
    def check_set(descriptor: RunDescriptor, artifacts: list[ArtifactPayload]) -> None:
        expected = (
            ["source_pdf"] + ["pdf_page_png"] * len(descriptor.pdf_pages)
            if descriptor.format == "pdf"
            else [
                "prototype_png" if descriptor.purpose == "prototype_offline" else "capture_png",
                "rendered_html",
            ]
        )
        if [artifact.kind for artifact in artifacts] != expected:
            raise SandboxFailure("artifact_set_mismatch")
        if descriptor.format == "web" and (artifacts[0].width, artifacts[0].height) != (
            descriptor.viewport_width,
            descriptor.viewport_height,
        ):
            raise SandboxFailure("image_dimensions_mismatch")
        if (
            descriptor.format == "pdf"
            and tuple(artifact.page for artifact in artifacts[1:]) != descriptor.pdf_pages
        ):
            raise SandboxFailure("artifact_pages_mismatch")

    def persist_metrics(self, group: str, metrics: dict[str, int]) -> None:
        self.db.execute(
            "UPDATE groups SET metrics=? WHERE group_id=?",
            (json.dumps(metrics, separators=(",", ":")), group),
        )
        self.db.commit()

    def versions(self) -> dict[str, str]:
        return {
            "image": self.config.image,
            "runtime": self.config.runtime,
            "protocol": PROTOCOL_VERSION,
            "seccomp": self.config.seccomp_sha256,
            "driver": "playwright-1.55.0",
            "pdf": "pymupdf-1.26.4",
            "browser_fonts": "pinned_by_image_digest",
            "cpu_accounting": "daemon_cumulative_sampled",
            "cpu_limit": "1_vcpu_10ms_period_shared_wall_reserve_1s",
        }

    async def status(self, descriptor: RunDescriptor, writer: asyncio.StreamWriter) -> None:
        group = hashlib.sha256(
            f"{descriptor.org_id}:{descriptor.job_id}:{descriptor.attempt_id}".encode()
        ).hexdigest()[:32]
        record = self.db.execute(
            "SELECT binding,state,metrics FROM groups WHERE group_id=?", (group,)
        ).fetchone()
        metrics: dict[str, int] = {}
        if record is None:
            names = await self.backend.inventory()
            if any(name.startswith(f"bid-sbx-{group}-") for name in names):
                raise SandboxFailure("sandbox_inventory_mismatch")
            state = "not_started"
        else:
            if record[0] != descriptor.digest:
                raise SandboxFailure("receipt_mismatch")
            metrics = json.loads(record[2])
            if group in self.active:
                state = "pending"
            elif record[1] == "complete":
                state = "complete"
            else:
                try:
                    await self.cleanup(group)
                    state = "complete"
                except SandboxFailure:
                    state = "failed"
        await send_frame(
            writer,
            {
                "type": "status",
                "descriptor_hash": descriptor.digest,
                "profile_digest": self.config.profile_digest,
                "cleanup_state": state,
                "metrics": metrics,
                "runtime_versions": {
                    **self.versions(),
                    "accounting": "recovered_samples_require_conservative_budget",
                },
            },
        )

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        group: str | None = None
        descriptor: RunDescriptor | None = None
        metrics: dict[str, int] = {}
        cleaned = False
        started = time.monotonic()
        try:
            if self.config.transport == "mtls":
                self.config.verify_tls_peer(writer, server=True)
            else:
                peer_socket = writer.get_extra_info("socket")
                peer_credential = vars(socket).get("SO_PEERCRED")
                if not isinstance(peer_credential, int):
                    raise SandboxFailure("sandbox_peer_check_unavailable")
                _, uid, _ = struct.unpack(
                    "3i", peer_socket.getsockopt(socket.SOL_SOCKET, peer_credential, 12)
                )
                if uid != self.config.client_uid:
                    raise SandboxFailure("sandbox_peer_denied")
        except SandboxFailure:
            # No application frame is exchanged with an unapproved peer certificate/UID.
            writer.close()
            with contextlib.suppress(OSError, TimeoutError):
                async with asyncio.timeout(3):
                    await writer.wait_closed()
            return
        if self.connections >= self.config.node_capacity + 4:
            await send_frame(writer, {"type": "error", "code": "sandbox_node_capacity"})
            writer.close()
            return
        self.connections += 1
        try:
            async with asyncio.timeout(5):
                header, source = await read_frame(reader, 4 * MIB)
            if set(header) != {"type", "protocol", "profile_digest", "descriptor", "length"}:
                raise SandboxFailure("invalid_frame")
            if (
                header["protocol"] != PROTOCOL_VERSION
                or header["profile_digest"] != self.config.profile_digest
            ):
                raise SandboxFailure("sandbox_profile_mismatch")
            descriptor = RunDescriptor.from_wire(header["descriptor"])
            if header["type"] == "status" and not source:
                await self.status(descriptor, writer)
                return
            if header["type"] != "run":
                raise SandboxFailure("invalid_frame")
            if self.config.synthetic_only and not descriptor.synthetic_input:
                raise SandboxFailure("sandbox_synthetic_only")
            if hashlib.sha256(source).hexdigest() != descriptor.input_sha256:
                raise SandboxFailure("input_hash_mismatch")
            started = time.monotonic()
            deadline = time.time() + descriptor.wall_seconds - 1
            group = self.register(descriptor, deadline)
            task = asyncio.current_task()
            assert task is not None
            self.active[group] = task
            metrics = {
                "wall_ms": 0,
                "cpu_ms": 0,
                "peak_memory_bytes": 0,
                "input_bytes": len(source),
                "network_bytes": 0,
                "output_bytes": 0,
                "request_count": 0,
            }
            self.persist_metrics(group, metrics)
            async with asyncio.timeout(descriptor.wall_seconds - 1):
                issues: list[str] = []
                rendered = await self.stage(
                    group,
                    "render",
                    descriptor,
                    source,
                    [],
                    reader,
                    writer,
                    metrics,
                    deadline,
                    issues,
                )
                self.check_set(descriptor, rendered)
                validated = await self.stage(
                    group, "validate", descriptor, b"", rendered, reader, writer, metrics, deadline
                )
                self.check_set(descriptor, validated)
                if [(x.kind, x.sha256, x.width, x.height, x.page) for x in rendered] != [
                    (x.kind, x.sha256, x.width, x.height, x.page) for x in validated
                ]:
                    raise SandboxFailure("validation_mismatch")
            await self.cleanup(group)
            cleaned = True
            metrics["wall_ms"] = int((time.monotonic() - started) * 1000)
            self.persist_metrics(group, metrics)
            async with asyncio.timeout(max(0.001, deadline - time.time())):
                for ordinal, artifact in enumerate(validated):
                    await send_frame(writer, artifact_header(artifact, ordinal), artifact.data)
                await send_frame(
                    writer,
                    {
                        "type": "complete",
                        "descriptor_hash": descriptor.digest,
                        "profile_digest": self.config.profile_digest,
                        "cleanup_state": "complete",
                        "metrics": metrics,
                        "runtime_versions": self.versions(),
                        "issues": issues,
                    },
                )
        except (SandboxFailure, OSError, TimeoutError, ValueError, KeyError, TypeError) as exc:
            code = exc.code if isinstance(exc, SandboxFailure) else "sandbox_execution_failed"
            cleanup_state = "unknown"
            if group is not None:
                metrics["wall_ms"] = int((time.monotonic() - started) * 1000)
                self.persist_metrics(group, metrics)
                try:
                    await asyncio.shield(self.cleanup(group))
                    cleaned = True
                    cleanup_state = "complete"
                except SandboxFailure:
                    cleanup_state = "failed"
                    code = "cleanup_pending"
            with contextlib.suppress(OSError, ConnectionError, TimeoutError):
                async with asyncio.timeout(2):
                    await send_frame(
                        writer,
                        {
                            "type": "error",
                            "code": code,
                            "descriptor_hash": descriptor.digest if descriptor else "",
                            "profile_digest": self.config.profile_digest,
                            "cleanup_state": cleanup_state,
                            "metrics": metrics,
                            "runtime_versions": self.versions(),
                        },
                    )
        finally:
            self.connections -= 1
            if group:
                if not cleaned:
                    with contextlib.suppress(SandboxFailure):
                        await asyncio.shield(self.cleanup(group))
                self.active.pop(group, None)
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    async def serve(self) -> None:
        await self.backend.preflight()
        await self.recover()
        if self.config.transport == "mtls":
            context = await asyncio.to_thread(self.config.tls_context, server=True)
            server = await asyncio.start_server(
                self.handle,
                self.config.bind_host,
                self.config.control_port,
                ssl=context,
                ssl_handshake_timeout=5,
                ssl_shutdown_timeout=2,
                limit=CONTROL_LIMIT,
                backlog=self.config.node_capacity + 4,
            )
        else:
            self.config.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.config.socket_path.exists():
                if (
                    not self.config.socket_path.is_socket()
                    or self.config.socket_path.stat().st_uid != os.getuid()
                ):
                    raise SandboxFailure("sandbox_socket_exists")
                self.config.socket_path.unlink()
            os.chown(self.config.socket_path.parent, -1, self.config.control_gid)
            os.chmod(self.config.socket_path.parent, 0o750)
            server = await asyncio.start_unix_server(
                self.handle, path=self.config.socket_path, limit=CONTROL_LIMIT
            )
            os.chown(self.config.socket_path, -1, self.config.control_gid)
            os.chmod(self.config.socket_path, 0o660)
        reaper = asyncio.create_task(self.reaper())
        try:
            async with server:
                serving = asyncio.create_task(server.serve_forever())
                try:
                    await asyncio.wait((serving, reaper), return_when=asyncio.FIRST_COMPLETED)
                    if reaper.done():
                        # A dead reaper must not leave a seemingly healthy admission socket.
                        self.quarantined = True
                        reaper.exception()
                        raise SandboxFailure("sandbox_reaper_failed")
                    await serving
                finally:
                    serving.cancel()
                    await asyncio.gather(serving, return_exceptions=True)
        finally:
            reaper.cancel()
            await asyncio.gather(reaper, return_exceptions=True)
            for task in list(self.active.values()):
                task.cancel()
            await asyncio.gather(*list(self.active.values()), return_exceptions=True)
            if self.config.transport == "unix":
                self.config.socket_path.unlink(missing_ok=True)
            self.db.close()
            self.lock_file.close()
