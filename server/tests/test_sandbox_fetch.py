"""Failure gates, written before the broker implementation.

Failures: ambiguous/credential URLs; unlisted methods/path/query; mixed or private DNS;
rebinding; redirect escape/loop; changed/revoked policy; response-cookie propagation;
compressed bombs/oversized streams; rate/concurrency resets; unsafe receipt leakage;
transport/TLS failures; revoked runs and unsuccessful HTTP responses.
All upstream responses are synthetic MockTransport streams, never vendor calls.
"""

import asyncio
import gzip
import hashlib
import io
import json
import zipfile
from uuid import uuid4

import httpx
import pytest
from app.providers.sandbox_fetch import (
    FetchBroker,
    FetchDenied,
    FetchLimits,
    PolicySource,
    SQLiteFetchQuota,
    canonical_url,
)

MAIN = "https://vendor.example/model"


class Chunks(httpx.AsyncByteStream):
    def __init__(self, data):
        self.data = data
        self.closed = False

    async def __aiter__(self):
        for chunk in self.data:
            yield chunk

    async def aclose(self):
        self.closed = True


async def public_dns(host):
    return ("93.184.216.34",)


def policy_file(tmp_path, urls=None):
    path = tmp_path / "policy.json"
    value = {
        "policies": [
            {
                "revision": "vendor-v1",
                "main_urls": [MAIN],
                "rules": [{"url": url, "methods": ["GET", "HEAD"]} for url in (urls or [MAIN])],
            }
        ],
        "revoked_revisions": [],
    }
    path.write_text(json.dumps(value))
    return path


def broker(tmp_path, handler, *, urls=None, resolver=public_dns, limits=None, bundle=False):
    return FetchBroker(
        PolicySource(policy_file(tmp_path, urls)),
        "vendor-v1",
        uuid4(),
        quota=SQLiteFetchQuota(tmp_path / "quota.sqlite3"),
        transport=httpx.MockTransport(handler),
        resolver=resolver,
        limits=limits or FetchLimits(),
        bundle=bundle,
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://vendor.example/model",
        "https://a:b@vendor.example/model",
        MAIN + "#fragment",
        MAIN + "\n",
        "https://127.1/",
        "https://0x7f000001/",
        "https://0177.0.0.1/",
        "https://vendor.example/%2fsecret",
        "https://vendor.example/a/../model",
        MAIN + "?token=canary",
        MAIN + "?%61pi_key=canary",
        "https://vendor.example\\@evil.example/model",
        "https://vendor.example/%252fsecret",
        "https://vendor.example/model?x=%0dfoo",
    ],
)
def test_reject_ambiguous_and_credential_urls(url):
    with pytest.raises(FetchDenied):
        canonical_url(url)


def test_canonicalization_is_stable():
    assert canonical_url("https://VENDOR.example:443/model") == MAIN
    assert canonical_url("https://例子.测试/") == "https://xn--fsqu00a.xn--0zwm56d/"


async def test_main_subresources_exact_policy_and_pinned_socket(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "set-cookie": "secret=x"},
            stream=Chunks([b"<html>public</html>"]),
        )

    fetch = broker(tmp_path, handler, bundle=True)
    result = await fetch.fetch(MAIN)
    assert result.url == MAIN and result.status == 200
    assert result.body == b"<html>public</html>"
    assert "set-cookie" not in result.headers
    assert requests[0].url.host == "93.184.216.34"
    assert requests[0].headers["host"] == "vendor.example"
    assert requests[0].extensions["sni_hostname"] == "vendor.example"
    assert set(requests[0].headers) == {"host", "accept", "accept-encoding", "user-agent"}
    assert fetch.receipts[0].sha256 == hashlib.sha256(result.body).hexdigest()
    with zipfile.ZipFile(io.BytesIO(fetch.archive_bytes())) as archive:
        assert archive.namelist() == ["manifest.json", "response-000.body"]
        assert archive.read("response-000.body") == result.body
    for url, method in [(MAIN + "?exfil=canary", "GET"), (MAIN + "/secret", "GET"), (MAIN, "POST")]:
        with pytest.raises(FetchDenied):
            await fetch.fetch(url, method)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "answers",
    [
        ("127.0.0.1",),
        ("169.254.169.254",),
        ("100.64.0.1",),
        ("fc00::1",),
        ("::ffff:93.184.216.34",),
        ("224.0.0.1",),
        ("93.184.216.34", "10.0.0.1"),
        (),
    ],
)
async def test_all_dns_answers_must_be_public(tmp_path, answers):
    calls = []

    async def resolve(host):
        return answers

    fetch = broker(tmp_path, lambda request: calls.append(request), resolver=resolve)
    with pytest.raises(FetchDenied, match="dns_denied"):
        await fetch.fetch(MAIN)
    assert calls == []


