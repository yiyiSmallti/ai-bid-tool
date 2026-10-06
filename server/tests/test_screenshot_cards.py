"""Screenshot evidence integration through the real card API and draft processor.

Failure modes covered by this module:

* the response-card contract rejects an image hash, region, claim scope, or
  observation that is missing or malformed while preserving every legacy
  evidence contract;
* card creation cannot link an image from another task or extraction, a
  mismatched rendition/asset pair, an unreviewed or withdrawn asset, an
  inactive fixed selection, a mismatched image hash, or an incompatible image
  kind and claim scope;
* image evidence fixes the exact asset, rendition, image hash, pixel region,
  claim scope, visual observation, and selected resource revision instead of
  putting an observation into the legacy quote field;
* tokens, agents, workers, administrators outside the assigned review domain,
  and reviewers who omit any linked evidence cannot confirm image evidence;
* confirmation revalidates the fixed image and changes only its image review
  state; privacy review never substitutes for the existing per-card human gate;
* draft preview, submission, background processing, and reading an existing
  draft each reject or invalidate image evidence that has become withdrawn,
  hash-invalid, selection-invalid, or otherwise unavailable;
* confirmed valid image evidence appears in the deterministic response row,
  while unconfirmed or invalid image evidence remains a gap and is never
  consumed as a response-row attachment;
* an undecided prototype remains eligible for a draft and does not create a
  gap; prototype keep/replace decisions belong to the later export gate;
* image views remain tenant-scoped and expose no storage key or long-lived URL.

The database-backed API fixtures are added with the screenshot schema and
service implementation.  Tests use synthetic local bytes and fake providers;
they do not call a real vendor.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.schemas.response_card_contracts import CardContent, EvidenceInput, EvidenceView
from app.schemas.screenshot_contracts import ImagePlan, ScreenshotIngest, UploadSource
from app.services import screenshots
from pydantic import TypeAdapter, ValidationError
from test_response_cards import (  # pyright: ignore[reportMissingImports]
    create_card,
    create_tender,
    phase_one_client,
    require_action,
    sanitized_artifact,
    set_role,
)
from test_screenshot_renderer import (  # pyright: ignore[reportMissingImports]
    ScreenshotRenderer,
    _rgb_png,
)

ARTIFACT_ROOT = Path(__file__).resolve().parents[2] / "data/work/team-workflow-acceptance/cosign"


def image_input(**changes):
    value = {
        "kind": "image_region",
        "asset_id": str(uuid4()),
        "rendition_id": str(uuid4()),
        "expected_image_sha256": "a" * 64,
        "region": {"x": 10, "y": 20, "width": 300, "height": 180},
        "claim_scope": "functional_observation",
        "visual_observation": "The selected region shows the configured workflow control.",
    }
    value.update(changes)
    return value


def test_image_evidence_contract_is_discriminated_and_keeps_legacy_quote_strict():
    adapter = TypeAdapter(EvidenceInput)
    image = adapter.validate_python(image_input())
    assert image.kind == "image_region" and not hasattr(image, "quote")
    CardContent(
        response_kind="evidence",
        response_text="The interface exposes the configured workflow control.",
        deviation="none",
        deviation_note="The visible control corresponds to the requested workflow.",
        evidence=[image],
    )

    invalid_images = (
        image_input(expected_image_sha256="not-a-hash"),
        image_input(region={"x": 0, "y": 0, "width": 0, "height": 1}),
        image_input(claim_scope="exact_field_match"),
        image_input(visual_observation="   "),
        image_input(quote="The image does not become a legacy quotation."),
    )
    for value in invalid_images:
        with pytest.raises(ValidationError):
            adapter.validate_python(value)

    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                "kind": "feature",
                "selection_id": str(uuid4()),
                "field_path": "description",
            }
        )


def test_image_evidence_view_requires_an_exact_fixed_reference_and_review_state():
    payload = image_input()
    common = {
        "id": str(uuid4()),
        "org_id": str(uuid4()),
        "task_id": str(uuid4()),
        "card_id": str(uuid4()),
        "input": payload,
        "selection_id": str(uuid4()),
        "resource_revision_id": str(uuid4()),
        "material_kind": "user_screenshot",
        "quote_check": "unreviewed_image",
        "source_archive": None,
        "screenshot_asset_id": payload["asset_id"],
        "screenshot_rendition_id": payload["rendition_id"],
        "image_sha256": payload["expected_image_sha256"],
        "region": payload["region"],
        "claim_scope": payload["claim_scope"],
        "visual_observation": payload["visual_observation"],
        "confirmed_by": None,
        "confirmed_at": None,
        "active_selection": True,
    }
    assert EvidenceView.model_validate(common).quote_check == "unreviewed_image"

    for changes in (
        {"image_sha256": "b" * 64},
        {"screenshot_rendition_id": str(uuid4())},
        {"quote_check": "exact_field_match"},
        {"visual_observation": None},
        {"quote_check": "human_image_review"},
    ):
        with pytest.raises(ValidationError):
            EvidenceView.model_validate(common | changes)


async def selected_feature(api, header, task_id):
    product = await api.post(
        "/resources/products",
        headers=header,
        json={
            "data": {
                "name": "Synthetic screenshot product",
                "vendor": "Synthetic vendor",
                "model": "Synthetic model",
            }
        },
    )
    assert product.status_code == 200, product.text
    feature = await api.post(
        "/resources/features",
        headers=header,
        json={
            "data": {
                "product_id": product.json()["data"]["product_id"],
                "name": "Synthetic delivery workflow",
                "description": "A synthetic workflow used only by the screenshot gate test.",
                "status": "developing",
            }
        },
    )
    assert feature.status_code == 200, feature.text
    selected = await api.post(
        f"/tasks/{task_id}/features",
        headers=header,
        json={"feature_id": feature.json()["data"]["feature_id"]},
    )
    assert selected.status_code == 200, selected.text
    return selected.json()["data"]


async def ingest_synthetic_screenshot(api, header, task_id, extraction_id, task_feature_id):
    renderer = ScreenshotRenderer()
    if not renderer.binary.is_file() or not os.access(renderer.binary, os.X_OK):
        pytest.skip(f"build the Rust renderer first: {renderer.binary}")
    source_png = _rgb_png(32, 20, [(230, 240, 250)] * (32 * 20))
    prepared_png, receipt = await screenshots.prepare(
        source_png,
        UploadSource(
            kind="upload",
            task_feature_id=UUID(task_feature_id),
            image_kind="screenshot",
            source_label="Synthetic local test screenshot",
            software_version="test-fixture-1",
            environment="test",
            captured_at=datetime(2026, 10, 2, tzinfo=UTC),
        ),
        ImagePlan(),
    )
    body = ScreenshotIngest(
        extraction_job_id=UUID(extraction_id),
        prepared=receipt,
        reviewed_upload_sha256=receipt.image.sha256,
        idempotency_key=uuid4(),
    )
    response = await api.post(
        f"/tasks/{task_id}/screenshots",
        headers=header,
        files={
            "file": ("SAFE.png", prepared_png, "image/png"),
            "input": (None, body.model_dump_json(), "application/json"),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def run_draft(api, app, header, task_id, extraction_id):
    submitted = await api.post(
        f"/tasks/{task_id}/drafts",
        headers=header,
        json={"extraction_job_id": extraction_id},
    )
    assert submitted.status_code == 200, submitted.text
    job_id = submitted.json()["data"]["job_id"]
    await app.state.processor(header["X-Org-Id"], job_id)
    job = await api.get(f"/jobs/{job_id}", headers=header)
    assert job.status_code == 200 and job.json()["data"]["status"] == "succeeded", job.text
    draft = await api.get(f"/drafts/{job.json()['data']['result']['draft_id']}", headers=header)
    assert draft.status_code == 200, draft.text
    return draft.json()["data"]


async def test_image_card_confirmation_draft_and_withdrawal_revalidation(
    tenants, tmp_path, admin_engine
):
    """The real API and processor consume only the fixed, live, human-reviewed rendition."""
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        header = headers[0]
        task_id, _, extraction_id, requirements = await create_tender(
            api, app, header, tmp_path, suffix="screenshot-card"
        )
        feature = await selected_feature(api, header, task_id)
        uploaded = await ingest_synthetic_screenshot(
            api, header, task_id, extraction_id, feature["id"]
        )
        asset = uploaded["asset"]
        rendition = uploaded["rendition"]
        evidence_input = image_input(
            asset_id=asset["id"],
            rendition_id=rendition["id"],
            expected_image_sha256=rendition["image"]["sha256"],
            region={"x": 0, "y": 0, "width": 16, "height": 10},
        )
        rejected = await api.post(
            f"/tasks/{task_id}/cards",
            headers=header,
            json={
                "extraction_job_id": extraction_id,
                "requirement_id": requirements[2]["id"],
                "content": {
                    "response_kind": "evidence",
                    "response_text": "A mismatched image hash must not become evidence.",
                    "deviation": "none",
                    "deviation_note": "This deliberately invalid card must roll back.",
                    "evidence": [evidence_input | {"expected_image_sha256": "b" * 64}],
                },
            },
        )
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["data"]["error"]["code"] == "image_hash_mismatch"
        card = await create_card(
            api,
            header,
            task_id,
            extraction_id,
            requirements[2],
            {
                "response_kind": "evidence",
                "response_text": "The test screenshot shows the delivery workflow control.",
                "deviation": "none",
                "deviation_note": "The visible control corresponds to the requested workflow.",
                "evidence": [evidence_input],
            },
        )
        linked = card["evidence"][0]
        assert linked["quote_check"] == "unreviewed_image"
        assert linked["screenshot_asset_id"] == asset["id"]
        assert linked["screenshot_rendition_id"] == rendition["id"]
        assert linked["image_sha256"] == rendition["image"]["sha256"]
        assert linked["region"] == evidence_input["region"]
        assert linked["claim_scope"] == "functional_observation"
        assert linked["visual_observation"] == evidence_input["visual_observation"]
        assert "storage_key" not in str(linked) and "url" not in str(linked).lower()

        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "technical")
        pending = await require_action(api, header, card, "submit")
        preview = await api.post(
            f"/tasks/{task_id}/drafts",
            headers=header,
            json={"extraction_job_id": extraction_id, "dry_run": True},
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["data"]["gap_reasons"]["unconfirmed"] == 1
        assert preview.json()["data"]["response_requirements"] == 0
        assert "cosign_required" not in preview.json()["data"]["gap_reasons"]
        pending_board = await api.get(
            f"/tasks/{task_id}/board",
            headers=header,
            params={"extraction_job_id": extraction_id},
        )
        assert pending_board.status_code == 200, pending_board.text
        pending_row = next(
            item
            for item in pending_board.json()["items"]
            if item["requirement_id"] == requirements[2]["id"]
        )
        assert not {"pending_cosign", "invalidated_cosign"} & set(pending_row["blockers"])
        assert [action["code"] for action in pending_row["next_actions"]] == ["confirm"]
        missing_review = await api.post(
            f"/cards/{pending['id']}/actions",
            headers=header,
            json={
                "expected_revision": pending["revision"],
                "action": "confirm",
                "reviewed_evidence_ids": [linked["id"]],
            },
        )
        assert missing_review.status_code == 400
        assert missing_review.json()["data"]["error"]["code"] == "warning_review_required"
        confirmed = await require_action(
            api,
            header,
            pending,
            "confirm",
            reviewed_evidence_ids=[linked["id"]],
            reviewed_warning_codes=pending["warning_codes"],
            reason="Reviewed the synthetic source, test environment and visible scope.",
        )
        assert confirmed["evidence"][0]["quote_check"] == "human_image_review"

        first = await run_draft(api, app, header, task_id, extraction_id)
        row = next(
            row
            for rows in first["tables"].values()
            for row in rows
            if row["requirement_id"] == requirements[2]["id"]
        )
        assert row["evidence"][0]["image_sha256"] == rendition["image"]["sha256"]
        assert first["validity"] == "current"

        withdrawn = await api.post(
            f"/screenshots/{asset['id']}/withdrawals",
            headers=header,
            json={"reason": "Synthetic privacy regression discovered during the gate test."},
        )
        assert withdrawn.status_code == 200, withdrawn.text
        stale = await api.get(f"/drafts/{first['id']}", headers=header)
        assert stale.status_code == 200, stale.text
        stale_view = stale.json()["data"]
        assert stale_view["validity"] == "stale"
        assert requirements[2]["id"] in stale_view["invalidated_requirements"]
        assert all(not rows for rows in stale_view["tables"].values())
        old_gap = next(
            item for item in stale_view["gaps"] if item["requirement_id"] == requirements[2]["id"]
        )
        assert old_gap["reasons"] == ["stale_material"]
        assert not {"response_text", "deviation_note", "evidence"} & old_gap.keys()

        board = await api.get(
            f"/tasks/{task_id}/board",
            headers=header,
            params={"extraction_job_id": extraction_id},
        )
        assert board.status_code == 200, board.text
        board_row = next(
            item
            for item in board.json()["items"]
            if item["requirement_id"] == requirements[2]["id"]
        )
        assert board_row["bucket"] == "gap" and board_row["eligibility"] == "stale_material"
        assert "stale_material" in board_row["blockers"]
        assert not {"pending_cosign", "invalidated_cosign"} & set(board_row["blockers"])
        assert [action["code"] for action in board_row["next_actions"]] == ["refresh_material"]

        second = await run_draft(api, app, header, task_id, extraction_id)
        gap = next(
            item for item in second["gaps"] if item["requirement_id"] == requirements[2]["id"]
        )
        assert gap["reasons"] == ["stale_material"]
        assert all(not rows for rows in second["tables"].values())
        assert not {"response_text", "deviation_note", "evidence"} & gap.keys()
        ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
        (ARTIFACT_ROOT / "single-domain-image-withdrawal.json").write_text(
            json.dumps(
                sanitized_artifact(
                    {
                        "case": "single-domain-image-withdrawal",
                        "pending_board": pending_row,
                        "withdrawn_board": board_row,
                        "original_draft": stale_view,
                        "new_draft": second,
                        "command": "uv run pytest -q server/tests/test_screenshot_cards.py -k withdrawal",
                    }
                ),
                indent=2,
            )
        )
