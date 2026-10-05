"""API/processor citation regressions with synthetic Word files and MockTransport.

Failure modes fixed before implementation:
* A quote inside a longer parameter and at one segment boundary must survive
  extraction, legacy repair, card review and database-enforced draft assembly.
* True normalized ambiguity, partial compatibility expansions and non-exact
  stored quotes must remain invalid; SQL must use original-character boundaries.
* A starred segment must not capture another parameter by substring, and a
  missing starred segment must be added even when its substring is already cited.
"""

import json
from uuid import UUID

import pytest
from app.models.entities import Requirement
from app.providers.llm import AnthropicExtractor
from app.services.extraction import locate_quote
from app.services.response_cards import citation_valid
from conftest import FakeQueue, credential_app
from sqlalchemy import text
from test_api import run_job
from test_citation_repair import create_commitment_card
from test_docx_extraction import item
from test_job_boundaries import session_for
from test_llm_providers import Vendor, anthropic_reply, settings_for
from test_parameter_extraction import extract_document, parameter_word
from test_response_cards import require_action, sanitized_artifact, set_role


@pytest.mark.parametrize(
    "parts",
    [
        ["3.5mm插孔：≥2个", "5mm插孔：≥2个"],
        ["内存：≥16 GB", "扩展内存：≥16 GB"],
    ],
)
@pytest.mark.parametrize("repair_legacy", [False, True])
async def test_boundary_citation_extract_repair_confirm_and_draft(
    tenants, tmp_path, admin_engine, parts, repair_legacy
):
    source = "；".join(parts)
    model_quotes = [part.replace("：", ":").replace(" ", "") for part in parts]
    vendor = Vendor(anthropic_reply([item("t1r1c1", quote) for quote in model_quotes]))
    settings = settings_for(tmp_path, "anthropic")
    app = await credential_app(
        settings, llm=AnthropicExtractor(settings, vendor.transport()), queue=FakeQueue()
    )
    async with app.router.lifespan_context(app), session_for(app, tenants) as (api, header):
        task = (
            await api.post("/tasks", headers=header, json={"name": "Synthetic boundaries"})
        ).json()["data"]["id"]
        uploaded = await api.post(
            f"/tasks/{task}/documents",
            headers=header,
            files={"file": ("boundaries.docx", parameter_word(source))},
        )
        assert uploaded.status_code == 200, uploaded.text
        document = uploaded.json()["data"]["id"]
        _, parsed = await run_job(api, app, header, document, "parse")
        assert parsed["status"] == "succeeded"
        extraction, extracted = await run_job(api, app, header, document, "extract")
        assert extracted["status"] == "succeeded", extracted
        rows = (await api.get(f"/tasks/{task}/requirements", headers=header)).json()["items"]
        assert {row["source"]["quote"] for row in rows} == set(parts)
        assert len(vendor.requests) == 1
        assert extracted["result"]["gap_fill"]["remaining"] == 0

        if repair_legacy:
            # Deliberately restore the pre-0017 normalized spelling; Word retains
            # source whitespace exactly, so this does not depend on PDF layout.
            with admin_engine.begin() as connection:
                for row in rows:
                    connection.execute(
                        text("UPDATE requirements SET quote=:quote, model_quote=NULL WHERE id=:id"),
                        {"quote": row["model_quote"], "id": row["id"]},
                    )
            preview = await api.get(
                f"/tasks/{task}/requirements/repair", headers=header, params={"job": extraction}
            )
            assert preview.status_code == 200, preview.text
            repaired = await api.post(
                f"/tasks/{task}/requirements/repair",
                headers=header,
                json={
                    "extraction_job_id": extraction,
                    "expected_preview": preview.json()["data"]["preview_hash"],
                    "reason": "Review synthetic boundary citations",
                },
            )
            assert repaired.status_code == 200, repaired.text
            assert repaired.json()["data"]["changed"] == 2

        confirmations = []
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "technical")
        for row in rows:
            async with app.state.db.transaction(tenants["orgs"][0]) as session:
                requirement = await session.get(Requirement, UUID(row["id"]))
                assert await citation_valid(session, requirement)
                assert (
                    await session.scalar(
                        text("SELECT response_citation_valid(:org,:requirement)"),
                        {"org": tenants["orgs"][0], "requirement": UUID(row["id"])},
                    )
                    is True
                )
            card = await create_commitment_card(api, header, task, extraction, row, "boundary")
            card = await require_action(api, header, card, "submit")
            card = await require_action(api, header, card, "confirm", reviewed_evidence_ids=[])
            assert card["state"] == "confirmed" and card["eligibility"] == "eligible"
            confirmations.append(card)
        receipt = await api.post(
            f"/tasks/{task}/drafts", headers=header, json={"extraction_job_id": extraction}
        )
        assert receipt.status_code == 200, receipt.text
        job = receipt.json()["data"]["job_id"]
        await app.state.processor(header["X-Org-Id"], job)
        status = (await api.get(f"/jobs/{job}", headers=header)).json()["data"]
        assert status["status"] == "succeeded", status
        draft = (await api.get(f"/drafts/{status['result']['draft_id']}", headers=header)).json()[
            "data"
        ]
        assert draft["validity"] == "current" and draft["gaps"] == []
        assert {row["tender_clause"]["quote"] for row in draft["tables"]["technical"]} == set(parts)
        assert len(vendor.requests) == 1  # assembly makes no paid calls
        (tmp_path / "boundary-review-draft.json").write_text(
            json.dumps(
                sanitized_artifact(
                    {
                        "source": source,
                        "extraction": extracted,
                        "repaired": repair_legacy,
                        "cards": confirmations,
                        "draft": draft,
                    }
                ),
                ensure_ascii=False,
                indent=2,
            )
        )