async def test_rebinding_rechecks_each_request(tmp_path):
    answers = iter([("93.184.216.34",), ("127.0.0.1",)])
    calls = []

    async def resolve(host):
        return next(answers)

    def handler(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type": "text/html"}, stream=Chunks([b"ok"]))

    fetch = broker(tmp_path, handler, resolver=resolve)
    await fetch.fetch(MAIN)
    with pytest.raises(FetchDenied, match="dns_denied"):
        await fetch.fetch(MAIN)
    assert len(calls) == 1


async def test_redirect_exact_allowlist_and_receipts(tmp_path):
    next_url = "https://cdn.example/assets/main.css?v=1"

    def handler(request):
        if request.headers["host"] == "vendor.example":
            return httpx.Response(302, headers={"location": next_url}, stream=Chunks([]))
        return httpx.Response(
            200, headers={"content-type": "text/css"}, stream=Chunks([b"body{} "])
        )

    fetch = broker(tmp_path, handler, urls=[MAIN, next_url])
    assert (await fetch.fetch(MAIN)).url == next_url
    assert len(fetch.receipts) == 2
    fetch2 = broker(tmp_path, handler)
    with pytest.raises(FetchDenied, match="url_denied"):
        await fetch2.fetch(MAIN)


async def test_redirect_limit_and_downgrade(tmp_path):
    fetch = broker(
        tmp_path, lambda r: httpx.Response(302, headers={"location": MAIN}, stream=Chunks([]))
    )
    with pytest.raises(FetchDenied, match="redirect_limit"):
        await fetch.fetch(MAIN)
    assert len(fetch.receipts) == 6
    fetch = broker(
        tmp_path,
        lambda r: httpx.Response(
            302, headers={"location": "http://vendor.example/model"}, stream=Chunks([])
        ),
    )
    with pytest.raises(FetchDenied):
        await fetch.fetch(MAIN)


@pytest.mark.parametrize("mutation", ["revoke", "change", "remove", "malformed"])
async def test_policy_reload_rejects_revocation_and_mutation(tmp_path, mutation):
    fetch = broker(
        tmp_path,
        lambda r: httpx.Response(
            200, headers={"content-type": "text/html"}, stream=Chunks([b"ok"])
        ),
    )
    path = tmp_path / "policy.json"
    value = json.loads(path.read_text())
    if mutation == "revoke":
        value["revoked_revisions"] = ["vendor-v1"]
    elif mutation == "change":
        value["policies"][0]["rules"].append({"url": MAIN + "/x", "methods": ["GET"]})
    elif mutation == "remove":
        value["policies"] = []
    path.write_text("not-json" if mutation == "malformed" else json.dumps(value))
    with pytest.raises(FetchDenied, match="policy_"):
        await fetch.fetch(MAIN)


@pytest.mark.parametrize(
    "encoding,body",
    [("gzip", gzip.compress(b"a" * 2000, mtime=0)), ("br", b"anything"), ("gzip", b"broken")],
)
async def test_encoding_and_decompression_bomb(tmp_path, encoding, body):
    stream = Chunks([body])
    fetch = broker(
        tmp_path,
        lambda r: httpx.Response(
            200, headers={"content-type": "text/html", "content-encoding": encoding}, stream=stream
        ),
        limits=FetchLimits(response_bytes=512, html_bytes=512, run_bytes=1024),
    )
    with pytest.raises(FetchDenied):
        await fetch.fetch(MAIN)
    assert stream.closed


async def test_valid_gzip_and_bounded_raw_stream(tmp_path):
    raw = b"hello public source"
    fetch = broker(
        tmp_path,
        lambda r: httpx.Response(
            200,
            headers={"content-type": "text/html", "content-encoding": "gzip"},
            stream=Chunks([gzip.compress(raw)]),
        ),
    )
    result = await fetch.fetch(MAIN)
    assert result.body == raw and "content-encoding" not in result.headers
    stream = Chunks([b"a" * 513])
    fetch = broker(
        tmp_path,
        lambda r: httpx.Response(200, headers={"content-type": "text/html"}, stream=stream),
        limits=FetchLimits(response_bytes=512, html_bytes=512, run_bytes=1024),
    )
    with pytest.raises(FetchDenied, match="byte_limit"):
        await fetch.fetch(MAIN)
    assert stream.closed


@pytest.mark.parametrize(
    "headers,status",
    [
        ({"content-type": "text/html", "content-length": "999999999"}, 200),
        ({"content-type": "application/octet-stream"}, 200),
        ({"content-type": "text/html"}, 403),
        ({"content-type": "text/html"}, 206),
    ],
)
async def test_invalid_size_type_or_response_status(tmp_path, headers, status):
    fetch = broker(
        tmp_path, lambda r: httpx.Response(status, headers=headers, stream=Chunks([b"bad"]))
    )
    with pytest.raises(FetchDenied):
        await fetch.fetch(MAIN)


async def test_persistent_org_quota_and_origin_leases(tmp_path):
    path = tmp_path / "quota.sqlite3"
    org = uuid4()
    for _ in range(60):
        async with SQLiteFetchQuota(path).acquire(org, "https://vendor.example"):
            pass
    with pytest.raises(FetchDenied, match="org_rate_limit"):
        async with SQLiteFetchQuota(path).acquire(org, "https://vendor.example"):
            pass
    # An open policy keeps an organization window, at its own configured limit.
    open_quota = SQLiteFetchQuota(path, open_requests_per_minute=62)
    for _ in range(2):
        async with open_quota.acquire(org, "https://vendor.example", open_egress=True):
            pass
    with pytest.raises(FetchDenied, match="org_rate_limit"):
        async with open_quota.acquire(org, "https://vendor.example", open_egress=True):
            pass
    with pytest.raises(ValueError):
        SQLiteFetchQuota(path, open_requests_per_minute=0)
    impatient = SQLiteFetchQuota(path, origin_wait_seconds=0.2)
    async with SQLiteFetchQuota(path).acquire(uuid4(), "https://vendor.example"):
        async with SQLiteFetchQuota(path).acquire(uuid4(), "https://vendor.example"):
            with pytest.raises(FetchDenied, match="origin_concurrency_limit"):
                async with impatient.acquire(uuid4(), "https://vendor.example"):
                    pass

    # A third request for a full origin waits for a released lease instead of failing.
    order = []

    async def holder():
        async with SQLiteFetchQuota(path).acquire(uuid4(), "https://other.example"):
            order.append("held")
            await asyncio.sleep(0.3)
        order.append("released")

    async def waiter():
        await asyncio.sleep(0.05)
        async with SQLiteFetchQuota(path).acquire(uuid4(), "https://other.example"):
            order.append("acquired")

    async with SQLiteFetchQuota(path).acquire(uuid4(), "https://other.example"):
        await asyncio.gather(holder(), waiter())
    assert order == ["held", "released", "acquired"]


async def test_transport_errors_safe_close_and_request_limit(tmp_path):
    def handler(request):
        raise httpx.ConnectError("unsafe https://vendor.example/?secret=canary")

    fetch = broker(tmp_path, handler)
    with pytest.raises(FetchDenied) as error:
        await fetch.fetch(MAIN)
    assert "canary" not in str(error.value)
    assert "canary" not in fetch.manifest_bytes().decode()
    fetch.close()
    with pytest.raises(FetchDenied, match="run_closed"):
        await fetch.fetch(MAIN)
    fetch = broker(
        tmp_path,
        lambda r: httpx.Response(
            200, headers={"content-type": "text/html"}, stream=Chunks([b"ok"])
        ),
        limits=FetchLimits(requests=1),
    )
    await fetch.fetch(MAIN)
    with pytest.raises(FetchDenied, match="request_limit"):
        await fetch.fetch(MAIN)


async def test_concurrency_limit_and_timeout(tmp_path):
    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200, headers={"content-type": "text/html"}, stream=Chunks([b"ok"]))

    fetch = broker(tmp_path, handler, limits=FetchLimits(timeout_seconds=0.01))
    with pytest.raises(FetchDenied, match="fetch_timeout"):
        await fetch.fetch(MAIN)


