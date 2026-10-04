"""Proxy attack acceptance (sandbox contract item 5) with real TLS origins.

The production FetchBroker and SocketBrowserProvider run unchanged against synthetic
HTTPS servers on loopback. Two constructor seams stand in for the network: the
resolver returns the forged DNS answers each case needs, and the transport trusts a
throwaway test CA and routes each pinned IP to the loopback server playing that
address. Every server counts the HTTP requests it actually receives, so a violation
shows up as a request at a target that must stay untouched. Part two plays a forged
supervisor and sends crafted fetch frames to the host relay.

Not covered here: kernel-level egress filtering on the proxy node, real DNS servers,
and the gVisor network namespace; those belong to the runtime isolation acceptance.

    PYTHONPATH=server .venv/bin/python scripts/sandbox_proxy_acceptance.py

Writes results.json under data/work/sandbox-verification/proxy-acceptance/ (or --output)
and exits 1 when any case fails.
"""

import argparse
import asyncio
import datetime
import gzip
import hashlib
import json
import os
import ssl
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from app.providers.browser import SocketBrowserProvider
from app.providers.sandbox_fetch import (
    FetchBroker,
    FetchDenied,
    PolicySource,
    SQLiteFetchQuota,
)
from app.providers.sandbox_runtime import (
    RunDescriptor,
    RuntimeConfig,
    SandboxFailure,
    read_frame,
)
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[1]
MIB = 1024 * 1024
PAGE = b"<!doctype html><html><body><h1>SYNTHETIC vendor page</h1></body></html>"
# Pinned addresses the resolver hands out and the transport routes to loopback servers.
VENDOR, OTHER, REBIND, MIXED, TLS_BAD = (
    "93.184.216.34",
    "93.184.216.35",
    "93.184.216.36",
    "93.184.216.37",
    "93.184.216.38",
)
INTERNAL, METADATA = "10.0.0.7", "169.254.169.254"
V = "https://vendor.example"
APPROVED = [
    f"{V}/model",
    f"{V}/asset.css",
    f"{V}/cookie",
    f"{V}/go-other",
    f"{V}/go-http",
    f"{V}/loop",
    f"{V}/bomb",
    f"{V}/chunk",
    "https://rebind.example/model",
    "https://mixed.example/model",
    "https://internal.example/model",
    "https://metadata.example/latest",
    "https://tls.example/model",
]


def certificate(name: str, issuer_key, issuer_name, *, ca: bool = False):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer_name or subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=2))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if ca:
        builder = builder.add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False),
            critical=True,
        )
    else:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False
        )
    signed = builder.sign(issuer_key or key, hashes.SHA256())
    return key, signed


def pem(key, cert) -> tuple[bytes, bytes]:
    return (
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        cert.public_bytes(serialization.Encoding.PEM),
    )


@dataclass
class Origin:
    """One synthetic HTTPS server standing in for one public or forbidden address."""

    name: str
    hostname: str
    requests: list[dict[str, Any]] = field(default_factory=list)
    port: int = 0
    server: asyncio.Server | None = None

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError, OSError):
            writer.close()
            return
        lines = head.decode("latin-1").split("\r\n")
        method, path, _ = lines[0].split(" ", 2)
        headers = {
            key.strip().lower(): value.strip()
            for key, value in (line.split(":", 1) for line in lines[1:] if ":" in line)
        }
        self.requests.append({"method": method, "path": path, "headers": headers})
        try:
            await self.respond(writer, method, path)
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()
            with suppress(OSError, ssl.SSLError):
                await writer.wait_closed()

    async def respond(self, writer: asyncio.StreamWriter, method: str, path: str):
        def send(status: str, headers: dict[str, str], body: bytes = b""):
            lines = [f"HTTP/1.1 {status}", f"Content-Length: {len(body)}", "Connection: close"]
            lines += [f"{key}: {value}" for key, value in headers.items()]
            writer.write(("\r\n".join(lines) + "\r\n\r\n").encode() + body)

        html = {"Content-Type": "text/html"}
        if path == "/model" or path == "/landing" or path == "/latest":
            send("200 OK", html, PAGE)
        elif path == "/asset.css":
            send("200 OK", {"Content-Type": "text/css"}, b"body{color:#123}")
        elif path == "/cookie":
            send("200 OK", {**html, "Set-Cookie": "session=synthetic; HttpOnly"}, PAGE)
        elif path == "/go-other":
            send("302 Found", {"Location": "https://other.example/landing"})
        elif path == "/go-http":
            send("302 Found", {"Location": "http://vendor.example/model"})
        elif path == "/loop":
            send("302 Found", {"Location": "/loop"})
        elif path == "/bomb":
            # 64 MiB of zeros in about 64 KiB on the wire, above every decoded limit.
            body = gzip.compress(b"\0" * (64 * MIB), compresslevel=9)
            send("200 OK", {"Content-Type": "text/css", "Content-Encoding": "gzip"}, body)
        elif path == "/chunk":
            # One chunk that claims 2 GiB, streamed until the client gives up or 48 MiB.
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/css\r\n"
                b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n7FFFFFFF\r\n"
            )
            block = b"a" * MIB
            for _ in range(48):
                writer.write(block)
                await writer.drain()
        else:
            send("404 Not Found", {"Content-Type": "text/plain"}, b"missing")
        await writer.drain()


