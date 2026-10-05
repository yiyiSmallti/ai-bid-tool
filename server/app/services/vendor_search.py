"""Vendor-source search: candidate URLs for a fixed product selection, chosen by a person.

The worker sends only the selected revision's vendor and model to the operator's search
service and stores filtered candidates. Adopting one writes it into a new product revision
and reselects that revision on the task, so later captures and future bids reuse it.
"""

from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import select

from app.core.errors import not_found
from app.jobs.execution import job_cost
from app.models.entities import Product, ProductRevision, TaskResource
from app.models.screenshots import ScreenshotSearchCandidate, ScreenshotSearchRun
from app.providers.base import ProviderFailure
from app.providers.sandbox_fetch import FetchDenied, canonical_url
from app.schemas.resource_contracts import ProductData, ProductUpdate, TaskProductSelection
from app.services import resources
from app.services import response_cards as cards
from app.services import screenshots as images
from app.services.card_generation import worker
from app.services.screenshot_jobs import create_job
from app.services.task_authorization import task_authorized
from app.services.versioned import audit

QUERY_VERSION = "vendor-search-v1"
MAX_CANDIDATES = 20


def queries(data: dict) -> list[str]:
    product = " ".join(
        part.strip() for part in (data["vendor"], data["model"]) if part and part.strip()
    )
    return [product, f"{product} pdf"]


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def registrable(host: str) -> str:
    # Matches www.h3c.com with h3c.com; enough to rank, never to authorize a source.
    parts = host.split(".")
    return ".".join(parts[-3:] if len(parts) > 2 and len(parts[-1]) == 2 else parts[-2:])


async def known_domains(session, vendor: str) -> list[str]:
    """Domains this organization already recorded for the same vendor's products."""
    rows = (
        await session.scalars(
            select(ProductRevision)
            .join(Product, Product.id == ProductRevision.product_id)
            .where(ProductRevision.revision == Product.current_revision)
        )
    ).all()
    domains = set()
    for row in rows:
        if str(row.data.get("vendor", "")).strip().lower() != vendor.strip().lower():
            continue
        for field in ("official_url", "whitepaper_url"):
            value = row.data.get(field)
            if isinstance(value, str) and host_of(value):
                domains.add(registrable(host_of(value)))
    return sorted(domains)


async def inputs(session, actor, task_id, body, provider):
    actor.require("resource:read")
    extraction, _ = await cards.extraction_scope(session, task_id, body.extraction_job_id)
    selection = await session.get(TaskResource, body.task_resource_id)
    if selection is None or selection.task_id != task_id:
        raise not_found()
    if not selection.active:
        images.fail("inactive_selection", "Product selection is no longer active", 409, 4)
    revision = await session.get(ProductRevision, selection.product_revision_id)
    if revision is None:
        raise not_found()
    manifest = {
        "task_id": str(task_id),
        "extraction_job_id": str(extraction.id),
        "task_resource_id": str(selection.id),
        "product_revision_id": str(selection.product_revision_id),
        "queries": queries(revision.data),
        "known_domains": await known_domains(session, revision.data["vendor"]),
        "search_identity": provider.identity if provider else None,
        "query_version": QUERY_VERSION,
    }
    return extraction, manifest


@task_authorized("screenshot:write", write=True)
async def submit(session, actor, task_id, body, provider):
    actor = await cards.access(session, actor, "screenshot:write")
    extraction, manifest = await inputs(session, actor, task_id, body, provider)
    input_hash = images.digest(manifest)
    blocker = None if provider else "search_unavailable"
    if body.dry_run:
        from app.services import budget_preflight

        return await budget_preflight.attach(
            session,
            {
                "dry_run": True,
                "input_hash": input_hash,
                "queries": manifest["queries"],
                "search_identity": provider.public_identity if provider else None,
                "admission_blocker": blocker,
            },
            command="evidence search",
            task_id=task_id,
            input_hash=input_hash,
            quotes=[provider.quote(query) for query in manifest["queries"]] if provider else [],
            planned_calls=len(manifest["queries"]) if provider else None,
        ), None
    if body.expected_input_hash != input_hash:
        images.fail("search_input_changed", "Inputs changed since the preview", 409, 3)
    if blocker:
        images.fail(blocker, "Vendor search is not configured", 409, 4)
    await cards.task_lock(session, task_id)
    return await create_job(
        session, actor, task_id, extraction, "screenshot_search", manifest, body.retry
    )


