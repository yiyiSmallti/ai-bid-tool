"""Typed, bounded transport shared by the trusted client and execution node."""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import re
import ssl
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

PROTOCOL_VERSION = "bid-sandbox-v1"
CONTROL_LIMIT = 64 * 1024
MIB = 1024 * 1024
KIND_LIMITS = {
    "prototype_png": 40 * MIB,
    "capture_png": 40 * MIB,
    "pdf_page_png": 40 * MIB,
    "rendered_html": 4 * MIB,
    "source_pdf": 32 * MIB,
    "request_manifest": 4 * MIB,
    "capture_archive": 64 * MIB,
}


class SandboxFailure(Exception):
    """Only a stable reason code may cross the trusted diagnostic boundary."""

    def __init__(
        self,
        code: str,
        *,
        cleanup_state: str = "unknown",
        descriptor_hash: str = "",
        metrics: dict[str, int] | None = None,
        runtime_versions: dict[str, str] | None = None,
    ):
        self.cleanup_state = cleanup_state
        self.descriptor_hash = descriptor_hash
        self.metrics = metrics or {}
        self.runtime_versions = runtime_versions or {}
        self.code = (
            code
            if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,100}", code)
            else "sandbox_failure"
        )
        super().__init__(self.code)


@dataclass(frozen=True)
class RunDescriptor:
    org_id: UUID
    job_id: UUID
    attempt_id: UUID
    input_sha256: str
    purpose: Literal["prototype_offline", "vendor_capture"]
    viewport_width: int = 1440
    viewport_height: int = 900
    format: Literal["web", "pdf"] = "web"
    pdf_pages: tuple[int, ...] = ()
    archive: Literal["manifest", "bundle"] = "manifest"
    synthetic_input: bool = False
    budget_wall_ms: int | None = None
    budget_cpu_ms: int | None = None
    budget_output_bytes: int | None = None

    def validate(self) -> None:
        cap = 60_000 if self.purpose == "prototype_offline" else 120_000
        for budget in (self.budget_wall_ms, self.budget_cpu_ms):
            if budget is not None and (type(budget) is not int or not 2000 <= budget <= cap):
                raise SandboxFailure("sandbox_budget_exhausted")
        if self.budget_output_bytes is not None and (
            type(self.budget_output_bytes) is not int
            or not 1 <= self.budget_output_bytes <= 128 * MIB
        ):
            raise SandboxFailure("sandbox_budget_exhausted")
        if not re.fullmatch("[a-f0-9]{64}", self.input_sha256):
            raise SandboxFailure("invalid_descriptor")
        if not 320 <= self.viewport_width <= 4096 or not 240 <= self.viewport_height <= 4096:
            raise SandboxFailure("invalid_viewport")
        if self.purpose not in ("prototype_offline", "vendor_capture"):
            raise SandboxFailure("invalid_descriptor")
        if self.format not in ("web", "pdf") or self.archive not in ("manifest", "bundle"):
            raise SandboxFailure("invalid_descriptor")
        if self.purpose == "prototype_offline" and self.format != "web":
            raise SandboxFailure("invalid_descriptor")
        if self.format == "pdf":
            if not 1 <= len(self.pdf_pages) <= 10 or len(set(self.pdf_pages)) != len(
                self.pdf_pages
            ):
                raise SandboxFailure("invalid_pdf_pages")
            if any(type(page) is not int or page < 1 for page in self.pdf_pages):
                raise SandboxFailure("invalid_pdf_pages")
        elif self.pdf_pages:
            raise SandboxFailure("invalid_pdf_pages")

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical(asdict(self))).hexdigest()

    @property
    def wall_seconds(self) -> int:
        maximum = 60 if self.purpose == "prototype_offline" else 120
        return min(
            maximum,
            (self.budget_wall_ms or maximum * 1000) // 1000,
            (self.budget_cpu_ms or maximum * 1000) // 1000,
        )

    @property
    def output_limit(self) -> int:
        maximum = (64 if self.purpose == "prototype_offline" else 128) * MIB
        return min(maximum, self.budget_output_bytes or maximum)

    @classmethod
    def from_wire(cls, value: dict[str, Any]) -> "RunDescriptor":
        values = dict(value)
        for key in ("org_id", "job_id", "attempt_id"):
            values[key] = UUID(values[key])
        values["pdf_pages"] = tuple(values.get("pdf_pages", ()))
        result = cls(**values)
        result.validate()
        return result


