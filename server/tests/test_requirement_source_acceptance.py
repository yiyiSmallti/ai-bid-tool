"""B02 source recovery and atomic decisions through the real API/PostgreSQL.

Failure inventory recorded before these acceptance scenarios were implemented:
* Manual entry must reject normalized-only, absent, ambiguous, wrong-page,
  wrong-Word-location, unverified OCR and oversized sources without creating rows.
* Rejected receipts cannot transfer across documents/jobs or survive a changed hash.
* Recovery of an all-rejected extraction must preserve its failure and metered cost,
  create an explicit unconfirmed manual scope, and make no new provider calls.
* Mixed/stale batches must roll back every decision; competing decisions with the
  same expected revision must yield one success and one conflict.
* Legacy publication without review metadata must remain visibly unconfirmed and
  support owner initialization through a bounded, explicit 100-item batch.

Providers are synthetic; parsing, authentication, transactions and DB triggers are real.
No trigger is disabled, and no fixture inserts a confirmed review. Run artifacts live
under data/work/requirement-confirmation/source-recovery, outside repository documentation.
"""

import asyncio
import copy
import io
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.schemas.contracts import (
    Category,
    ExtractedRequirement,
    Extraction,
    LLMResult,
    ProviderUsage,
)
from app.services import requirement_confirmation
from app.services.extraction import location_of
from docx import Document
from fakes import source_for
from sqlalchemy import text
from test_requirement_confirmation import decision, post_decision, review
from test_response_cards import (
    create_tender,
    labelled_pdf,
    phase_one_client,
    run_document_job,
    sanitized_artifact,
)

ARTIFACTS = (
    Path(__file__).resolve().parents[2] / "data/work/requirement-confirmation/source-recovery"
)


