"""Browser capability backed exclusively by the dedicated sandbox supervisor."""

import asyncio
import hashlib
import ssl
from contextlib import suppress
from dataclasses import asdict
from typing import Any, Protocol

from app.providers.sandbox_fetch import RUN_FATAL_FETCH_CODES, FetchBroker, FetchDenied
from app.providers.sandbox_runtime import (
    CONTROL_LIMIT,
    MIB,
    PROTOCOL_VERSION,
    ArtifactPayload,
    CleanupReceipt,
    ExecutionResult,
    RunDescriptor,
    RuntimeConfig,
    SandboxFailure,
    read_frame,
    send_frame,
    validate_artifact,
)

ERROR_CODES = frozenset(
    {
        "archive_byte_limit",
        "artifact_hash_mismatch",
        "artifact_limit",
        "artifact_pages_mismatch",
        "artifact_sequence",
        "artifact_set_mismatch",
        "authentication_page_denied",
        "blank_capture",
        "browser_launch_failed",
        "bundle_not_requested",
        "byte_limit",
        "cleanup_pending",
        "content_encoding_denied",
        "content_encoding_invalid",
        "content_length_invalid",
        "content_type_denied",
        "control_limit",
        "cpu_limit",
        "diagnostic_limit",
        "dns_denied",
        "encrypted_pdf",
        "entry_fetch_ambiguous",
        "entry_fetch_denied",
        "entry_fetch_incomplete",
        "fetch_method_denied",
        "fetch_timeout",
        "fetch_transport_failed",
        "head_body_denied",
        "http_status_denied",
        "image_dimensions_mismatch",
        "image_limit",
        "input_hash_mismatch",
        "invalid_artifact",
        "invalid_cleanup_receipt",
        "invalid_descriptor",
        "invalid_fetch",
        "invalid_frame",
        "invalid_html",
        "invalid_input",
        "invalid_pdf",
        "invalid_pdf_pages",
        "invalid_png",
        "invalid_stage",
        "invalid_viewport",
        "manifest_byte_limit",
        "method_denied",
        "network_denied",
        "org_rate_limit",
        "origin_concurrency_limit",
        "output_limit",
        "partial_response_denied",
        "pdf_page_missing",
        "policy_changed",
        "policy_invalid",
        "policy_missing",
        "policy_revoked",
        "policy_selection_denied",
        "quota_unavailable",
        "receipt_mismatch",
        "redirect_invalid",
        "redirect_limit",
        "request_limit",
        "response_headers_invalid",
        "response_stream_invalid",
        "run_closed",
        "run_concurrency_limit",
        "runner_unexpected_failure",
        "sandbox_attempt_exists",
        "sandbox_budget_exhausted",
        "sandbox_caller_disconnected",
        "sandbox_cgroup_limits_unavailable",
        "sandbox_deadline",
        "sandbox_disabled",
        "sandbox_execution_failed",
        "sandbox_failure",
        "sandbox_image_not_pinned",
        "sandbox_inventory_mismatch",
        "sandbox_metrics_unavailable",
        "sandbox_node_capacity",
        "sandbox_node_quarantined",
        "sandbox_org_capacity",
        "sandbox_peer_check_unavailable",
        "sandbox_peer_configuration_required",
        "sandbox_peer_denied",
        "sandbox_profile_mismatch",
        "sandbox_reaper_failed",
        "sandbox_recovery_unavailable",
        "sandbox_rootless_required",
        "sandbox_runsc_cgroups_unenforced",
        "sandbox_runtime_failure",
        "sandbox_runtime_not_accepted",
        "sandbox_runtime_unavailable",
        "sandbox_seccomp_mismatch",
        "sandbox_seccomp_not_pinned",
        "sandbox_separate_service_identity_required",
        "sandbox_socket_exists",
        "sandbox_supervisor_already_running",
        "sandbox_supervisor_unavailable",
        "sandbox_synthetic_only",
        "source_fetch_failed",
        "source_http_error",
        "source_login_or_challenge",
        "source_navigation_failed",
        "source_timeout",
        "stream_limit",
        "unexpected_fetch",
        "unexpected_output",
        "url_denied",
        "url_invalid",
        "validation_mismatch",
    }
)