def test_operator_policy_selection_and_duplicate_revision(tmp_path):
    source = PolicySource(policy_file(tmp_path, [MAIN, "https://vendor.example/asset.css"]))
    assert source.select(MAIN).revision == "vendor-v1"
    with pytest.raises(FetchDenied):
        source.select("https://vendor.example/asset.css")
    value = json.loads(source.path.read_text())
    value["policies"] *= 2
    source.path.write_text(json.dumps(value))
    with pytest.raises(FetchDenied, match="policy_invalid"):
        source.select(MAIN)


def open_source(tmp_path):
    path = tmp_path / "open-policy.json"
    path.write_text(
        json.dumps(
            {
                "policies": [{"revision": "dev-open-v1", "open_public_https": True}],
                "revoked_revisions": [],
            }
        )
    )
    return PolicySource(path)


def open_broker(tmp_path, handler, *, resolver=public_dns):
    source = open_source(tmp_path)
    return source, FetchBroker(
        source,
        "dev-open-v1",
        uuid4(),
        quota=SQLiteFetchQuota(tmp_path / "quota.sqlite3"),
        transport=httpx.MockTransport(handler),
        resolver=resolver,
        bundle=True,
    )


async def test_open_development_policy_requires_the_node_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("BID_SANDBOX_DEV_OPEN_EGRESS", raising=False)
    source = open_source(tmp_path)
    with pytest.raises(FetchDenied) as refused:
        source.select("https://any-vendor.example/page")
    assert refused.value.code == "policy_invalid"
    monkeypatch.setenv("BID_SANDBOX_DEV_OPEN_EGRESS", "0")
    with pytest.raises(FetchDenied):
        source.check("dev-open-v1")


