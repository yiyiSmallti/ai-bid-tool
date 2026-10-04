"""Opt-in large synthetic export for opening in Word or WPS.

Builds a long labelled tender, a many-page synthetic certificate whose every page is
confirmed evidence, confirms every card through the human roles, and releases a final
section. Writes the DOCX and a receipt to data/work/export-acceptance/scale. Set
BID_EXPORT_SCALE_PAGES to run it; CI leaves it skipped.
"""

import asyncio
import hashlib
import json
import os
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from app.providers.base import LLMProvider
from app.schemas.contracts import (
    Category,
    ExtractedRequirement,
    Extraction,
    LLMResult,
    ProviderUsage,
)
from fakes import source_for
from test_exports import draft, prepared
from test_response_cards import (
    create_card,
    labelled_pdf,
    phase_one_client,
    require_action,
    run_document_job,
    set_role,
)

PAGES = int(os.environ.get("BID_EXPORT_SCALE_PAGES", "0"))
pytestmark = pytest.mark.skipif(PAGES < 1, reason="set BID_EXPORT_SCALE_PAGES to build it")
EXTRA = 20


def clause(number: int) -> str:
    # One line within the page width: labelled_pdf draws each line without wrapping.
    return f"Clause {number}: the synthetic appliance shall meet scale requirement {number}."


def certificate_line(number: int) -> str:
    return f"Synthetic certificate fixture page {number}: test record QMS-SCALE-{number:04d}."


def is_qualification(number: int) -> bool:
    return number % 5 == 2


class ScaleExtraction(LLMProvider):
    """Labelled fake: one requirement per tender page, no model call."""

    name = "test-fake"
    model = "scale-requirements"
    version = "test-v1"
    test_only = True

    async def extract(self, chunks, schema):
        items = [
            ExtractedRequirement(
                category=Category.qualification
                if is_qualification(chunk["page"])
                else Category.technical,
                starred=chunk["page"] % 7 == 0,
                text=clause(chunk["page"]),
                source=source_for(chunk),
            )
            for chunk in chunks
        ]
        return LLMResult(
            extraction=Extraction(items=items),
            usage=ProviderUsage(
                provider=self.name,
                model=self.model,
                version=self.version,
                duration_ms=1,
                tokens=len(items),
                usd=0,
                test_only=True,
            ),
        )


async def starter_template(api, header, task):
    """Upload the built-in starter template and bind it with its recommended columns."""
    from app.services.export_template_sample import binding_sections, build

    uploaded = await api.post(
        "/resources/templates",
        headers=header,
        data={"metadata": json.dumps({"data": {"name": "标准模板", "chapters": []}})},
        files={"file": ("starter.docx", build(), "application/octet-stream")},
    )
    assert uploaded.status_code == 200, uploaded.text
    template = uploaded.json()["data"]
    selected = (
        await api.post(
            f"/tasks/{task}/templates",
            headers=header,
            json={"template_id": template["template_id"]},
        )
    ).json()["data"]
    request = {
        "template_revision_id": template["id"],
        "expected_template_sha256": template["file"]["sha256"],
        "sections": binding_sections(),
        "dry_run": True,
    }
    preview = await api.post("/export-template-bindings", headers=header, json=request)
    assert preview.status_code == 200, preview.text
    request |= {
        "dry_run": False,
        "expected_static_content_hash": preview.json()["data"]["static_content_hash"],
    }
    binding = await api.post("/export-template-bindings", headers=header, json=request)
    assert binding.status_code == 200, binding.text
    return selected, binding.json()["data"]


