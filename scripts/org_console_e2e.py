"""Provision the real org-console browser fixture through API and job processor.

The script accepts only a PostgreSQL database whose name starts with ``bid_test``.
It creates identities with the migration owner, then creates every business object
through the restricted application API. Parse, extraction and draft jobs are run
by the production Processor. The only vendor is an in-process
``httpx.MockTransport`` returning explicitly synthetic, exact citations.

The generated manifest contains object IDs and fixture metadata needed by
Playwright. It never contains passwords, sessions, authorization headers, signed
URLs or vendor request bodies. Run artifacts and storage must stay under
``data/work`` in this worktree.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pymupdf
from app.api.main import create_app
from app.core.config import Settings
from app.core.security import hash_password
from app.models.entities import Membership, Org, User
from app.providers.llm import OpenAICompatibleExtractor
from docx import Document as WordDocument
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[1]
WORK_ROOT = (REPO_ROOT / "data" / "work").resolve()
ROLE_NAMES = ("admin", "bidder", "technical", "viewer")
ORG_NAMES = {"a": "合成控制台单位 A", "b": "合成控制台单位 B"}
WORD_REQUIREMENTS = 1_200
SYNTHETIC_MODEL = "org-console-synthetic-model"


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def tcp_port(raw: str) -> int:
    value = int(raw)
    if not 1 <= value <= 65_535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return value


def guarded_url(name: str) -> str:
    value = env(name)
    database = make_url(value).database or ""
    if not database.startswith("bid_test"):
        raise SystemExit(f"{name} must select a database whose name starts with bid_test")
    return value


def output_directory(raw: str) -> Path:
    output = Path(raw).resolve()
    if not output.is_relative_to(WORK_ROOT):
        raise SystemExit(f"--output must be under {WORK_ROOT}")
    output.mkdir(parents=True, exist_ok=True)
    return output


def configured_data_directory() -> Path:
    data_dir = Path(env("BID_DATA_DIR")).resolve()
    if not data_dir.is_relative_to(WORK_ROOT):
        raise SystemExit(f"BID_DATA_DIR must be under {WORK_ROOT}")
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def write_browser_materials(output: Path) -> dict[str, str]:
    pdf_path = output / "browser-upload-synthetic.pdf"
    word_path = output / "browser-upload-synthetic.docx"
    pdf_path.write_bytes(pdf_fixture(2, "A"))
    word_path.write_bytes(word_fixture(4, "A"))
    return {"pdf": str(pdf_path), "word": str(word_path)}


def word_fixture(count: int, label: str) -> bytes:
    """Return distinct paragraphs so every quote has one structural location."""
    document = WordDocument()
    for index in range(1, count + 1):
        document.add_paragraph(
            f"合成条款 {label}-{index:04d} 要求投标人在交付清单中提交唯一标识 "
            f"SYN-{label}-{index:04d} 的说明文件。"
        )
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def pdf_fixture(count: int, label: str) -> bytes:
    """Return a real PDF with a different sentence on every page."""
    with pymupdf.open() as document:
        for index in range(1, count + 1):
            page = document.new_page()
            sentence = (
                f"Synthetic tender {label} page {index} requires unique record "
                f"PDF-{label}-{index:02d} at delivery."
            )
            page.insert_text((54, 72), sentence)
        return document.tobytes()


class SyntheticVendor:
    """Build exact extraction citations from the text actually sent by the adapter."""

    block = re.compile(r'<block id="([^"]+)">\n(.*?)\n</block>', re.DOTALL)
    page = re.compile(r'<page number="(\d+)">\n(.*?)\n</page>', re.DOTALL)

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @staticmethod
    def item(ref: str, quote: str) -> dict[str, Any]:
        marker = re.search(r"(?:SYN-[AB]-|PDF-[AB]-)(\d+)", quote)
        ordinal = int(marker.group(1)) if marker else 1
        category = ("qualification", "technical", "scoring", "substantive")[(ordinal - 1) % 4]
        return {
            "category": category,
            "starred": category == "substantive" or ordinal % 17 == 0,
            "text": f"合成响应要求 {ordinal:04d}",
            "ref": ref,
            "quote": quote,
            "condition": None,
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prompt = body["messages"][-1]["content"]
        items = [self.item(ref, text.strip()) for ref, text in self.block.findall(prompt)]
        items.extend(self.item(ref, text.strip()) for ref, text in self.page.findall(prompt))
        self.calls.append(
            {
                "kind": "extract",
                "items": len(items),
                "reasoning_effort": body.get("reasoning_effort"),
            }
        )
        return httpx.Response(
            200,
            json={
                "model": SYNTHETIC_MODEL,
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"items": items}, ensure_ascii=False)},
                    }
                ],
                "usage": {"prompt_tokens": max(1, len(prompt) // 4), "completion_tokens": 50},
            },
        )


class SyntheticExtractor(OpenAICompatibleExtractor):
    test_only = True


@dataclass
class FixtureQueue:
    calls: list[tuple[str, str]] = field(default_factory=list)
    processor: Any = None
    run_jobs: bool = False
    tasks: set[asyncio.Task[None]] = field(default_factory=set)

    async def enqueue(self, org_id: str, job_id: str) -> int:
        self.calls.append((org_id, job_id))
        if self.run_jobs:
            if self.processor is None:
                raise RuntimeError("Fixture processor is not configured")
            task = asyncio.create_task(self.processor(org_id, job_id))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        return len(self.calls)


def fixture_settings(data_dir: Path, *, web_dir: Path | None = None) -> Settings:
    return Settings.load().model_copy(
        update={
            "data_dir": data_dir,
            "web_dir": web_dir,
            "llm_provider": "openai",
            "llm_model": SYNTHETIC_MODEL,
            "llm_api_key": None,
            "llm_base_url": "https://synthetic.example.invalid/v1",
            "llm_input_usd_per_mtok": 1.0,
            "llm_output_usd_per_mtok": 2.0,
            "llm_concurrency": 8,
            "llm_max_output_tokens": 64_000,
        }
    )


def fixture_app(settings: Settings, vendor: SyntheticVendor, *, run_jobs: bool = False):
    llm = SyntheticExtractor(settings, httpx.MockTransport(vendor))
    llm.reasoning_levels = {
        "low": {
            "name": "low",
            "label": "轻度推理",
            "request_options": {"reasoning_effort": "low"},
            "batch_chars": 16_000,
        },
        "high": {
            "name": "high",
            "label": "深度推理",
            "request_options": {"reasoning_effort": "high"},
            "batch_chars": 8_000,
        },
    }
    llm.default_reasoning = "low"
    llm.model_revision = 1
    return create_app(settings, llm=llm, queue=FixtureQueue(run_jobs=run_jobs))


def seed_identities(admin_url: str, password: str) -> dict[str, dict[str, Any]]:
    engine = create_engine(admin_url, hide_parameters=True)
    password_hash = hash_password(password)
    seeded: dict[str, dict[str, Any]] = {}
    try:
        with Session(engine) as session, session.begin():
            existing = session.scalar(select(Org.id).where(Org.name.in_(ORG_NAMES.values())))
            if existing is not None:
                raise SystemExit(
                    "The org-console fixture already exists; use a fresh isolated bid_test database"
                )
            for org_key, org_name in ORG_NAMES.items():
                org_id = uuid4()
                session.add(Org(id=org_id, org_id=org_id, name=org_name))
                users: dict[str, dict[str, str]] = {}
                for role in ROLE_NAMES:
                    user_id = uuid4()
                    email = f"org-console-{org_key}-{role}@example.test"
                    session.add(User(id=user_id, email=email, password_hash=password_hash))
                    # Memberships reference users without an ORM relationship, so write users first.
                    session.flush()
                    session.add(Membership(org_id=org_id, user_id=user_id, role=role, active=True))
                    users[role] = {"id": str(user_id), "email": email}
                seeded[org_key] = {
                    "id": str(org_id),
                    "name": org_name,
                    "users": users,
                }
    finally:
        engine.dispose()
    return seeded


async def response_json(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    expected: int = 200,
    **kwargs: Any,
) -> dict[str, Any]:
    response = await client.request(method, path, **kwargs)
    if response.status_code != expected:
        raise RuntimeError(
            f"{method} {path} returned {response.status_code}: {response.text[:1000]}"
        )
    payload = response.json()
    if expected < 400 and set(payload) != {
        "ok",
        "command",
        "data",
        "items",
        "warnings",
        "cost",
        "duration_ms",
    }:
        raise RuntimeError(f"{method} {path} did not return the Result seven-key contract")
    return payload


async def login(
    client: httpx.AsyncClient, org: dict[str, Any], role: str, password: str
) -> dict[str, str]:
    payload = await response_json(
        client,
        "POST",
        "/auth/login",
        json={
            "email": org["users"][role]["email"],
            "password": password,
            "org_id": org["id"],
        },
    )
    return {
        "Authorization": "Bearer " + payload["data"]["session"],
        "X-Org-Id": org["id"],
    }


async def run_job(
    client: httpx.AsyncClient,
    app: Any,
    headers: dict[str, str],
    document_id: str,
    action: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    receipt = await response_json(
        client,
        "POST",
        f"/documents/{document_id}/{action}",
        headers=headers,
        json=body or {},
    )
    job_id = receipt["data"]["job_id"]
    await app.state.processor(headers["X-Org-Id"], job_id)
    status = await response_json(client, "GET", f"/jobs/{job_id}", headers=headers)
    if status["data"]["status"] != "succeeded":
        raise RuntimeError(f"{action} job failed: {status['data']}")
    return status["data"]


async def create_task_documents(
    client: httpx.AsyncClient,
    app: Any,
    headers: dict[str, str],
    org_key: str,
    word_count: int,
) -> dict[str, Any]:
    task = await response_json(
        client,
        "POST",
        "/tasks",
        headers=headers,
        json={
            "name": f"合成投标审阅任务 {org_key.upper()}",
            "tender_number": f"E2E-{org_key.upper()}-2026",
            "deadline": "2027-01-15T12:00:00+08:00",
            "budget_usd": 250.5,
        },
    )
    task_id = task["data"]["id"]
    word = word_fixture(word_count, org_key.upper())
    pdf = pdf_fixture(3, org_key.upper())
    uploaded: dict[str, dict[str, Any]] = {}
    for kind, content, name, media_type in (
        (
            "word",
            word,
            f"synthetic-{org_key}-requirements.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        ("pdf", pdf, f"synthetic-{org_key}-pages.pdf", "application/pdf"),
    ):
        result = await response_json(
            client,
            "POST",
            f"/tasks/{task_id}/documents",
            headers=headers,
            files={"file": (name, content, media_type)},
        )
        uploaded[kind] = result["data"]
        uploaded[kind]["parse_job"] = await run_job(
            client, app, headers, result["data"]["id"], "parse"
        )

    extraction_preview = await response_json(
        client,
        "POST",
        f"/documents/{uploaded['word']['id']}/extract",
        headers=headers,
        json={"dry_run": True},
    )
    low = await run_job(
        client,
        app,
        headers,
        uploaded["word"]["id"],
        "extract",
        {"reasoning": "low"},
    )
    high = await run_job(
        client,
        app,
        headers,
        uploaded["word"]["id"],
        "extract",
        {"reasoning": "high"},
    )
    pdf_extract = await run_job(
        client,
        app,
        headers,
        uploaded["pdf"]["id"],
        "extract",
        {"reasoning": "low"},
    )
    requirements = await response_json(
        client,
        "GET",
        f"/tasks/{task_id}/requirements",
        headers=headers,
        params={"job": low["id"]},
    )
    pdf_requirements = await response_json(
        client,
        "GET",
        f"/tasks/{task_id}/requirements",
        headers=headers,
        params={"job": pdf_extract["id"]},
    )
    if (
        len(requirements["items"]) != word_count
        or len({row["source"]["quote"] for row in requirements["items"]}) != word_count
        or len(pdf_requirements["items"]) != 3
        or len({row["source"]["quote"] for row in pdf_requirements["items"]}) != 3
    ):
        raise RuntimeError("The processor did not persist the expected exact-citation fixture")
    return {
        "task": task["data"],
        "documents": uploaded,
        "extraction_preview": extraction_preview["data"],
        "extractions": {"low": low, "high": high, "pdf": pdf_extract},
        "requirements": requirements["items"],
        "pdf_requirements": pdf_requirements["items"],
    }


async def create_card(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    task_id: str,
    extraction_job_id: str,
    requirement_id: str,
    content: dict[str, Any],
) -> dict[str, Any]:
    result = await response_json(
        client,
        "POST",
        f"/tasks/{task_id}/cards",
        headers=headers,
        json={
            "extraction_job_id": extraction_job_id,
            "requirement_id": requirement_id,
            "content": content,
        },
    )
    return result["data"]


async def card_action(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    card: dict[str, Any],
    action: str,
    **extra: Any,
) -> dict[str, Any]:
    result = await response_json(
        client,
        "POST",
        f"/cards/{card['id']}/actions",
        headers=headers,
        json={"expected_revision": card["revision"], "action": action, **extra},
    )
    return result["data"]


def commitment_content(index: int, deviation: str = "none") -> dict[str, Any]:
    return {
        "response_kind": "commitment",
        "response_text": f"我方承诺按合成条款 {index:04d} 完成响应。",
        "deviation": deviation,
        "deviation_note": (
            "该合成响应保留一项负偏离供界面醒目展示。"
            if deviation == "negative"
            else "该合成响应与招标要求一致。"
        ),
        "evidence": [],
    }


async def create_review_fixture(
    client: httpx.AsyncClient,
    app: Any,
    org: dict[str, Any],
    headers: dict[str, dict[str, str]],
    task_data: dict[str, Any],
) -> dict[str, Any]:
    task_id = task_data["task"]["id"]
    extraction = task_data["extractions"]["low"]["id"]
    requirements = task_data["requirements"]

    product_data = {
        "name": "合成存储设备声明",
        "vendor": "Synthetic Vendor",
        "model": "SYN-MODEL-64",
        "model_version": "2026.10",
        "official_url": "https://vendor.example.invalid/synthetic-model",
    }
    product = await response_json(
        client,
        "POST",
        "/resources/products",
        headers=headers["admin"],
        json={"data": product_data},
    )
    selection = await response_json(
        client,
        "POST",
        f"/tasks/{task_id}/products",
        headers=headers["admin"],
        json={"product_id": product["data"]["product_id"]},
    )
    evidence_input = {
        "kind": "product",
        "selection_id": selection["data"]["id"],
        "field_path": "model",
        "quote": product_data["model"],
    }
    second_evidence_input = {
        "kind": "product",
        "selection_id": selection["data"]["id"],
        "field_path": "model_version",
        "quote": product_data["model_version"],
    }

    cards: dict[str, dict[str, Any]] = {}
    cards["confirmed_evidence"] = await create_card(
        client,
        headers["admin"],
        task_id,
        extraction,
        requirements[0]["id"],
        {
            "response_kind": "evidence",
            "response_text": "合成型号声明可逐字支持本条响应。",
            "deviation": "none",
            "deviation_note": "型号声明与合成要求一致。",
            "evidence": [evidence_input],
        },
    )
    cards["confirmed_evidence"] = await card_action(
        client, headers["admin"], cards["confirmed_evidence"], "submit"
    )
    cards["confirmed_evidence"] = await card_action(
        client,
        headers["bidder"],
        cards["confirmed_evidence"],
        "confirm",
        reviewed_evidence_ids=[item["id"] for item in cards["confirmed_evidence"]["evidence"]],
    )

    cards["pending_evidence"] = await create_card(
        client,
        headers["admin"],
        task_id,
        extraction,
        requirements[1]["id"],
        {
            "response_kind": "evidence",
            "response_text": "待技术人员逐项核对的合成证据响应。",
            "deviation": "none",
            "deviation_note": "证据尚未经过技术角色确认。",
            "evidence": [evidence_input],
        },
    )
    cards["pending_evidence"] = await card_action(
        client, headers["admin"], cards["pending_evidence"], "submit"
    )

    cards["rejected"] = await create_card(
        client,
        headers["admin"],
        task_id,
        extraction,
        requirements[4]["id"],
        commitment_content(5),
    )
    cards["rejected"] = await card_action(client, headers["admin"], cards["rejected"], "submit")
    cards["rejected"] = await card_action(
        client,
        headers["bidder"],
        cards["rejected"],
        "reject",
        reason="合成审阅：商务响应文字需要修改。",
    )

    cards["needs_material"] = await create_card(
        client,
        headers["admin"],
        task_id,
        extraction,
        requirements[5]["id"],
        commitment_content(6),
    )
    cards["needs_material"] = await card_action(
        client, headers["admin"], cards["needs_material"], "submit"
    )
    cards["needs_material"] = await card_action(
        client,
        headers["technical"],
        cards["needs_material"],
        "needs_material",
        reason="合成审阅：需要补充真实材料。",
    )

    cards["pending_reject"] = await create_card(
        client,
        headers["admin"],
        task_id,
        extraction,
        requirements[12]["id"],
        commitment_content(13),
    )
    cards["pending_reject"] = await card_action(
        client, headers["admin"], cards["pending_reject"], "submit"
    )

    cards["pending_needs_material"] = await create_card(
        client,
        headers["admin"],
        task_id,
        extraction,
        requirements[17]["id"],
        commitment_content(18),
    )
    cards["pending_needs_material"] = await card_action(
        client, headers["admin"], cards["pending_needs_material"], "submit"
    )

    cards["confirmed_commitment"] = await create_card(
        client,
        headers["admin"],
        task_id,
        extraction,
        requirements[9]["id"],
        commitment_content(10, "negative"),
    )
    cards["confirmed_commitment"] = await card_action(
        client, headers["admin"], cards["confirmed_commitment"], "submit"
    )
    cards["confirmed_commitment"] = await card_action(
        client,
        headers["technical"],
        cards["confirmed_commitment"],
        "confirm",
        reviewed_evidence_ids=[],
    )

    cards["conflict_target"] = await create_card(
        client,
        headers["admin"],
        task_id,
        extraction,
        requirements[13]["id"],
        commitment_content(14),
    )
    cards["evidence_draft"] = await create_card(
        client,
        headers["admin"],
        task_id,
        extraction,
        requirements[8]["id"],
        {
            "response_kind": "evidence",
            "response_text": "保留为草稿的合成证据响应。",
            "deviation": "none",
            "deviation_note": "用于核验草稿状态和证据选择。",
            "evidence": [evidence_input],
        },
    )

    multi_evidence_cards = []
    for offset, requirement in enumerate(requirements[820:900]):
        card = await create_card(
            client,
            headers["admin"],
            task_id,
            extraction,
            requirement["id"],
            {
                "response_kind": "evidence",
                "response_text": f"大列表合成双材料响应 {offset + 1:03d}。",
                "deviation": "none",
                "deviation_note": "型号与型号版本两个真实声明字段均逐字关联。",
                "evidence": [evidence_input, second_evidence_input],
            },
        )
        if offset % 3 == 0:
            card = await card_action(client, headers["admin"], card, "submit")
        multi_evidence_cards.append(
            {
                "id": card["id"],
                "requirement_id": card["requirement_id"],
                "state": card["state"],
                "evidence_count": len(card["evidence"]),
            }
        )
    if len(multi_evidence_cards) != 80 or any(
        card["evidence_count"] != 2 for card in multi_evidence_cards
    ):
        raise RuntimeError("The large-list fixture needs 80 cards with two resolved Evidence rows")

    bidder_items = [
        {
            "requirement_id": row["id"],
            "expected_revision": None,
            "disposition": "comply_only",
            "reason": "合成批次：该商务条款只需遵守。",
        }
        for index, row in enumerate(requirements[20:820], start=20)
        if index % 4 == 0
    ][:200]
    technical_items = [
        {
            "requirement_id": row["id"],
            "expected_revision": None,
            "disposition": "comply_only",
            "reason": "合成批次：该技术条款只需遵守。",
        }
        for index, row in enumerate(requirements[20:820], start=20)
        if index % 4 == 1
    ][:200]
    for role, items in (("bidder", bidder_items), ("technical", technical_items)):
        await response_json(
            client,
            "POST",
            f"/tasks/{task_id}/cards/dispositions",
            headers=headers[role],
            json={"extraction_job_id": extraction, "items": items},
        )

    draft_one_receipt = await response_json(
        client,
        "POST",
        f"/tasks/{task_id}/drafts",
        headers=headers["admin"],
        json={"extraction_job_id": extraction},
    )
    draft_one_job = draft_one_receipt["data"]["job_id"]
    await app.state.processor(org["id"], draft_one_job)
    draft_one_status = await response_json(
        client, "GET", f"/jobs/{draft_one_job}", headers=headers["admin"]
    )
    draft_one_id = draft_one_status["data"]["result"]["draft_id"]

    cards["confirmed_evidence"] = await card_action(
        client,
        headers["bidder"],
        cards["confirmed_evidence"],
        "reopen",
        reason="合成审阅：使旧初稿进入 stale 状态。",
    )
    stale_draft = await response_json(
        client, "GET", f"/drafts/{draft_one_id}", headers=headers["admin"]
    )
    if stale_draft["data"]["validity"] != "stale":
        raise RuntimeError("Reopening a reviewed response did not stale the existing draft")

    draft_two_receipt = await response_json(
        client,
        "POST",
        f"/tasks/{task_id}/drafts",
        headers=headers["admin"],
        json={"extraction_job_id": extraction},
    )
    draft_two_job = draft_two_receipt["data"]["job_id"]
    await app.state.processor(org["id"], draft_two_job)
    draft_two_status = await response_json(
        client, "GET", f"/jobs/{draft_two_job}", headers=headers["admin"]
    )
    current_draft_id = draft_two_status["data"]["result"]["draft_id"]
    current_draft = await response_json(
        client, "GET", f"/drafts/{current_draft_id}", headers=headers["admin"]
    )

    generation_preview = await response_json(
        client,
        "POST",
        f"/tasks/{task_id}/cards/generations",
        headers=headers["admin"],
        json={
            "extraction_job_id": extraction,
            "requirement_ids": [requirements[18]["id"]],
            "reasoning": "low",
            "dry_run": True,
        },
    )
    if generation_preview["data"]["estimated_cost"]["usd"] is None:
        raise RuntimeError("The generation fixture needs a visible cost estimate")

    batch_candidates = [
        row["id"] for index, row in enumerate(requirements[900:1000], start=900) if index % 4 == 1
    ][:3]
    return {
        "product_selection": selection["data"],
        "cards": cards,
        "multi_evidence_cards": multi_evidence_cards,
        "batch_candidates": batch_candidates,
        "disposition_counts": {
            "commercial": len(bidder_items),
            "technical": len(technical_items),
        },
        "drafts": {
            "stale": stale_draft["data"],
            "current": current_draft["data"],
        },
        "generation_preview": generation_preview["data"],
    }


def public_task_data(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": value["task"],
        "documents": value["documents"],
        "extraction_preview": value["extraction_preview"],
        "extractions": value["extractions"],
        "requirement_count": len(value["requirements"]),
        "requirement_ids": [row["id"] for row in value["requirements"]],
        "pdf_requirement_ids": [row["id"] for row in value["pdf_requirements"]],
    }


async def provision(output: Path) -> Path:
    admin_url = guarded_url("BID_TEST_ADMIN_URL")
    runtime_url = guarded_url("BID_DATABASE_URL")
    if make_url(admin_url).database != make_url(runtime_url).database:
        raise SystemExit(
            "BID_TEST_ADMIN_URL and BID_DATABASE_URL must select the same test database"
        )
    password = env("E2E_ORG_PASSWORD")
    data_dir = await asyncio.to_thread(configured_data_directory)
    browser_materials = await asyncio.to_thread(write_browser_materials, output)

    identities = seed_identities(admin_url, password)
    settings = fixture_settings(data_dir)
    vendor = SyntheticVendor()
    app = fixture_app(settings, vendor)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://org-console.fixture"
        ) as client,
    ):
        headers: dict[str, dict[str, dict[str, str]]] = {}
        for org_key, org in identities.items():
            headers[org_key] = {
                role: await login(client, org, role, password) for role in ROLE_NAMES
            }
        task_a = await create_task_documents(
            client, app, headers["a"]["admin"], "a", WORD_REQUIREMENTS
        )
        task_b = await create_task_documents(client, app, headers["b"]["admin"], "b", 12)
        review = await create_review_fixture(client, app, identities["a"], headers["a"], task_a)

        # Prove the three G1 discovery routes and their tenant boundary in the fixture itself.
        task_b_id = task_b["task"]["id"]
        for path in (
            f"/tasks/{task_b_id}",
            f"/tasks/{task_b_id}/documents",
            f"/tasks/{task_b_id}/jobs?kind=parse",
        ):
            await response_json(client, "GET", path, headers=headers["a"]["viewer"], expected=404)

    manifest = {
        "schema": "org-console-e2e-v1",
        "fixture": "synthetic-labelled-materials-only",
        "provider": {
            "transport": "httpx.MockTransport",
            "model": SYNTHETIC_MODEL,
            "calls": vendor.calls,
        },
        "orgs": identities,
        "tasks": {"a": public_task_data(task_a), "b": public_task_data(task_b)},
        "review": review,
        "browser_materials": browser_materials,
        "expectations": {
            "large_requirement_count": WORD_REQUIREMENTS,
            "page_size": 50,
            "first_load_ms_max": 5_000,
            "interaction_p95_ms_max": 300,
            "g1_job_kind": "parse",
        },
        "secrets": "Passwords and sessions are supplied only through environment variables.",
    }
    manifest_path = output / "fixture-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, help="New or existing directory under data/work")
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Serve the built console and synthetic API after provisioning",
    )
    parser.add_argument("--host", choices=("127.0.0.1",), default="127.0.0.1")
    parser.add_argument("--port", type=tcp_port, default=8000)
    args = parser.parse_args()
    output = output_directory(args.output)
    manifest = asyncio.run(provision(output))
    print(manifest)
    if args.serve:
        import uvicorn

        web_dir = REPO_ROOT / "web" / "dist"
        if not (web_dir / "index.html").is_file():
            raise SystemExit("Run npm run build in web/ before --serve")
        settings = fixture_settings(configured_data_directory(), web_dir=web_dir)
        app = fixture_app(settings, SyntheticVendor(), run_jobs=True)
        uvicorn.run(app, host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
