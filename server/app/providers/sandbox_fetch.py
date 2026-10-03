"""Trusted exact-URL fetch broker; no browser-controlled headers or credentials."""

from __future__ import annotations

import asyncio
import hashlib
import io
import ipaddress
import json
import os
import re
import socket
import sqlite3
import time
import zipfile
import zlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

_MIB = 1024 * 1024
_REDIRECTS = {301, 302, 303, 307, 308}
_HEADERS = {"content-type", "content-length", "etag", "last-modified"}
_CONTENT_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "text/css",
    "text/javascript",
    "application/javascript",
    "application/json",
    "text/plain",
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/svg+xml",
    "image/x-icon",
    "font/woff",
    "font/woff2",
    "application/font-woff",
}
_BLOCKED_HOSTS = {"metadata.google.internal", "host.docker.internal", "localhost"}
_CREDENTIAL_KEYS = re.compile(
    r"token|password|passwd|secret|credential|authorization|signature|api.?key|^key$|^sig$|^auth$",
    re.I,
)


# Authorization or ledger failures end the whole run. Any other denial omits one
# resource and leaves the capture incomplete.
RUN_FATAL_FETCH_CODES = frozenset(
    {
        "policy_invalid",
        "policy_missing",
        "policy_revoked",
        "policy_changed",
        "run_closed",
        "quota_unavailable",
        "manifest_byte_limit",
    }
)


