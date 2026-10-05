"""Simulated product proposals for demonstration: propose, search, fetch, quote, record.

Technical requirements are grouped by the tender table row that lists one procurement
item. For each hardware item the model proposes candidate vendors with their official
domains; the worker searches the operator's search service, fetches pages on those
domains through the trusted fetch broker, and keeps a product only when the model
judges the page to be the vendor's own and its name and parameter statements occur
verbatim in that page. Each
kept product and statement is stored as an ordinary declaration and registered in
`simulated_resources`, so every revision stays marked and final exports refuse it.
"""

import asyncio
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

from app.core.errors import ServiceError, not_found
from app.jobs.execution import JobExecution
from app.models.entities import (
    Chunk,
    Requirement,
    SimulatedResource,
    Task,
    TaskFeature,
    TaskResource,
)
from app.providers import simulation
from app.providers.base import MalformedOutput, ProviderFailure
from app.providers.calls import plan_calls
from app.providers.llm import HTTPExtractor
from app.providers.sandbox_fetch import (
    FetchBroker,
    FetchDenied,
    SQLiteFetchQuota,
    canonical_url,
    system_resolver,
)
from app.schemas.feature_contracts import FeatureCreate, TaskFeatureSelection
from app.schemas.resource_contracts import ProductCreate, TaskProductSelection
from app.schemas.simulation_contracts import ProductSimulationInput
from app.services import features, resources
from app.services import response_cards as cards
from app.services import screenshots as images
from app.services.card_generation import worker
from app.services.screenshot_jobs import create_job
from app.services.task_authorization import task_authorized
from app.services.vendor_search import host_of, ranked, registrable

JOB_KIND = "product_simulation"
PROPOSAL_BATCH = 12
MAX_ITEM_CHARS = 6000
MIN_PAGE_CHARS = 200
VENDORS_PER_ITEM = 3
PAGES_PER_VENDOR = 3
FOLLOWED_PER_PAGE = 2
# Every page read is one quoting call: found pages plus the links followed from them.
QUOTES_PER_ITEM = VENDORS_PER_ITEM * PAGES_PER_VENDOR * (1 + FOLLOWED_PER_PAGE)


NAME_HEADER = re.compile(r"名称|项目|模块|设备|产品|货物|品目|内容")
UNITS = {
    "套",
    "台",
    "个",
    "项",
    "批",
    "年",
    "人",
    "次",
    "块",
    "件",
    "组",
    "张",
    "根",
    "米",
    "处",
    "间",
}


def cell_text(block: dict) -> str:
    return " ".join(str(block.get("text", "")).split())


def row_names(chunk: Chunk) -> dict[tuple, str]:
    """Item name per (table, row): the row's cells under name-like header columns.

    A merged cell appears only in its first row, so a missing name cell repeats the value
    above it. Rows without a name column fall back to their first short descriptive cell.
    """
    cells: dict[int, dict[int, dict[int, str]]] = {}
    for block in chunk.blocks or []:
        if block.get("kind") == "cell":
            table, row, column = int(block["table"]), int(block["row"]), int(block["column"])
            cells.setdefault(table, {}).setdefault(row, {})[column] = cell_text(block)
    names: dict[tuple, str] = {}
    for table, rows in cells.items():
        header_row = min(rows)
        columns = [
            col for col, text in sorted(rows[header_row].items()) if NAME_HEADER.search(text)
        ]
        carried: dict[int, str] = {}
        for row in sorted(rows):
            if row == header_row:
                continue
            parts = []
            for column in columns:
                value = rows[row].get(column) or carried.get(column, "")
                if value:
                    carried[column] = value
                    if len(value) <= 60 and value not in parts:
                        parts.append(value)
            if not parts:
                parts = [
                    text
                    for _, text in sorted(rows[row].items())
                    if text
                    and len(text) <= 60
                    and text not in UNITS
                    and not text.replace(".", "").isdigit()
                ][:1]
            names[(table, row)] = " · ".join(parts) or f"表 {table} 第 {row} 行"
    return names


