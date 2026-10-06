"""Response cards and draft tables through the real API and job processor.

Gate failure matrix (enumerated before the gate tests, per repository policy):

* actor context: a missing actor, API token, agent, or worker must not perform a
  human decision even when a user id and a confirm scope are supplied;
* responsibility: an admin cannot confirm across a professional domain, a
  bidder cannot decide technical cards, a technical member cannot decide
  commercial cards, and an unclassified card cannot be decided;
* state and concurrency: decisions from the wrong state, stale expected
  revisions, protected pending/confirmed content, and an illegal current
  revision pointer must fail without a partial revision or success audit;
* evidence completeness: evidence responses need at least one real linked
  material, reviewed ids must be unique and exactly cover the links, every
  linked item must be confirmed, and commitment responses must have none;
* decision completeness: confirmed revisions need a human confirmer and time,
  disposition=respond, complete response/deviation fields, and no unresolved
  warning; comply_only needs its own human decision and cannot masquerade as a
  confirmed response;
* immutable history: card revisions, evidence, evidence links, draft runs, and
  response items cannot be changed or deleted after insertion;
* relational binding: cross-organization, cross-task, cross-extraction-job,
  cross-card, wrong selection/revision, and wrong certificate page bindings
  must fail or be hidden as not found;
* material freshness: replacing a task selection makes dependent evidence and
  confirmed cards stale, while unrelated commitments and comply-only choices
  remain consumable only according to their own gates;
* atomic batches: one unauthorized, foreign-job, duplicate, or revision-
  conflicted disposition item rolls the whole batch back;
* draft completeness: each extracted requirement appears exactly once in a
  response row, comply-only entry, or gap; gaps force partial completion and a
  negative deviation remains visible in its table row;
* job boundaries: dry-run writes nothing and enqueues nothing, cancelled or
  foreign-organization jobs cannot publish response items, and a re-extraction
  never imports cards from another extraction job.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pymupdf
import pytest
from app.api.main import create_app
from app.core.config import Settings
from app.schemas.contracts import (
    Category,
    ExtractedRequirement,
    Extraction,
    LLMResult,
    ProviderUsage,
)
from conftest import PASSWORD, FakeQueue
from fakes import FakeLLM, source_for
from sqlalchemy import select, text
from task_fixtures import confirm_requirements_async, reviewer_header
from task_fixtures import set_role as set_task_actor_role

TENDER_LINES = [
    "The offered appliance memory shall be at least 64 GB.",
    "The bidder shall provide a current quality management certificate.",
    "Delivery shall be completed within thirty calendar days.",
    "Bid complaints shall follow the procedure stated in this invitation.",
    "The support team shall provide a named escalation contact.",
]
CERTIFICATE_PAGE = "Synthetic certificate fixture: quality management certificate QMS-2026."
PRODUCT_DATA = {
    "name": "Synthetic appliance declaration",
    "vendor": "Synthetic vendor",
    "model": "Model with 64 GB memory",
    "model_version": "2026.1",
    "official_url": "https://vendor.example.invalid/appliance",
}


def labelled_pdf(lines: list[str]) -> bytes:
    """Build a genuine local PDF whose text is explicitly labelled as a test fixture."""
    with pymupdf.open() as document:
        for line in lines:
            page = document.new_page()
            page.insert_text((40, 60), "SYNTHETIC TEST MATERIAL - NOT A REAL BID OR PROOF")
            page.insert_text((40, 90), line)
        return document.tobytes()


def sanitized_artifact(value):
    """Replace synthetic UUIDs with stable hashes so artifacts never become access handles."""
    if isinstance(value, dict):
        return {key: sanitized_artifact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitized_artifact(item) for item in value]
    if isinstance(value, str):
        try:
            UUID(value)
        except ValueError:
            return value
        return "uuid-sha256:" + hashlib.sha256(value.encode()).hexdigest()[:16]
    return value


class PhaseOneExtraction(FakeLLM):
    """A labelled fake extraction provider; response drafting never calls a model in phase 1."""

    name = "test-fake"
    model = "phase-one-requirements"
    version = "test-v1"
    test_only = True

    def __init__(self) -> None:
        self.calls = 0

    async def _extract(self, chunks, schema):
        self.calls += 1
        items = []
        for chunk in chunks:
            page = chunk["page"]
            categories = {
                1: (Category.scoring, True),
                2: (Category.qualification, False),
                3: (Category.technical, False),
                4: (Category.technical, False),
                5: (Category.technical, False),
            }
            category, starred = categories[page]
            items.append(
                ExtractedRequirement(
                    category=category,
                    starred=starred,
                    text=TENDER_LINES[page - 1],
                    source=source_for(chunk),
                )
            )
        return LLMResult(
            extraction=Extraction(items=items),
            usage=ProviderUsage(
                provider=self.name,
                model=self.model,
                version=self.version,
                duration_ms=1,
                tokens=10,
                usd=0,
                test_only=True,
            ),
        )


async def login(api, org_id: UUID, label: str) -> dict[str, str]:
    response = await api.post(
        "/auth/login",
        json={"email": f"{label}@example.test", "password": PASSWORD, "org_id": str(org_id)},
    )
    assert response.status_code == 200, response.text
    return {
        "Authorization": "Bearer " + response.json()["data"]["session"],
        "X-Org-Id": str(org_id),
    }


async def run_document_job(api, app, header, document_id, action, body=None):
    response = await api.post(f"/documents/{document_id}/{action}", headers=header, json=body or {})
    assert response.status_code == 200, response.text
    job_id = response.json()["data"]["job_id"]
    await app.state.processor(header["X-Org-Id"], job_id)
    status = await api.get(f"/jobs/{job_id}", headers=header)
    assert status.status_code == 200 and status.json()["data"]["status"] == "succeeded", status.text
    return job_id


async def create_tender(
    api, app, header, tmp_path: Path, *, suffix: str = "one", confirmed: bool = False
):
    task = (
        await api.post("/tasks", headers=header, json={"name": f"Synthetic phase 1 {suffix}"})
    ).json()["data"]["id"]
    tender = tmp_path / f"synthetic-tender-{suffix}.pdf"
    tender.write_bytes(labelled_pdf(TENDER_LINES))
    uploaded = await api.post(
        f"/tasks/{task}/documents",
        headers=header,
        files={"file": (tender.name, tender.read_bytes(), "application/pdf")},
    )
    assert uploaded.status_code == 200, uploaded.text
    document = uploaded.json()["data"]["id"]
    await run_document_job(api, app, header, document, "parse")
    extraction = await run_document_job(api, app, header, document, "extract")
    requirements = (
        await api.get(f"/tasks/{task}/requirements", headers=header, params={"job": extraction})
    ).json()["items"]
    assert [row["source"]["page"] for row in requirements] == [1, 2, 3, 4, 5]
    if confirmed:
        async with app.state.db.transaction(UUID(header["X-Org-Id"])) as session:
            await confirm_requirements_async(
                session, UUID(header["X-Org-Id"]), UUID(task), settings=app.state.processor.settings
            )
    return task, document, extraction, requirements


async def select_real_materials(api, header, task, tmp_path: Path):
    product = (
        await api.post("/resources/products", headers=header, json={"data": PRODUCT_DATA})
    ).json()["data"]
    product_selection_response = await api.post(
        f"/tasks/{task}/products",
        headers=header,
        json={"product_id": product["product_id"]},
    )
    assert product_selection_response.status_code == 200, product_selection_response.text
    product_selection = product_selection_response.json()["data"]

    certificate_data = {
        "kind": "qualification",
        "name": "Synthetic certificate declaration",
        "number": "QMS-2026",
        "valid_from": "2026-01-01",
        "valid_until": "2027-01-01",
    }
    certificate = (
        await api.post("/resources/certificates", headers=header, json={"data": certificate_data})
    ).json()["data"]
    certificate_pdf = tmp_path / "synthetic-certificate.pdf"
    certificate_pdf.write_bytes(labelled_pdf([CERTIFICATE_PAGE]))
    uploaded = await api.post(
        f"/resources/certificates/{certificate['certificate_id']}/file-revisions",
        headers=header,
        data={"metadata": json.dumps({"expected_revision": 1, "data": certificate_data})},
        files={
            "file": (
                certificate_pdf.name,
                certificate_pdf.read_bytes(),
                "application/pdf",
            )
        },
    )
    assert uploaded.status_code == 200, uploaded.text
    selected_response = await api.post(
        f"/tasks/{task}/certificates",
        headers=header,
        json={"certificate_id": certificate["certificate_id"]},
    )
    assert selected_response.status_code == 200, selected_response.text
    selected_certificate = selected_response.json()["data"]
    source_response = await api.post(
        f"/tasks/{task}/evidence-sources",
        headers=header,
        json={"task_certificate_id": selected_certificate["id"], "page": 1},
    )
    assert source_response.status_code == 200, source_response.text
    source = source_response.json()["data"]["source"]
    return product, product_selection, certificate, selected_certificate, source


def set_role(admin_engine, org_id, user_id, role):
    set_task_actor_role(admin_engine, org_id, user_id, role)


@asynccontextmanager
async def phase_one_client(tenants, tmp_path):
    provider = PhaseOneExtraction()
    app = create_app(Settings(data_dir=tmp_path), llm=provider, queue=FakeQueue())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api,
    ):
        headers = [
            await login(api, org, label)
            for org, label in zip(tenants["orgs"], ("a", "b"), strict=True)
        ]
        yield api, app, headers, provider


async def create_card(api, header, task, extraction, requirement, content):
    response = await api.post(
        f"/tasks/{task}/cards",
        headers=header,
        json={
            "extraction_job_id": extraction,
            "requirement_id": requirement["id"],
            "content": content,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    return response.json()["data"]


async def card_action(api, header, card, action, **extra):
    response = await api.post(
        f"/cards/{card['id']}/actions",
        headers=header,
        json={"expected_revision": card["revision"], "action": action, **extra},
    )
    return response


async def require_action(api, header, card, action, **extra):
    response = await card_action(api, header, card, action, **extra)
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    return response.json()["data"]


async def test_full_review_chain_and_draft_partition(tenants, tmp_path, admin_engine):
    """Human decisions consume fixed materials and the deterministic worker partitions all rows."""
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, provider):
        header = headers[0]
        task, _, extraction, requirements = await create_tender(
            api, app, header, tmp_path, confirmed=True
        )
        _, product_selection, _, _, page_source = await select_real_materials(
            api, header, task, tmp_path
        )

        # A scoring requirement starts unclassified.  Admin classification and a
        # subsequent update exercise both routes without pretending admin owns the
        # technical review decision.
        substantive = await create_card(
            api,
            header,
            task,
            extraction,
            requirements[0],
            {
                "response_kind": None,
                "response_text": None,
                "deviation": None,
                "deviation_note": None,
                "evidence": [],
            },
        )
        classified_response = await api.post(
            f"/cards/{substantive['id']}/classification",
            headers=header,
            json={
                "expected_revision": substantive["revision"],
                "review_domain": "technical",
                "reason": "The scored appliance specification needs technical review.",
            },
        )
        assert classified_response.status_code == 200, classified_response.text
        substantive = classified_response.json()["data"]
        updated_response = await api.put(
            f"/cards/{substantive['id']}",
            headers=header,
            json={
                "expected_revision": substantive["revision"],
                "content": {
                    "response_kind": "evidence",
                    "response_text": "The offered appliance has 64 GB memory.",
                    "deviation": "none",
                    "deviation_note": "The declared 64 GB equals the tender minimum.",
                    "evidence": [
                        {
                            "kind": "product",
                            "selection_id": product_selection["id"],
                            "field_path": "model",
                            "quote": PRODUCT_DATA["model"],
                        }
                    ],
                },
            },
        )
        assert updated_response.status_code == 200, updated_response.text
        substantive = updated_response.json()["data"]

        forged_page = await api.post(
            f"/tasks/{task}/cards",
            headers=header,
            json={
                "extraction_job_id": extraction,
                "requirement_id": requirements[1]["id"],
                "content": {
                    "response_kind": "evidence",
                    "response_text": "A forged page quote must never become evidence.",
                    "deviation": "none",
                    "deviation_note": "This request deliberately cites absent page text.",
                    "evidence": [
                        {
                            "kind": "certificate_pdf_page",
                            "evidence_source_id": page_source["id"],
                            "quote": "Forged certificate text that is absent from the PDF.",
                        }
                    ],
                },
            },
        )
        assert forged_page.status_code == 400
        assert forged_page.json()["data"]["error"]["code"] == "invalid_evidence_quote"

        commercial = await create_card(
            api,
            header,
            task,
            extraction,
            requirements[1],
            {
                "response_kind": "evidence",
                "response_text": "The supplied quality certificate identifies QMS-2026.",
                "deviation": "none",
                "deviation_note": "The supplied page identifies the requested certificate.",
                "evidence": [
                    {
                        "kind": "certificate_pdf_page",
                        "evidence_source_id": page_source["id"],
                        "quote": CERTIFICATE_PAGE,
                    }
                ],
            },
        )
        commitment = await create_card(
            api,
            header,
            task,
            extraction,
            requirements[2],
            {
                "response_kind": "commitment",
                "response_text": "We commit to delivery within forty calendar days.",
                "deviation": "negative",
                "deviation_note": "The offered forty days exceeds the required thirty days.",
                "evidence": [],
            },
        )

        # The technical reviewer owns both the technical response and the
        # separate procedural disposition decision.
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "technical")
        _, review_header = await reviewer_header(
            api, admin_engine, tenants["orgs"][0], UUID(task), "technical"
        )
        disposition = await api.post(
            f"/tasks/{task}/cards/dispositions",
            headers=header,
            json={
                "extraction_job_id": extraction,
                "items": [
                    {
                        "requirement_id": requirements[3]["id"],
                        "expected_revision": None,
                        "disposition": "comply_only",
                        "reason": "This clause states the complaint procedure only.",
                    }
                ],
            },
        )
        assert disposition.status_code == 200, disposition.text
        [comply_card] = disposition.json()["data"]["cards"]
        assert comply_card["disposition"] == "comply_only"
        assert comply_card["confirmed_by"] is None

        for card in (substantive, commitment):
            submitted = await require_action(api, review_header, card, "submit")
            reviewed = [row["id"] for row in submitted["evidence"]]
            if reviewed:
                mismatch = await card_action(
                    api, review_header, submitted, "confirm", reviewed_evidence_ids=[]
                )
                assert mismatch.status_code == 400
                assert mismatch.json()["data"]["error"]["code"] == "review_mismatch"
            confirmed = await require_action(
                api,
                review_header,
                submitted,
                "confirm",
                reviewed_evidence_ids=reviewed,
            )
            assert confirmed["state"] == "confirmed"
            assert confirmed["disposition"] == "respond"
            assert bool(confirmed["evidence"]) == (
                confirmed["content"]["response_kind"] == "evidence"
            )
        substantive = confirmed if confirmed["id"] == substantive["id"] else substantive

        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "bidder")
        _, review_header = await reviewer_header(
            api, admin_engine, tenants["orgs"][0], UUID(task), "commercial"
        )
        commercial = await require_action(api, review_header, commercial, "submit")
        warning_missing = await card_action(
            api,
            review_header,
            commercial,
            "confirm",
            reviewed_evidence_ids=[row["id"] for row in commercial["evidence"]],
        )
        assert warning_missing.status_code == 400
        assert warning_missing.json()["data"]["error"]["code"] == "warning_review_required"
        commercial = await require_action(
            api,
            review_header,
            commercial,
            "confirm",
            reviewed_evidence_ids=[row["id"] for row in commercial["evidence"]],
            reviewed_warning_codes=commercial["warning_codes"],
            reason=(
                "The certificate page was compared with the supplied original."
                if commercial["warning_codes"]
                else None
            ),
        )
        assert commercial["state"] == "confirmed"
        assert commercial["evidence"][0]["quote_check"] == "human_page_review"
        assert commercial["evidence"][0]["source_archive"]["status"] == "unconfirmed_source"
        assert commercial["evidence"][0]["source_archive"]["confirmed_by"] is None

        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "admin")
        queue_calls = len(app.state.queue.calls)
        extraction_calls = provider.calls
        preview_response = await api.post(
            f"/tasks/{task}/drafts",
            headers=header,
            json={"extraction_job_id": extraction, "dry_run": True},
        )
        assert preview_response.status_code == 200, preview_response.text
        preview = preview_response.json()["data"]
        assert preview["dry_run"] is True
        assert (
            preview["response_requirements"],
            preview["comply_only_requirements"],
            preview["gap_requirements"],
        ) == (3, 1, 1)
        assert preview["table_rows"] == {
            "substantive": 1,
            "commercial": 1,
            "technical": 1,
        }
        assert preview["negative_deviations"] == 1
        assert len(app.state.queue.calls) == queue_calls
        assert (
            await api.get(f"/tasks/{task}/drafts", headers=header, params={"job": extraction})
        ).json()["items"] == []

        queued_response = await api.post(
            f"/tasks/{task}/drafts",
            headers=header,
            json={"extraction_job_id": extraction},
        )
        assert queued_response.status_code == 200, queued_response.text
        queued = queued_response.json()["data"]
        await app.state.processor(header["X-Org-Id"], queued["job_id"])
        job = (await api.get(f"/jobs/{queued['job_id']}", headers=header)).json()["data"]
        assert job["status"] == "succeeded"
        assert job["result"]["completion"] == "partial"
        assert job["result"]["cost"] == {
            "llm_tokens": 0,
            "ocr_pages": 0,
            "usd": 0.0,
        }

        shown_response = await api.get(f"/drafts/{job['result']['draft_id']}", headers=header)
        assert shown_response.status_code == 200, shown_response.text
        shown = shown_response.json()["data"]
        assert shown["completion"] == "partial" and shown["validity"] == "current"
        assert {key: len(value) for key, value in shown["tables"].items()} == {
            "substantive": 1,
            "commercial": 1,
            "technical": 1,
        }
        assert len(shown["comply_only"]) == len(shown["gaps"]) == 1
        assert shown["comply_only"][0]["requirement_id"] == requirements[3]["id"]
        assert shown["gaps"][0]["requirement_id"] == requirements[4]["id"]
        assert shown["gaps"][0]["reasons"] == ["missing_card"]
        assert shown["tables"]["technical"][0]["deviation"] == "negative"
        assert shown["tables"]["substantive"][0]["category"] == "scoring"
        assert shown["tables"]["substantive"][0]["starred"] is True
        assert "forty" in shown["tables"]["technical"][0]["deviation_note"]
        covered = (
            {row["requirement_id"] for rows in shown["tables"].values() for row in rows}
            | {row["requirement_id"] for row in shown["comply_only"]}
            | {row["requirement_id"] for row in shown["gaps"]}
        )
        assert covered == {row["id"] for row in requirements}
        assert provider.calls == extraction_calls  # draft generation made no model call

        repeated = await api.post(
            f"/tasks/{task}/drafts",
            headers=header,
            json={"extraction_job_id": extraction},
        )
        assert repeated.status_code == 200
        assert repeated.json()["data"]["cached"] is True
        assert repeated.json()["data"]["job_id"] == queued["job_id"]
        listed = await api.get(f"/tasks/{task}/drafts", headers=header, params={"job": extraction})
        assert [row["id"] for row in listed.json()["items"]] == [shown["id"]]

        from app.models.entities import AuditLog

        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            logs = (await session.scalars(select(AuditLog))).all()
        details = json.dumps([row.details for row in logs])
        assert "The offered appliance has 64 GB memory" not in details
        assert CERTIFICATE_PAGE not in details
        assert not any(line in details for line in TENDER_LINES)

        artifact = tmp_path / "phase1-response-cards-artifact.json"
        artifact.write_text(
            json.dumps(
                {
                    "fixture": "synthetic-labelled-materials-only",
                    "task_id_hash": sanitized_artifact(task),
                    "extraction_job_id_hash": sanitized_artifact(extraction),
                    "draft_id_hash": sanitized_artifact(shown["id"]),
                    "input_hash": shown["input_hash"],
                    "commands": [
                        "card create/update/classify/actions",
                        "card disposition",
                        "draft dry-run/submit/show/list",
                    ],
                    "tables": sanitized_artifact(shown["tables"]),
                    "comply_only": sanitized_artifact(shown["comply_only"]),
                    "gaps": sanitized_artifact(shown["gaps"]),
                    "rerun": (
                        ".venv/bin/python -m pytest -q -p no:cacheprovider "
                        "server/tests/test_response_cards.py::"
                        "test_full_review_chain_and_draft_partition"
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        saved = json.loads(artifact.read_text())
        assert saved["input_hash"] == shown["input_hash"]
        assert sum(len(rows) for rows in saved["tables"].values()) == 3
        artifact_text = artifact.read_text()
        assert "Authorization" not in artifact_text
        assert all(
            identifier not in artifact_text for identifier in (task, extraction, shown["id"])
        )


async def test_human_gate_roles_evidence_and_revision_conflicts(tenants, tmp_path, admin_engine):
    """The service rejects every nonhuman path before a confirmation revision is appended."""
    from app.core.errors import ServiceError
    from app.schemas.response_card_contracts import CardAction
    from app.services import response_cards as service
    from app.services.auth import ROLE_SCOPES, Identity

    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        header = headers[0]
        task, _, extraction, requirements = await create_tender(
            api, app, header, tmp_path, confirmed=True
        )
        _, product_selection, _, _, _ = await select_real_materials(api, header, task, tmp_path)
        expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        token_record = (
            await api.post(
                "/tokens",
                headers=header,
                json={
                    "name": "Synthetic response writer",
                    "scopes": ["task:read", "card:read", "card:write"],
                    "expires_at": expiry,
                },
            )
        ).json()["data"]
        token_header = {
            **header,
            "Authorization": "Bearer " + token_record["token"],
        }

        commitment = await create_card(
            api,
            header,
            task,
            extraction,
            requirements[2],
            {
                "response_kind": "commitment",
                "response_text": "We commit to delivery within thirty calendar days.",
                "deviation": "none",
                "deviation_note": "The committed period is exactly thirty calendar days.",
                "evidence": [],
            },
        )
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "technical")
        submitted = await require_action(api, header, commitment, "submit")

        # Tokens fail through the public service boundary and cannot smuggle a
        # confirm capability through the request body or their creator's role.
        token_attempt = await card_action(
            api, token_header, submitted, "confirm", reviewed_evidence_ids=[]
        )
        assert token_attempt.status_code == 403
        assert token_attempt.json()["data"]["error"]["code"] == "forbidden"

        action = CardAction(
            expected_revision=submitted["revision"],
            action="confirm",
            reviewed_evidence_ids=[],
        )
        for actor in (
            Identity(
                user_id=tenants["users"][0],
                org_id=tenants["orgs"][0],
                scopes=set(ROLE_SCOPES["technical"]),
                role="technical",
                actor_kind="agent",
            ),
            Identity(
                user_id=tenants["users"][0],
                org_id=tenants["orgs"][0],
                scopes={*ROLE_SCOPES["technical"], "evidence:confirm"},
                role="technical",
                token_id=UUID(token_record["id"]),
                actor_kind="token",
            ),
        ):
            with pytest.raises(ServiceError) as error:
                async with app.state.db.transaction(tenants["orgs"][0]) as session:
                    await service.card_action(
                        session,
                        actor,
                        UUID(submitted["id"]),
                        action,
                        app.state.storage,
                    )
            assert error.value.code == "forbidden"

        # A valid session with the wrong live membership remains unable to make
        # the decision.  Returning to the assigned role then succeeds once.
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "admin")
        wrong_role = await card_action(api, header, submitted, "confirm")
        assert wrong_role.status_code == 403
        assert wrong_role.json()["data"]["error"]["code"] == "forbidden"
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "technical")
        confirmed = await require_action(api, header, submitted, "confirm")
        assert confirmed["state"] == "confirmed" and confirmed["confirmed_by"] == str(
            tenants["users"][0]
        )

        # Old revisions never overwrite the current pointer or append history.
        stale_update = await api.put(
            f"/cards/{confirmed['id']}",
            headers=header,
            json={
                "expected_revision": submitted["revision"],
                "content": submitted["content"],
            },
        )
        assert stale_update.status_code == 409
        assert stale_update.json()["data"]["error"]["code"] == "revision_conflict"
        history = (
            await api.get(f"/cards/{confirmed['id']}", headers=header, params={"history": True})
        ).json()["data"]
        assert [row["revision"] for row in history["history"]] == [1, 2, 3]

        missing = await create_card(
            api,
            header,
            task,
            extraction,
            requirements[4],
            {
                "response_kind": "evidence",
                "response_text": "A named escalation contact will be provided.",
                "deviation": "none",
                "deviation_note": "The response repeats the requested support obligation.",
                "evidence": [],
            },
        )
        missing = await require_action(api, header, missing, "submit")
        missing_attempt = await card_action(api, header, missing, "confirm")
        assert missing_attempt.status_code == 400
        assert missing_attempt.json()["data"]["error"]["code"] == "missing_evidence"
        shown = (await api.get(f"/cards/{missing['id']}", headers=header)).json()["data"]
        assert shown["state"] == "pending_review" and shown["revision"] == missing["revision"]

        # Input validation blocks commitments from manufacturing a placeholder
        # link, and duplicate review acknowledgements are rejected before work.
        invalid_commitment = await api.post(
            f"/tasks/{task}/cards",
            headers=header,
            json={
                "extraction_job_id": extraction,
                "requirement_id": requirements[0]["id"],
                "content": {
                    "response_kind": "commitment",
                    "response_text": "We promise the declared model exists.",
                    "deviation": "none",
                    "deviation_note": "The promise repeats the requested declaration.",
                    "evidence": [
                        {
                            "kind": "product",
                            "selection_id": product_selection["id"],
                            "field_path": "model",
                            "quote": PRODUCT_DATA["model"],
                        }
                    ],
                },
            },
        )
        assert invalid_commitment.status_code == 422
        duplicate_id = str(uuid4())
        duplicate_review = await card_action(
            api,
            header,
            missing,
            "confirm",
            reviewed_evidence_ids=[duplicate_id, duplicate_id],
        )
        assert duplicate_review.status_code == 422

        lifecycle = await create_card(
            api,
            header,
            task,
            extraction,
            requirements[3],
            {
                "response_kind": "commitment",
                "response_text": "We will follow the stated complaint procedure.",
                "deviation": "none",
                "deviation_note": "The commitment follows the procedure without a deviation.",
                "evidence": [],
            },
        )
        lifecycle = await require_action(api, header, lifecycle, "submit")
        lifecycle = await require_action(
            api,
            header,
            lifecycle,
            "withdraw",
            reason="The editor withdrew this fixture for another wording check.",
        )
        lifecycle = await require_action(api, header, lifecycle, "submit")
        lifecycle = await require_action(
            api,
            header,
            lifecycle,
            "needs_material",
            reason="The reviewer requested a procedural reference for this fixture.",
        )
        revised = await api.put(
            f"/cards/{lifecycle['id']}",
            headers=header,
            json={
                "expected_revision": lifecycle["revision"],
                "content": lifecycle["content"],
            },
        )
        assert revised.status_code == 200, revised.text
        lifecycle = await require_action(api, header, revised.json()["data"], "submit")
        lifecycle = await require_action(
            api,
            header,
            lifecycle,
            "reject",
            reason="The reviewer rejected the first reviewed wording.",
        )
        revised = await api.put(
            f"/cards/{lifecycle['id']}",
            headers=header,
            json={
                "expected_revision": lifecycle["revision"],
                "content": lifecycle["content"],
            },
        )
        assert revised.status_code == 200, revised.text
        lifecycle = await require_action(api, header, revised.json()["data"], "submit")
        lifecycle = await require_action(api, header, lifecycle, "confirm")
        lifecycle = await require_action(
            api,
            header,
            lifecycle,
            "reopen",
            reason="The reviewer reopened the confirmed fixture for a later revision.",
        )
        assert lifecycle["state"] == "draft"
        lifecycle_history = (
            await api.get(f"/cards/{lifecycle['id']}", headers=header, params={"history": True})
        ).json()["data"]["history"]
        assert [row["state"] for row in lifecycle_history] == [
            "draft",
            "pending_review",
            "draft",
            "pending_review",
            "needs_material",
            "draft",
            "pending_review",
            "rejected",
            "draft",
            "pending_review",
            "confirmed",
            "draft",
        ]


async def test_atomic_disposition_and_reextraction_do_not_mix_requirements(
    tenants, tmp_path, admin_engine
):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, provider):
        header = headers[0]
        task, document, extraction, requirements = await create_tender(
            api, app, header, tmp_path, confirmed=True
        )
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "technical")
        first, second = sorted(requirements[2:4], key=lambda row: row["id"])
        failed = await api.post(
            f"/tasks/{task}/cards/dispositions",
            headers=header,
            json={
                "extraction_job_id": extraction,
                "items": [
                    {
                        "requirement_id": first["id"],
                        "expected_revision": None,
                        "disposition": "comply_only",
                        "reason": "First decision in a deliberately conflicted batch.",
                    },
                    {
                        "requirement_id": second["id"],
                        "expected_revision": 1,
                        "disposition": "comply_only",
                        "reason": "No card exists at this deliberately stale revision.",
                    },
                ],
            },
        )
        assert failed.status_code == 409
        assert failed.json()["data"]["error"]["code"] == "revision_conflict"
        slots = (
            await api.get(f"/tasks/{task}/cards", headers=header, params={"job": extraction})
        ).json()["items"]
        by_requirement = {row["requirement_id"]: row for row in slots}
        assert by_requirement[first["id"]]["status"] == "missing_card"
        assert by_requirement[second["id"]]["status"] == "missing_card"

        accepted = await api.post(
            f"/tasks/{task}/cards/dispositions",
            headers=header,
            json={
                "extraction_job_id": extraction,
                "items": [
                    {
                        "requirement_id": row["id"],
                        "expected_revision": None,
                        "disposition": "comply_only",
                        "reason": "The reviewer treats this fixture clause as procedural.",
                    }
                    for row in (first, second)
                ],
            },
        )
        assert accepted.status_code == 200, accepted.text
        cards = accepted.json()["data"]["cards"]
        assert [row["requirement_id"] for row in cards] == [first["id"], second["id"]]
        assert len({row["revision_id"] for row in cards}) == 2
        assert all(row["disposition"] == "comply_only" for row in cards)

        duplicate = await api.post(
            f"/tasks/{task}/cards/dispositions",
            headers=header,
            json={
                "extraction_job_id": extraction,
                "items": [
                    {
                        "requirement_id": first["id"],
                        "expected_revision": 1,
                        "disposition": "respond",
                        "reason": "Duplicate fixture item.",
                    },
                    {
                        "requirement_id": first["id"],
                        "expected_revision": 1,
                        "disposition": "respond",
                        "reason": "Duplicate fixture item again.",
                    },
                ],
            },
        )
        assert duplicate.status_code == 422

        # A provider-version change creates a fresh extraction of the same
        # document.  Old cards remain under their explicit original job.
        provider.version = "test-v2"
        extraction_two = await run_document_job(api, app, header, document, "extract")
        requirements_two = (
            await api.get(
                f"/tasks/{task}/requirements",
                headers=header,
                params={"job": extraction_two},
            )
        ).json()["items"]
        assert {row["id"] for row in requirements}.isdisjoint(
            {row["id"] for row in requirements_two}
        )
        new_slots = (
            await api.get(f"/tasks/{task}/cards", headers=header, params={"job": extraction_two})
        ).json()["items"]
        assert len(new_slots) == 5 and all(row["status"] == "missing_card" for row in new_slots)
        cross_job = await api.post(
            f"/tasks/{task}/cards",
            headers=header,
            json={
                "extraction_job_id": extraction_two,
                "requirement_id": first["id"],
                "content": {
                    "response_kind": None,
                    "response_text": None,
                    "deviation": None,
                    "deviation_note": None,
                    "evidence": [],
                },
            },
        )
        assert cross_job.status_code == 404


async def test_every_response_route_hides_the_other_organization(tenants, tmp_path):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task_b, _, job_b, requirements_b = await create_tender(
            api, app, headers[1], tmp_path, suffix="foreign", confirmed=True
        )
        card_b = await create_card(
            api,
            headers[1],
            task_b,
            job_b,
            requirements_b[2],
            {
                "response_kind": "commitment",
                "response_text": "Synthetic foreign organization response.",
                "deviation": "none",
                "deviation_note": "Synthetic foreign organization explanation.",
                "evidence": [],
            },
        )
        queued = (
            await api.post(
                f"/tasks/{task_b}/drafts",
                headers=headers[1],
                json={"extraction_job_id": job_b},
            )
        ).json()["data"]
        await app.state.processor(headers[1]["X-Org-Id"], queued["job_id"])
        foreign_job = (await api.get(f"/jobs/{queued['job_id']}", headers=headers[1])).json()[
            "data"
        ]
        draft_b = foreign_job["result"]["draft_id"]

        routes = [
            ("GET", f"/tasks/{task_b}/cards?job={job_b}", None),
            (
                "POST",
                f"/tasks/{task_b}/cards",
                {
                    "extraction_job_id": job_b,
                    "requirement_id": requirements_b[4]["id"],
                    "content": {
                        "response_kind": None,
                        "response_text": None,
                        "deviation": None,
                        "deviation_note": None,
                        "evidence": [],
                    },
                },
            ),
            ("GET", f"/cards/{card_b['id']}", None),
            (
                "PUT",
                f"/cards/{card_b['id']}",
                {"expected_revision": 1, "content": card_b["content"]},
            ),
            (
                "POST",
                f"/cards/{card_b['id']}/classification",
                {
                    "expected_revision": 1,
                    "review_domain": "commercial",
                    "reason": "Cross-organization request must stay hidden.",
                },
            ),
            (
                "POST",
                f"/cards/{card_b['id']}/actions",
                {"expected_revision": 1, "action": "submit"},
            ),
            (
                "POST",
                f"/tasks/{task_b}/cards/dispositions",
                {
                    "extraction_job_id": job_b,
                    "items": [
                        {
                            "requirement_id": requirements_b[3]["id"],
                            "expected_revision": None,
                            "disposition": "comply_only",
                            "reason": "Cross-organization request must stay hidden.",
                        }
                    ],
                },
            ),
            (
                "POST",
                f"/tasks/{task_b}/drafts",
                {"extraction_job_id": job_b},
            ),
            ("GET", f"/tasks/{task_b}/drafts?job={job_b}", None),
            ("GET", f"/drafts/{draft_b}", None),
        ]
        for method, path, body in routes:
            response = await api.request(method, path, headers=headers[0], json=body)
            assert response.status_code == 404, (method, path, response.text)
            assert response.json()["data"]["error"]["code"] == "not_found"


async def test_selection_replacement_stales_card_and_draft_and_cancel_publishes_nothing(
    tenants, tmp_path, admin_engine
):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        header = headers[0]
        task, _, extraction, requirements = await create_tender(
            api, app, header, tmp_path, confirmed=True
        )
        product, selection, _, _, _ = await select_real_materials(api, header, task, tmp_path)
        card = await create_card(
            api,
            header,
            task,
            extraction,
            requirements[0],
            {
                "response_kind": "evidence",
                "response_text": "The appliance has 64 GB memory.",
                "deviation": "none",
                "deviation_note": "The declaration equals the required memory capacity.",
                "evidence": [
                    {
                        "kind": "product",
                        "selection_id": selection["id"],
                        "field_path": "model",
                        "quote": PRODUCT_DATA["model"],
                    }
                ],
            },
        )
        classified = await api.post(
            f"/cards/{card['id']}/classification",
            headers=header,
            json={
                "expected_revision": card["revision"],
                "review_domain": "technical",
                "reason": "The scored memory clause needs technical review.",
            },
        )
        assert classified.status_code == 200, classified.text
        card = classified.json()["data"]
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "technical")
        card = await require_action(api, header, card, "submit")
        card = await require_action(
            api,
            header,
            card,
            "confirm",
            reviewed_evidence_ids=[row["id"] for row in card["evidence"]],
        )
        initial_draft = (
            await api.post(
                f"/tasks/{task}/drafts",
                headers=header,
                json={"extraction_job_id": extraction},
            )
        ).json()["data"]
        await app.state.processor(header["X-Org-Id"], initial_draft["job_id"])
        initial_job = (await api.get(f"/jobs/{initial_draft['job_id']}", headers=header)).json()[
            "data"
        ]
        draft_id = initial_job["result"]["draft_id"]
        assert (await api.get(f"/drafts/{draft_id}", headers=header)).json()["data"][
            "validity"
        ] == "current"

        revised_data = {**PRODUCT_DATA, "model": "Model with 128 GB memory"}
        revision_response = await api.post(
            f"/resources/products/{product['product_id']}/revisions",
            headers=header,
            json={"expected_revision": 1, "data": revised_data},
        )
        assert revision_response.status_code == 200, revision_response.text
        replacement_response = await api.post(
            f"/tasks/{task}/products",
            headers=header,
            json={"product_id": product["product_id"]},
        )
        assert replacement_response.status_code == 200, replacement_response.text
        replacement = replacement_response.json()["data"]
        assert replacement["replaced_snapshot_id"] == selection["id"]

        stale = (await api.get(f"/cards/{card['id']}", headers=header)).json()["data"]
        assert stale["state"] == "confirmed" and stale["eligibility"] == "stale_material"
        assert stale["evidence"][0]["active_selection"] is False
        old_draft = (await api.get(f"/drafts/{draft_id}", headers=header)).json()["data"]
        assert old_draft["validity"] == "stale"
        assert old_draft["invalidated_requirements"] == [requirements[0]["id"]]
        preview = (
            await api.post(
                f"/tasks/{task}/drafts",
                headers=header,
                json={"extraction_job_id": extraction, "dry_run": True},
            )
        ).json()["data"]
        assert preview["response_requirements"] == 0
        assert preview["gap_requirements"] == 5
        assert preview["gap_reasons"]["stale_material"] == 1

        # Input changed, so this is a new job.  Cancellation before the worker
        # runs leaves the prior immutable draft as the only published run.
        queued = (
            await api.post(
                f"/tasks/{task}/drafts",
                headers=header,
                json={"extraction_job_id": extraction},
            )
        ).json()["data"]
        assert queued["cached"] is False and queued["job_id"] != initial_draft["job_id"]
        cancelled = await api.post(f"/jobs/{queued['job_id']}/cancel", headers=header)
        assert cancelled.status_code == 200
        await app.state.processor(header["X-Org-Id"], queued["job_id"])
        status = (await api.get(f"/jobs/{queued['job_id']}", headers=header)).json()["data"]
        assert status["status"] == "cancelled"
        listed = (
            await api.get(f"/tasks/{task}/drafts", headers=header, params={"job": extraction})
        ).json()["items"]
        assert [row["id"] for row in listed] == [draft_id]


async def test_only_a_human_org_admin_can_disable_task_redaction(tenants, tmp_path, admin_engine):
    async with phase_one_client(tenants, tmp_path) as (api, _, headers, _):
        header = headers[0]
        task = (
            await api.post("/tasks", headers=header, json={"name": "Synthetic redaction gate"})
        ).json()["data"]
        task_id = task["id"]
        assert task["model_redaction_enabled"] is True
        assert task["model_redaction_revision"] == 1

        expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        token = (
            await api.post(
                "/tokens",
                headers=header,
                json={
                    "name": "Synthetic task writer",
                    "scopes": ["task:read", "card:write"],
                    "expires_at": expiry,
                },
            )
        ).json()["data"]["token"]
        token_header = {**header, "Authorization": "Bearer " + token}
        token_attempt = await api.put(
            f"/tasks/{task_id}/model-redaction",
            headers=token_header,
            json={"expected_revision": 1, "model_redaction_enabled": False},
        )
        assert token_attempt.status_code == 403

        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "technical")
        role_attempt = await api.put(
            f"/tasks/{task_id}/model-redaction",
            headers=header,
            json={"expected_revision": 1, "model_redaction_enabled": False},
        )
        assert role_attempt.status_code == 403
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "admin")
        changed = await api.put(
            f"/tasks/{task_id}/model-redaction",
            headers=header,
            json={"expected_revision": 1, "model_redaction_enabled": False},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["data"] == {
            "task_id": task_id,
            "model_redaction_enabled": False,
            "revision": 2,
            "changed_by": str(tenants["users"][0]),
        }
        conflict = await api.put(
            f"/tasks/{task_id}/model-redaction",
            headers=header,
            json={"expected_revision": 1, "model_redaction_enabled": True},
        )
        assert conflict.status_code == 409
        assert conflict.json()["data"]["error"]["code"] == "revision_conflict"

        foreign = await api.put(
            f"/tasks/{task_id}/model-redaction",
            headers=headers[1],
            json={"expected_revision": 2, "model_redaction_enabled": True},
        )
        assert foreign.status_code == 404


async def test_draft_worker_rechecks_inputs_membership_and_run_identity(
    tenants, tmp_path, admin_engine, monkeypatch
):
    from app.services import response_cards as card_service

    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        header = headers[0]

        gap_task, _, gap_extraction, gap_requirements = await create_tender(
            api, app, header, tmp_path, suffix="all-gap", confirmed=True
        )
        all_gap = (
            await api.post(
                f"/tasks/{gap_task}/drafts",
                headers=header,
                json={"extraction_job_id": gap_extraction},
            )
        ).json()["data"]
        await app.state.processor(header["X-Org-Id"], all_gap["job_id"])
        gap_status_response = await api.get(f"/jobs/{all_gap['job_id']}", headers=header)
        gap_status = gap_status_response.json()["data"]
        assert gap_status["status"] == "succeeded"
        assert gap_status_response.json()["ok"] is False
        assert gap_status["result"]["completion"] == "partial"
        assert gap_status["result"]["exit_code"] == 5
        gap_view = (
            await api.get(f"/drafts/{gap_status['result']['draft_id']}", headers=header)
        ).json()["data"]
        assert not any(gap_view["tables"].values())
        assert gap_view["comply_only"] == []
        assert len(gap_view["gaps"]) == len(gap_requirements) == 5

        task, _, extraction, requirements = await create_tender(
            api, app, header, tmp_path, suffix="worker-boundaries", confirmed=True
        )
        changed_job = (
            await api.post(
                f"/tasks/{task}/drafts",
                headers=header,
                json={"extraction_job_id": extraction},
            )
        ).json()["data"]
        card = await create_card(
            api,
            header,
            task,
            extraction,
            requirements[0],
            {
                "response_kind": None,
                "response_text": None,
                "deviation": None,
                "deviation_note": None,
                "evidence": [],
            },
        )
        await app.state.processor(header["X-Org-Id"], changed_job["job_id"])
        changed_status = (await api.get(f"/jobs/{changed_job['job_id']}", headers=header)).json()[
            "data"
        ]
        assert changed_status["status"] == "failed"
        assert changed_status["error"]["code"] == "draft_input_changed"
        assert (
            await api.get(f"/tasks/{task}/drafts", headers=header, params={"job": extraction})
        ).json()["items"] == []

        classified = await api.post(
            f"/cards/{card['id']}/classification",
            headers=header,
            json={
                "expected_revision": card["revision"],
                "review_domain": "technical",
                "reason": "Classify before testing worker membership revalidation.",
            },
        )
        assert classified.status_code == 200, classified.text
        card = classified.json()["data"]
        revoked_job = (
            await api.post(
                f"/tasks/{task}/drafts",
                headers=header,
                json={"extraction_job_id": extraction},
            )
        ).json()["data"]
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "viewer")
        await app.state.processor(header["X-Org-Id"], revoked_job["job_id"])
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "admin")
        revoked_status = (await api.get(f"/jobs/{revoked_job['job_id']}", headers=header)).json()[
            "data"
        ]
        assert revoked_status["status"] == "failed"
        assert revoked_status["error"]["code"] == "forbidden"

        revised = await api.put(
            f"/cards/{card['id']}",
            headers=header,
            json={"expected_revision": card["revision"], "content": card["content"]},
        )
        assert revised.status_code == 200, revised.text
        stale_attempt = (
            await api.post(
                f"/tasks/{task}/drafts",
                headers=header,
                json={"extraction_job_id": extraction},
            )
        ).json()["data"]
        entered, release = asyncio.Event(), asyncio.Event()
        original_task_lock = card_service.task_lock

        async def pause_before_job_recheck(session, task_id):
            entered.set()
            await release.wait()
            return await original_task_lock(session, task_id)

        monkeypatch.setattr(card_service, "task_lock", pause_before_job_recheck)
        old_attempt = asyncio.create_task(
            app.state.processor(header["X-Org-Id"], stale_attempt["job_id"])
        )
        await entered.wait()
        cancelled = await api.post(f"/jobs/{stale_attempt['job_id']}/cancel", headers=header)
        assert cancelled.status_code == 200
        release.set()
        await old_attempt
        attempt_status = (await api.get(f"/jobs/{stale_attempt['job_id']}", headers=header)).json()[
            "data"
        ]
        assert attempt_status["status"] == "cancelled"
        assert (
            await api.get(f"/tasks/{task}/drafts", headers=header, params={"job": extraction})
        ).json()["items"] == []


async def test_all_comply_only_draft_is_complete_with_exit_zero(tenants, tmp_path, admin_engine):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        header = headers[0]
        task, _, extraction, requirements = await create_tender(
            api, app, header, tmp_path, suffix="all-comply", confirmed=True
        )
        scored = await create_card(
            api,
            header,
            task,
            extraction,
            requirements[0],
            {
                "response_kind": None,
                "response_text": None,
                "deviation": None,
                "deviation_note": None,
                "evidence": [],
            },
        )
        classified = await api.post(
            f"/cards/{scored['id']}/classification",
            headers=header,
            json={
                "expected_revision": scored["revision"],
                "review_domain": "technical",
                "reason": "Assign the synthetic scored clause to technical review.",
            },
        )
        assert classified.status_code == 200, classified.text
        scored = classified.json()["data"]

        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "technical")
        technical_items = []
        for index in (0, 2, 3, 4):
            technical_items.append(
                {
                    "requirement_id": requirements[index]["id"],
                    "expected_revision": scored["revision"] if index == 0 else None,
                    "disposition": "comply_only",
                    "reason": "The reviewer marked this synthetic clause as comply only.",
                }
            )
        technical = await api.post(
            f"/tasks/{task}/cards/dispositions",
            headers=header,
            json={"extraction_job_id": extraction, "items": technical_items},
        )
        assert technical.status_code == 200, technical.text
        assert len(technical.json()["data"]["cards"]) == 4

        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "bidder")
        commercial = await api.post(
            f"/tasks/{task}/cards/dispositions",
            headers=header,
            json={
                "extraction_job_id": extraction,
                "items": [
                    {
                        "requirement_id": requirements[1]["id"],
                        "expected_revision": None,
                        "disposition": "comply_only",
                        "reason": "The reviewer marked this synthetic clause as comply only.",
                    }
                ],
            },
        )
        assert commercial.status_code == 200, commercial.text

        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "admin")
        queued = (
            await api.post(
                f"/tasks/{task}/drafts",
                headers=header,
                json={"extraction_job_id": extraction},
            )
        ).json()["data"]
        await app.state.processor(header["X-Org-Id"], queued["job_id"])
        status_response = await api.get(f"/jobs/{queued['job_id']}", headers=header)
        status = status_response.json()["data"]
        assert status_response.json()["ok"] is True
        assert status["status"] == "succeeded"
        assert status["result"]["completion"] == "complete"
        assert status["result"]["exit_code"] == 0
        view = (await api.get(f"/drafts/{status['result']['draft_id']}", headers=header)).json()[
            "data"
        ]
        assert view["completion"] == "complete"
        assert not any(view["tables"].values())
        assert len(view["comply_only"]) == 5
        assert view["gaps"] == []
        assert {row["requirement_id"] for row in view["comply_only"]} == {
            row["id"] for row in requirements
        }


async def test_citation_already_reported_as_a_gap_keeps_a_new_draft_current(
    tenants, tmp_path, admin_engine
):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        header = headers[0]
        task, _, extraction, requirements = await create_tender(
            api, app, header, tmp_path, suffix="known-invalid", confirmed=True
        )
        # A quote accepted only by normalized matching fails the verbatim check from the start.
        with admin_engine.begin() as connection:
            connection.execute(
                text("UPDATE requirements SET quote = replace(quote, ' ', '') WHERE id = :id"),
                {"id": requirements[0]["id"]},
            )
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "admin")
        queued = await api.post(
            f"/tasks/{task}/drafts", headers=header, json={"extraction_job_id": extraction}
        )
        assert queued.status_code == 200, queued.text
        await app.state.processor(header["X-Org-Id"], queued.json()["data"]["job_id"])
        [summary] = (
            await api.get(f"/tasks/{task}/drafts", headers=header, params={"job": extraction})
        ).json()["items"]
        draft = (await api.get(f"/drafts/{summary['id']}", headers=header)).json()["data"]

    gaps = {gap["requirement_id"]: gap["reasons"] for gap in draft["gaps"]}
    assert "invalid_citation" in gaps[requirements[0]["id"]]
    assert draft["validity"] == "current" and draft["invalidated_requirements"] == []
