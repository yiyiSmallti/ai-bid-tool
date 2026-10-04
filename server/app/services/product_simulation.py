"""Simulated product proposals for demonstration: propose, search, fetch, quote, record.

Technical requirements are grouped by the tender table row that lists one procurement
item. For each hardware item the model proposes a real product; the worker searches
the operator's search service, fetches one public page through the trusted fetch
broker, and keeps only parameter statements that occur verbatim in that page. Each
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
from app.providers.base import ProviderFailure
from app.providers.calls import plan_calls
from app.providers.sandbox_fetch import FetchBroker, FetchDenied, SQLiteFetchQuota, system_resolver
from app.schemas.feature_contracts import FeatureCreate, TaskFeatureSelection
from app.schemas.resource_contracts import ProductCreate, TaskProductSelection
from app.schemas.simulation_contracts import ProductSimulationInput
from app.services import features, resources
from app.services import response_cards as cards
from app.services import screenshots as images
from app.services.card_generation import worker
from app.services.screenshot_jobs import create_job
from app.services.vendor_search import ranked

JOB_KIND = "product_simulation"
PROPOSAL_BATCH = 12
MAX_ITEM_CHARS = 6000
MIN_PAGE_CHARS = 200
CANDIDATES_PER_ITEM = 3


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
            table, row, column = block.get("table"), block.get("row"), block.get("column")
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


async def submit(session, actor, task_id: UUID, body: ProductSimulationInput, provider, settings):
    actor = await access(session, actor)
    extraction, items = await items_for(session, task_id, body.extraction_job_id)
    manifest = manifest_for(
        task_id, extraction.id, items, provider.identity if provider is not None else None
    )
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
        return {
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
            "search_identity": manifest["search_identity"],
            "admission_blocker": blocker,
        }, None
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


async def fetch_text(org_id: UUID, url: str, processor) -> tuple[str, str] | None:
    """Fetch one public page through the trusted broker; (final URL, text) or None."""
    from app.services.sandbox import policy_source

    source = policy_source()
    try:
        policy = source.select(url)
    except FetchDenied:
        return None
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
    except FetchDenied:
        return None
    finally:
        broker.close()
    if payload.status != 200:
        return None
    try:
        text = await asyncio.to_thread(
            simulation.page_text, payload.body, payload.headers.get("content-type", "")
        )
    except Exception:
        return None
    return (payload.url, text) if len(text) >= MIN_PAGE_CHARS else None


def item_text(item: dict, quotes: dict[str, str]) -> dict:
    lines, size = [], 0
    for requirement_id in item["requirement_ids"]:
        quote = quotes[requirement_id]
        if size + len(quote) > MAX_ITEM_CHARS:
            break
        lines.append(quote)
        size += len(quote)
    return {"item_key": item["key"], "name": item["name"], "requirements": lines}


async def simulate_item(llm, client, item, proposal, quotes, search, processor, org_id, limit):
    """Search, fetch and quote one hardware item; returns the outcome for the job result."""
    outcome = {
        "key": item["key"],
        "name": item["name"],
        "kind": proposal.kind,
        "vendor": proposal.vendor,
        "model": proposal.model,
    }
    if proposal.item_key != item["key"]:
        return outcome | {"kind": None, "status": "not_proposed"}
    if proposal.kind != "hardware" or not proposal.vendor.strip() or not proposal.model.strip():
        return outcome | {"status": "not_hardware" if proposal.kind != "hardware" else "no_product"}
    hits = []
    for query in proposal.queries[:2] or [f"{proposal.vendor} {proposal.model}"]:
        try:
            hits.extend((await search.search(query)).hits)
        except ProviderFailure:
            continue
    candidates = ranked(hits, [])[:CANDIDATES_PER_ITEM]
    model_token = proposal.model.strip().lower()
    matched = False
    for candidate in candidates:
        fetched = await fetch_text(org_id, candidate["url"], processor)
        if fetched is None:
            continue
        url, text = fetched
        # A page that never names the proposed model is not that product's page.
        if model_token not in text.lower():
            continue
        matched = True
        async with limit:
            parameters = await simulation.quote(llm, client, item_text(item, quotes), text)
        kept, seen = [], set()
        for parameter in parameters:
            statement = simulation.normalize(parameter.quote)
            if len(statement) < 4 or statement in seen or statement not in text:
                continue
            seen.add(statement)
            kept.append({"label": parameter.label.strip()[:60] or "参数", "quote": statement})
        if kept:
            return outcome | {"status": "quoted", "url": url, "parameters": kept[:30]}
    status = "no_search_result" if not candidates else "no_parameters" if matched else "no_page"
    return outcome | {"status": status, "candidates": [row["url"] for row in candidates]}


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

    search = create_search_provider(processor.settings, processor.search_transport)
    if search is None:
        raise ProviderFailure("Vendor search is not configured", code="search_unavailable")
    async with execution.db.transaction(execution.org_id) as session:
        job = await execution.owned_job(session)
        manifest = job.result["submission"]["input_manifest"]
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
    # Every item may need one quoting call after the proposal batches.
    plan_calls(len(batches) + len(items))
    limit = asyncio.Semaphore(max(1, processor.settings.llm_concurrency))
    async with httpx.AsyncClient(
        transport=llm.transport,
        timeout=httpx.Timeout(llm.settings.llm_timeout_seconds, connect=10),
        follow_redirects=False,
    ) as client:

        async def proposed(batch):
            async with limit:
                return await simulation.propose(
                    llm, client, [item_text(item, quotes) for item in batch]
                )

        proposals = {
            proposal.item_key: proposal
            for answered in await asyncio.gather(*(proposed(batch) for batch in batches))
            for proposal in answered
        }
        missing = simulation.ItemProposal(
            item_key="-", kind="service", vendor="", model="", queries=[]
        )
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