async def test_open_development_policy_keeps_address_and_url_gates(tmp_path, monkeypatch):
    monkeypatch.setenv("BID_SANDBOX_DEV_OPEN_EGRESS", "1")

    def handler(request):
        return httpx.Response(
            200, headers={"content-type": "text/html"}, stream=Chunks([b"<html>public</html>"])
        )

    source, fetch = open_broker(tmp_path, handler)
    page = "https://any-vendor.example/page"
    assert source.select(page).open_public_https
    result = await fetch.fetch(page)
    assert result.status == 200 and fetch.entry_receipt(page).status == 200
    other = await fetch.fetch("https://cdn.other.example/app.css")
    assert other.status == 200
    for url in ("http://any-vendor.example/page", page + "?token=canary"):
        with pytest.raises(FetchDenied):
            await fetch.fetch(url)

    async def private_dns(host):
        return ("10.0.0.8",)

    (tmp_path / "private").mkdir()
    _, private = open_broker(tmp_path / "private", handler, resolver=private_dns)
    with pytest.raises(FetchDenied):
        await private.fetch(page)

    async def fake_ip_dns(host):
        return ("198.18.2.77",)

    (tmp_path / "proxy").mkdir()
    _, proxied = open_broker(tmp_path / "proxy", handler, resolver=fake_ip_dns)
    assert (await proxied.fetch(page)).status == 200
    exact = broker(tmp_path / "proxy", handler, resolver=fake_ip_dns)
    with pytest.raises(FetchDenied) as denied:
        await exact.fetch(MAIN)
    assert denied.value.code == "dns_denied"

    exact = {"revision": "x", "open_public_https": True, "main_urls": [MAIN]}
    from app.providers.sandbox_fetch import FetchPolicy
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FetchPolicy.model_validate({**exact, "rules": [{"url": MAIN}]})
    with pytest.raises(ValidationError):
        FetchPolicy.model_validate({"revision": "x"})


async def test_a_denied_resource_leaves_the_run_open_but_revocation_closes_it(tmp_path):
    missing = "https://vendor.example/missing.css"

    def handler(request):
        if request.url.path == "/missing.css":
            return httpx.Response(404)
        return httpx.Response(
            200, headers={"content-type": "text/html"}, stream=Chunks([b"<html>public</html>"])
        )

    fetch = broker(tmp_path, handler, urls=[MAIN, missing])
    with pytest.raises(FetchDenied, match="http_status_denied"):
        await fetch.fetch(missing)
    assert (await fetch.fetch(MAIN)).status == 200
    assert fetch.entry_receipt(MAIN).status == 200
    assert [denial.code for denial in fetch.denials] == ["http_status_denied"]

    path = tmp_path / "policy.json"
    value = json.loads(path.read_text())
    value["revoked_revisions"] = ["vendor-v1"]
    path.write_text(json.dumps(value))
    with pytest.raises(FetchDenied, match="policy_revoked"):
        await fetch.fetch(MAIN)
    with pytest.raises(FetchDenied, match="run_closed"):
        await fetch.fetch(MAIN)


def test_open_fetch_limit_comes_from_operator_environment(tmp_path, monkeypatch):
    from app.providers.sandbox_fetch import quota_from_env

    monkeypatch.setenv("BID_SANDBOX_OPEN_FETCH_PER_MINUTE", "250")
    assert quota_from_env(tmp_path / "q.sqlite3").open_requests_per_minute == 250
    monkeypatch.delenv("BID_SANDBOX_OPEN_FETCH_PER_MINUTE")
    assert quota_from_env(tmp_path / "q.sqlite3").open_requests_per_minute == 600
    for bad in ("0", "-5", "many", ""):
        monkeypatch.setenv("BID_SANDBOX_OPEN_FETCH_PER_MINUTE", bad)
        with pytest.raises(FetchDenied, match="policy_invalid"):
            quota_from_env(tmp_path / "q.sqlite3")