class FetchDenied(Exception):
    """Only a fixed reason code may cross diagnostic/audit boundaries."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def canonical_url(value: str) -> str:
    if not isinstance(value, str) or len(value) > 8192 or not value:
        raise FetchDenied("url_invalid")
    if re.search(r"[\x00-\x20\x7f\\]", value) or "#" in value:
        raise FetchDenied("url_invalid")
    try:
        parts = urlsplit(value)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
        ):
            raise ValueError
        host = parts.hostname.encode("idna").decode("ascii").lower()
        if host.endswith(".") or host in _BLOCKED_HOSTS or "%" in host:
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            # Browser-compatible legacy numeric forms cannot become DNS names here.
            if re.fullmatch(r"[0-9.]+", host) or any(
                part.lower().startswith("0x") for part in host.split(".")
            ):
                raise ValueError from None
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
                raise ValueError from None
        else:
            if not _public_address(str(address)):
                raise ValueError
            host = address.compressed
        port = parts.port if parts.port is not None else 443
        if port < 1 or port > 65535:
            raise ValueError
        path = parts.path or "/"
        decoded = unquote(path, errors="strict")
        if re.search(r"%(?:2f|5c|3f|23|25)", path, re.I) or re.search(r"%(?![a-fA-F0-9]{2})", path):
            raise ValueError
        if any(segment in {".", ".."} for segment in decoded.split("/")) or re.search(
            r"[\x00-\x1f\x7f\\]", decoded
        ):
            raise ValueError
        query_decoded = unquote(parts.query, errors="strict")
        if re.search(r"[\x00-\x1f\x7f]", query_decoded) or "%" in query_decoded:
            raise ValueError
        if any(
            _CREDENTIAL_KEYS.search(key)
            for key, _ in parse_qsl(parts.query, keep_blank_values=True)
        ):
            raise ValueError
        authority = f"[{host}]" if ":" in host else host
        if port != 443:
            authority += f":{port}"
        normalized = urlunsplit(("https", authority, path, parts.query, ""))
        # The parser used to send requests performs the final encoding canonicalization.
        return str(httpx.URL(normalized))
    except (ValueError, UnicodeError, httpx.InvalidURL):
        raise FetchDenied("url_invalid") from None


def _public_address(value: str) -> bool:
    if "%" in value or value == "168.63.129.16":
        return False
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(
        address.is_global
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
        and not address.is_loopback
        and not (isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped)
    )


class URLRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    url: str
    methods: tuple[Literal["GET", "HEAD"], ...] = ("GET", "HEAD")

    @model_validator(mode="after")
    def normalize(self) -> URLRule:
        object.__setattr__(self, "url", canonical_url(self.url))
        if not self.methods or len(set(self.methods)) != len(self.methods):
            raise ValueError("invalid methods")
        return self


# Development nodes only: a policy may admit every public HTTPS URL. Canonicalization,
# public-address DNS checks, budgets and content rules still apply to each request.
DEV_OPEN_EGRESS_ENV = "BID_SANDBOX_DEV_OPEN_EGRESS"
# A local fake-IP proxy answers DNS from the RFC 2544 benchmark range and routes the
# connection by hostname; only an open development policy accepts these answers.
_FAKE_IP_RANGE = ipaddress.ip_network("198.18.0.0/15")


def _dev_proxy_address(value: str) -> bool:
    try:
        return ipaddress.ip_address(value) in _FAKE_IP_RANGE
    except ValueError:
        return False


class FetchPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    revision: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.-]+$")
    main_urls: tuple[str, ...] = Field(default=(), max_length=200)
    rules: tuple[URLRule, ...] = Field(default=(), max_length=1000)
    open_public_https: bool = False

    @model_validator(mode="after")
    def normalize(self) -> FetchPolicy:
        object.__setattr__(self, "main_urls", tuple(canonical_url(url) for url in self.main_urls))
        urls = [rule.url for rule in self.rules]
        if len(set(urls)) != len(urls) or any(url not in urls for url in self.main_urls):
            raise ValueError("invalid policy URL set")
        if self.open_public_https:
            if self.main_urls or self.rules:
                raise ValueError("an open development policy lists no URLs")
        elif not self.main_urls or not self.rules:
            raise ValueError("an exact policy needs main URLs and rules")
        return self

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    def admits_main(self, url: str) -> bool:
        return self.open_public_https or url in self.main_urls

    def allows(self, url: str, method: str) -> bool:
        if self.open_public_https:
            return method in {"GET", "HEAD"}
        return any(rule.url == url and method in rule.methods for rule in self.rules)


class _PolicyFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    policies: tuple[FetchPolicy, ...] = Field(max_length=1000)
    revoked_revisions: tuple[str, ...] = ()
    active_revisions: dict[str, str] = Field(default_factory=dict, max_length=10000)

    @model_validator(mode="after")
    def validate_active_revisions(self) -> _PolicyFile:
        active: dict[str, str] = {}
        for raw_url, revision in self.active_revisions.items():
            url = canonical_url(raw_url)
            if url in active or revision in self.revoked_revisions:
                raise ValueError("invalid active revision")
            if not any(
                policy.revision == revision and url in policy.main_urls for policy in self.policies
            ):
                raise ValueError("active revision entry is missing")
            active[url] = revision
        object.__setattr__(self, "active_revisions", active)
        return self


class PolicySource:
    """Operator-owned file; atomically replace it to revoke a revision immediately."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> _PolicyFile:
        try:
            with self.path.open("rb") as source:
                raw = source.read(4 * _MIB + 1)
            if len(raw) > 4 * _MIB:
                raise ValueError
            result = _PolicyFile.model_validate_json(raw)
            revisions = [policy.revision for policy in result.policies]
            if len(set(revisions)) != len(revisions):
                raise ValueError
            if any(policy.open_public_https for policy in result.policies) and (
                os.environ.get(DEV_OPEN_EGRESS_ENV) != "1"
            ):
                raise ValueError
            return result
        except (OSError, ValueError, ValidationError, FetchDenied):
            raise FetchDenied("policy_invalid") from None

    def check(self, revision: str, expected_hash: str | None = None) -> FetchPolicy:
        value = self._load()
        if revision in value.revoked_revisions:
            raise FetchDenied("policy_revoked")
        for policy in value.policies:
            if policy.revision == revision:
                if expected_hash is not None and policy.sha256 != expected_hash:
                    raise FetchDenied("policy_changed")
                return policy
        raise FetchDenied("policy_missing")

    def select(self, url: str) -> FetchPolicy:
        normalized = canonical_url(url)
        value = self._load()
        active_revision = value.active_revisions.get(normalized)
        matches = [
            policy
            for policy in value.policies
            if normalized in policy.main_urls
            and policy.revision not in value.revoked_revisions
            and (active_revision is None or policy.revision == active_revision)
        ]
        if not matches and active_revision is None:
            matches = [
                policy
                for policy in value.policies
                if policy.open_public_https and policy.revision not in value.revoked_revisions
            ]
        if len(matches) != 1:
            raise FetchDenied("policy_selection_denied")
        return matches[0]