@dataclass(frozen=True)
class ArtifactPayload:
    kind: str
    data: bytes
    width: int | None = None
    height: int | None = None
    page: int | None = None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


@dataclass(frozen=True)
class ExecutionResult:
    artifacts: tuple[ArtifactPayload, ...]
    metrics: dict[str, int]
    runtime_versions: dict[str, str]
    cleanup_state: str = "complete"
    issues: tuple[str, ...] = ()
    descriptor_hash: str = ""


@dataclass(frozen=True)
class CleanupReceipt:
    cleanup_state: str
    descriptor_hash: str
    metrics: dict[str, int]
    runtime_versions: dict[str, str]


@dataclass(frozen=True)
class RuntimeConfig:
    enabled: bool = False
    socket_path: Path = Path("/run/bid-sandbox/control.sock")
    transport: Literal["unix", "mtls"] = "unix"
    control_host: str = ""
    control_port: int = 8443
    bind_host: str = "127.0.0.1"
    tls_server_name: str = ""
    tls_ca_path: Path | None = None
    tls_cert_path: Path | None = None
    tls_key_path: Path | None = None
    tls_server_sha256: str = ""
    tls_client_sha256: str = ""
    image: str = ""
    runtime: str = "runsc"
    synthetic_only: bool = False
    seccomp_path: Path = Path("/etc/bid-sandbox/seccomp.json")
    seccomp_sha256: str = ""
    accepted: bool = False
    docker_socket: Path = Path("/run/user/1000/docker.sock")
    state_path: Path = Path("/var/lib/bid-sandbox/supervisor.sqlite3")
    client_uid: int = -1
    control_gid: int = -1
    node_capacity: int = 4
    memory_capacity_bytes: int = 4 * 1024 * MIB

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        def optional_path(name: str) -> Path | None:
            value = os.environ.get(name)
            return Path(value) if value else None

        transport = os.environ.get("BID_SANDBOX_TRANSPORT", "unix")
        if transport not in ("unix", "mtls"):
            raise SandboxFailure("sandbox_transport_invalid")
        return cls(
            transport=transport,
            control_host=os.environ.get("BID_SANDBOX_HOST", ""),
            control_port=int(os.environ.get("BID_SANDBOX_PORT", "8443")),
            bind_host=os.environ.get("BID_SANDBOX_BIND_HOST", "127.0.0.1"),
            tls_server_name=os.environ.get("BID_SANDBOX_TLS_SERVER_NAME", ""),
            tls_ca_path=optional_path("BID_SANDBOX_TLS_CA"),
            tls_cert_path=optional_path("BID_SANDBOX_TLS_CERT"),
            tls_key_path=optional_path("BID_SANDBOX_TLS_KEY"),
            tls_server_sha256=os.environ.get("BID_SANDBOX_TLS_SERVER_SHA256", ""),
            tls_client_sha256=os.environ.get("BID_SANDBOX_TLS_CLIENT_SHA256", ""),
            enabled=os.environ.get("BID_SANDBOX_ENABLED") == "1",
            socket_path=Path(os.environ.get("BID_SANDBOX_SOCKET", "/run/bid-sandbox/control.sock")),
            image=os.environ.get("BID_SANDBOX_IMAGE", ""),
            runtime=os.environ.get("BID_SANDBOX_RUNTIME", "runsc"),
            synthetic_only=os.environ.get("BID_SANDBOX_SYNTHETIC_ONLY") == "1",
            seccomp_sha256=os.environ.get("BID_SANDBOX_SECCOMP_SHA256", ""),
            accepted=os.environ.get("BID_SANDBOX_RUNTIME_ACCEPTED") == "1",
            seccomp_path=Path(
                os.environ.get("BID_SANDBOX_SECCOMP", "/etc/bid-sandbox/seccomp.json")
            ),
            docker_socket=Path(
                os.environ.get("BID_SANDBOX_DOCKER_SOCKET", "/run/user/1000/docker.sock")
            ),
            state_path=Path(
                os.environ.get("BID_SANDBOX_STATE", "/var/lib/bid-sandbox/supervisor.sqlite3")
            ),
            client_uid=int(os.environ.get("BID_SANDBOX_CLIENT_UID", "-1")),
            control_gid=int(os.environ.get("BID_SANDBOX_CONTROL_GID", "-1")),
        )

    def validate(self) -> None:
        if not self.enabled:
            raise SandboxFailure("sandbox_disabled")
        if self.transport not in ("unix", "mtls"):
            raise SandboxFailure("sandbox_transport_invalid")
        if self.transport == "mtls":
            if type(self.control_port) is not int or not 1 <= self.control_port <= 65535:
                raise SandboxFailure("sandbox_tls_configuration_invalid")
            if not re.fullmatch(r"[a-zA-Z0-9._:-]{1,253}", self.control_host):
                raise SandboxFailure("sandbox_tls_configuration_invalid")
            if not re.fullmatch(r"[a-zA-Z0-9.:-]{1,253}", self.tls_server_name):
                raise SandboxFailure("sandbox_tls_configuration_invalid")
            try:
                ipaddress.ip_address(self.bind_host)
            except ValueError as exc:
                raise SandboxFailure("sandbox_tls_configuration_invalid") from exc
            if any(
                not re.fullmatch("[a-f0-9]{64}", pin)
                for pin in (self.tls_server_sha256, self.tls_client_sha256)
            ):
                raise SandboxFailure("sandbox_tls_pin_required")
            if any(
                path is None for path in (self.tls_ca_path, self.tls_cert_path, self.tls_key_path)
            ):
                raise SandboxFailure("sandbox_tls_configuration_invalid")
        if not self.accepted:
            raise SandboxFailure("sandbox_runtime_not_accepted")
        if not re.fullmatch(r"[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}", self.image):
            raise SandboxFailure("sandbox_image_not_pinned")
        if self.runtime != "runsc" and not (self.runtime == "runc" and self.synthetic_only):
            raise SandboxFailure("sandbox_runtime_not_accepted")
        if not re.fullmatch("[a-f0-9]{64}", self.seccomp_sha256):
            raise SandboxFailure("sandbox_seccomp_not_pinned")

    def check_tls_files(self) -> None:
        """Local readiness only: no network connection or certificate negotiation."""
        try:
            for path in (self.tls_ca_path, self.tls_cert_path, self.tls_key_path):
                if path is None or not path.is_file() or not os.access(path, os.R_OK):
                    raise SandboxFailure("sandbox_tls_files_unavailable")
            assert self.tls_key_path is not None
            if self.tls_key_path.stat().st_mode & 0o077:
                raise SandboxFailure("sandbox_tls_key_permissions")
        except OSError as exc:
            raise SandboxFailure("sandbox_tls_files_unavailable") from exc

    def tls_context(self, *, server: bool) -> ssl.SSLContext:
        self.validate()
        self.check_tls_files()
        assert self.tls_ca_path and self.tls_cert_path and self.tls_key_path
        try:
            purpose = ssl.Purpose.CLIENT_AUTH if server else ssl.Purpose.SERVER_AUTH
            context = ssl.create_default_context(purpose, cafile=str(self.tls_ca_path))
            context.minimum_version = ssl.TLSVersion.TLSv1_3
            context.maximum_version = ssl.TLSVersion.TLSv1_3
            context.verify_mode = ssl.CERT_REQUIRED
            context.check_hostname = not server
            context.set_alpn_protocols([PROTOCOL_VERSION])
            # Empty password explicitly prevents an interactive private-key prompt.
            context.load_cert_chain(str(self.tls_cert_path), str(self.tls_key_path), password="")
            return context
        except (OSError, ValueError, ssl.SSLError) as exc:
            raise SandboxFailure("sandbox_tls_configuration_invalid") from exc

    def verify_tls_peer(self, writer: asyncio.StreamWriter, *, server: bool) -> None:
        connection = writer.get_extra_info("ssl_object")
        if (
            connection is None
            or connection.version() != "TLSv1.3"
            or connection.selected_alpn_protocol() != PROTOCOL_VERSION
        ):
            raise SandboxFailure("sandbox_tls_peer_denied")
        certificate = connection.getpeercert(binary_form=True)
        if not isinstance(certificate, bytes) or not 0 < len(certificate) <= CONTROL_LIMIT:
            raise SandboxFailure("sandbox_tls_peer_denied")
        expected = self.tls_client_sha256 if server else self.tls_server_sha256
        if not hmac.compare_digest(hashlib.sha256(certificate).hexdigest(), expected):
            raise SandboxFailure("sandbox_tls_peer_denied")

    @property
    def profile_digest(self) -> str:
        return hashlib.sha256(
            canonical(
                {
                    "protocol": PROTOCOL_VERSION,
                    "image": self.image,
                    "runtime": self.runtime,
                    "seccomp_sha256": self.seccomp_sha256,
                    "profile": "bounded-v2-cpu1-period10ms-reserve1s",
                    "synthetic_only": self.synthetic_only,
                    "transport": self.transport,
                    "control_host": self.control_host if self.transport == "mtls" else None,
                    "control_port": self.control_port if self.transport == "mtls" else None,
                    "tls_server_name": self.tls_server_name if self.transport == "mtls" else None,
                    "tls_server_sha256": self.tls_server_sha256
                    if self.transport == "mtls"
                    else None,
                    "tls_client_sha256": self.tls_client_sha256
                    if self.transport == "mtls"
                    else None,
                }
            )
        ).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    ).encode("ascii")