def record(name, **values):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / f"{name}.json").write_text(
        json.dumps(
            sanitized_artifact(
                {
                    "command": ".venv/bin/pytest server/tests/test_requirement_source_acceptance.py -q",
                    "scenario": name,
                    **values,
                }
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


def manual_input(content, **changes):
    return {"content": copy.deepcopy(content), "reason": "Inspect synthetic original", **changes}


async def preview(api, header, task, body):
    return await api.post(
        f"/v4/tasks/{task}/requirements/manual-preview", headers=header, json=body
    )


async def review_counts(app, org, task):
    async with app.state.db.transaction(org) as session:
        return {
            table: await session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE task_id=:task"), {"task": UUID(task)}
            )
            for table in (
                "requirements",
                "requirement_reviews",
                "requirement_review_events",
                "requirement_review_requests",
            )
        }


async def upload_parse(api, app, header, task, name, content):
    response = await api.post(
        f"/tasks/{task}/documents", headers=header, files={"file": (name, content)}
    )
    assert response.status_code == 200, response.text
    document = response.json()["data"]["id"]
    parse_job = await run_document_job(api, app, header, document, "parse")
    response = await api.get(f"/documents/{document}/chunks", headers=header)
    assert response.status_code == 200, response.text
    return document, parse_job, response.json()["items"]


def word_bytes(paragraphs):
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


@pytest.mark.parametrize(
    "failure,status,code",
    [
        ("normalize_only", 422, "nonverbatim_quote"),
        ("missing", 422, "quote_not_at_position"),
        ("ambiguous", 422, "ambiguous_quote"),
        ("wrong_page", 422, "unverified_location"),
        ("unverified_ocr", 422, "unverified_location"),
        ("oversize_text", 422, None),
        ("oversize_quote", 422, None),
        ("oversize_request", 422, "input_too_large"),
    ],
)
async def test_manual_source_rejections_are_write_free(
    tenants, tmp_path, admin_engine, failure, status, code
):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, provider):
        header = headers[0]
        task, _, _, requirements = await create_tender(api, app, header, tmp_path)
        initial = await review(api, header, requirements[0]["id"])
        body = manual_input(initial["requirement"]["content"])
        source = body["content"]["source"]
        if failure == "normalize_only":
            source["quote"] = source["quote"].replace(" ", "")
        elif failure == "missing":
            source["quote"] = "This synthetic sentence is absent from the source."
        elif failure == "wrong_page":
            source["page"] += 1
        elif failure in {"ambiguous", "unverified_ocr"}:
            # Seed a parser-maintenance condition with every DB trigger still active.
            with admin_engine.begin() as connection:
                connection.execute(
                    text("SELECT set_config('app.current_org',:org,true)"),
                    {"org": header["X-Org-Id"]},
                )
                if failure == "ambiguous":
                    connection.execute(
                        text("UPDATE chunks SET text=text || :extra WHERE id=:id"),
                        {"extra": "\n" + source["quote"], "id": source["chunk_id"]},
                    )
                else:
                    connection.execute(
                        text("UPDATE chunks SET ocr=true,citation_verified=false WHERE id=:id"),
                        {"id": source["chunk_id"]},
                    )
        elif failure == "oversize_text":
            body["content"]["text"] = "x" * 20_001
        elif failure == "oversize_quote":
            source["quote"] = "x" * 20_001
        else:
            body["content"]["condition"] = {"description": "x" * (256 * 1024)}
        before = await review_counts(app, tenants["orgs"][0], task)
        provider_calls = provider.calls
        response = await preview(api, header, task, body)
        assert response.status_code == status, response.text
        if code:
            assert response.json()["data"]["error"]["code"] == code, response.text
        direct_save = await api.post(
            f"/v4/tasks/{task}/requirements/manual",
            headers=header,
            json={**body, "request_id": str(uuid4()), "expected_preview_hash": "0" * 64},
        )
        assert direct_save.status_code == status, direct_save.text
        if code:
            assert direct_save.json()["data"]["error"]["code"] == code, direct_save.text
        assert await review_counts(app, tenants["orgs"][0], task) == before
        assert provider.calls == provider_calls
        record(failure, status=response.status_code, expected_code=code, unchanged_counts=before)


async def test_manual_word_location_and_document_binding(tenants, tmp_path):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        header = headers[0]
        task, _, _, requirements = await create_tender(api, app, header, tmp_path)
        initial = await review(api, header, requirements[0]["id"])
        document, _, chunks = await upload_parse(
            api,
            app,
            header,
            task,
            "synthetic-word-location.docx",
            word_bytes(["Synthetic Word requirement with an exact structural location."]),
        )
        block = chunks[0]["blocks"][0]
        content = {
            "category": "technical",
            "text": block["text"],
            "source": {
                "document_id": document,
                "chunk_id": chunks[0]["id"],
                "location": location_of(block).model_dump(mode="json"),
                "quote": block["text"],
            },
        }
        valid = await preview(api, header, task, manual_input(content))
        assert valid.status_code == 200, valid.text
        wrong = manual_input(content)
        wrong["content"]["source"]["location"]["label"] += " fabricated label"
        rejected = await preview(api, header, task, wrong)
        assert rejected.status_code == 422, rejected.text
        assert rejected.json()["data"]["error"]["code"] == "unverified_location"
        cross_document = manual_input(
            content,
            extraction_job_id=initial["scope"]["extraction_job_id"],
            expected_set_revision=initial["scope"]["revision"],
        )
        rejected = await preview(api, header, task, cross_document)
        assert rejected.status_code == 404, rejected.text
        record("word-and-document-binding", valid_status=200, wrong_location=422, wrong_scope=404)


def provider_result(provider, items, rejected=()):
    return LLMResult(
        extraction=Extraction(items=items),
        rejected=list(rejected),
        usage=ProviderUsage(
            provider=provider.name,
            model=provider.model,
            version=provider.version,
            duration_ms=1,
            tokens=10,
            usd=0,
            test_only=True,
        ),
    )


async def test_all_rejected_recovery_preserves_receipt_cost_and_explicit_scope(
    tenants, tmp_path, monkeypatch
):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, provider):
        header = headers[0]
        created = await api.post(
            "/tasks", headers=header, json={"name": "Synthetic rejected recovery"}
        )
        assert created.status_code == 200, created.text
        task = created.json()["data"]["id"]
        document, parse_job, chunks = await upload_parse(
            api,
            app,
            header,
            task,
            "synthetic-rejected.pdf",
            labelled_pdf(["Exact recovery clause."]),
        )

        async def reject_all(chunks, schema):
            provider.calls += 1
            return provider_result(
                provider,
                [],
                [
                    {
                        "position": "page:1",
                        "quote": "Absent model quote",
                        "reason": "quote_not_at_position",
                    }
                ],
            )

        monkeypatch.setattr(provider, "_extract", reject_all)
        submitted = await api.post(f"/documents/{document}/extract", headers=header, json={})
        assert submitted.status_code == 200, submitted.text
        failed_job = submitted.json()["data"]["job_id"]
        await app.state.processor(header["X-Org-Id"], failed_job)
        failed = await api.get(f"/jobs/{failed_job}", headers=header)
        assert failed.status_code == 200, failed.text
        original = failed.json()["data"]
        assert original["status"] == "failed", original
        assert original["error"]["code"] == "invalid_citation", original
        receipts = await api.get(
            f"/v4/tasks/{task}/extractions/{failed_job}/rejected-items", headers=header
        )
        assert receipts.status_code == 200, receipts.text
        rejected_item = receipts.json()["items"][0]
        reference = {key: rejected_item[key] for key in ("job_id", "index", "summary_sha256")}
        source = {
            "document_id": document,
            "chunk_id": chunks[0]["id"],
            "page": chunks[0]["page"],
            "quote": "Exact recovery clause.",
        }
        body = manual_input(
            {"category": "technical", "text": "Recovered exact clause", "source": source},
            rejected_item=reference,
        )
        invalid_references = [
            ({**reference, "job_id": str(uuid4())}, 404),
            ({**reference, "job_id": parse_job}, 404),
            ({**reference, "summary_sha256": "0" * 64}, 409),
            ({**reference, "index": reference["index"] + 1}, 409),
        ]
        for invalid_ref, status in invalid_references:
            response = await preview(api, header, task, {**body, "rejected_item": invalid_ref})
            assert response.status_code == status, response.text
            if status == 409:
                assert response.json()["data"]["error"]["code"] == "rejected_reference_changed"
        other_doc, _, other_chunks = await upload_parse(
            api, app, header, task, "synthetic-other.pdf", labelled_pdf(["Other document clause."])
        )
        cross_document = copy.deepcopy(body)
        cross_document["content"]["source"] = {
            "document_id": other_doc,
            "chunk_id": other_chunks[0]["id"],
            "page": 1,
            "quote": "Other document clause.",
        }
        assert (await preview(api, header, task, cross_document)).status_code == 404
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            before_usage = (
                await session.execute(
                    text(
                        "SELECT id,tokens,usd,charge FROM usage_records WHERE job_id=:job ORDER BY id"
                    ),
                    {"job": UUID(failed_job)},
                )
            ).all()
            before_job = await session.scalar(
                text("SELECT to_jsonb(j) FROM jobs j WHERE id=:job"), {"job": UUID(failed_job)}
            )
        assert before_usage and sum(row.tokens for row in before_usage) > 0
        calls = provider.calls
        queue_calls = len(app.state.queue.calls)
        checked = await preview(api, header, task, body)
        assert checked.status_code == 200, checked.text
        save = await api.post(
            f"/v4/tasks/{task}/requirements/manual",
            headers=header,
            json={
                **body,
                "request_id": str(uuid4()),
                "expected_preview_hash": checked.json()["data"]["preview_hash"],
            },
        )
        assert save.status_code == 201, save.text
        saved = save.json()["data"]
        assert saved["created_scope"] and saved["scope"]["origin"] == "manual"
        assert saved["requirement"]["origin"] == "manual_rejected"
        assert saved["requirement"]["state"] == "unconfirmed"
        assert saved["scope"]["extraction_job_id"] != failed_job
        assert provider.calls == calls and len(app.state.queue.calls) == queue_calls
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            assert (
                await session.scalar(
                    text("SELECT to_jsonb(j) FROM jobs j WHERE id=:job"), {"job": UUID(failed_job)}
                )
                == before_job
            )
            assert (
                await session.execute(
                    text(
                        "SELECT id,tokens,usd,charge FROM usage_records WHERE job_id=:job ORDER BY id"
                    ),
                    {"job": UUID(failed_job)},
                )
            ).all() == before_usage
            manual_job = UUID(saved["scope"]["extraction_job_id"])
            for table in ("usage_records", "vendor_calls"):
                assert (
                    await session.scalar(
                        text(f"SELECT count(*) FROM {table} WHERE job_id=:job"), {"job": manual_job}
                    )
                    == 0
                )
        implicit = await api.get(f"/tasks/{task}/requirements", headers=header)
        assert implicit.status_code == 200 and implicit.json()["items"] == [], implicit.text
        explicit = await api.get(
            f"/tasks/{task}/requirements", headers=header, params={"job": str(manual_job)}
        )
        assert explicit.status_code == 200 and len(explicit.json()["items"]) == 1, explicit.text
        record(
            "all-rejected-recovery",
            original_status=original["status"],
            original_cost=original.get("cost"),
            usage_rows_preserved=len(before_usage),
            manual_scope=str(manual_job),
            additional_provider_calls=provider.calls - calls,
            default_saved_count=0,
            explicit_saved_count=1,
        )