async def test_large_synthetic_final_export(tenants, tmp_path, admin_engine, monkeypatch):
    org, user = tenants["orgs"][0], tenants["users"][0]
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        header = headers[0]
        app.state.processor.llm = ScaleExtraction()
        count = PAGES + EXTRA
        task = (
            await api.post("/tasks", headers=header, json={"name": "Synthetic scale export"})
        ).json()["data"]["id"]
        uploaded = await api.post(
            f"/tasks/{task}/documents",
            headers=header,
            files={
                "file": (
                    "synthetic-scale-tender.pdf",
                    labelled_pdf([clause(n) for n in range(1, count + 1)]),
                    "application/pdf",
                )
            },
        )
        assert uploaded.status_code == 200, uploaded.text
        document = uploaded.json()["data"]["id"]
        await run_document_job(api, app, header, document, "parse")
        extraction = await run_document_job(api, app, header, document, "extract")
        requirements = (
            await api.get(f"/tasks/{task}/requirements", headers=header, params={"job": extraction})
        ).json()["items"]
        assert len(requirements) == count
        selected, binding = await starter_template(api, header, task)

        data = {
            "kind": "qualification",
            "name": "合成测试质量管理体系认证证书",
            "number": "QMS-SCALE",
            "valid_from": "2026-01-01",
            "valid_until": "2027-01-01",
        }
        certificate = (
            await api.post("/resources/certificates", headers=header, json={"data": data})
        ).json()["data"]
        response = await api.post(
            f"/resources/certificates/{certificate['certificate_id']}/file-revisions",
            headers=header,
            data={"metadata": json.dumps({"expected_revision": 1, "data": data})},
            files={
                "file": (
                    "synthetic-scale-certificate.pdf",
                    labelled_pdf([certificate_line(n) for n in range(1, PAGES + 1)]),
                    "application/pdf",
                )
            },
        )
        assert response.status_code == 200, response.text
        chosen = await api.post(
            f"/tasks/{task}/certificates",
            headers=header,
            json={"certificate_id": certificate["certificate_id"]},
        )
        assert chosen.status_code == 200, chosen.text
        sources = []
        for page in range(1, PAGES + 1):
            response = await api.post(
                f"/tasks/{task}/evidence-sources",
                headers=header,
                json={"task_certificate_id": chosen.json()["data"]["id"], "page": page},
            )
            assert response.status_code == 200, response.text
            sources.append(response.json()["data"]["source"])

        cards = []
        for index, requirement in enumerate(requirements):
            number = index + 1
            evidence = (
                [
                    {
                        "kind": "certificate_pdf_page",
                        "evidence_source_id": sources[index]["id"],
                        "quote": certificate_line(number),
                    }
                ]
                if index < PAGES
                else []
            )
            cards.append(
                await create_card(
                    api,
                    header,
                    task,
                    extraction,
                    requirement,
                    {
                        "response_kind": "evidence" if evidence else "commitment",
                        "response_text": (
                            f"完全响应。我单位承诺所投合成测试设备满足第 {number} 项规模要求，"
                            "并按招标文件约定提供相应服务（合成测试材料）。"
                        ),
                        "deviation": "negative" if number % 25 == 0 else "none",
                        "deviation_note": f"合成测试：第 {number} 项的质保期少于要求。"
                        if number % 25 == 0
                        else f"合成测试：第 {number} 项按要求响应。",
                        "evidence": evidence,
                    },
                )
            )
        for role in ("technical", "bidder"):
            set_role(admin_engine, org, user, role)
            for index, card in enumerate(cards):
                if (role == "bidder") != is_qualification(index + 1):
                    continue
                submitted = await require_action(api, header, card, "submit")
                cards[index] = await require_action(
                    api,
                    header,
                    submitted,
                    "confirm",
                    reviewed_evidence_ids=[e["id"] for e in submitted["evidence"]],
                    reviewed_warning_codes=submitted["warning_codes"],
                    reason="Synthetic review of the scale fixture.",
                )

        draft_id = await draft(api, app, header, task, extraction)
        body = {
            "draft_id": draft_id,
            "task_template_id": selected["id"],
            "binding_id": binding["id"],
            "mode": "final_section",
        }
        run = await prepared(api, app, header, task, body)
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
        media = sorted(
            hashlib.sha256(package.read(name)).hexdigest()
            for name in package.namelist()
            if name.startswith("word/media/")
        )
    assert media == sorted(source["preview"]["sha256"] for source in sources)
    artifact = Path("data/work/export-acceptance/scale")
    await asyncio.to_thread(artifact.mkdir, parents=True, exist_ok=True)
    (artifact / "synthetic-scale-final.docx").write_bytes(content)
    (artifact / "receipt.json").write_text(
        json.dumps(
            {
                "synthetic": True,
                "file_sha256": hashlib.sha256(content).hexdigest(),
                "file_bytes": len(content),
                "requirements": count,
                "attachment_pages": PAGES,
                "negative_deviations": count // 25,
                "media_sha256": media,
            },
            indent=2,
        )
    )