@dataclass(frozen=True)
class FetchLimits:
    requests: int = 200
    redirects: int = 5
    concurrency: int = 4
    response_bytes: int = 32 * _MIB
    html_bytes: int = 4 * _MIB
    run_bytes: int = 64 * _MIB
    timeout_seconds: float = 15.0

    def __post_init__(self):
        maxima = {
            "requests": 200,
            "redirects": 5,
            "concurrency": 4,
            "response_bytes": 32 * _MIB,
            "html_bytes": 4 * _MIB,
            "run_bytes": 64 * _MIB,
            "timeout_seconds": 15.0,
        }
        if any(not 0 < getattr(self, key) <= maximum for key, maximum in maxima.items()):
            raise ValueError("fetch limits must be positive and cannot increase the profile")


@dataclass(frozen=True)
class FetchPayload:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class FetchReceipt:
    ordinal: int
    parent_ordinal: int | None
    archive_entry: str | None
    url: str
    url_sha256: str
    method: str
    status: int
    headers: dict[str, str]
    sha256: str
    wire_sha256: str
    bytes: int
    wire_bytes: int
    captured_at: float
    duration_ms: int
    policy_revision: str
    policy_sha256: str


@dataclass(frozen=True)
class FetchDenial:
    ordinal: int
    parent_ordinal: int | None
    url_sha256: str
    code: str
    occurred_at: float


class FetchQuota(Protocol):
    def acquire(
        self, org_id: UUID, origin: str, *, org_window: bool = True
    ) -> AbstractAsyncContextManager[None]: ...


class SQLiteFetchQuota:
    """Durable node control counters, never business records or source content.

    Every proxy process on a dedicated execution node must use the same local ledger.
    A lease outlives the maximum request timeout; dead processes recover on expiry.
    """

    def __init__(self, path: Path, *, origin_wait_seconds: float = 15.0):
        self.path = Path(path)
        # A full origin queues for a lease up to the request timeout; the org rate denies.
        self.origin_wait_seconds = origin_wait_seconds

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=2, isolation_level=None)
        self.path.chmod(0o600)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS requests (org_hash TEXT NOT NULL, at REAL NOT NULL)"
        )
        connection.execute("CREATE INDEX IF NOT EXISTS requests_org_at ON requests(org_hash, at)")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS leases (id TEXT PRIMARY KEY, origin_hash TEXT NOT NULL, expires REAL NOT NULL)"
        )
        return connection

    def _claim(self, org_hash: str, origin_hash: str, lease_id: str, org_window: bool = True):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = time.time()
            connection.execute("DELETE FROM requests WHERE at <= ?", (now - 60,))
            connection.execute("DELETE FROM leases WHERE expires <= ?", (now,))
            if (
                org_window
                and connection.execute(
                    "SELECT count(*) FROM requests WHERE org_hash = ?", (org_hash,)
                ).fetchone()[0]
                >= 60
            ):
                raise FetchDenied("org_rate_limit")
            if (
                connection.execute(
                    "SELECT count(*) FROM leases WHERE origin_hash = ?", (origin_hash,)
                ).fetchone()[0]
                >= 2
            ):
                raise FetchDenied("origin_concurrency_limit")
            connection.execute("INSERT INTO requests VALUES (?, ?)", (org_hash, now))
            connection.execute(
                "INSERT INTO leases VALUES (?, ?, ?)", (lease_id, origin_hash, now + 30)
            )
            connection.commit()
        finally:
            connection.close()

    def _release(self, lease_id: str):
        connection = self._connect()
        try:
            connection.execute("DELETE FROM leases WHERE id = ?", (lease_id,))
        finally:
            connection.close()

    @asynccontextmanager
    async def acquire(
        self, org_id: UUID, origin: str, *, org_window: bool = True
    ) -> AsyncIterator[None]:
        lease_id = uuid4().hex
        deadline = time.monotonic() + self.origin_wait_seconds
        try:
            while True:
                try:
                    await asyncio.to_thread(
                        self._claim,
                        hashlib.sha256(str(org_id).encode()).hexdigest(),
                        hashlib.sha256(origin.encode()).hexdigest(),
                        lease_id,
                        org_window,
                    )
                    break
                except FetchDenied as exc:
                    if exc.code != "origin_concurrency_limit" or time.monotonic() >= deadline:
                        raise
                    await asyncio.sleep(0.1)
            try:
                yield
            finally:
                await asyncio.shield(asyncio.to_thread(self._release, lease_id))
        except (sqlite3.Error, OSError):
            raise FetchDenied("quota_unavailable") from None