def batch_body(views, revision, **changes):
    return {
        "request_id": str(uuid4()),
        "expected_set_revision": revision,
        "items": [
            {
                "requirement_id": value["requirement_id"],
                "expected_revision": value["revision"],
                "expected_review_hash": value["review_hash"],
            }
            for value in views
        ],
        "reviewed_each": True,
        "reason": "Individually inspect every selected synthetic source",
        **changes,
    }


async def test_owner_mixed_and_stale_batches_are_atomic(tenants, tmp_path):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        header = headers[0]
        task, _, job, requirements = await create_tender(api, app, header, tmp_path)
        other_task, _, _, other = await create_tender(api, app, header, tmp_path, suffix="other")
        first, second, foreign = await asyncio.gather(
            review(api, header, requirements[0]["id"]),
            review(api, header, requirements[1]["id"]),
            review(api, header, other[0]["id"]),
        )
        route = f"/v4/tasks/{task}/extractions/{job}/requirement-confirmations"
        before = await review_counts(app, tenants["orgs"][0], task)
        wrong_parent = batch_body(
            [first["requirement"], foreign["requirement"]], first["scope"]["revision"]
        )
        response = await api.post(route, headers=header, json=wrong_parent)
        assert response.status_code == 404, response.text
        assert await review_counts(app, tenants["orgs"][0], task) == before
        stale = batch_body(
            [first["requirement"], second["requirement"]], first["scope"]["revision"]
        )
        # The service sorts by UUID: place the invalid target last so rollback is
        # tested after a valid earlier item could already have been flushed.
        stale["items"].sort(key=lambda row: row["requirement_id"])
        stale["items"][-1]["expected_review_hash"] = "0" * 64
        response = await api.post(route, headers=header, json=stale)
        assert response.status_code == 409, response.text
        assert await review_counts(app, tenants["orgs"][0], task) == before
        for rid in (requirements[0]["id"], requirements[1]["id"]):
            assert (await review(api, header, rid))["requirement"]["state"] == "unconfirmed"
        good = batch_body([first["requirement"], second["requirement"]], first["scope"]["revision"])
        response = await api.post(route, headers=header, json=good)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["changed"] == 2
        record(
            "atomic-batches",
            task=task,
            foreign_task=other_task,
            mixed_status=404,
            stale_status=409,
            confirmed=2,
        )