@pytest.mark.parametrize("missing_starred", [False, True])
async def test_starred_membership_uses_the_located_span(tenants, tmp_path, missing_starred):
    source = "★扩展内存≥16GB；内存≥16GB"
    quotes = ["内存≥16GB"] if missing_starred else ["扩展内存≥16GB", "内存≥16GB"]
    replies = [anthropic_reply([item("t1r1c1", quote) for quote in quotes])]
    if missing_starred:
        replies.append(anthropic_reply([]))  # one unanswered gap, covered by the star rule
    vendor = Vendor(*replies)
    settings = settings_for(tmp_path, "anthropic")
    app = await credential_app(
        settings, llm=AnthropicExtractor(settings, vendor.transport()), queue=FakeQueue()
    )
    async with app.router.lifespan_context(app), session_for(app, tenants) as (api, header):
        status, rows = await extract_document(api, app, header, parameter_word(source))
    assert status["status"] == "succeeded", status
    expected_starred = "★扩展内存≥16GB" if missing_starred else "扩展内存≥16GB"
    assert {row["source"]["quote"]: row["starred"] for row in rows} == {
        expected_starred: True,
        "内存≥16GB": False,
    }
    assert status["result"]["gap_fill"]["remaining"] == 0
    assert len(vendor.requests) == (2 if missing_starred else 1)
    (tmp_path / "starred-spans.json").write_text(
        json.dumps(
            {"source": source, "rows": sanitized_artifact(rows), "result": status["result"]},
            ensure_ascii=False,
            indent=2,
        )
    )


def test_sql_locator_matches_python_boundaries_and_normalization(admin_engine, tmp_path):
    # Part of the PostgreSQL integration coverage; no substitute/in-memory database.
    cases = [
        ("3.5mm插孔：≥2个；5mm插孔：≥2个", "5mm插孔：≥2个"),
        ("内存：≥16 GB；扩展内存：≥16 GB", "内存:≥16GB"),
        ("扩展内存：≥16 GB；内存：≥16 GB", "内存:≥16GB"),
        ("Memory:16 GB;Memory:16GB", "Memory:16GB"),
        ("A-1;B/1;C.1", "1"),
        ("A-1;B/1;C.1;1", "1"),
        ("x\u3000ﬁ;xfi", "fi"),
        ("ﬁ;f", "f"),
        ("ﬁ", "f"),
        ("e\u0301;e", "e"),
        ("q\u0301;q", "q"),
        ("가;가", "가"),
        ('“one”;x"one"', '"one"'),
        ("aaaa", "aa"),
        ("X;a\u001c", "a"),
        ("not present", ""),
    ]
    evidence = []
    with admin_engine.connect() as connection:
        for source, quote in cases:
            expected, reason = locate_quote(source, quote)
            actual = connection.scalar(
                text("SELECT response_locate_quote(:source,:quote)"),
                {"source": source, "quote": quote},
            )
            assert actual == expected, (source, quote, reason)
            evidence.append({"source": source, "quote": quote, "located": actual, "reason": reason})
    (tmp_path / "sql-python-citation-parity.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2)
    )