async def items_for(session, task_id: UUID, extraction_job_id: UUID):
    """Technical requirements grouped by tender table row, in document order."""
    extraction, requirements = await cards.extraction_scope(session, task_id, extraction_job_id)
    chunks = {
        chunk.id: chunk
        for chunk in (
            await session.scalars(select(Chunk).where(Chunk.document_id == extraction.document_id))
        ).all()
    }
    groups: dict[tuple, dict] = {}
    names: dict = {}
    for requirement in requirements:
        location = requirement.location or {}
        if requirement.category != "technical" or location.get("kind") != "cell":
            continue
        key = (str(requirement.chunk_id), location.get("table"), location.get("row"))
        if key not in groups:
            if requirement.chunk_id not in names:
                names[requirement.chunk_id] = row_names(chunks[requirement.chunk_id])
            groups[key] = {
                "key": f"I{len(groups) + 1:03d}",
                "name": names[requirement.chunk_id].get(
                    (location.get("table"), location.get("row")),
                    f"表 {location.get('table')} 第 {location.get('row')} 行",
                ),
                "requirement_ids": [],
            }
        groups[key]["requirement_ids"].append(str(requirement.id))
    return extraction, list(groups.values())


def manifest_for(task_id: UUID, extraction_job_id: UUID, items: list[dict], identity: str | None):
    return {
        "task_id": str(task_id),
        "extraction_job_id": str(extraction_job_id),
        "items": items,
        "prompt_version": simulation.PROMPT_VERSION,
        "search_identity": identity,
    }


async def access(session, actor):
    if actor.actor_kind != "session" or actor.token_id is not None:
        raise ServiceError("forbidden", "Human session required", 403, 4)
    actor = await cards.access(session, actor, "resource:write")
    actor.require("task:resource")
    return actor


@task_authorized("task:resource", write=True)
async def submit(
    session, actor, task_id: UUID, body: ProductSimulationInput, provider, settings, llm=None
):
    actor = await access(session, actor)
    extraction, items = await items_for(session, task_id, body.extraction_job_id)
    manifest = manifest_for(
        task_id, extraction.id, items, provider.identity if provider is not None else None
    )
    from app.providers.configured import model_identity

    manifest["model"] = model_identity(llm) if llm is not None else None
    input_hash = images.digest(manifest)
    blocker = (
        "search_unavailable"
        if provider is None
        else "fetch_unavailable"
        if not os.environ.get("BID_SANDBOX_POLICY_FILE")
        or not os.environ.get("BID_SANDBOX_FETCH_QUOTA")
        else None
    )
    if body.dry_run:
        from app.services import budget_preflight

        ids = [UUID(value) for item in items for value in item["requirement_ids"]]
        originals = {
            str(row.id): row.quote
            for row in (
                await session.scalars(select(Requirement).where(Requirement.id.in_(ids)))
            ).all()
        }
        batches = [
            items[start : start + PROPOSAL_BATCH] for start in range(0, len(items), PROPOSAL_BATCH)
        ]
        estimates = (
            [
                lambda batch=batch, adapter=llm: adapter.quote(
                    simulation.propose_body(adapter, [item_text(item, originals) for item in batch])
                )
                for batch in batches
            ]
            if isinstance(llm, HTTPExtractor)
            else []
        )

        return await budget_preflight.attach(
            session,
            {
                "dry_run": True,
                "input_hash": input_hash,
                "items": [
                    {
                        "key": item["key"],
                        "name": item["name"],
                        "requirements": len(item["requirement_ids"]),
                    }
                    for item in items
                ],
                "requirement_count": sum(len(item["requirement_ids"]) for item in items),
                "search_identity": provider.public_identity if provider else None,
                "admission_blocker": blocker,
            },
            command="product simulate",
            task_id=task_id,
            input_hash=input_hash,
            quote_sources=estimates,
            planned_calls=None if items else 0,
            dynamic=bool(items),
            settings=settings,
            currency=settings.billing_currency,
        ), None
    if body.expected_input_hash != input_hash:
        images.fail("simulation_input_changed", "Inputs changed since the preview", 409, 3)
    if blocker:
        images.fail(blocker, "Product simulation needs search and fetch configuration", 409, 4)
    if not items:
        images.fail(
            "simulation_no_items", "No table-listed technical requirements to simulate", 400, 2
        )
    await cards.task_lock(session, task_id)
    return await create_job(session, actor, task_id, extraction, JOB_KIND, manifest, body.retry)