async def rebuild(session, actor, job, provider):
    from app.schemas.screenshot_contracts import VendorSearchInput

    manifest = job.result["submission"]["input_manifest"]
    body = VendorSearchInput(
        extraction_job_id=UUID(manifest["extraction_job_id"]),
        task_resource_id=UUID(manifest["task_resource_id"]),
        dry_run=True,
    )
    _, current = await inputs(session, actor, job.task_id, body, provider)
    if current != manifest:
        images.fail("search_input_changed", "Fixed search inputs changed", 409, 3)
    return manifest


def ranked(hits, domains: list[str]) -> list[dict]:
    """Keep capturable https URLs once each; known vendor domains and PDFs first."""
    seen: dict[str, dict] = {}
    for order, hit in enumerate(hits):
        try:
            url = canonical_url(hit.url)
        except FetchDenied:
            continue
        if url in seen:
            seen[url]["engines"] = sorted(set(seen[url]["engines"]) | set(hit.engines))
            continue
        host = host_of(url)
        seen[url] = {
            "url": url,
            "title": hit.title or host,
            "engines": sorted(set(hit.engines)),
            "host": host,
            "pdf": urlsplit(url).path.lower().endswith(".pdf"),
            "known_vendor_domain": registrable(host) in domains,
            "order": order,
        }
    rows = sorted(
        seen.values(),
        key=lambda row: (not row["known_vendor_domain"], not row["pdf"], row["order"]),
    )
    return rows[:MAX_CANDIDATES]


def candidate_view(row: ScreenshotSearchCandidate, meta: dict) -> dict:
    return {
        "id": str(row.id),
        "ref": row.ref,
        "url": row.source_url,
        "title": row.title,
        "host": meta["host"],
        "engines": meta["engines"],
        "pdf": meta["pdf"],
        "known_vendor_domain": meta["known_vendor_domain"],
    }


async def view(session, run: ScreenshotSearchRun) -> dict:
    rows = (
        await session.scalars(
            select(ScreenshotSearchCandidate)
            .where(ScreenshotSearchCandidate.search_run_id == run.id)
            .order_by(ScreenshotSearchCandidate.ref)
        )
    ).all()
    meta = run.manifest["candidates"]
    ordered = sorted(rows, key=lambda row: meta[row.ref]["rank"])
    return {
        "id": str(run.id),
        "task_id": str(run.task_id),
        "extraction_job_id": str(run.extraction_job_id),
        "task_resource_id": str(run.task_resource_id),
        "product_revision_id": str(run.product_revision_id),
        "job_id": str(run.job_id),
        "input_hash": run.input_hash,
        "queries": run.manifest["queries"],
        "unresponsive_engines": run.manifest["unresponsive_engines"],
        "candidates": [candidate_view(row, meta[row.ref]) for row in ordered],
    }


