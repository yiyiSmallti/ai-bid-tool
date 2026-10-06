"""Draft reads through API/Processor, compared to the original per-item path.

Failure modes enumerated before these gates:
* N+1 reads: SQL count must be bounded for both one draft and its history list,
  regardless of requirement, card, evidence-link or retained-draft counts.
* Projection drift: row/comply-only/gap snapshots, source order, evidence order,
  partial completion, negative deviations and invalidation order must exactly
  match the retained slow reference, for current and stale drafts.
* Contract drift: versioned reads retain the complete 4.0 Result and nullable
  agent provenance, while legacy reads omit that attachment and retain the
  strict three-field Cost.
* Freshness drift: changed card pointers, newly created cards for old gaps,
  replaced material selections, repaired quotes and invalid citations must keep
  their precedence, including already-reported invalid-citation gaps.
* Access drift: foreign org drafts and history stay hidden; scoped tokens still
  need grants for historical page evidence and model inputs even when those
  inputs were not cited or a comply-only decision takes eligibility precedence.
* Query-count evidence must use fresh transactions (no warmed identity map), and
  count every SQL statement on the restricted application's engine, including
  authentication. Gate artifacts contain counts and hashes only.
"""

from __future__ import annotations

import hashlib
import io
import json
from contextlib import contextmanager
from uuid import UUID

import httpx
from app.api.main import create_app
from app.core.config import Settings
from app.core.errors import not_found
from app.models.entities import Requirement
from app.models.response_cards import DraftRun, ResponseCard, ResponseCardRevision, ResponseItem
from app.providers.llm import OpenAICompatibleExtractor
from app.schemas.compatibility import legacy_projection
from app.schemas.contracts import Result
from app.schemas.response_card_contracts import DraftView
from app.services import drafts, task_cosign
from app.services import response_cards as cards
from app.services.agent_tools import provenance
from app.services.auth import ROLE_SCOPES, Identity
from conftest import FakeQueue
from docx import Document
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from task_fixtures import reviewer_header
from test_response_cards import (
    PRODUCT_DATA,
    create_card,
    login,
    require_action,
    run_document_job,
    select_real_materials,
    set_role,
)

TABLES = drafts.TABLES