class BrowserProvider(Protocol):
    @property
    def available(self) -> bool: ...

    @property
    def profile_digest(self) -> str: ...

    async def render_prototype(self, descriptor: RunDescriptor, html: bytes) -> ExecutionResult: ...

    async def capture(
        self, descriptor: RunDescriptor, source_url: str, fetcher: FetchBroker
    ) -> ExecutionResult: ...

    async def recover(self, descriptor: RunDescriptor) -> CleanupReceipt: ...


class SocketBrowserProvider:
    def __init__(self, config: RuntimeConfig):
        self.config = config

    @property
    def profile_digest(self) -> str:
        return self.config.profile_digest

    @property
    def available(self) -> bool:
        try:
            self.check_ready()
        except SandboxFailure:
            return False
        return not self.config.synthetic_only

    def check_ready(self) -> None:
        self.config.validate()
        if self.config.transport == "mtls":
            self.config.check_tls_files()
        elif not self.config.socket_path.is_socket():
            raise SandboxFailure("sandbox_supervisor_unavailable")

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self.config.transport == "unix":
            return await asyncio.open_unix_connection(self.config.socket_path, limit=CONTROL_LIMIT)
        context = await asyncio.to_thread(self.config.tls_context, server=False)
        writer: asyncio.StreamWriter | None = None
        try:
            async with asyncio.timeout(5):
                reader, writer = await asyncio.open_connection(
                    self.config.control_host,
                    self.config.control_port,
                    ssl=context,
                    server_hostname=self.config.tls_server_name,
                    ssl_handshake_timeout=5,
                    ssl_shutdown_timeout=2,
                    limit=CONTROL_LIMIT,
                )
                self.config.verify_tls_peer(writer, server=False)
                return reader, writer
        except (OSError, TimeoutError, ssl.SSLError, SandboxFailure) as exc:
            if writer is not None:
                writer.close()
                with suppress(OSError, TimeoutError):
                    async with asyncio.timeout(3):
                        await writer.wait_closed()
            if isinstance(exc, SandboxFailure):
                raise
            raise SandboxFailure("sandbox_tls_connection_failed") from exc

    def _receipt(
        self, header: dict[str, Any], descriptor: RunDescriptor, kind: str
    ) -> tuple[str, dict[str, int], dict[str, str]]:
        expected = {
            "type",
            "length",
            "descriptor_hash",
            "profile_digest",
            "cleanup_state",
            "metrics",
            "runtime_versions",
        }
        if kind == "error":
            expected.add("code")
        if kind == "complete":
            expected.add("issues")
            allowed_issue = (
                "offline_resources_blocked"
                if descriptor.purpose == "prototype_offline"
                else "vendor_resources_incomplete"
            )
            if header.get("issues") not in ([], [allowed_issue]):
                raise SandboxFailure("invalid_cleanup_receipt")
        if set(header) != expected or header.get("length") != 0:
            raise SandboxFailure("invalid_cleanup_receipt")
        if (
            header.get("descriptor_hash") != descriptor.digest
            or header.get("profile_digest") != self.profile_digest
        ):
            raise SandboxFailure("receipt_mismatch")
        state = header.get("cleanup_state")
        states = (
            {"complete"}
            if kind == "complete"
            else {"complete", "pending", "failed", "not_started", "unknown"}
        )
        if not isinstance(state, str) or state not in states:
            raise SandboxFailure("invalid_cleanup_receipt")
        metrics = header.get("metrics")
        caps = {
            "wall_ms": 180_000,
            "cpu_ms": 240_000,
            "peak_memory_bytes": 4 * 1024 * MIB,
            "input_bytes": 4 * MIB,
            "network_bytes": 64 * MIB,
            "output_bytes": 128 * MIB,
            "request_count": 200,
        }
        if not isinstance(metrics, dict) or (
            set(metrics) != set(caps) and not (not metrics and kind != "complete")
        ):
            raise SandboxFailure("invalid_cleanup_receipt")
        if any(
            type(value) is not int or not 0 <= value <= caps[key] for key, value in metrics.items()
        ):
            raise SandboxFailure("invalid_cleanup_receipt")
        versions = header.get("runtime_versions")
        expected_versions = {
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
        if kind == "status":
            expected_versions["accounting"] = "recovered_samples_require_conservative_budget"
        if versions != expected_versions:
            raise SandboxFailure("invalid_cleanup_receipt")
        return state, dict(metrics), dict(expected_versions)

    @staticmethod
    def _error_code(value: object) -> str:
        return (
            value if isinstance(value, str) and value in ERROR_CODES else "sandbox_execution_failed"
        )

    async def recover(self, descriptor: RunDescriptor) -> CleanupReceipt:
        self.check_ready()
        descriptor.validate()
        writer: asyncio.StreamWriter | None = None
        try:
            async with asyncio.timeout(12):
                reader, writer = await self._connect()
                await send_frame(
                    writer,
                    {
                        "type": "status",
                        "protocol": PROTOCOL_VERSION,
                        "descriptor": asdict(descriptor),
                        "profile_digest": self.profile_digest,
                    },
                )
                header, payload = await read_frame(reader, 0)
                if header.get("type") != "status" or payload:
                    raise SandboxFailure("sandbox_recovery_unavailable")
                state, metrics, versions = self._receipt(header, descriptor, "status")
                return CleanupReceipt(state, descriptor.digest, metrics, versions)
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            raise SandboxFailure("invalid_cleanup_receipt") from exc
        except (OSError, TimeoutError) as exc:
            raise SandboxFailure("sandbox_recovery_unavailable") from exc
        finally:
            if writer is not None:
                writer.close()
                with suppress(OSError):
                    await writer.wait_closed()

    async def render_prototype(self, descriptor: RunDescriptor, html: bytes) -> ExecutionResult:
        if not self.config.enabled:
            raise SandboxFailure("sandbox_disabled")
        descriptor.validate()
        if descriptor.purpose != "prototype_offline" or not 0 < len(html) <= 4 * MIB:
            raise SandboxFailure("invalid_input")
        if hashlib.sha256(html).hexdigest() != descriptor.input_sha256:
            raise SandboxFailure("input_hash_mismatch")
        try:
            html.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise SandboxFailure("invalid_html") from exc
        return await self._execute(descriptor, html, None)

    async def capture(
        self, descriptor: RunDescriptor, source_url: str, fetcher: FetchBroker
    ) -> ExecutionResult:
        descriptor.validate()
        if descriptor.purpose != "vendor_capture":
            raise SandboxFailure("invalid_input")
        raw = source_url.encode("utf-8")
        if hashlib.sha256(raw).hexdigest() != descriptor.input_sha256 or len(raw) > 8192:
            raise SandboxFailure("input_hash_mismatch")
        try:
            return await self._execute(descriptor, raw, fetcher)
        finally:
            fetcher.close()

    async def _execute(
        self, descriptor: RunDescriptor, data: bytes, fetcher: FetchBroker | None
    ) -> ExecutionResult:
        self.check_ready()
        if self.config.synthetic_only and not descriptor.synthetic_input:
            raise SandboxFailure("sandbox_synthetic_only")
        writer: asyncio.StreamWriter | None = None
        serving: set[asyncio.Task[None]] = set()
        try:
            async with asyncio.timeout(descriptor.wall_seconds + 15):
                reader, writer = await self._connect()
                await send_frame(
                    writer,
                    {
                        "type": "run",
                        "protocol": PROTOCOL_VERSION,
                        "descriptor": asdict(descriptor),
                        "profile_digest": self.profile_digest,
                    },
                    data,
                )
                artifacts: list[ArtifactPayload] = []
                total = 0
                # Numbered fetches are served concurrently; the broker bounds parallelism.
                write_lock = asyncio.Lock()
                denied: asyncio.Future[None] = asyncio.get_running_loop().create_future()
                expected_id = 0

                async def serve(identifier: int, url: str, method: str) -> None:
                    assert fetcher is not None and writer is not None
                    try:
                        response = await fetcher.fetch(url, method=method)
                    except FetchDenied as exc:
                        if exc.code not in RUN_FATAL_FETCH_CODES:
                            # The broker recorded the denial; the page continues without it.
                            async with write_lock:
                                await send_frame(writer, {"type": "fetch_failed", "id": identifier})
                            return
                        async with write_lock:
                            await send_frame(writer, {"type": "error", "code": exc.code})
                        if not denied.done():
                            denied.set_exception(SandboxFailure(exc.code))
                        return
                    async with write_lock:
                        await send_frame(
                            writer,
                            {
                                "type": "fetch_result",
                                "id": identifier,
                                "status": response.status,
                                "headers": response.headers,
                                "url": response.url,
                            },
                            response.body,
                        )

                while True:
                    reading = asyncio.ensure_future(
                        read_frame(reader, descriptor.output_limit - total)
                    )
                    await asyncio.wait({reading, denied}, return_when=asyncio.FIRST_COMPLETED)
                    if denied.done():
                        reading.cancel()
                        for task in serving:
                            task.cancel()
                        denied.result()
                    header, payload = reading.result()
                    kind = header.get("type")
                    if kind == "fetch":
                        if (
                            fetcher is None
                            or payload
                            or set(header) != {"type", "id", "url", "method", "length"}
                        ):
                            raise SandboxFailure("unexpected_fetch")
                        if (
                            not isinstance(header["url"], str)
                            or len(header["url"]) > 8192
                            or header["id"] != expected_id
                        ):
                            raise SandboxFailure("invalid_fetch")
                        method = header["method"]
                        if method not in ("GET", "HEAD"):
                            raise SandboxFailure("fetch_method_denied")
                        expected_id += 1
                        task = asyncio.create_task(serve(header["id"], header["url"], method))
                        serving.add(task)
                        task.add_done_callback(serving.discard)
                    elif kind == "artifact":
                        if len(artifacts) >= 32 or header.get("ordinal") != len(artifacts):
                            raise SandboxFailure("artifact_sequence")
                        artifacts.append(validate_artifact(header, payload))
                        total += len(payload)
                    elif kind == "complete":
                        if payload:
                            raise SandboxFailure("invalid_cleanup_receipt")
                        _, metrics, versions = self._receipt(header, descriptor, "complete")
                        expected_kinds = (
                            ["source_pdf"] + ["pdf_page_png"] * len(descriptor.pdf_pages)
                            if descriptor.format == "pdf"
                            else [
                                "prototype_png"
                                if descriptor.purpose == "prototype_offline"
                                else "capture_png",
                                "rendered_html",
                            ]
                        )
                        if [item.kind for item in artifacts] != expected_kinds:
                            raise SandboxFailure("artifact_set_mismatch")
                        if (
                            descriptor.format == "pdf"
                            and tuple(item.page for item in artifacts[1:]) != descriptor.pdf_pages
                        ):
                            raise SandboxFailure("artifact_pages_mismatch")
                        render_output = sum(len(x.data) for x in artifacts)
                        if fetcher is not None:
                            artifacts.append(
                                ArtifactPayload("request_manifest", fetcher.manifest_bytes())
                            )
                            if descriptor.archive == "bundle":
                                artifacts.append(
                                    ArtifactPayload("capture_archive", fetcher.archive_bytes())
                                )
                        total = sum(len(x.data) for x in artifacts)
                        if total > descriptor.output_limit:
                            raise SandboxFailure("output_limit")
                        metrics["output_bytes"] += total - render_output
                        if metrics["output_bytes"] > descriptor.output_limit:
                            raise SandboxFailure("output_limit")
                        return ExecutionResult(
                            tuple(artifacts),
                            metrics,
                            versions,
                            descriptor_hash=descriptor.digest,
                            issues=tuple(header["issues"]),
                        )
                    elif kind == "error":
                        if payload:
                            raise SandboxFailure("invalid_cleanup_receipt")
                        if header.get("descriptor_hash") == descriptor.digest:
                            state, metrics, versions = self._receipt(header, descriptor, "error")
                            raise SandboxFailure(
                                self._error_code(header.get("code")),
                                cleanup_state=state,
                                descriptor_hash=descriptor.digest,
                                metrics=metrics,
                                runtime_versions=versions,
                            )
                        if set(header) != {"type", "code", "length"}:
                            raise SandboxFailure("invalid_cleanup_receipt")
                        raise SandboxFailure(self._error_code(header.get("code")))
                    else:
                        raise SandboxFailure("invalid_frame")
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            raise SandboxFailure("invalid_frame") from exc
        except TimeoutError as exc:
            raise SandboxFailure("sandbox_deadline") from exc
        except (OSError, ConnectionError) as exc:
            raise SandboxFailure("sandbox_supervisor_unavailable") from exc
        finally:
            for task in serving:
                task.cancel()
            if writer is not None:
                writer.close()
                with suppress(OSError):
                    await writer.wait_closed()


def create_browser_provider() -> BrowserProvider:
    return SocketBrowserProvider(RuntimeConfig.from_env())