async def send_frame(
    writer: asyncio.StreamWriter, header: dict[str, Any], data: bytes = b""
) -> None:
    metadata = canonical({**header, "length": len(data)}) + b"\n"
    if len(metadata) > CONTROL_LIMIT:
        raise SandboxFailure("control_limit")
    writer.write(metadata)
    await writer.drain()
    for offset in range(0, len(data), CONTROL_LIMIT):
        writer.write(data[offset : offset + CONTROL_LIMIT])
        await writer.drain()


async def read_frame(reader: asyncio.StreamReader, limit: int) -> tuple[dict[str, Any], bytes]:
    try:
        raw = await reader.readline()
        if not raw or len(raw) > CONTROL_LIMIT or not raw.endswith(b"\n"):
            raise SandboxFailure("invalid_frame")
        header = json.loads(raw)
        if not isinstance(header, dict) or type(header.get("length")) is not int:
            raise SandboxFailure("invalid_frame")
        length = header["length"]
        if length < 0 or length > limit:
            raise SandboxFailure("stream_limit")
        return header, await reader.readexactly(length)
    except (ValueError, RecursionError, asyncio.IncompleteReadError) as exc:
        raise SandboxFailure("invalid_frame") from exc


def validate_artifact(header: dict[str, Any], data: bytes) -> ArtifactPayload:
    """Only inspect fixed headers here; all image/PDF decoding runs in validator."""
    allowed = {"type", "ordinal", "kind", "length", "width", "height", "page", "sha256"}
    if set(header) - allowed or type(header.get("ordinal")) is not int:
        raise SandboxFailure("invalid_artifact")
    kind = header.get("kind")
    if (
        not isinstance(kind, str)
        or kind not in KIND_LIMITS
        or not data
        or len(data) > KIND_LIMITS[kind]
    ):
        raise SandboxFailure("artifact_limit")
    width, height, page = header.get("width"), header.get("height"), header.get("page")
    if kind.endswith("_png"):
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise SandboxFailure("invalid_png")
        if any(type(x) is not int or x < 1 or x > 8192 for x in (width, height)):
            raise SandboxFailure("image_limit")
        assert isinstance(width, int) and isinstance(height, int)
        if width * height > 20_000_000:
            raise SandboxFailure("image_limit")
        if (
            len(data) < 24
            or int.from_bytes(data[16:20], "big") != width
            or int.from_bytes(data[20:24], "big") != height
        ):
            raise SandboxFailure("image_dimensions_mismatch")
    elif kind == "source_pdf" and not data.startswith(b"%PDF-"):
        raise SandboxFailure("invalid_pdf")
    elif kind == "rendered_html":
        try:
            data.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise SandboxFailure("invalid_html") from exc
    artifact = ArtifactPayload(kind, data, width, height, page)
    if "sha256" in header and header["sha256"] != artifact.sha256:
        raise SandboxFailure("artifact_hash_mismatch")
    return artifact