async def test_concurrent_same_revision_has_one_committed_decision(tenants, tmp_path):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        header = headers[0]
        task, _, _, requirements = await create_tender(api, app, header, tmp_path)
        rid = requirements[0]["id"]
        initial = await review(api, header, rid)
        first, second = decision(initial), decision(initial)
        # Each HTTP request creates its own authenticated DB transaction.
        responses = await asyncio.gather(
            post_decision(api, header, rid, first), post_decision(api, header, rid, second)
        )
        assert sorted(response.status_code for response in responses) == [200, 409], [
            response.text for response in responses
        ]
        current = await review(api, header, rid)
        assert current["requirement"]["revision"] == initial["requirement"]["revision"] + 1
        history = await api.get(f"/v4/requirements/{rid}/review-history", headers=header)
        assert history.status_code == 200, history.text
        assert [item["action"] for item in history.json()["items"]] == ["seed", "confirm"]
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM requirement_review_requests WHERE task_id=:task"),
                    {"task": UUID(task)},
                )
                == 1
            )
        record(
            "concurrent-cas",
            status_codes=sorted(r.status_code for r in responses),
            final_revision=current["requirement"]["revision"],
            committed_confirmations=1,
        )


async def test_legacy_missing_metadata_owner_seed_and_hundred_item_bound(
    tenants, tmp_path, monkeypatch
):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, provider):
        header = headers[0]
        response = await api.post("/tasks", headers=header, json={"name": "Synthetic legacy scope"})
        assert response.status_code == 200, response.text
        task = response.json()["data"]["id"]
        document, _, _ = await upload_parse(
            api,
            app,
            header,
            task,
            "synthetic-legacy-hundred.docx",
            word_bytes(
                [
                    f"Synthetic requirement number {index:03d} shall be inspected."
                    for index in range(100)
                ]
            ),
        )

        async def extract_every_paragraph(chunks, schema):
            provider.calls += 1
            items = []
            for chunk in chunks:
                for block in chunk["blocks"]:
                    source = source_for({**chunk, "blocks": [block]})
                    items.append(
                        ExtractedRequirement(
                            category=Category.technical, text=block["text"], source=source
                        )
                    )
            return provider_result(provider, items)

        async def legacy_publication(*args, **kwargs):
            # Emulate a pre-B02 publisher only: all real Requirement inserts and
            # triggers remain active. Lazy initialization uses the real service.
            return None

        with monkeypatch.context() as legacy:
            legacy.setattr(provider, "_extract", extract_every_paragraph)
            legacy.setattr(requirement_confirmation, "seed_reviews", legacy_publication)
            job = await run_document_job(api, app, header, document, "extract")
        before = await review_counts(app, tenants["orgs"][0], task)
        assert before["requirements"] == 100 and before["requirement_reviews"] == 0
        listing = await api.get(
            f"/v4/tasks/{task}/extractions/{job}/requirement-reviews",
            headers=header,
            params={"limit": 100},
        )
        assert listing.status_code == 200, listing.text
        data = listing.json()
        assert len(data["items"]) == 100
        assert {row["state"] for row in data["items"]} == {"legacy_unconfirmed"}
        route = f"/v4/tasks/{task}/extractions/{job}/requirement-confirmations"
        body = batch_body(data["items"], data["data"]["scope"]["revision"])
        oversized = copy.deepcopy(body)
        oversized["items"].append({**oversized["items"][0], "requirement_id": str(uuid4())})
        rejected = await api.post(route, headers=header, json=oversized)
        assert rejected.status_code == 422, rejected.text
        assert await review_counts(app, tenants["orgs"][0], task) == before
        approved = await api.post(route, headers=header, json=body)
        assert approved.status_code == 200, approved.text
        assert approved.json()["data"]["changed"] == 100
        assert len(approved.json()["items"]) == 100
        assert {item["current_state"] for item in approved.json()["items"]} == {"confirmed"}
        counts = await review_counts(app, tenants["orgs"][0], task)
        assert counts == {
            "requirements": 100,
            "requirement_reviews": 100,
            "requirement_review_events": 200,
            "requirement_review_requests": 1,
        }
        record(
            "legacy-owner-batch", selected=100, oversize_status=422, confirmed=100, counts=counts
        )
