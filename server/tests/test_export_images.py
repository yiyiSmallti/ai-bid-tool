"""Image evidence in human exports, through real ingest, cards, decisions and the worker.

Failure modes enumerated before implementation:
- an undecided prototype must block final_section but never review_copy;
- a keep decision must be fixed in the input hash and linked on the run evidence row;
- a replace decision after release must make the published export stale, and a new
  final preview must report prototype_replacement_pending;
- the DOCX must embed the exact confirmed rendition bytes and never reveal, in any
  part, that an image is a prototype (no material kind, no label, no decision reason).

The Rust renderer is a genuine dependency and the test is skipped when it is absent.
"""

import asyncio
import hashlib
import json
from io import BytesIO
from pathlib import Path
from uuid import UUID
from zipfile import ZipFile

from app.models.exports import ExportRunEvidence
from sqlalchemy import select
from sqlalchemy.orm import Session
from task_fixtures import reviewer_header
from test_exports import draft, prepared, setup_template  # pyright: ignore[reportMissingImports]
from test_prototype_decisions import (  # pyright: ignore[reportMissingImports]
    confirmed_prototype_card,
    decision_input,
    prepare_ingest_prototype,
    preview_input,
    renderer_required,
    seed_prototype,
)
from test_response_cards import (  # pyright: ignore[reportMissingImports]
    create_card,
    create_tender,
    phase_one_client,
    require_action,
    set_role,
)
from test_screenshot_cards import selected_feature  # pyright: ignore[reportMissingImports]


async def commitment(api, header, task, extraction, requirement, index):
    return await create_card(
        api,
        header,
        task,
        extraction,
        requirement,
        {
            "response_kind": "commitment",
            "response_text": f"Synthetic confirmed commitment {index + 1}.",
            "deviation": "none",
            "deviation_note": "The synthetic commitment is retained verbatim.",
            "evidence": [],
        },
    )


async def confirm(api, header, card):
    submitted = await require_action(api, header, card, "submit")
    return await require_action(
        api,
        header,
        submitted,
        "confirm",
        reviewed_evidence_ids=[e["id"] for e in submitted["evidence"]],
        reviewed_warning_codes=submitted["warning_codes"],
        reason="Synthetic review of proof obligations.",
    )


async def decide(api, header, task, extraction, feature_id, **choice):
    preview = await api.post(
        f"/tasks/{task}/prototype-decisions/preview",
        headers=header,
        json=preview_input(extraction, feature_id),
    )
    assert preview.status_code == 200, preview.text
    applied = await api.post(
        f"/tasks/{task}/prototype-decisions",
        headers=header,
        json=decision_input(preview.json()["data"], extraction, feature_id, **choice),
    )
    assert applied.status_code == 200, applied.text
    return applied.json()["data"]["decisions"][0]