async def fetch_page(org_id: UUID, url: str, processor) -> simulation.Page:
    """Fetch one public page through the trusted broker, or raise FetchDenied."""
    from app.services.sandbox import policy_source

    source = policy_source()
    policy = source.select(url)
    broker = FetchBroker(
        source,
        policy.revision,
        org_id,
        quota=SQLiteFetchQuota(Path(os.environ["BID_SANDBOX_FETCH_QUOTA"])),
        transport=processor.sandbox_fetch_transport,
        resolver=processor.sandbox_resolver or system_resolver,
    )
    try:
        payload = await broker.fetch(url)
    finally:
        broker.close()
    if payload.status != 200:
        raise FetchDenied(f"http_{payload.status}")
    try:
        page = await asyncio.to_thread(
            simulation.read_page,
            payload.body,
            payload.headers.get("content-type", ""),
            payload.url,
        )
    except (ValueError, RuntimeError):
        raise FetchDenied("page_unreadable") from None
    if len(page.text) < MIN_PAGE_CHARS:
        raise FetchDenied("page_too_short")
    host = registrable(host_of(page.url))
    # Only links on the same site may be followed; the model picks among these.
    return page.model_copy(
        update={"links": [row for row in page.links if registrable(host_of(row["url"])) == host]}
    )


def official_domain(value: str) -> str:
    """Registrable domain of a model-proposed official site, or empty when malformed."""
    host = value.strip().lower().removeprefix("https://").removeprefix("http://")
    host = host.split("/", 1)[0].removeprefix("www.")
    return registrable(host) if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", host) else ""


async def vendor_pages(search, domain: str, query: str) -> list[tuple[str, str]]:
    """(URL, search extract) of pages on the proposed domain first, else the top results.

    Proposed domains are often wrong, so other hosts stay candidates; the quoting step
    decides from the URL whether a page is the vendor's own site. A provider that filters
    by domain is asked for that domain first and searched openly only when it finds none.
    """
    narrowed = bool(domain) and search.filters_domains
    try:
        hits = (await search.search(query, [domain] if narrowed else ())).hits
        if narrowed and not hits:
            hits = (await search.search(query)).hits
    except ProviderFailure as error:
        if error.code != "search_unavailable":
            # Admission and accounting failures stop this atomic command; they
            # cannot be reinterpreted as an empty vendor search result.
            raise
        return []
    extracts: dict[str, str] = {}
    for hit in hits:
        try:
            extracts.setdefault(canonical_url(hit.url), hit.content)
        except FetchDenied:
            continue
    # Keep the search service's relevance order: ranked() puts PDFs first for spec sheets,
    # but on vendor domains those are mostly annual reports rather than product pages.
    rows = sorted(ranked(hits, [domain] if domain else []), key=lambda row: row["order"])
    official = [row for row in rows if row["known_vendor_domain"]]
    return [(row["url"], extracts.get(row["url"], "")) for row in (official or rows)][
        :PAGES_PER_VENDOR
    ]


def item_text(item: dict, quotes: dict[str, str]) -> dict:
    lines, size = [], 0
    for requirement_id in item["requirement_ids"]:
        quote = quotes[requirement_id]
        if size + len(quote) > MAX_ITEM_CHARS:
            break
        lines.append(quote)
        size += len(quote)
    return {"item_key": item["key"], "name": item["name"], "requirements": lines}


async def read_vendor_page(
    llm, client, item, vendor, url, extract, org_id, processor, limit, tried
):
    """Quote one page; returns (model, kept quotes, url, followable links) and logs the attempt.

    When the page cannot be fetched, the search service's extract of it is read instead
    and the attempt says so; quotes are then checked against that extract.
    """
    attempt = {"vendor": vendor, "url": url, "source": "page"}
    tried.append(attempt)
    try:
        page = await fetch_page(org_id, url, processor)
    except FetchDenied as denied:
        text = simulation.normalize(extract)[: simulation.MAX_PAGE_CHARS]
        if len(text) < MIN_PAGE_CHARS:
            attempt["result"] = denied.code
            return "", [], url, []
        attempt |= {"source": "search_extract", "fetch": denied.code}
        page = simulation.Page(url=url, text=text, links=[])
    async with limit:
        answer = await simulation.quote(llm, client, item, vendor, page)
    allowed = {row["url"] for row in page.links}
    follow = [link for link in answer.next_urls if link in allowed][:FOLLOWED_PER_PAGE]
    model = simulation.normalize(answer.model)[:120]
    # The product must be named on the page; a remembered model is never stored.
    if not model or model not in page.text:
        attempt["result"] = "no_product"
        return "", [], page.url, follow
    kept, seen = [], set()
    for parameter in answer.parameters:
        statement = simulation.normalize(parameter.quote)
        if not 4 <= len(statement) <= 300 or statement in seen or statement not in page.text:
            continue
        seen.add(statement)
        kept.append({"label": parameter.label.strip()[:60] or "参数", "quote": statement})
    attempt["result"] = "quoted" if kept else "no_parameters"
    return model, kept[:30], page.url, []


