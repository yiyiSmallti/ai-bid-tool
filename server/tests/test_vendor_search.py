"""Vendor-source search through the real API, worker and database with a mock SearXNG.

Failure modes enumerated before implementation:
- tender text, requirement quotes or organization data must not reach the search service;
  only the selected revision's vendor and model may;
- a paid-style preview/submit split must bind the exact queries, selection and service;
- http, credential-bearing or malformed URLs must not become candidates, and duplicates
  across queries must merge;
- another organization must not read a search or adopt its candidates;
- adopting must create a new product revision and reselect it, and a stale adoption must
  be refused instead of overwriting a newer revision;
- an unavailable search service must fail the job with a retryable code, not store
  partial or empty results as a success.
"""

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from app.providers.base import ProviderFailure
from test_response_cards import create_tender  # pyright: ignore[reportMissingImports]
from test_vendor_screenshots import (  # pyright: ignore[reportMissingImports]
    PDF_URL,
    capture,
    select_product,
    vendor_client,
)

SEARCH_URL = "http://searxng.test"


def searxng(hits_by_query, sent, *, fail=False):
    def handler(request: httpx.Request) -> httpx.Response:
        query = parse_qs(urlsplit(str(request.url)).query)
        sent.append(query)
        if fail:
            return httpx.Response(502)
        return httpx.Response(
            200,
            json={
                "results": hits_by_query.get(query["q"][0], []),
                "unresponsive_engines": [["baidu", "CAPTCHA"]],
            },
        )

    return httpx.MockTransport(handler)


def hit(url, title, *engines):
    return {"url": url, "title": title, "engines": list(engines or ["360search"])}


async def search(api, header, task, body):
    return await api.post(f"/tasks/{task}/screenshot-searches", headers=header, json=body)


async def test_search_preview_worker_candidates_adopt_and_capture(tenants, tmp_path, monkeypatch):
    monkeypatch.setenv("BID_SEARCH_URL", SEARCH_URL)
    monkeypatch.setenv("BID_SEARCH_PROVIDER", "searxng")
    sent = []
    async with vendor_client(tenants, tmp_path, monkeypatch) as (api, app, headers):
        header = headers[0]
        task, _, extraction, requirements = await create_tender(api, app, header, tmp_path)
        # The organization already recorded this vendor's official site on another product.
        reference = await api.post(
            "/resources/products",
            headers=header,
            json={
                "data": {
                    "name": "Synthetic M0",
                    "vendor": "Synthetic vendor",
                    "model": "M0",
                    "official_url": "https://www.vendor.example/m0",
                }
            },
        )
        assert reference.status_code == 200, reference.text
        product = (
            await api.post(
                "/resources/products",
                headers=header,
                json={
                    "data": {"name": "Synthetic M1", "vendor": "Synthetic vendor", "model": "M1"}
                },
            )
        ).json()["data"]
        selection = (
            await api.post(
                f"/tasks/{task}/products",
                headers=header,
                json={"product_id": product["product_id"]},
            )
        ).json()["data"]
        app.state.processor.search_transport = searxng(
            {
                "Synthetic vendor M1": [
                    hit("http://insecure.example/m1", "Plain HTTP"),
                    hit("https://user:pw@third.example/m1", "Credentials"),
                    hit("https://third.example/m1", "Third-party listing", "360search"),
                    hit(PDF_URL, "M1 datasheet", "duckduckgo"),
                ],
                "Synthetic vendor M1 pdf": [hit(PDF_URL, "M1 datasheet", "360search")],
            },
            sent,
        )
        body = {"extraction_job_id": extraction, "task_resource_id": selection["id"]}

        preview = await search(api, header, task, {**body, "dry_run": True})
        assert preview.status_code == 200, preview.text
        planned = preview.json()["data"]
        assert planned["queries"] == ["Synthetic vendor M1", "Synthetic vendor M1 pdf"]
        assert planned["admission_blocker"] is None and not sent
        stale = await search(api, header, task, {**body, "expected_input_hash": "0" * 64})
        assert stale.status_code == 409
        assert stale.json()["data"]["error"]["code"] == "search_input_changed"

        submitted = await search(
            api, header, task, {**body, "expected_input_hash": planned["input_hash"]}
        )
        assert submitted.status_code == 200, submitted.text
        job_id = submitted.json()["data"]["job_id"]
        await app.state.processor(header["X-Org-Id"], job_id)
        status = (await api.get(f"/jobs/{job_id}", headers=header)).json()["data"]
        assert status["status"] == "succeeded", status
        found = status["result"]["vendor_search"]
        assert "submission" not in status["result"]

        # Only the vendor and model left the worker; no tender or requirement text.
        assert [query["q"][0] for query in sent] == planned["queries"]
        outbound = repr(sent)
        assert all(row["source"]["quote"][:12] not in outbound for row in requirements)
        assert all(row["text"][:12] not in outbound for row in requirements)
        assert {key for query in sent for key in query} == {"q", "format", "language"}

        urls = [candidate["url"] for candidate in found["candidates"]]
        assert urls == [PDF_URL, "https://third.example/m1"]
        first = found["candidates"][0]
        assert first["known_vendor_domain"] and first["pdf"]
        assert first["engines"] == ["360search", "duckduckgo"]
        assert found["unresponsive_engines"] == ["baidu"]
        shown = await api.get(f"/screenshot-searches/{found['id']}", headers=header)
        assert shown.json()["data"] == found
        assert (
            await api.get(f"/screenshot-searches/{found['id']}", headers=headers[1])
        ).status_code == 404

        adopt_path = f"/screenshot-search-candidates/{first['id']}/adopt"
        adoption = {"field": "whitepaper_url", "expected_product_revision": 1}
        assert (await api.post(adopt_path, headers=headers[1], json=adoption)).status_code == 404
        adopted = await api.post(adopt_path, headers=header, json=adoption)
        assert adopted.status_code == 200, adopted.text
        chosen = adopted.json()["data"]["selection"]
        assert chosen["revision"] == 2 and chosen["data"]["whitepaper_url"] == PDF_URL
        assert chosen["id"] != selection["id"]
        again = await api.post(adopt_path, headers=header, json=adoption)
        assert again.status_code == 409, again.text

        # The adopted source is captured from the new selected revision.
        revision = {"id": chosen["product_revision_id"], "data": chosen["data"]}
        artifacts, _ = await capture(
            api, app, header, task, extraction, revision, chosen, format="pdf", pdf_pages=[1]
        )
        assert artifacts["source_pdf"]["sha256"]

        failing = []
        app.state.processor.search_transport = searxng({}, failing, fail=True)
        retry_body = {**body, "task_resource_id": chosen["id"]}
        replanned = (await search(api, header, task, {**retry_body, "dry_run": True})).json()[
            "data"
        ]
        queued = await search(
            api, header, task, {**retry_body, "expected_input_hash": replanned["input_hash"]}
        )
        down_job = queued.json()["data"]["job_id"]
        with pytest.raises(ProviderFailure):
            await app.state.processor(header["X-Org-Id"], down_job)
        down = (await api.get(f"/jobs/{down_job}", headers=header)).json()["data"]
        assert down["status"] == "queued", down
        # Retryable: requeued with exit code 3 until the attempt limit, never a success.
        assert down["error"] == {
            "code": "search_unavailable",
            "message": "Search service is unavailable",
            "exit_code": 3,
        }
        assert failing