async def process(execution, provider):
    if provider is None:
        raise ProviderFailure("Vendor search is not configured", code="search_unavailable")
    async with execution.db.transaction(execution.org_id) as session:
        job = await execution.owned_job(session)
        actor = await cards.access(session, worker(job), "screenshot:write")
        manifest = await rebuild(session, actor, job, provider)
        task_id = job.task_id
    hits, unresponsive = [], set()
    execution.plan(len(manifest["queries"]))
    for query in manifest["queries"]:
        result = await provider.search(query)
        hits.extend(result.hits)
        unresponsive.update(result.unresponsive_engines)
    candidates = ranked(hits, manifest["known_domains"])

    async with execution.db.transaction(execution.org_id) as session:
        await cards.task_lock(session, task_id)
        job = await execution.owned_job(session)
        actor = await cards.access(session, worker(job), "screenshot:write")
        await rebuild(session, actor, job, provider)
        run = ScreenshotSearchRun(
            id=uuid4(),
            org_id=execution.org_id,
            task_id=task_id,
            extraction_job_id=UUID(manifest["extraction_job_id"]),
            task_resource_id=UUID(manifest["task_resource_id"]),
            product_revision_id=UUID(manifest["product_revision_id"]),
            job_id=job.id,
            input_hash=images.digest(manifest),
            manifest={
                "queries": manifest["queries"],
                "search_identity": manifest["search_identity"],
                "query_version": QUERY_VERSION,
                "unresponsive_engines": sorted(unresponsive),
                "searched_at": datetime.now(UTC).isoformat(),
                "candidates": {
                    f"c{rank + 1:02d}": {
                        key: row[key] for key in ("host", "engines", "pdf", "known_vendor_domain")
                    }
                    | {"rank": rank}
                    for rank, row in enumerate(candidates)
                },
            },
        )
        session.add(run)
        await session.flush()
        for rank, row in enumerate(candidates):
            session.add(
                ScreenshotSearchCandidate(
                    id=uuid4(),
                    org_id=execution.org_id,
                    task_id=task_id,
                    extraction_job_id=run.extraction_job_id,
                    search_run_id=run.id,
                    ref=f"c{rank + 1:02d}",
                    source_url=row["url"],
                    title=row["title"],
                )
            )
        await session.flush()
        audit(
            session,
            actor,
            "screenshot.vendor_search",
            run.id,
            {
                "job_id": str(job.id),
                "input_hash": run.input_hash,
                "candidate_count": len(candidates),
            },
        )
        job.status, job.finished_at = "succeeded", datetime.now(UTC)
        job.result = {
            "submission": job.result["submission"],
            "vendor_search": await view(session, run),
            "cost": await job_cost(session, job.id),
        }


async def check_job_access(session, actor, job):
    actor = await cards.access(session, actor, "screenshot:read")
    actor.require("resource:read")
    manifest = job.result["submission"]["input_manifest"]
    selection = await session.get(TaskResource, UUID(manifest["task_resource_id"]))
    if selection is None or selection.task_id != job.task_id:
        raise not_found()
    return actor


@task_authorized("screenshot:read", parent=("search_id", "screenshot_search_runs"))
async def show(session, actor, search_id) -> ScreenshotSearchRun:
    actor = await cards.access(session, actor, "screenshot:read")
    actor.require("resource:read")
    run = await session.get(ScreenshotSearchRun, search_id)
    if run is None:
        raise not_found()
    return run


@task_authorized(
    "screenshot:write", parent=("candidate_id", "screenshot_search_candidates"), write=True
)
async def adopt(session, actor, candidate_id, body) -> dict:
    """Record a person's chosen URL as a new product revision and select it on the task."""
    actor = await cards.access(session, actor, "screenshot:write")
    candidate = await session.get(ScreenshotSearchCandidate, candidate_id)
    if candidate is None:
        raise not_found()
    run = await show(session, actor, candidate.search_run_id)
    await cards.task_lock(session, run.task_id)
    selection = await session.get(TaskResource, run.task_resource_id)
    if selection is None or selection.task_id != run.task_id:
        raise not_found()
    if not selection.active or selection.product_revision_id != run.product_revision_id:
        images.fail("inactive_selection", "The searched product selection changed", 409, 4)
    current = await session.get(ProductRevision, selection.product_revision_id)
    if current is None:
        raise not_found()
    data = ProductData.model_validate({**current.data, body.field: candidate.source_url})
    revision = await resources.update_product(
        session,
        actor,
        selection.product_id,
        ProductUpdate(expected_revision=body.expected_product_revision, data=data),
    )
    selected = await resources.select_product(
        session,
        actor,
        run.task_id,
        TaskProductSelection(
            product_id=selection.product_id,
            revision=revision["revision"],
            lot=selection.lot or None,
        ),
    )
    audit(
        session,
        actor,
        "screenshot.vendor_search.adopt",
        candidate.id,
        {
            "search_run_id": str(run.id),
            "field": body.field,
            "new_revision_id": revision["id"],
            "task_resource_id": selected["id"],
        },
    )
    return {"candidate_id": str(candidate.id), "field": body.field, "selection": selected}