async def simulate_item(llm, client, item, proposal, quotes, search, processor, org_id, limit):
    """Try each proposed vendor's pages, one link level deep, until one names a product."""
    outcome = {"key": item["key"], "name": item["name"], "kind": proposal.kind, "tried": []}
    if proposal.item_key != item["key"]:
        return outcome | {"kind": None, "status": "not_proposed"}
    if proposal.kind != "hardware":
        return outcome | {"status": "not_hardware"}
    candidates = [row for row in proposal.candidates if row.vendor.strip() and row.query.strip()]
    if not candidates:
        return outcome | {"status": "no_product"}
    text = item_text(item, quotes)
    found = named = False
    for candidate in candidates[:VENDORS_PER_ITEM]:
        vendor = candidate.vendor.strip()[:100]
        query = candidate.query.strip()[:120]
        pages = await vendor_pages(search, official_domain(candidate.domain), query)
        found = found or bool(pages)
        queue = [(url, extract, True) for url, extract in pages]
        while queue:
            url, extract, may_follow = queue.pop(0)
            tried = outcome["tried"]
            model, kept, url, follow = await read_vendor_page(
                llm, client, text, vendor, url, extract, org_id, processor, limit, tried
            )
            named = named or bool(model)
            if kept:
                return outcome | {
                    "status": "quoted",
                    "vendor": vendor,
                    "model": model,
                    "url": url,
                    "source": tried[-1]["source"],
                    "parameters": kept,
                }
            if may_follow:
                queue[:0] = [(link, "", False) for link in follow]
    status = "no_parameters" if named else "no_page" if found else "no_search_result"
    return outcome | {"status": status}


async def record(session, actor, task_id: UUID, job_id: UUID, outcome: dict) -> None:
    """Store one quoted product with its statements, select them on the task and mark them."""
    product = await resources.create_product(
        session,
        actor,
        ProductCreate.model_validate(
            {
                "data": {
                    "name": f"【模拟】{outcome['name']}"[:200],
                    "vendor": outcome["vendor"][:200],
                    "model": outcome["model"][:200],
                    "official_url": outcome["url"],
                }
            }
        ),
    )
    product_id = UUID(product["product_id"])
    session.add(
        SimulatedResource(
            id=uuid4(),
            org_id=actor.org_id,
            job_id=job_id,
            product_id=product_id,
            source_url=outcome["url"],
        )
    )
    await resources.select_product(
        session, actor, task_id, TaskProductSelection(product_id=product_id)
    )
    feature_ids = []
    for parameter in outcome["parameters"]:
        feature = await features.create_feature(
            session,
            actor,
            FeatureCreate.model_validate(
                {
                    "data": {
                        "product_id": str(product_id),
                        "name": parameter["label"][:200],
                        "description": parameter["quote"],
                        "status": "implemented",
                    }
                }
            ),
        )
        feature_id = UUID(feature["feature_id"])
        session.add(
            SimulatedResource(
                id=uuid4(),
                org_id=actor.org_id,
                job_id=job_id,
                feature_id=feature_id,
                source_url=outcome["url"],
            )
        )
        await features.select_feature(
            session, actor, task_id, TaskFeatureSelection(feature_id=feature_id)
        )
        feature_ids.append(str(feature_id))
    outcome["product_id"], outcome["feature_ids"] = str(product_id), feature_ids


