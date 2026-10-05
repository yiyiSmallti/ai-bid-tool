"""Vendor-source search through the Perplexity Search API or an operator-run SearXNG.

Only the fixed vendor and model text leave the worker. Results are unverified candidate
URLs; a person confirms one before it can become a product source or be captured.
The deployment selects one provider explicitly; there is no credential fallback.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from app.core.config import Settings
from app.core.errors import ServiceError
from app.providers.base import ProviderFailure

MAX_RESULTS_PER_QUERY = 30
TIMEOUT_SECONDS = 20.0
PERPLEXITY_URL = "https://api.perplexity.ai/search"
PERPLEXITY_MAX_RESULTS = 20
PERPLEXITY_TOKENS_PER_PAGE = 4096
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    engines: tuple[str, ...]
    # Page text the search service extracted; it is not a capture of the page.
    content: str = ""


@dataclass(frozen=True)
class SearchResult:
    hits: tuple[SearchHit, ...]
    unresponsive_engines: tuple[str, ...]


class SearXNGSearch:
    name = "searxng"
    filters_domains = False

    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.transport = transport

    @property
    def identity(self) -> str:
        return f"{self.name}:{self.base_url}"

    @property
    def public_identity(self) -> str:
        return self.identity

    async def search(self, query: str, domains: Sequence[str] = ()) -> SearchResult:
        """`domains` narrows only providers that filter by domain; SearXNG ignores it."""
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
        except (httpx.HTTPError, ValueError):
            raise ProviderFailure(
                "Search service is unavailable", code="search_unavailable", retryable=True
            ) from None
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


class PerplexitySearch:
    name = "perplexity"
    filters_domains = True

    def __init__(
        self,
        credential_resolver,
        credential_target,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.credential_resolver = credential_resolver
        self.credential_target = credential_target
        self.transport = transport

    @property
    def identity(self) -> str:
        return f"{self.name}:{PERPLEXITY_URL}:{self.credential_target.credential_id}"

    @property
    def public_identity(self) -> str:
        return f"{self.name}:{PERPLEXITY_URL}"

    async def search(self, query: str, domains: Sequence[str] = ()) -> SearchResult:
        body: dict = {
            "query": query,
            "max_results": PERPLEXITY_MAX_RESULTS,
            "max_tokens_per_page": PERPLEXITY_TOKENS_PER_PAGE,
        }
        if domains:
            body["search_domain_filter"] = list(domains)[:20]
        try:
            credential = await self.credential_resolver.resolve_for_call(self.credential_target)
        except ServiceError as exc:
            raise ServiceError(
                "provider_unavailable", "Selected provider is unavailable", 503, exc.exit_code
            ) from None
        if (
            credential.provider != "perplexity"
            or credential.endpoint != "https://api.perplexity.ai"
        ):
            raise ServiceError("provider_unavailable", "Selected provider is unavailable", 503, 4)
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=TIMEOUT_SECONDS, trust_env=False
            ) as client:
                response = await client.post(
                    PERPLEXITY_URL,
                    json=body,
                    headers={"Authorization": f"Bearer {credential.api_key.get_secret_value()}"},
                    follow_redirects=False,
                )
        except httpx.HTTPError:
            raise ProviderFailure(
                "Search service is unavailable", code="search_unavailable", retryable=True
            ) from None
        if response.status_code != 200:
            # The body is not kept: it may echo the request.
            raise ProviderFailure(
                f"Search service failed with HTTP {response.status_code}",
                code="search_unavailable",
                retryable=response.status_code in RETRYABLE_STATUS,
            )
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("results")
            escaped_key = json.dumps(credential.api_key.get_secret_value(), ensure_ascii=False)[
                1:-1
            ]
            if escaped_key in json.dumps(payload, ensure_ascii=False):
                raise ProviderFailure(
                    "Search service returned an unsafe response", code="search_unavailable"
                )
            results = payload.get("results")
            if not isinstance(results, list):
                raise ValueError("results")
        except ValueError:
            raise ProviderFailure(
                "Search service returned an invalid response", code="search_unavailable"
            ) from None
        hits = []
        for item in results[:PERPLEXITY_MAX_RESULTS]:
            if not isinstance(item, dict):
                continue
            url, title, snippet = item.get("url"), item.get("title"), item.get("snippet")
            if isinstance(url, str) and isinstance(title, str):
                content = snippet if isinstance(snippet, str) else ""
                hits.append(SearchHit(url, title.strip()[:500], (self.name,), content))
        return SearchResult(tuple(hits), ())


async def create_search_provider(
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
    *,
    credential_resolver=None,
) -> PerplexitySearch | SearXNGSearch | None:
    settings.assert_vendor_credentials_absent()
    if settings.search_provider == "disabled":
        return None
    if settings.search_provider == "searxng":
        if not settings.search_url:
            raise ProviderFailure("SearXNG endpoint is not configured", code="search_unavailable")
        return SearXNGSearch(settings.search_url, transport)
    if credential_resolver is None:
        from app.services.platform_credentials import PlatformCredentialResolver

        credential_resolver = PlatformCredentialResolver(settings)
    try:
        target = await credential_resolver.select_service("vendor_search")
    except ServiceError as exc:
        raise ServiceError(
            "provider_unavailable", "Selected provider is unavailable", 503, exc.exit_code
        ) from None
    return PerplexitySearch(credential_resolver, target, transport)