async def test_search_unconfigured_blocks_admission(tenants, tmp_path, monkeypatch):
    monkeypatch.delenv("BID_SEARCH_URL", raising=False)
    async with vendor_client(tenants, tmp_path, monkeypatch) as (api, app, headers):
        header = headers[0]
        task, _, extraction, _ = await create_tender(api, app, header, tmp_path)
        _, selection = await select_product(api, header, task)
        body = {"extraction_job_id": extraction, "task_resource_id": selection["id"]}
        preview = (await search(api, header, task, {**body, "dry_run": True})).json()["data"]
        assert preview["admission_blocker"] == "search_unavailable"
        refused = await search(
            api, header, task, {**body, "expected_input_hash": preview["input_hash"]}
        )
        assert refused.status_code == 409
        assert refused.json()["data"]["error"]["code"] == "search_unavailable"


@pytest.mark.skipif(
    not os.environ.get("BID_SEARCH_LIVE_URL") or not os.environ.get("BID_SEARCH_LIVE_PRODUCT"),
    reason="requires a running SearXNG and an explicit 'vendor|model' product",
)
async def test_live_search_returns_candidates(tenants, tmp_path, monkeypatch):
    """Development check against a real SearXNG; never runs in CI."""
    monkeypatch.setenv("BID_SEARCH_URL", os.environ["BID_SEARCH_LIVE_URL"])
    monkeypatch.setenv("BID_SEARCH_PROVIDER", "searxng")
    vendor, model = os.environ["BID_SEARCH_LIVE_PRODUCT"].split("|", 1)
    async with vendor_client(tenants, tmp_path, monkeypatch) as (api, app, headers):
        header = headers[0]
        task, _, extraction, _ = await create_tender(api, app, header, tmp_path)
        product = (
            await api.post(
                "/resources/products",
                headers=header,
                json={"data": {"name": model, "vendor": vendor, "model": model}},
            )
        ).json()["data"]
        selection = (
            await api.post(
                f"/tasks/{task}/products",
                headers=header,
                json={"product_id": product["product_id"]},
            )
        ).json()["data"]
        body = {"extraction_job_id": extraction, "task_resource_id": selection["id"]}
        planned = (await search(api, header, task, {**body, "dry_run": True})).json()["data"]
        job_id = (
            await search(api, header, task, {**body, "expected_input_hash": planned["input_hash"]})
        ).json()["data"]["job_id"]
        await app.state.processor(header["X-Org-Id"], job_id)
        status = (await api.get(f"/jobs/{job_id}", headers=header)).json()["data"]
        assert status["status"] == "succeeded", status
        found = status["result"]["vendor_search"]
        assert found["candidates"], found
        target = Path(os.environ.get("BID_VENDOR_LIVE_DIR", "data/work/vendor-live"))
        await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(
            (target / "live-search.json").write_text,
            json.dumps(found, ensure_ascii=False, indent=2),
        )