async def process(execution: JobExecution, processor, llm) -> None:
    from app.providers.search import create_search_provider

    search = await create_search_provider(processor.settings, processor.search_transport)
    if search is None:
        raise ProviderFailure("Vendor search is not configured", code="search_unavailable")
    async with execution.db.transaction(execution.org_id) as session:
        job = await execution.owned_job(session)
        manifest = job.result["submission"]["input_manifest"]
        if manifest.get("search_identity") != search.identity:
            images.fail("simulation_input_changed", "Fixed search identity changed", 409, 3)
        task_id, job_id = job.task_id, job.id
        assert task_id is not None
        _, items = await items_for(session, task_id, UUID(manifest["extraction_job_id"]))
        if items != manifest["items"]:
            images.fail("simulation_input_changed", "Fixed simulation inputs changed", 409, 3)
        ids = [UUID(value) for item in items for value in item["requirement_ids"]]
        quotes = {
            str(row.id): row.quote
            for row in (
                await session.scalars(select(Requirement).where(Requirement.id.in_(ids)))
            ).all()
        }
    batches = [
        items[start : start + PROPOSAL_BATCH] for start in range(0, len(items), PROPOSAL_BATCH)
    ]
    plan_calls(len(batches))
    limit = asyncio.Semaphore(max(1, processor.settings.llm_concurrency))
    async with httpx.AsyncClient(
        transport=llm.transport,
        timeout=httpx.Timeout(llm.settings.llm_timeout_seconds, connect=10),
        follow_redirects=False,
    ) as client:

        async def proposed(batch):
            try:
                async with limit:
                    return await simulation.propose(
                        llm, client, [item_text(item, quotes) for item in batch]
                    )
            except MalformedOutput:
                # A single item the model cannot answer stays visible as not_proposed.
                if len(batch) == 1:
                    return []
                half = len(batch) // 2
                first, second = await asyncio.gather(proposed(batch[:half]), proposed(batch[half:]))
                return first + second

        proposals = {
            proposal.item_key: proposal
            for answered in await asyncio.gather(*(proposed(batch) for batch in batches))
            for proposal in answered
        }
        hardware = sum(proposal.kind == "hardware" for proposal in proposals.values())
        plan_calls(len(batches) + hardware * (QUOTES_PER_ITEM + 2 * VENDORS_PER_ITEM))
        missing = simulation.ItemProposal(item_key="-", kind="service", candidates=[])
        outcomes = await asyncio.gather(
            *(
                simulate_item(
                    llm,
                    client,
                    item,
                    proposals.get(item["key"], missing),
                    quotes,
                    search,
                    processor,
                    execution.org_id,
                    limit,
                )
                for item in items
            )
        )
    async with execution.db.transaction(execution.org_id) as session:
        # Resource selection also locks Task; acquire it before the job publication fence.
        await session.scalar(select(Task).where(Task.id == task_id).with_for_update())
        job = await execution.owned_job(session)
        actor = worker(job)
        for outcome in outcomes:
            if outcome["status"] == "quoted":
                await record(session, actor, task_id, job_id, outcome)
        counts = {
            "items": len(outcomes),
            "hardware": sum(outcome["kind"] == "hardware" for outcome in outcomes),
            "products": sum("product_id" in outcome for outcome in outcomes),
            "parameters": sum(len(outcome.get("feature_ids", [])) for outcome in outcomes),
        }
        job.status, job.error, job.finished_at = "succeeded", None, datetime.now(UTC)
        job.result = {**job.result, "simulated": True, "counts": counts, "items": outcomes}


@task_authorized("task:read")
async def task_marks(session, actor, task_id: UUID) -> dict:
    """Selection IDs on this task whose product or feature was created by a simulation."""
    actor.require("task:read")
    if await session.get(Task, task_id) is None:
        raise not_found()
    products, feature_roots = await simulated_ids(session)
    product_rows = (
        await session.scalars(select(TaskResource).where(TaskResource.task_id == task_id))
    ).all()
    feature_rows = (
        await session.scalars(select(TaskFeature).where(TaskFeature.task_id == task_id))
    ).all()
    return {
        "task_id": str(task_id),
        "selection_ids": sorted(
            [str(row.id) for row in product_rows if row.product_id in products]
            + [str(row.id) for row in feature_rows if row.feature_id in feature_roots]
        ),
    }


async def simulated_ids(session) -> tuple[set[UUID], set[UUID]]:
    """Product and feature roots registered as simulated in the current org."""
    rows = (await session.scalars(select(SimulatedResource))).all()
    return (
        {row.product_id for row in rows if row.product_id},
        {row.feature_id for row in rows if row.feature_id},
    )