async def test_prototype_image_export_gate_keep_attachment_and_replace(
    tenants, tmp_path, admin_engine
):
    renderer_required()
    org, user = tenants["orgs"][0], tenants["users"][0]
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        header = headers[0]
        task, _, extraction, requirements = await create_tender(
            api, app, header, tmp_path, suffix="export-images"
        )
        selected, binding = await setup_template(api, header, task)
        feature = await selected_feature(api, header, task)
        prototype = await seed_prototype(
            app,
            org,
            user,
            UUID(task),
            UUID(extraction),
            UUID(requirements[2]["id"]),
            UUID(feature["id"]),
        )
        uploaded = await prepare_ingest_prototype(
            api, app, header, task, UUID(extraction), prototype
        )
        rendition_sha = uploaded["rendition"]["image"]["sha256"]

        cards = {}
        for index in (0, 1, 4):
            cards[index] = await commitment(
                api, header, task, extraction, requirements[index], index
            )
        classified = await api.post(
            f"/cards/{cards[0]['id']}/classification",
            headers=header,
            json={
                "expected_revision": cards[0]["revision"],
                "review_domain": "technical",
                "reason": "The synthetic scored requirement is technical.",
            },
        )
        assert classified.status_code == 200, classified.text
        cards[0] = classified.json()["data"]
        _, technical_header = await reviewer_header(api, admin_engine, org, UUID(task), "technical")
        _, commercial_header = await reviewer_header(
            api, admin_engine, org, UUID(task), "commercial"
        )
        set_role(admin_engine, org, user, "technical")
        for index in (0, 4):
            await confirm(api, technical_header, cards[index])
        await confirmed_prototype_card(
            api, technical_header, task, extraction, requirements[2], uploaded
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
                        "reason": "The complaint procedure requires compliance only.",
                    }
                ],
            },
        )
        assert disposition.status_code == 200, disposition.text
        set_role(admin_engine, org, user, "bidder")
        await confirm(api, commercial_header, cards[1])
        body = {
            "draft_id": await draft(api, app, header, task, extraction),
            "task_template_id": selected["id"],
            "binding_id": binding["id"],
            "mode": "final_section",
        }

        undecided = await api.post(
            f"/tasks/{task}/export-runs", headers=header, json={**body, "dry_run": True}
        )
        # A blocked preflight answers 400 export_blocked and still lists every issue.
        assert undecided.json()["data"]["error"]["code"] == "export_blocked", undecided.text
        blocks = {i["code"] for i in undecided.json()["data"]["issues"] if i["severity"] == "block"}
        assert blocks == {"prototype_decision_required"}
        review = await api.post(
            f"/tasks/{task}/export-runs",
            headers=header,
            json={**body, "mode": "review_copy", "dry_run": True},
        )
        assert review.json()["data"]["ready"], review.text
        assert not any(i["code"].startswith("prototype_") for i in review.json()["data"]["issues"])

        set_role(admin_engine, org, user, "technical")
        kept = await decide(api, technical_header, task, extraction, feature["id"], decision="keep")
        set_role(admin_engine, org, user, "bidder")
        decided = await api.post(
            f"/tasks/{task}/export-runs", headers=header, json={**body, "dry_run": True}
        )
        fixed = decided.json()["data"]
        assert fixed["ready"] and fixed["attachment_pages"] == 1, decided.text
        assert fixed["input_hash"] != undecided.json()["data"]["input_hash"]

        run = await prepared(api, app, header, task, body)
        with Session(admin_engine) as session:
            links = session.scalars(
                select(ExportRunEvidence).where(ExportRunEvidence.run_id == UUID(run["id"]))
            ).all()
            assert [str(row.prototype_decision_id) for row in links] == [kept["id"]]
        released = await api.post(
            f"/export-runs/{run['id']}/release",
            headers=header,
            json={
                "expected_input_hash": run["input_hash"],
                "expected_candidate_sha256": run["candidate_sha256"],
            },
        )
        assert released.status_code == 200 and released.json()["ok"], released.text
        export = released.json()["data"]
        link = await api.get(f"/exports/{export['id']}/download-link", headers=header)
        content = (await api.get(link.json()["data"]["url"], headers=header)).content
        with ZipFile(BytesIO(content)) as package:
            media = [
                hashlib.sha256(package.read(n)).hexdigest()
                for n in package.namelist()
                if n.startswith("word/media/")
            ]
            text = "".join(
                package.read(n).decode()
                for n in package.namelist()
                if n.endswith(".xml") or n.endswith(".rels")
            )
        assert media == [rendition_sha]
        assert "证据图片" in text and "Synthetic workflow control is visible." in text
        # The fixture's own human-written response mentions a prototype; system text must not.
        system_text = text.replace("The synthetic prototype displays the requested workflow.", "")
        for hidden in ("prototype", "原型", "will_deliver", kept["id"]):
            assert hidden not in system_text.lower(), hidden

        set_role(admin_engine, org, user, "technical")
        await decide(
            api,
            technical_header,
            task,
            extraction,
            feature["id"],
            decision="replace",
            reason="A real screenshot of the delivered workflow will replace it.",
        )
        set_role(admin_engine, org, user, "bidder")
        stale = await api.get(f"/exports/{export['id']}", headers=header)
        assert stale.json()["data"]["validity"] == "stale", stale.text
        pending = await api.post(
            f"/tasks/{task}/export-runs", headers=header, json={**body, "dry_run": True}
        )
        assert {
            i["code"] for i in pending.json()["data"]["issues"] if i["severity"] == "block"
        } == {"prototype_replacement_pending"}

        artifact = Path("data/work/export-acceptance/images")
        await asyncio.to_thread(artifact.mkdir, parents=True, exist_ok=True)
        (artifact / "synthetic-prototype-final.docx").write_bytes(content)
        (artifact / "receipt.json").write_text(
            json.dumps(
                {
                    "file_sha256": hashlib.sha256(content).hexdigest(),
                    "media_sha256": media,
                    "input_hash": run["input_hash"],
                    "synthetic": True,
                },
                indent=2,
            )
        )
