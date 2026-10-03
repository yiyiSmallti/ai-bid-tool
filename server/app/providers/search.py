"""Vendor-source search through an operator-run SearXNG instance.

Only the fixed vendor and model text leave the worker. Results are unverified candidate
URLs; a person confirms one before it can become a product source or be captured.
"""

from dataclasses import dataclass

import httpx

from app.core.config import Settings
from app.providers.base import ProviderFailure

MAX_RESULTS_PER_QUERY = 30
TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    engines: tuple[str, ...]


@dataclass(frozen=True)
class SearchResult:
    hits: tuple[SearchHit, ...]
    unresponsive_engines: tuple[str, ...]


class SearXNGSearch:
    name = "searxng"

    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.transport = transport

    @property
    def identity(self) -> str:
        return f"{self.name}:{self.base_url}"

    async def search(self, query: str) -> SearchResult:
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=TIMEOUT_SECONDS, trust_env=False
            ) as client:
                response = await client.get(
                    self.base_url + "/search",
                    params={"q": query, "format": "json", "language": "zh-CN"},
                    follow_redirects=False,
                )
            response.raise_for_status()
            body = response.json()
            results = body.get("results")
            if not isinstance(results, list):
                raise ValueError("results")
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderFailure(
                "Search service is unavailable", code="search_unavailable", retryable=True
            ) from exc
        hits = []
        for item in results[:MAX_RESULTS_PER_QUERY]:
            if not isinstance(item, dict):
                continue
            url, title = item.get("url"), item.get("title")
            engines = item.get("engines") or []
            if isinstance(url, str) and isinstance(title, str) and isinstance(engines, list):
                hits.append(
                    SearchHit(url, title.strip()[:500], tuple(str(e) for e in engines)[:10])
                )
        unresponsive = tuple(
            str(entry[0])
            for entry in body.get("unresponsive_engines") or []
            if isinstance(entry, list) and entry
        )
        return SearchResult(tuple(hits), unresponsive)


def create_search_provider(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> SearXNGSearch | None:
    return SearXNGSearch(settings.search_url, transport) if settings.search_url else None