class RoutedTransport(httpx.AsyncBaseTransport):
    """Real TLS over loopback: the pinned IP chooses the server, Host/SNI stay intact."""

    def __init__(self, routes: dict[str, int], trust: ssl.SSLContext):
        self.routes = routes
        self.trust = trust
        self.pinned: list[dict[str, str]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        address = request.url.host
        self.pinned.append(
            {"ip": address, "host": request.headers.get("host", ""), "path": request.url.path}
        )
        if address not in self.routes:
            raise httpx.ConnectError("no route to synthetic address")
        routed = httpx.Request(
            request.method,
            request.url.copy_with(host="127.0.0.1", port=self.routes[address]),
            headers=request.headers,
            extensions=request.extensions,
        )
        # A fresh single-connection transport per request, like the production default.
        inner = httpx.AsyncHTTPTransport(
            verify=self.trust,
            trust_env=False,
            retries=0,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        )
        response = await inner.handle_async_request(routed)

        class Closing(httpx.AsyncByteStream):
            async def __aiter__(self):
                async for chunk in response.stream:  # type: ignore[union-attr]
                    yield chunk

            async def aclose(self):
                await response.aclose()
                await inner.aclose()

        return httpx.Response(
            response.status_code,
            headers=response.headers,
            stream=Closing(),
            extensions=response.extensions,
        )


class Rebinding:
    def __init__(self):
        self.calls: dict[str, int] = {}

    async def __call__(self, host: str) -> tuple[str, ...]:
        count = self.calls[host] = self.calls.get(host, 0) + 1
        table = {
            "vendor.example": (VENDOR,),
            "other.example": (OTHER,),
            "tls.example": (TLS_BAD,),
            "internal.example": (INTERNAL,),
            "metadata.example": (METADATA,),
            "mixed.example": (MIXED, "fd00::7"),
            # Public on first lookup, private afterwards.
            "rebind.example": (REBIND,) if count == 1 else (INTERNAL,),
        }
        return table.get(host, ())


class Harness:
    def __init__(self, work: Path):
        self.work = work
        ca_key, ca = certificate("SYNTHETIC acceptance CA", None, None, ca=True)
        rogue_key, rogue = certificate("SYNTHETIC untrusted CA", None, None, ca=True)
        self.trust = ssl.create_default_context(
            cadata=ca.public_bytes(serialization.Encoding.PEM).decode()
        )
        self.origins = {
            VENDOR: Origin("vendor", "vendor.example"),
            OTHER: Origin("other", "other.example"),
            REBIND: Origin("rebind", "rebind.example"),
            MIXED: Origin("mixed", "mixed.example"),
            TLS_BAD: Origin("tls_untrusted", "tls.example"),
            INTERNAL: Origin("internal", "internal.example"),
            METADATA: Origin("metadata", "metadata.example"),
        }
        self.issuers = {address: (ca_key, ca) for address in self.origins}
        self.issuers[TLS_BAD] = (rogue_key, rogue)
        self.policy = work / "policy.json"
        self.policy.write_text(
            json.dumps(
                {
                    "policies": [
                        {
                            "revision": "proxy-acceptance-v1",
                            "main_urls": [f"{V}/model"],
                            "rules": [{"url": url, "methods": ["GET", "HEAD"]} for url in APPROVED],
                        }
                    ],
                    "revoked_revisions": [],
                }
            )
        )
        self.transport = RoutedTransport({}, self.trust)

    async def start(self):
        for address, origin in self.origins.items():
            issuer_key, issuer = self.issuers[address]
            key, cert = certificate(origin.hostname, issuer_key, issuer.subject)
            key_pem, cert_pem = pem(key, cert)
            (self.work / f"{origin.name}.key").write_bytes(key_pem)
            (self.work / f"{origin.name}.crt").write_bytes(cert_pem)
            context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            context.load_cert_chain(
                self.work / f"{origin.name}.crt", self.work / f"{origin.name}.key"
            )
            origin.server = await asyncio.start_server(origin.handle, "127.0.0.1", 0, ssl=context)
            origin.port = origin.server.sockets[0].getsockname()[1]
        self.transport.routes = {address: origin.port for address, origin in self.origins.items()}

    async def stop(self):
        for origin in self.origins.values():
            if origin.server is not None:
                origin.server.close()
                await origin.server.wait_closed()

    def counts(self) -> dict[str, int]:
        return {origin.name: len(origin.requests) for origin in self.origins.values()}

    def broker(self, resolver=None) -> FetchBroker:
        # A fresh organization per broker keeps the minute window out of the results.
        return FetchBroker(
            PolicySource(self.policy),
            "proxy-acceptance-v1",
            uuid4(),
            quota=SQLiteFetchQuota(self.work / "quota.sqlite3"),
            transport=self.transport,
            resolver=resolver or Rebinding(),
        )


async def fetch(broker: FetchBroker, url: str, method: str = "GET") -> dict[str, Any]:
    started = time.monotonic()
    try:
        payload = await broker.fetch(url, method=method)
        outcome = {"ok": True, "status": payload.status, "headers": sorted(payload.headers)}
    except FetchDenied as exc:
        outcome = {"ok": False, "code": exc.code}
    outcome["seconds"] = round(time.monotonic() - started, 3)
    return outcome


def delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {name: after[name] - before[name] for name in after if after[name] != before[name]}


async def broker_cases(h: Harness) -> list[dict[str, Any]]:
    results = []

    async def case(name, urls, expect_codes, expect_requests, *, method="GET", resolver=None):
        broker = h.broker(resolver)
        before, pinned = h.counts(), len(h.transport.pinned)
        outcomes = [await fetch(broker, url, method) for url in urls]
        broker.close()
        reached = delta(before, h.counts())
        codes = [item.get("code", item.get("status")) for item in outcomes]
        passed = codes == expect_codes and reached == expect_requests
        results.append(
            {
                "case": name,
                "passed": passed,
                "outcomes": outcomes,
                "expected": expect_codes,
                "requests_received": reached,
                "expected_requests": expect_requests,
                "pinned": h.transport.pinned[pinned:],
            }
        )
        return outcomes

    await case(
        "allowed page and asset", [f"{V}/model", f"{V}/asset.css"], [200, 200], {"vendor": 2}
    )
    await case(
        "malicious query and path",
        [
            f"{V}/model?token=x",
            f"{V}/model?a=1",
            f"{V}/model/../admin",
            f"{V}/%2e%2e/admin",
            f"{V}/model%2fadmin",
            f"{V}/model#frag",
        ],
        ["url_invalid", "url_denied", "url_invalid", "url_invalid", "url_invalid", "url_invalid"],
        {},
    )
    await case(
        "userinfo",
        ["https://user:pass@vendor.example/model", "https://user@vendor.example/model"],
        ["url_invalid", "url_invalid"],
        {},
    )
    await case(
        "encoded IP hosts",
        [
            "https://1572395042/model",
            "https://0x5db8d822/model",
            "https://0135.0270.0330.042/model",
            "https://93.184.216.34/model",
            "https://[::ffff:5db8:d822]/model",
        ],
        ["url_invalid", "url_invalid", "url_invalid", "url_denied", "url_invalid"],
        {},
    )
    await case(
        "internal and metadata targets",
        [
            "https://internal.example/model",
            "https://metadata.example/latest",
            "https://169.254.169.254/latest",
            "https://10.0.0.7/model",
        ],
        ["dns_denied", "dns_denied", "url_invalid", "url_invalid"],
        {},
    )
    await case("mixed A/AAAA answers", ["https://mixed.example/model"], ["dns_denied"], {})
    rebinding = Rebinding()
    await case(
        "DNS rebinding after a public answer",
        ["https://rebind.example/model", "https://rebind.example/model"],
        [200, "dns_denied"],
        {"rebind": 1},
        resolver=rebinding,
    )
    await case("cross-origin redirect", [f"{V}/go-other"], ["url_denied"], {"vendor": 1})
    await case("HTTPS downgrade redirect", [f"{V}/go-http"], ["url_invalid"], {"vendor": 1})
    await case("unbounded redirect", [f"{V}/loop"], ["redirect_limit"], {"vendor": 6})
    await case(
        "untrusted TLS certificate", ["https://tls.example/model"], ["fetch_transport_failed"], {}
    )
    await case("POST", [f"{V}/model"], ["method_denied"], {}, method="POST")
    await case("compression bomb", [f"{V}/bomb"], ["byte_limit"], {"vendor": 1})
    await case("oversized chunk", [f"{V}/chunk"], ["byte_limit"], {"vendor": 1})

    # Cookies: a Set-Cookie response is neither returned nor replayed on the next request.
    broker = h.broker()
    vendor = h.origins[VENDOR]
    start = len(vendor.requests)
    first = await fetch(broker, f"{V}/cookie")
    second = await fetch(broker, f"{V}/model")
    broker.close()
    sent = vendor.requests[start:]
    results.append(
        {
            "case": "cookies are dropped",
            "passed": first.get("status") == 200
            and "set-cookie" not in first.get("headers", [])
            and second.get("status") == 200
            and len(sent) == 2
            and all("cookie" not in item["headers"] for item in sent),
            "outcomes": [first, second],
            "request_headers": [sorted(item["headers"]) for item in sent],
        }
    )

    # Every connection went to the address the resolver validated for that request.
    validated = {"vendor.example": VENDOR, "rebind.example": REBIND}
    mismatched = [
        item
        for item in h.transport.pinned
        if item["host"] in validated and item["ip"] != validated[item["host"]]
    ]
    results.append(
        {
            "case": "connections use the validated address",
            "passed": not mismatched
            and all(item["ip"] in h.origins for item in h.transport.pinned),
            "connections": len(h.transport.pinned),
            "mismatched": mismatched,
        }
    )
    return results


class ForgedSupervisor:
    """Accepts one run and sends crafted fetch frames to the host relay."""

    def __init__(self, path: Path):
        self.path = path
        self.frames: list[tuple[dict[str, Any], bytes]] = []
        self.replies: list[dict[str, Any]] = []
        self.server: asyncio.Server | None = None

    async def handle(self, reader, writer):
        try:
            header, _ = await read_frame(reader, 16 * MIB)
            assert header.get("type") == "run"
            for frame, data in self.frames:
                writer.write(
                    json.dumps({**frame, "length": len(data)}, separators=(",", ":")).encode()
                    + b"\n"
                    + data
                )
                await writer.drain()
            while True:
                reply, _ = await asyncio.wait_for(read_frame(reader, 64 * MIB), 20)
                self.replies.append({k: v for k, v in reply.items() if k != "headers"})
                if "headers" in reply:
                    self.replies[-1]["header_names"] = sorted(reply["headers"])
                if len(self.replies) >= len(self.frames) or reply.get("type") == "error":
                    break
        except (SandboxFailure, TimeoutError, ConnectionError, OSError, AssertionError):
            pass
        finally:
            writer.close()

    async def start(self):
        self.server = await asyncio.start_unix_server(self.handle, path=str(self.path))

    async def stop(self):
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()


async def relay_cases(h: Harness, work: Path) -> list[dict[str, Any]]:
    results = []
    source = f"{V}/model"
    config = RuntimeConfig(
        enabled=True,
        # Relative: the run directory is the working directory, and the absolute path
        # would exceed the Unix socket length limit.
        socket_path=Path("control.sock"),
        accepted=True,
        image="synthetic/proxy-acceptance@sha256:" + "0" * 64,
        seccomp_sha256="0" * 64,
    )

    def frame(identifier, url, method="GET", **extra):
        return ({"type": "fetch", "id": identifier, "url": url, "method": method, **extra}, b"")

    scenarios = [
        ("approved frame", [frame(0, source)], ["fetch_result"], None, {"vendor": 1}),
        (
            "frame for an unlisted origin",
            [frame(0, "https://other.example/landing")],
            ["fetch_failed"],
            None,
            {},
        ),
        (
            "frame with an encoded IP",
            [frame(0, "https://1572395042/model")],
            ["fetch_failed"],
            None,
            {},
        ),
        ("frame with POST", [frame(0, source, "POST")], [], "fetch_method_denied", {}),
        (
            "frame with injected headers",
            [frame(0, source, headers={"Cookie": "a=b"})],
            [],
            "unexpected_fetch",
            {},
        ),
        ("frame with a request body", [(frame(0, source)[0], b"x=1")], [], "unexpected_fetch", {}),
        ("frame skipping an id", [frame(3, source)], [], "invalid_fetch", {}),
        (
            "frame with an oversized URL",
            [frame(0, source + "?" + "a" * 9000)],
            [],
            "invalid_fetch",
            {},
        ),
        (
            "Set-Cookie through the relay",
            [frame(0, f"{V}/cookie")],
            ["fetch_result"],
            None,
            {"vendor": 1},
        ),
    ]
    for name, frames, expected_replies, expected_failure, expected_requests in scenarios:
        supervisor = ForgedSupervisor(config.socket_path)
        supervisor.frames = frames
        config.socket_path.unlink(missing_ok=True)
        await supervisor.start()
        broker = h.broker()
        descriptor = RunDescriptor(
            org_id=uuid4(),
            job_id=uuid4(),
            attempt_id=uuid4(),
            input_sha256=hashlib.sha256(source.encode()).hexdigest(),
            purpose="vendor_capture",
        )
        before = h.counts()
        failure = None
        try:
            await SocketBrowserProvider(config).capture(descriptor, source, broker)
        except SandboxFailure as exc:
            failure = exc.code
        await supervisor.stop()
        reached = delta(before, h.counts())
        replies = [reply.get("type") for reply in supervisor.replies]
        cookie_free = all(
            "set-cookie" not in reply.get("header_names", []) for reply in supervisor.replies
        )
        passed = (
            replies == expected_replies
            and reached == expected_requests
            and cookie_free
            and (expected_failure is None or failure == expected_failure)
        )
        results.append(
            {
                "case": name,
                "passed": passed,
                "replies": supervisor.replies,
                "provider_failure": failure,
                "expected_failure": expected_failure,
                "requests_received": reached,
                "expected_requests": expected_requests,
            }
        )
    return results


async def run(work: Path) -> list[dict[str, Any]]:
    harness = Harness(work)
    await harness.start()
    try:
        cases = await broker_cases(harness)
        cases += await relay_cases(harness, work)
    finally:
        await harness.stop()
    # Every forbidden address stayed untouched across the whole run.
    untouched = {
        name: count
        for name, count in harness.counts().items()
        if name in {"other", "internal", "metadata", "mixed", "tls_untrusted"}
    }
    cases.append(
        {
            "case": "forbidden targets received zero requests",
            "passed": not any(untouched.values()),
            "requests_received": untouched,
        }
    )
    return cases


def main(output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    previous = Path.cwd()
    with tempfile.TemporaryDirectory(dir=output) as scratch:
        work = Path(scratch)
        work.chmod(0o700)
        os.chdir(work)
        try:
            cases = asyncio.run(run(work))
        finally:
            os.chdir(previous)
    report = {
        "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "passed": all(item["passed"] for item in cases),
        "cases": cases,
    }
    (output / "results.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    for item in cases:
        print(("PASS " if item["passed"] else "FAIL ") + item["case"])
    print(f"{sum(item['passed'] for item in cases)}/{len(cases)} passed; {output / 'results.json'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data/work/sandbox-verification/proxy-acceptance"
    )
    raise SystemExit(main(parser.parse_args().output))