async def system_resolver(host: str) -> tuple[str, ...]:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        answers = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return tuple(dict.fromkeys(str(answer[4][0]) for answer in answers))
    return (str(literal),)


class FetchBroker:
    def __init__(
        self,
        policy_source: PolicySource,
        policy_revision: str,
        org_id: UUID,
        *,
        quota: FetchQuota,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Callable[[str], Awaitable[tuple[str, ...]]] = system_resolver,
        bundle: bool = False,
        limits: FetchLimits | None = None,
    ):
        self.policy_source = policy_source
        self.policy = policy_source.check(policy_revision)
        self.org_id = org_id
        self.quota = quota
        self.transport = transport
        self.resolver = resolver
        self.bundle = bundle
        self.limits = limits or FetchLimits()
        self._receipts: list[FetchReceipt] = []
        self._denials: list[FetchDenial] = []
        self._bundle: dict[int, FetchPayload] = {}
        self._ordinal = 0
        self._closed = False
        self._active = 0
        self._requests = 0
        self._wire_bytes = 0
        self._body_bytes = 0

    @property
    def receipts(self) -> tuple[FetchReceipt, ...]:
        return tuple(sorted(self._receipts, key=lambda receipt: receipt.ordinal))

    @property
    def denials(self) -> tuple[FetchDenial, ...]:
        return tuple(sorted(self._denials, key=lambda denial: denial.ordinal))

    @property
    def bundle_entries(self) -> tuple[FetchPayload, ...]:
        return tuple(self._bundle[index] for index in sorted(self._bundle))

    def entry_receipt(self, source_url: str) -> FetchReceipt:
        """Prove one completed GET chain for the selected entry, without reopening the run."""
        source = canonical_url(source_url)
        # Denied subresources make a capture incomplete, not invalid; the entry chain
        # itself must still consist only of allowed responses.
        if not self.policy.admits_main(source):
            raise FetchDenied("entry_fetch_denied")
        roots = [
            receipt
            for receipt in self._receipts
            if receipt.parent_ordinal is None and receipt.url == source and receipt.method == "GET"
        ]
        if len(roots) != 1:
            raise FetchDenied("entry_fetch_ambiguous" if roots else "entry_fetch_missing")
        current = roots[0]
        for _ in range(self.limits.redirects + 1):
            children = [
                receipt for receipt in self._receipts if receipt.parent_ordinal == current.ordinal
            ]
            if current.status == 200:
                if children:
                    raise FetchDenied("entry_fetch_ambiguous")
                return current
            if current.status not in _REDIRECTS or len(children) != 1:
                raise FetchDenied("entry_fetch_incomplete")
            child = children[0]
            if child.ordinal <= current.ordinal or child.method != "GET":
                raise FetchDenied("entry_fetch_incomplete")
            current = child
        raise FetchDenied("entry_fetch_incomplete")

    def _next_ordinal(self) -> int:
        ordinal = self._ordinal
        self._ordinal += 1
        return ordinal

    def _deny(self, ordinal: int, parent: int | None, url: str, code: str) -> None:
        if code in RUN_FATAL_FETCH_CODES:
            self.close()
        digest_input = (
            url.encode("utf-8", errors="surrogatepass")
            if isinstance(url, str)
            else b"invalid-url-type"
        )
        self._denials.append(
            FetchDenial(
                ordinal, parent, hashlib.sha256(digest_input).hexdigest(), code, time.time()
            )
        )

    def close(self) -> None:
        self._closed = True

    def _live(self) -> None:
        if self._closed:
            raise FetchDenied("run_closed")
        try:
            self.policy_source.check(self.policy.revision, self.policy.sha256)
        except FetchDenied:
            self.close()
            raise

    def manifest_bytes(self) -> bytes:
        return json.dumps(
            {
                "policy_revision": self.policy.revision,
                "policy_sha256": self.policy.sha256,
                "requests": [asdict(receipt) for receipt in self.receipts],
                "denials": [asdict(denial) for denial in self.denials],
            },
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode()

    def archive_bytes(self) -> bytes:
        if not self.bundle:
            raise FetchDenied("bundle_not_requested")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("manifest.json", self.manifest_bytes())
            for index, payload in sorted(self._bundle.items()):
                archive.writestr(f"response-{index:03d}.body", payload.body)
        if output.tell() > 64 * _MIB:
            raise FetchDenied("archive_byte_limit")
        return output.getvalue()

    async def fetch(self, url: str, method: str = "GET") -> FetchPayload:
        ordinal = self._next_ordinal()
        parent: int | None = None
        current_url = url
        active = False
        try:
            self._live()
            if self._active >= self.limits.concurrency:
                raise FetchDenied("run_concurrency_limit")
            self._active += 1
            active = True
            if not isinstance(method, str) or method not in {"GET", "HEAD"}:
                raise FetchDenied("method_denied")
            for redirects in range(self.limits.redirects + 1):
                self._live()
                normalized = canonical_url(current_url)
                if not self.policy.allows(normalized, method):
                    raise FetchDenied("url_denied")
                if self._requests >= self.limits.requests:
                    raise FetchDenied("request_limit")
                self._requests += 1
                async with asyncio.timeout(self.limits.timeout_seconds):
                    payload, location = await self._request(normalized, method, ordinal, parent)
                if payload.status not in _REDIRECTS:
                    return payload
                if redirects == self.limits.redirects:
                    raise FetchDenied("redirect_limit")
                if not location:
                    raise FetchDenied("redirect_invalid")
                parent = ordinal
                ordinal = self._next_ordinal()
                current_url = location
                if re.search(r"[\x00-\x20\x7f\\]", current_url):
                    raise FetchDenied("url_invalid")
                current_url = urljoin(normalized, current_url)
            raise FetchDenied("redirect_limit")
        except FetchDenied as error:
            self._deny(ordinal, parent, current_url, error.code)
            raise
        except TimeoutError:
            self._deny(ordinal, parent, current_url, "fetch_timeout")
            raise FetchDenied("fetch_timeout") from None
        except (httpx.HTTPError, OSError, ValueError):
            self._deny(ordinal, parent, current_url, "fetch_transport_failed")
            raise FetchDenied("fetch_transport_failed") from None
        finally:
            if active:
                self._active -= 1

    async def _request(
        self, url: str, method: str, ordinal: int, parent: int | None
    ) -> tuple[FetchPayload, str | None]:
        target = httpx.URL(url)
        answers = await self.resolver(target.host)
        if not answers or any(
            not _public_address(answer)
            and not (self.policy.open_public_https and _dev_proxy_address(answer))
            for answer in answers
        ):
            raise FetchDenied("dns_denied")
        origin = str(target.copy_with(path="/", query=None)).rstrip("/")
        # The per-organization minute window protects named vendors; an open development
        # policy has no such list, so only the per-origin connection leases apply.
        async with self.quota.acquire(
            self.org_id, origin, org_window=not self.policy.open_public_https
        ):
            self._live()
            # Numeric URL pins the real socket. Host and SNI preserve hostname verification.
            # Each request gets a fresh transport: no pool reuse or implicit DNS retry.
            transport = self.transport or httpx.AsyncHTTPTransport(
                verify=True,
                trust_env=False,
                retries=0,
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
            )
            request = httpx.Request(
                method,
                target.copy_with(host=answers[0]),
                headers={
                    "Host": target.netloc.decode("ascii"),
                    "Accept": "*/*",
                    "Accept-Encoding": "gzip, deflate",
                    "User-Agent": "AI-Bid-Capture/1",
                },
                extensions={
                    "sni_hostname": target.host,
                    "timeout": {
                        kind: self.limits.timeout_seconds
                        for kind in ("connect", "read", "write", "pool")
                    },
                },
            )
            response: httpx.Response | None = None
            started = time.monotonic()
            try:
                response = await transport.handle_async_request(request)
                headers = {key: value for key, value in response.headers.items() if key in _HEADERS}
                if any(
                    len(value) > 4096 or re.search(r"[\x00-\x1f\x7f]", value)
                    for value in headers.values()
                ):
                    raise FetchDenied("response_headers_invalid")
                if response.status_code not in _REDIRECTS and not 200 <= response.status_code < 300:
                    raise FetchDenied("http_status_denied")
                if response.status_code == 206:
                    raise FetchDenied("partial_response_denied")
                content_type = headers.get("content-type", "").split(";", 1)[0].lower().strip()
                if response.status_code not in _REDIRECTS and content_type not in _CONTENT_TYPES:
                    raise FetchDenied("content_type_denied")
                bound = (
                    min(self.limits.response_bytes, self.limits.html_bytes)
                    if content_type in {"text/html", "application/xhtml+xml"}
                    else self.limits.response_bytes
                )
                if "content-length" in headers:
                    try:
                        declared = int(headers["content-length"])
                    except ValueError:
                        raise FetchDenied("content_length_invalid") from None
                    if declared < 0 or declared > bound:
                        raise FetchDenied("byte_limit")
                body, wire_hash, wire_bytes = await self._body(response, bound, method)
                if content_type in {"text/html", "application/xhtml+xml"} and re.search(
                    rb"type\s*=\s*['\"]?password\b|(?:g-recaptcha|h-captcha|cf-chl-widget)",
                    body,
                    re.I,
                ):
                    raise FetchDenied("authentication_page_denied")
                self._live()
                headers["content-length"] = str(len(body))
                payload = FetchPayload(url, response.status_code, headers, body)
                self._receipts.append(
                    FetchReceipt(
                        ordinal,
                        parent,
                        f"response-{ordinal:03d}.body" if self.bundle else None,
                        url,
                        hashlib.sha256(url.encode()).hexdigest(),
                        method,
                        response.status_code,
                        headers,
                        hashlib.sha256(body).hexdigest(),
                        wire_hash,
                        len(body),
                        wire_bytes,
                        time.time(),
                        int((time.monotonic() - started) * 1000),
                        self.policy.revision,
                        self.policy.sha256,
                    )
                )
                if len(self.manifest_bytes()) > 4 * _MIB:
                    raise FetchDenied("manifest_byte_limit")
                if self.bundle:
                    self._bundle[ordinal] = payload
                return payload, response.headers.get("location")
            finally:
                if response is not None:
                    await response.aclose()
                if self.transport is None:
                    await transport.aclose()

    async def _body(
        self, response: httpx.Response, bound: int, method: str
    ) -> tuple[bytes, str, int]:
        encoding = response.headers.get("content-encoding", "identity").lower().strip()
        if encoding not in {"identity", "gzip", "deflate"}:
            raise FetchDenied("content_encoding_denied")
        decoder = (
            zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS)
            if encoding != "identity"
            else None
        )
        output = bytearray()
        wire_hash = hashlib.sha256()
        wire_bytes = 0
        if not isinstance(response.stream, httpx.AsyncByteStream):
            raise FetchDenied("response_stream_invalid")
        try:
            async for chunk in response.stream:
                if self._closed:
                    raise FetchDenied("run_closed")
                wire_bytes += len(chunk)
                self._wire_bytes += len(chunk)
                if wire_bytes > bound or self._wire_bytes > self.limits.run_bytes:
                    raise FetchDenied("byte_limit")
                wire_hash.update(chunk)
                remaining = min(bound - len(output), self.limits.run_bytes - self._body_bytes)
                decoded = decoder.decompress(chunk, remaining + 1) if decoder else chunk
                self._body_bytes += len(decoded)
                output.extend(decoded)
                if len(output) > bound or self._body_bytes > self.limits.run_bytes:
                    raise FetchDenied("byte_limit")
                if decoder and (decoder.unused_data or decoder.unconsumed_tail):
                    raise FetchDenied("content_encoding_invalid")
            if decoder and method != "HEAD" and not decoder.eof:
                raise FetchDenied("content_encoding_invalid")
            if method == "HEAD" and output:
                raise FetchDenied("head_body_denied")
        except zlib.error:
            raise FetchDenied("content_encoding_invalid") from None
        return bytes(output), wire_hash.hexdigest(), wire_bytes