async def slow_show_draft(session: AsyncSession, actor: Identity, draft_id: UUID):
    actor = await cards.access(session, actor, "draft:read")
    run = await session.get(DraftRun, draft_id)
    if run is None:
        raise not_found()
    rows = list(
        (await session.scalars(select(ResponseItem).where(ResponseItem.draft_id == run.id))).all()
    )
    positions = {
        entry["requirement_id"]: index
        for index, entry in enumerate(run.input_manifest["requirements"])
    }
    rows.sort(key=lambda item: positions[str(item.requirement_id)])
    tables = {key: [] for key in TABLES}
    _, _, current_manifest, _, _ = await drafts.assemble(
        session, actor, run.task_id, run.extraction_job_id
    )
    current_inputs = {entry["requirement_id"]: entry for entry in current_manifest["requirements"]}
    invalidated = [
        entry["requirement_id"]
        for entry in run.input_manifest["requirements"]
        if current_inputs.get(entry["requirement_id"]) != entry
    ]
    comply_only, gaps = [], []
    for item in rows:
        requirement = await session.get(Requirement, item.requirement_id)
        if requirement is None:
            raise not_found()
        card = await session.get(ResponseCard, item.card_id) if item.card_id else None
        if card is None:
            current = await session.scalar(
                select(ResponseCard).where(ResponseCard.requirement_id == item.requirement_id)
            )
            if current is not None:
                invalidated.append(str(item.requirement_id))
        elif card.current_revision_id != item.card_revision_id:
            invalidated.append(str(item.requirement_id))
        elif item.kind != "gap":
            revision = await session.get(ResponseCardRevision, item.card_revision_id)
            if revision is None:
                raise not_found()
            view = await cards.card_view(session, actor, card, revision, requirement)
            if view["eligibility"] != ("eligible" if item.kind == "row" else "comply_only"):
                invalidated.append(str(item.requirement_id))
        # A citation already listed as an invalid_citation gap is not a change since generation.
        already_reported = item.kind == "gap" and "invalid_citation" in (item.gap_reasons or [])
        if (
            requirement
            and not already_reported
            and not await cards.citation_valid(session, requirement)
        ):
            invalidated.append(str(item.requirement_id))
        entry = {
            "requirement_id": str(item.requirement_id),
            "card_id": str(item.card_id) if item.card_id else None,
            "card_revision_id": str(item.card_revision_id) if item.card_revision_id else None,
            "tender_clause": item.source,
            "location_label": item.location_label,
        }
        # The slow reference keeps its own reads and consumption gate; only
        # reason precedence is shared with the production projection.
        if (
            card is not None
            and item.kind != "gap"
            and not await session.scalar(
                select(func.team_cosign_card_approved(actor.org_id, card.id))
            )
        ):
            current_revision = await session.get(ResponseCardRevision, card.current_revision_id)
            if current_revision is None:
                raise not_found()
            current_view = await cards.card_view(
                session, actor, card, current_revision, requirement
            )
            review = (await task_cosign.projections(session, actor.org_id, [card.id]))[card.id]
            entry["reasons"] = drafts.gap_reasons(
                current_view,
                review,
                valid_citation=await cards.citation_valid(session, requirement),
                quote_current=cards.revision_quote_hash(current_revision, requirement)
                == cards.quote_hash(requirement.quote),
                generation_stale=await cards.generation_materials_stale(
                    session, actor, current_revision
                ),
            )
            gaps.append(entry)
            continue
        if item.kind == "row":
            if item.card_revision_id is None or item.table is None:
                cards.fail("invalid_draft", "Draft row is incomplete", 500, 4)
            entry |= {
                "category": item.category,
                "starred": item.starred,
                "table": item.table,
                **{key: getattr(item, key) for key in cards.CONTENT_FIELDS},
                "evidence": [
                    await cards.evidence_view(session, actor, row)
                    for row in await cards.linked_evidence(session, item.card_revision_id)
                ],
            }
            tables[item.table].append(entry)
        elif item.kind == "comply_only":
            entry |= {"disposition_by": item.disposition_by, "disposition_at": item.disposition_at}
            comply_only.append(entry)
        else:
            entry["reasons"] = item.gap_reasons
            gaps.append(entry)
    memory_warnings, memory_lineage = set(), []
    for item in rows:
        revision = (
            await session.get(ResponseCardRevision, item.card_revision_id)
            if item.card_revision_id
            else None
        )
        if revision is not None:
            warning, lineage = await cards.generation_memory_state(
                session, actor, revision, item.requirement_id
            )
            if warning:
                memory_warnings.add(warning + ":" + str(item.requirement_id))
            if lineage:
                memory_lineage.append({"requirement_id": str(item.requirement_id), **lineage})
    return DraftView.model_validate(
        {
            "agent_provenance": await provenance(session, run.generation_job_id),
            "id": run.id,
            "org_id": run.org_id,
            "task_id": run.task_id,
            "extraction_job_id": run.extraction_job_id,
            "generation_job_id": run.generation_job_id,
            "completion": "partial" if gaps else "complete",
            "validity": "stale" if invalidated else "current",
            "input_hash": run.input_hash,
            "tables": tables,
            "comply_only": comply_only,
            "gaps": gaps,
            "invalidated_requirements": list(dict.fromkeys(invalidated)),
            "memory_warnings": sorted(memory_warnings),
            "memory_lineage": memory_lineage,
        }
    ).model_dump(mode="json")


@contextmanager
def statement_count(app):
    """Count statements, not parameters or text, on fresh API transactions only."""
    counter = {"statements": 0}

    def executed(conn, cursor, statement, parameters, context, executemany):
        counter["statements"] += 1

    engine = app.state.db.engine.sync_engine
    event.listen(engine, "before_cursor_execute", executed)
    try:
        yield counter
    finally:
        event.remove(engine, "before_cursor_execute", executed)


def synthetic_word(count: int) -> bytes:
    document = Document()
    for index in range(count):
        document.add_paragraph(
            f"Synthetic clause {index:04d}: delivery item {index:04d} must meet obligation {index:04d}."
        )
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def synthetic_vendor(request):
    """Drive the real extraction and generation adapters with unique source text."""
    import re

    from test_llm_providers import provider_reply

    sent = json.loads(request.content)["messages"][-1]["content"]
    if sent.startswith("{"):
        inputs = json.loads(sent)
        return provider_reply(
            "openai",
            [
                {
                    "requirement_id": requirement["requirement_id"],
                    "response_kind": "commitment",
                    "suggested_disposition": "respond",
                    "response_text": "Synthetic model commitment informed by the fixed materials.",
                    "deviation": "none",
                    "deviation_note": "The model input materials remain dependencies, without citations.",
                    "evidence": [],
                }
                for requirement in inputs["requirements"]
            ],
        )
    items = []
    for ref, quote in re.findall(r'<block id="([^"]+)">\n(.*?)\n</block>', sent, re.S):
        index = int(re.search(r"Synthetic clause (\d+)", quote)[1])
        items.append(
            {
                "category": "qualification"
                if index % 6 == 1
                else "scoring"
                if index % 12 == 11
                else "technical",
                "starred": index % 12 == 0,
                "text": f"Synthetic requirement {index:04d}",
                "ref": ref,
                "quote": quote,
                "condition": None,
            }
        )
    return provider_reply("openai", items)


