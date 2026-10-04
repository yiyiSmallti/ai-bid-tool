"""Simulated product proposals through the real API, worker, search, fetch broker and exports.

Failure modes enumerated before implementation:
- Only a human with resource:write and task:resource may simulate; tokens, bidders and
  viewers are refused; another org's task is a 404.
- The dry run sends nothing to the model, search or fetch, and a changed preview is refused.
- A page that never names the proposed model, a denied fetch or an empty search yields no
  product; software and service items never get one.
- A model quote that is not verbatim in the fetched page is dropped before storage.
- Kept products and statements are selected on the task and registered as simulated; a
  later human revision keeps the mark.
- A confirmed card citing a simulated statement blocks a final section and needs an
  acknowledgment in a review copy.
- The generic job reader does not expose the submission.
"""

import io
import json
from decimal import Decimal
from uuid import UUID

import httpx
from app.api.main import create_app
from app.models.entities import BalanceEntry, OrgBalance, SimulatedResource
from app.providers.llm import OpenAICompatibleExtractor
from conftest import FakeQueue
from docx import Document
from sqlalchemy import select
from test_exports import draft, setup_template
from test_llm_providers import settings_for
from test_response_cards import login, run_document_job, set_role

PAGE = (
    "<html><head><script>ignore()</script></head><body><h1>SP-100 智能打印终端规格</h1>"
    "<p>内存：8GB DDR4</p><p>打印：支持A4双面自动打印</p>"
    + "<p>"
    + "产品适用于校园自助打印与文印服务场景，" * 12
    + "</p></body></html>"
)
OTHER_PAGE = "<html><body><p>" + "交换机系列产品介绍，" * 40 + "</p></body></html>"


def tender_docx() -> bytes:
    document = Document()
    document.add_paragraph("第三章 采购需求")
    table = document.add_table(rows=4, cols=3)
    for row, cells in enumerate(
        [
            ("序号", "名称", "技术参数"),
            ("1", "智能打印终端", "内存不少于8GB，支持A4双面打印"),
            ("2", "学生管理系统定制开发", "支持学生信息管理与统计"),
            ("3", "接入交换机", "端口不少于24个千兆电口"),
        ]
    ):
        for column, text in enumerate(cells):
            table.cell(row, column).text = text
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


class Vendor:
    """Synthetic model replies: extraction by block, proposals by item name, quotes."""

    def __init__(self):
        self.kinds: list[str] = []

    @staticmethod
    def reply(payload: dict) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "synthetic-model",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(payload, ensure_ascii=False)},
                    }
                ],
                "usage": {"prompt_tokens": 500, "completion_tokens": 50},
            },
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        system, user = body["messages"][0]["content"], body["messages"][-1]["content"]
        if "拟投产品的模拟助手" in system:
            self.kinds.append("propose")
            items = []
            for item in json.loads(user)["items"]:
                name = item["name"]
                if "打印" in name:
                    items.append(
                        {
                            "item_key": item["item_key"],
                            "kind": "hardware",
                            "vendor": "SynVendor",
                            "model": "SP-100",
                            "queries": ["SynVendor SP-100 规格"],
                        }
                    )
                elif "交换机" in name:
                    items.append(
                        {
                            "item_key": item["item_key"],
                            "kind": "hardware",
                            "vendor": "SynNet",
                            "model": "SW-24",
                            "queries": ["SynNet SW-24 规格"],
                        }
                    )
                else:
                    items.append(
                        {
                            "item_key": item["item_key"],
                            "kind": "software",
                            "vendor": "",
                            "model": "",
                            "queries": [],
                        }
                    )
            return self.reply({"items": items})
        if "参数摘录助手" in system:
            self.kinds.append("quote")
            return self.reply(
                {
                    "parameters": [
                        {"label": "内存", "quote": "内存：8GB DDR4"},
                        {"label": "臆造", "quote": "内存：16GB"},
                    ]
                }
            )
        self.kinds.append("extract")
        items = []
        for ref, text in __import__("re").findall(
            r'<block id="([^"]+)">\n(.*?)\n</block>', user, 2
        ):
            if "不少于" in text or "支持" in text:
                items.append(
                    {
                        "category": "technical",
                        "starred": False,
                        "text": text,
                        "ref": ref,
                        "quote": text,
                        "condition": None,
                    }
                )
        return self.reply({"items": items})