async def api_data(api, method, path, header, **kwargs):
    response = await api.request(method, path, headers=header, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def build_large_draft(api, app, header, tenants, admin_engine, tmp_path, count):
    org, user = tenants["orgs"][0], tenants["users"][0]
    set_role(admin_engine, org, user, "admin")
    task = (
        await api_data(api, "POST", "/tasks", header, json={"name": f"Synthetic batch {count}"})
    )["id"]
    uploaded = await api_data(
        api,
        "POST",
        f"/tasks/{task}/documents",
        header,
        files={
            "file": (
                f"unique-{count}.docx",
                synthetic_word(count),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    await run_document_job(api, app, header, uploaded["id"], "parse")
    extraction = await run_document_job(api, app, header, uploaded["id"], "extract")
    response = await api.get(
        f"/tasks/{task}/requirements", headers=header, params={"job": extraction}
    )
    assert response.status_code == 200, response.text
    requirements = response.json()["items"]
    assert len(requirements) == count
    (
        product,
        product_selection,
        certificate,
        cert_selection,
        page_source,
    ) = await select_real_materials(api, header, task, tmp_path)
    feature = await api_data(
        api,
        "POST",
        "/resources/features",
        header,
        json={
            "data": {
                "product_id": product["product_id"],
                "name": "Synthetic feature",
                "description": "Declared synthetic delivery workflow.",
                "status": "implemented",
            }
        },
    )
    feature_selection = await api_data(
        api, "POST", f"/tasks/{task}/features", header, json={"feature_id": feature["feature_id"]}
    )
    profile = await api_data(
        api,
        "POST",
        "/resources/profiles",
        header,
        json={"data": {"name": "Synthetic organization profile"}},
    )
    profile_selection = await api_data(
        api, "POST", f"/tasks/{task}/profiles", header, json={"profile_id": profile["profile_id"]}
    )
    generation = await api_data(
        api,
        "POST",
        f"/tasks/{task}/cards/generations",
        header,
        json={
            "extraction_job_id": extraction,
            "requirement_ids": [requirements[index]["id"] for index in (8, 9)],
        },
    )
    await app.state.processor(str(org), generation["job_id"])
    status = await api_data(api, "GET", f"/jobs/{generation['job_id']}", header)
    assert status["status"] == "succeeded", status
    slots = (
        await api.get(f"/tasks/{task}/cards", headers=header, params={"job": extraction})
    ).json()["items"]
    generated = {slot["requirement_id"]: slot["card"] for slot in slots if slot["card"]}
    created = {}
    for index, requirement in enumerate(requirements):
        if index % 6 in {3, 4}:
            continue
        if index == 8:
            created[index] = generated[requirement["id"]]
            continue
        evidence = []
        if index % 6 == 0:
            evidence = [
                {
                    "kind": "product",
                    "selection_id": product_selection["id"],
                    "field_path": "model",
                    "quote": PRODUCT_DATA["model"],
                },
                {
                    "kind": "feature",
                    "selection_id": feature_selection["id"],
                    "field_path": "description",
                    "quote": "Declared synthetic delivery workflow.",
                },
            ]
        elif index % 6 == 1:
            evidence = [
                {
                    "kind": "certificate_pdf_page",
                    "evidence_source_id": page_source["id"],
                    "quote": "quality management certificate QMS-2026.",
                },
                {
                    "kind": "certificate",
                    "selection_id": cert_selection["id"],
                    "field_path": "number",
                    "quote": "QMS-2026",
                },
                {
                    "kind": "org_profile",
                    "selection_id": profile_selection["id"],
                    "field_path": "name",
                    "quote": "Synthetic organization profile",
                },
            ]
        created[index] = await create_card(
            api,
            header,
            task,
            extraction,
            requirement,
            {
                "response_kind": "evidence" if evidence else "commitment",
                "response_text": f"Synthetic reviewed response {index:04d}.",
                "deviation": "negative" if index % 6 == 2 else "none",
                "deviation_note": f"Synthetic correspondence and delivery difference {index:04d}.",
                "evidence": evidence,
            },
        )
    for role in ("technical", "bidder"):
        set_role(admin_engine, org, user, role)
        _, review_header = await reviewer_header(
            api,
            admin_engine,
            org,
            UUID(task),
            "technical" if role == "technical" else "commercial",
        )
        for index, card in list(created.items()):
            domain = "commercial" if role == "bidder" else "technical"
            if card["review_domain"] != domain:
                continue
            card = await require_action(api, review_header, card, "submit")
            if index % 6 == 5:
                card = await require_action(
                    api, review_header, card, "reject", reason="Synthetic unresolved response."
                )
            else:
                card = await require_action(
                    api,
                    review_header,
                    card,
                    "confirm",
                    reviewed_evidence_ids=[e["id"] for e in card["evidence"]],
                    reviewed_warning_codes=card["warning_codes"],
                    reason="Each fixed synthetic declaration and page has been reviewed.",
                )
            created[index] = card
    set_role(admin_engine, org, user, "technical")
    await api_data(
        api,
        "POST",
        f"/tasks/{task}/cards/dispositions",
        header,
        json={
            "extraction_job_id": extraction,
            "items": [
                {
                    "requirement_id": requirement["id"],
                    "expected_revision": generated[requirement["id"]]["revision"]
                    if index == 9
                    else None,
                    "disposition": "comply_only",
                    "reason": f"Synthetic procedural clause {index:04d}.",
                }
                for index, requirement in enumerate(requirements)
                if index % 6 == 3
            ],
        },
    )
    set_role(admin_engine, org, user, "admin")
    return {
        "task": task,
        "extraction": extraction,
        "requirements": requirements,
        "cards": created,
        "product": product,
        "certificate": certificate,
    }


async def assemble_draft(api, app, header, fixture):
    receipt = await api_data(
        api,
        "POST",
        f"/tasks/{fixture['task']}/drafts",
        header,
        json={"extraction_job_id": fixture["extraction"]},
    )
    await app.state.processor(header["X-Org-Id"], receipt["job_id"])
    status = await api_data(api, "GET", f"/jobs/{receipt['job_id']}", header)
    assert status["status"] == "succeeded", status
    return status["result"]["draft_id"]


async def compare_reads(api, app, header, actor, fixture, draft_ids):
    references, slow_counts = {}, []
    scope_warnings = []
    for draft_id in draft_ids:
        # New transactions keep the reference independent of any preloaded graph.
        with statement_count(app) as counter:
            async with app.state.db.transaction(actor.org_id) as session:
                references[draft_id] = await slow_show_draft(session, actor, UUID(draft_id))
                scope_warnings = await cards.scope_warnings(session, UUID(fixture["extraction"]))
        slow_counts.append(counter["statements"])
    fast_counts, legacy_counts = [], []
    for draft_id in draft_ids:
        with statement_count(app) as counter:
            response = await api.get(f"/v4/drafts/{draft_id}", headers=header)
        assert response.status_code == 200, response.text
        assert response.headers["X-Bid-Contract-Version"] == "4.0"
        reference = references[draft_id]
        warnings = [
            f"negative_deviation:{row['requirement_id']}"
            for rows in reference["tables"].values()
            for row in rows
            if row["deviation"] == "negative"
        ] + scope_warnings
        if reference["validity"] == "stale":
            warnings.append("stale_draft")
        # Identical except the request timing.
        expected_result = Result(
            ok=reference["completion"] != "partial",
            command="draft show",
            data=reference,
            warnings=warnings,
        ).model_dump(mode="json")
        assert {**response.json(), "duration_ms": 0} == expected_result
        with statement_count(app) as legacy_counter:
            legacy_response = await api.get(f"/drafts/{draft_id}", headers=header)
        assert legacy_response.status_code == 200, legacy_response.text
        assert legacy_response.headers["X-Bid-Contract-Version"] == "3.0"
        assert {**legacy_response.json(), "duration_ms": 0} == legacy_projection(expected_result)
        assert legacy_counter["statements"] == counter["statements"]
        legacy_counts.append(legacy_counter["statements"])
        partition = [row["requirement_id"] for rows in reference["tables"].values() for row in rows]
        partition += [row["requirement_id"] for row in reference["comply_only"] + reference["gaps"]]
        assert len(partition) == len(set(partition)) == len(fixture["requirements"])
        assert counter["statements"] <= 40
        fast_counts.append(counter["statements"])
        assert slow_counts[len(fast_counts) - 1] > counter["statements"]
    with statement_count(app) as counter:
        response = await api.get(
            f"/v4/tasks/{fixture['task']}/drafts",
            headers=header,
            params={"job": fixture["extraction"]},
        )
    assert response.status_code == 200, response.text
    assert response.headers["X-Bid-Contract-Version"] == "4.0"
    summary_keys = (
        "id",
        "task_id",
        "extraction_job_id",
        "generation_job_id",
        "status",
        "completion",
        "validity",
        "input_hash",
        "invalidated_requirements",
        "memory_warnings",
        "memory_lineage",
        "agent_provenance",
    )
    async with app.state.db.transaction(actor.org_id) as session:
        runs = (
            await session.scalars(
                select(DraftRun)
                .where(
                    DraftRun.task_id == UUID(fixture["task"]),
                    DraftRun.extraction_job_id == UUID(fixture["extraction"]),
                )
                .order_by(DraftRun.created_at, DraftRun.id)
            )
        ).all()
        expected = [
            {key: references[str(run.id)][key] for key in summary_keys} | {"summary": run.summary}
            for run in runs
        ]
    expected_result = Result(
        ok=True,
        command="draft list",
        data={"task_id": fixture["task"], "extraction_job_id": fixture["extraction"]},
        items=expected,
        warnings=scope_warnings,
    ).model_dump(mode="json")
    assert {**response.json(), "duration_ms": 0} == expected_result
    assert counter["statements"] <= 40
    with statement_count(app) as legacy_counter:
        legacy_response = await api.get(
            f"/tasks/{fixture['task']}/drafts",
            headers=header,
            params={"job": fixture["extraction"]},
        )
    assert legacy_response.status_code == 200, legacy_response.text
    assert legacy_response.headers["X-Bid-Contract-Version"] == "3.0"
    assert {**legacy_response.json(), "duration_ms": 0} == legacy_projection(expected_result)
    assert legacy_counter["statements"] == counter["statements"]
    return {
        "detail_queries": fast_counts,
        "list_queries": counter["statements"],
        "legacy_detail_queries": legacy_counts,
        "legacy_list_queries": legacy_counter["statements"],
        "slow_detail_queries": slow_counts,
        "response_hashes": [
            hashlib.sha256(json.dumps(view, sort_keys=True).encode()).hexdigest()
            for view in references.values()
        ],
        "validity": [view["validity"] for view in references.values()],
        "partitions": [
            {
                "rows": sum(len(rows) for rows in view["tables"].values()),
                "comply_only": len(view["comply_only"]),
                "gaps": len(view["gaps"]),
            }
            for view in references.values()
        ],
    }


async def test_large_draft_reads_match_reference_and_have_bounded_queries(
    tenants, tmp_path, admin_engine
):
    settings = Settings(
        data_dir=tmp_path,
        llm_provider="openai",
        llm_model="synthetic-model",  # the model provider_reply answers as
        llm_base_url="https://synthetic.example.invalid/v1",
    )
    provider = OpenAICompatibleExtractor(settings, httpx.MockTransport(synthetic_vendor))
    app = create_app(settings, llm=provider, queue=FakeQueue())
    org, user = tenants["orgs"][0], tenants["users"][0]
    actor = Identity(user, org, ROLE_SCOPES["admin"], "admin")
    reports = []
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api,
    ):
        header, other = [
            await login(api, organization, label)
            for organization, label in zip(tenants["orgs"], ("a", "b"), strict=True)
        ]
        for count in (12, 324):
            fixture = await build_large_draft(
                api, app, header, tenants, admin_engine, tmp_path, count
            )
            first = await assemble_draft(api, app, header, fixture)
            current = await compare_reads(api, app, header, actor, fixture, [first])
            assert current["validity"] == ["current"]
            assert current["partitions"] == [
                {"rows": count // 2, "comply_only": count // 6, "gaps": count // 3}
            ]
            # Normal API writes invalidate pointers, old missing-card gaps and both
            # direct Evidence and uncited model-input material dependencies.
            set_role(admin_engine, org, user, "technical")
            await require_action(
                api, header, fixture["cards"][0], "reopen", reason="Synthetic renewed review."
            )
            await create_card(
                api,
                header,
                fixture["task"],
                fixture["extraction"],
                fixture["requirements"][4],
                {
                    "response_kind": "commitment",
                    "response_text": "A new response for an old gap.",
                    "deviation": "none",
                    "deviation_note": "This remains an unconfirmed draft.",
                    "evidence": [],
                },
            )
            set_role(admin_engine, org, user, "admin")
            await api_data(
                api,
                "POST",
                f"/resources/products/{fixture['product']['product_id']}/revisions",
                header,
                json={
                    "expected_revision": 1,
                    "data": PRODUCT_DATA | {"model": "Replacement declaration"},
                },
            )
            await api_data(
                api,
                "POST",
                f"/tasks/{fixture['task']}/products",
                header,
                json={"product_id": fixture["product"]["product_id"]},
            )
            await api_data(
                api,
                "POST",
                f"/resources/certificates/{fixture['certificate']['certificate_id']}/revisions",
                header,
                json={
                    "expected_revision": 2,
                    "data": fixture["certificate"]["data"] | {"number": "QMS-2027"},
                },
            )
            await api_data(
                api,
                "POST",
                f"/tasks/{fixture['task']}/certificates",
                header,
                json={"certificate_id": fixture["certificate"]["certificate_id"]},
            )
            # Simulate retained legacy citation changes, just as the citation-repair
            # gates do. These writes are confined to the disposable owner's fixture.
            from sqlalchemy import text

            with admin_engine.begin() as connection:
                connection.execute(
                    text("UPDATE requirements SET quote = :quote WHERE id = :id"),
                    {"quote": "delivery item 0002", "id": fixture["requirements"][2]["id"]},
                )
                connection.execute(
                    text("UPDATE requirements SET quote = :quote WHERE id = :id"),
                    {
                        "quote": "Absent synthetic source quote.",
                        "id": fixture["requirements"][5]["id"],
                    },
                )
            stale = await compare_reads(api, app, header, actor, fixture, [first])
            assert stale["validity"] == ["stale"]
            second = await assemble_draft(api, app, header, fixture)
            assert second != first
            both = await compare_reads(api, app, header, actor, fixture, [first, second])
            assert both["validity"] == ["stale", "current"]
            assert current["list_queries"] == stale["list_queries"] == both["list_queries"]
            assert (
                len(
                    set(
                        current["detail_queries"] + stale["detail_queries"] + both["detail_queries"]
                    )
                )
                == 1
            )
            for path in (
                f"/v4/drafts/{first}",
                f"/v4/tasks/{fixture['task']}/drafts?job={fixture['extraction']}",
                f"/drafts/{first}",
                f"/tasks/{fixture['task']}/drafts?job={fixture['extraction']}",
            ):
                foreign = await api.get(path, headers=other)
                assert foreign.status_code == 404
            from test_card_generation import token_header

            for missing_scope in ("resource:read", "certificate:file:read", "profile:read"):
                token = await token_header(
                    api,
                    header,
                    scopes=[
                        scope
                        for scope in (
                            "draft:read",
                            "task:read",
                            "memory:read",
                            "memory:retrieve",
                            "resource:read",
                            "certificate:read",
                            "certificate:file:read",
                            "evidence:source:read",
                            "profile:read",
                        )
                        if scope != missing_scope
                    ],
                )
                for path in (
                    f"/v4/drafts/{first}",
                    f"/v4/tasks/{fixture['task']}/drafts?job={fixture['extraction']}",
                    f"/drafts/{first}",
                    f"/tasks/{fixture['task']}/drafts?job={fixture['extraction']}",
                ):
                    denied = await api.get(path, headers=token)
                    assert denied.status_code == 403, denied.text
            reports.append(
                {"requirements": count, "current": current, "stale": stale, "history": both}
            )
    assert reports[0]["history"]["detail_queries"] == reports[1]["history"]["detail_queries"]
    assert reports[0]["history"]["list_queries"] == reports[1]["history"]["list_queries"]
    assert (
        reports[1]["history"]["slow_detail_queries"][0]
        > reports[0]["history"]["slow_detail_queries"][0] * 5
    )
    (tmp_path / "draft-batch-read-result.json").write_text(json.dumps(reports, indent=2) + "\n")