def search_handler(request: httpx.Request) -> httpx.Response:
    query = request.url.params["q"]
    url = "https://vendor.example/sp100" if "SP-100" in query else "https://vendor.example/sw24"
    return httpx.Response(
        200,
        json={
            "results": [{"url": url, "title": query, "engines": ["synthetic"]}],
            "unresponsive_engines": [],
        },
    )


def fetch_handler(request: httpx.Request) -> httpx.Response:
    page = PAGE if request.url.path == "/sp100" else OTHER_PAGE
    return httpx.Response(
        200, content=page.encode(), headers={"content-type": "text/html; charset=utf-8"}
    )


async def public_dns(host):
    return ("93.184.216.34",)


async def test_simulation_records_only_verbatim_marked_parameters(
    tenants, tmp_path, admin_engine, monkeypatch
):
    policy = tmp_path / "open-policy.json"
    policy.write_text(
        json.dumps(
            {
                "policies": [{"revision": "dev-open-v1", "open_public_https": True}],
                "revoked_revisions": [],
            }
        )
    )
    monkeypatch.setenv("BID_SANDBOX_POLICY_FILE", str(policy))
    monkeypatch.setenv("BID_SANDBOX_DEV_OPEN_EGRESS", "1")
    monkeypatch.setenv("BID_SANDBOX_FETCH_QUOTA", str(tmp_path / "quota.sqlite3"))
    vendor = Vendor()
    settings = settings_for(tmp_path, "openai", search_url="https://search.test")
    llm = OpenAICompatibleExtractor(
        settings,
        httpx.MockTransport(vendor),
        platform_model_id="sim-test",
        sale_usd_per_mtok=(1, 1),
    )
    llm.model_revision = 1
    app = create_app(settings, llm=llm, queue=FakeQueue())
    app.state.processor.search_transport = httpx.MockTransport(search_handler)
    app.state.processor.sandbox_fetch_transport = httpx.MockTransport(fetch_handler)
    app.state.processor.sandbox_resolver = public_dns
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api,
    ):
        for org in tenants["orgs"]:
            async with app.state.db.transaction(org) as session:
                session.add(OrgBalance(org_id=org, currency="USD", balance=Decimal("10")))
                session.add(
                    BalanceEntry(
                        org_id=org,
                        kind="adjust",
                        currency="USD",
                        amount=Decimal("10"),
                        balance_after=Decimal("10"),
                        actor="test",
                        reason="Synthetic initial funds",
                    )
                )
        header, foreign = [
            await login(api, org, label)
            for org, label in zip(tenants["orgs"], ("a", "b"), strict=True)
        ]
        task = (
            await api.post("/tasks", headers=header, json={"name": "Synthetic simulation"})
        ).json()["data"]["id"]
        uploaded = await api.post(
            f"/tasks/{task}/documents",
            headers=header,
            files={
                "file": (
                    "tender.docx",
                    tender_docx(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        document = uploaded.json()["data"]["id"]
        await run_document_job(api, app, header, document, "parse")
        extraction = await run_document_job(api, app, header, document, "extract")
        route = f"/tasks/{task}/product-simulations"

        before = len(vendor.kinds)
        preview = await api.post(
            route, headers=header, json={"extraction_job_id": extraction, "dry_run": True}
        )
        assert preview.status_code == 200, preview.text
        data = preview.json()["data"]
        assert [item["name"] for item in data["items"]] == [
            "智能打印终端",
            "学生管理系统定制开发",
            "接入交换机",
        ]
        assert data["admission_blocker"] is None and len(vendor.kinds) == before
        stale = await api.post(
            route,
            headers=header,
            json={"extraction_job_id": extraction, "expected_input_hash": "0" * 64},
        )
        assert stale.status_code == 409
        assert (
            await api.post(
                route, headers=foreign, json={"extraction_job_id": extraction, "dry_run": True}
            )
        ).status_code == 404
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "bidder")
        denied = await api.post(
            route, headers=header, json={"extraction_job_id": extraction, "dry_run": True}
        )
        assert denied.status_code == 403
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "admin")

        submitted = await api.post(
            route,
            headers=header,
            json={"extraction_job_id": extraction, "expected_input_hash": data["input_hash"]},
        )
        assert submitted.status_code == 200, submitted.text
        job = submitted.json()["data"]["job_id"]
        await app.state.processor(header["X-Org-Id"], job)
        status = (await api.get(f"/jobs/{job}", headers=header)).json()["data"]
        assert status["status"] == "succeeded", status
        result = status["result"]
        assert "submission" not in result and result["simulated"] is True
        assert result["counts"] == {"items": 3, "hardware": 2, "products": 1, "parameters": 1}, (
            json.dumps(result["items"], ensure_ascii=False)
        )
        outcome = {item["name"]: item for item in result["items"]}
        assert outcome["学生管理系统定制开发"]["status"] == "not_hardware"
        assert (
            outcome["接入交换机"]["status"] == "no_parameters"
            or outcome["接入交换机"]["status"] == "no_page"
        )
        printer = outcome["智能打印终端"]
        assert printer["status"] == "quoted" and printer["parameters"] == [
            {"label": "内存", "quote": "内存：8GB DDR4"}
        ]

        products = (await api.get(f"/tasks/{task}/products", headers=header)).json()["items"]
        features = (await api.get(f"/tasks/{task}/features", headers=header)).json()["items"]
        assert [row["data"]["name"] for row in products] == ["【模拟】智能打印终端"]
        assert [row["data"]["description"] for row in features] == ["内存：8GB DDR4"]
        marks = (await api.get(f"/tasks/{task}/simulated-resources", headers=header)).json()["data"]
        assert sorted(marks["selection_ids"]) == sorted([products[0]["id"], features[0]["id"]])
        assert (
            await api.get(f"/tasks/{task}/simulated-resources", headers=foreign)
        ).status_code == 404

        # A human revision of the simulated product cannot clear the mark.
        product_id = printer["product_id"]
        revised = await api.post(
            f"/resources/products/{product_id}/revisions",
            headers=header,
            json={
                "expected_revision": 1,
                "data": {"name": "改名后的打印终端", "vendor": "SynVendor", "model": "SP-100"},
            },
        )
        assert revised.status_code == 200, revised.text
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            marked = (await session.scalars(select(SimulatedResource))).all()
            assert {str(row.product_id) for row in marked if row.product_id} == {product_id}

        # A confirmed card citing the simulated statement cannot go into a final section.
        requirements = (
            await api.get(f"/tasks/{task}/requirements", headers=header, params={"job": extraction})
        ).json()["items"]
        memory = next(row for row in requirements if "内存" in row["text"])
        card = await api.post(
            f"/tasks/{task}/cards",
            headers=header,
            json={
                "extraction_job_id": extraction,
                "requirement_id": memory["id"],
                "content": {
                    "response_kind": "evidence",
                    "response_text": "模拟拟投产品内存满足要求。",
                    "deviation": "none",
                    "deviation_note": "模拟参数与要求对应。",
                    "evidence": [
                        {
                            "kind": "feature",
                            "selection_id": features[0]["id"],
                            "field_path": "description",
                            "quote": "内存：8GB DDR4",
                        }
                    ],
                },
            },
        )
        assert card.status_code == 200, card.text
        card = card.json()["data"]
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "technical")
        for action, extra in (("submit", {}), ("confirm", None)):
            body = {"expected_revision": card["revision"], "action": action}
            if extra is None:
                body |= {
                    "reviewed_evidence_ids": [item["id"] for item in card["evidence"]],
                    "reviewed_warning_codes": card["warning_codes"],
                    "reason": "合成审阅。" if card["warning_codes"] else None,
                }
            response = await api.post(f"/cards/{card['id']}/actions", headers=header, json=body)
            assert response.status_code == 200, response.text
            card = response.json()["data"]
        assert card["state"] == "confirmed"
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "admin")
        selected, binding = await setup_template(api, header, task)
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "bidder")
        draft_id = await draft(api, app, header, task, extraction)
        for mode, severity in (("final_section", "block"), ("review_copy", "acknowledge")):
            preview = await api.post(
                f"/tasks/{task}/export-runs",
                headers=header,
                json={
                    "draft_id": draft_id,
                    "task_template_id": selected["id"],
                    "binding_id": binding["id"],
                    "mode": mode,
                    "dry_run": True,
                },
            )
            issues = [
                item
                for item in preview.json()["data"]["issues"]
                if item["code"] == "export_simulated_material"
            ]
            assert [item["severity"] for item in issues] == [severity], preview.text
            assert issues[0]["requirement_ids"] == [memory["id"]]
        assert UUID(job)
