"""Confidential fields end to end: registered values never reach the model, cards keep
`{{secret.key}}`, and the export fills the values the run fixed.

Failure modes identified before implementation:
* Leak: a registered value appears in a vendor request, a preview/manifest, a job
  result, an audit row, a list/history view, an export run manifest or a CLI result.
* Over-reach: a token or agent sets or reveals a value, directly or through a token
  scope; technical/viewer roles reveal a value; org B reads or writes org A's fields.
* Wrong fill: the export fills a stale value after it changed, fills nothing silently
  when a value is missing, or leaves a raw `{{secret.key}}` in the delivered file.
* Bad references: a card names a field that does not exist or is archived, a model
  writes a placeholder it was not given, or `[REDACTED_…]` text is confirmed.
* Switch: with masking turned off the values are sent as typed (an approved decision).
"""

import asyncio
import hashlib
import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from app.models.confidential import ConfidentialField, ConfidentialValue
from app.models.entities import ApiToken, AuditLog
from app.models.exports import ExportRun
from app.models.response_cards import CardGenerationRun
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from test_api import create_document, run_job
from test_card_generation import (
    SENSITIVE,
    SENSITIVE_LINES,
    drafting_client,
    execute,
    slots,
    submit,
    token_header,
)
from test_exports import complete_inputs, prepared
from test_llm_providers import provider_reply
from test_response_cards import (
    card_action,
    create_card,
    create_tender,
    labelled_pdf,
    phase_one_client,
    require_action,
    set_role,
)

CONTACT = "Synthetic Contact Zhang"
BID_TOTAL, NEW_TOTAL = "98765.43", "87654.32"


async def add_field(api, header, key, label, kind, scope):
    response = await api.post(
        "/confidential-fields",
        headers=header,
        json={"key": key, "label": label, "kind": kind, "scope": scope},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def set_value(api, header, field, value, task=None):
    response = await api.post(
        f"/confidential-fields/{field['id']}/values",
        headers=header,
        json={"value": value, "task_id": task},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert value not in response.text
    return data


async def docx_text(api, header, export_id):
    link = await api.get(f"/exports/{export_id}/download-link", headers=header)
    assert link.status_code == 200, link.text
    content = (await api.get(link.json()["data"]["url"], headers=header)).content
    with ZipFile(BytesIO(content)) as package:
        return content, package.read("word/document.xml").decode()


async def release(api, header, run):
    released = await api.post(
        f"/export-runs/{run['id']}/release",
        headers=header,
        json={
            "expected_input_hash": run["input_hash"],
            "expected_candidate_sha256": run["candidate_sha256"],
        },
    )
    return released


async def test_export_fills_fixed_values_and_blocks_missing_or_changed(
    tenants, tmp_path, admin_engine
):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        header = headers[0]
        contact = await add_field(api, header, "contact_name", "联系人", "contact", "org")
        total = await add_field(api, header, "bid_total", "投标总价", "amount", "task")
        await set_value(api, header, contact, CONTACT)
        text_with_fields = "联系人：{{secret.contact_name}}，投标总价 {{secret.bid_total}} 元。"
        task, body, _, _ = await complete_inputs(
            api, app, header, tenants, admin_engine, tmp_path, texts={0: text_with_fields}
        )
        # The task value is still missing: a final section is blocked, a review copy is not.
        preview = await api.post(
            f"/tasks/{task}/export-runs", headers=header, json={**body, "dry_run": True}
        )
        fixed = preview.json()["data"]
        assert not fixed["ready"]
        assert {i["code"] for i in fixed["issues"] if i["severity"] == "block"} == {
            "confidential_value_missing"
        }
        assert {(f["key"], f["status"]) for f in fixed["confidential"]} == {
            ("bid_total", "missing"),
            ("contact_name", "filled"),
        }
        review = await prepared(api, app, header, task, {**body, "mode": "review_copy"})
        released = await release(api, header, review)
        assert released.status_code == 200, released.text
        _, review_xml = await docx_text(api, header, released.json()["data"]["id"])
        assert CONTACT in review_xml and "【投标总价】" in review_xml
        assert "{{secret." not in review_xml

        await set_value(api, header, total, BID_TOTAL, task)
        run = await prepared(api, app, header, task, body)
        # A value changed after submission is a changed input; the old candidate cannot ship.
        await set_value(api, header, total, NEW_TOTAL, task)
        stale = await release(api, header, run)
        assert stale.status_code == 409, stale.text
        assert stale.json()["data"]["error"]["code"] == "export_input_changed"
        run = await prepared(api, app, header, task, body)
        released = await release(api, header, run)
        assert released.status_code == 200, released.text
        content, final_xml = await docx_text(api, header, released.json()["data"]["id"])
        assert CONTACT in final_xml and NEW_TOTAL in final_xml
        assert BID_TOTAL not in final_xml and "{{secret." not in final_xml

        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            stored = json.dumps(
                [row.manifest for row in (await session.scalars(select(ExportRun))).all()]
                + [row.details for row in (await session.scalars(select(AuditLog))).all()],
                ensure_ascii=False,
            )
        for value in (CONTACT, BID_TOTAL, NEW_TOTAL):
            assert value not in stored

        artifact = Path("data/work/confidential-acceptance")
        await asyncio.to_thread(artifact.mkdir, parents=True, exist_ok=True)
        (artifact / "synthetic-final.docx").write_bytes(content)
        (artifact / "receipt.json").write_text(
            json.dumps(
                {
                    "file_sha256": hashlib.sha256(content).hexdigest(),
                    "input_hash": run["input_hash"],
                    "filled_fields": ["bid_total", "contact_name"],
                    "synthetic": True,
                },
                indent=2,
            )
        )


async def test_declaration_naming_a_field_is_filled_in_the_export_excerpt(
    tenants, tmp_path, admin_engine
):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        header = headers[0]
        bank = await add_field(api, header, "bank_account", "银行账号", "bank_account", "org")
        await set_value(api, header, bank, "6222 0212 3456 7890 123")
        refused = await api.post(
            "/resources/profiles",
            headers=header,
            json={"data": {"name": "Synthetic", "standard_wording": "{{secret.unknown_key}}"}},
        )
        assert refused.json()["data"]["error"]["code"] == "unknown_confidential_field"
        wording = "开户账号：{{secret.bank_account}}"
        task, body, _, _ = await complete_inputs(
            api, app, header, tenants, admin_engine, tmp_path, profile_wording=wording
        )
        preview = await api.post(
            f"/tasks/{task}/export-runs", headers=header, json={**body, "dry_run": True}
        )
        assert [(f["key"], f["status"]) for f in preview.json()["data"]["confidential"]] == [
            ("bank_account", "filled")
        ]
        run = await prepared(api, app, header, task, body)
        released = await release(api, header, run)
        assert released.status_code == 200, released.text
        _, xml = await docx_text(api, header, released.json()["data"]["id"])
        assert "开户账号：6222 0212 3456 7890 123" in xml and "{{secret." not in xml


@pytest.mark.parametrize("enabled", [True, False])
async def test_drafting_sends_placeholders_never_registered_values(
    tenants, tmp_path, admin_engine, enabled
):
    registered = {
        "contact_person": ("contact", SENSITIVE["contact"]),
        "contact_phone": ("contact", SENSITIVE["phone"]),
        "legal_identity": ("identity", SENSITIVE["identity"]),
        "bank_account": ("bank_account", SENSITIVE["bank_account"]),
    }
    async with drafting_client(tenants, tmp_path, lines=SENSITIVE_LINES) as (
        api,
        app,
        headers,
        vendor,
        _,
    ):
        header = headers[0]
        task, document = await create_document(api, header, labelled_pdf(SENSITIVE_LINES))
        await run_job(api, app, header, document, "parse")
        extraction, status = await run_job(api, app, header, document, "extract")
        assert status["status"] == "succeeded", status
        for key, (kind, value) in registered.items():
            field = await add_field(api, header, key, key.replace("_", " "), kind, "org")
            await set_value(api, header, field, value)
        total = await add_field(api, header, "bid_total", "投标总价", "amount", "task")
        await set_value(api, header, total, SENSITIVE["amount"], task)
        if not enabled:
            changed = await api.put(
                f"/tasks/{task}/model-redaction",
                headers=header,
                json={"expected_revision": 1, "model_redaction_enabled": False},
            )
            assert changed.status_code == 200, changed.text

        async def respond(sent):
            items = vendor.proposals(sent)
            for item, requirement in zip(items, sent["requirements"], strict=True):
                page = requirement["location"]["page"]
                if page == 3:
                    item["response_text"] = (
                        "联系人 {{secret.contact_person}}，总价 {{secret.bid_total}}"
                    )
                elif page == 4:
                    item["response_text"] = "Unlisted {{secret.not_registered}}"
            return provider_reply(vendor.provider, items)

        vendor.respond = respond
        preview = await submit(api, header, task, extraction, dry_run=True)
        assert (preview["data"]["redacted_counts"]["confidential"] > 0) is enabled
        receipt = await submit(api, header, task, extraction)
        terminal = await execute(api, app, header, receipt)
        assert terminal["data"]["status"] == "succeeded", terminal
        wire = json.dumps(vendor.drafts, ensure_ascii=False)
        assert all(
            sent["confidential_fields"][0]["placeholder"].startswith("{{secret.")
            for sent in vendor.drafts
        )
        for _, value in registered.values():
            assert (value in wire) is (not enabled)
        if enabled:
            assert "{{secret.contact_person}}" in wire and "{{secret.bank_account}}" in wire
        for value in [v for _, v in registered.values()] + [SENSITIVE["amount"]]:
            assert value not in json.dumps(preview, ensure_ascii=False)
            assert value not in json.dumps(terminal, ensure_ascii=False)
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            run = await session.scalar(select(CardGenerationRun))
            assert run is not None
            assert all(v not in run.encrypted_input for _, v in registered.values())
        cards = {
            item["requirement_id"]: item for item in await slots(api, header, task, extraction)
        }
        texts = [
            item["card"]["content"]["response_text"] for item in cards.values() if item["card"]
        ]
        assert "联系人 {{secret.contact_person}}，总价 {{secret.bid_total}}" in texts
        # A placeholder the model was not given never becomes card text.
        assert not any("not_registered" in value for value in texts)
        assert any(
            reason == "unknown_confidential_field"
            for reason in terminal["data"]["result"]["skipped"].values()
        )


async def test_permissions_isolation_and_card_references(tenants, tmp_path, admin_engine):
    org_a, org_b = tenants["orgs"]
    user_a = tenants["users"][0]
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        header, other = headers
        field = await add_field(api, header, "bank_account", "银行账号", "bank_account", "org")
        value = await set_value(api, header, field, "6222 0212 3456 7890 123")
        assert value["tail"] == "0123" and value["status"] == "filled"
        task, _, extraction, requirements = await create_tender(api, app, header, tmp_path)

        # Scope rules, duplicate keys and archive state.
        duplicate = await api.post(
            "/confidential-fields",
            headers=header,
            json={"key": "bank_account", "label": "x", "kind": "other", "scope": "org"},
        )
        assert duplicate.json()["data"]["error"]["code"] == "confidential_key_exists"
        misplaced = await api.post(
            f"/confidential-fields/{field['id']}/values",
            headers=header,
            json={"value": "1", "task_id": task},
        )
        assert misplaced.json()["data"]["error"]["code"] == "confidential_task_not_allowed"
        per_task = await add_field(api, header, "bid_total", "投标总价", "amount", "task")
        missing_task = await api.post(
            f"/confidential-fields/{per_task['id']}/values", headers=header, json={"value": "1"}
        )
        assert missing_task.json()["data"]["error"]["code"] == "confidential_task_required"
        listed = await api.get("/confidential-values", headers=header, params={"task_id": task})
        assert {(i["key"], i["status"]) for i in listed.json()["items"]} == {
            ("bank_account", "filled"),
            ("bid_total", "missing"),
        }
        assert "6222" not in listed.text

        # Org B sees nothing of org A and cannot address A's rows.
        assert (await api.get("/confidential-fields", headers=other)).json()["items"] == []
        for method, path, payload in (
            ("post", f"/confidential-fields/{field['id']}/revisions", {"expected_revision": 1, "archived": True}),
            ("post", f"/confidential-fields/{field['id']}/values", {"value": "9"}),
            ("get", f"/confidential-fields/{field['id']}/values", None),
            ("post", f"/confidential-values/{value['value_id']}/reveal", None),
        ):  # fmt: skip
            response = await getattr(api, method)(
                path, headers=other, **({"json": payload} if payload else {})
            )
            assert response.status_code == 404, (path, response.text)

        # Tokens can list and name fields but never set or reveal, nor be granted to.
        agent = await token_header(api, header, scopes=["confidential:read", "task:read"])
        assert (await api.get("/confidential-fields", headers=agent)).status_code == 200
        for path, payload in (
            ("/confidential-fields", {"key": "x_y", "label": "x", "kind": "other", "scope": "org"}),
            (f"/confidential-fields/{field['id']}/values", {"value": "9"}),
            (f"/confidential-values/{value['value_id']}/reveal", {}),
        ):  # fmt: skip
            assert (await api.post(path, headers=agent, json=payload)).status_code == 403
        granted = await api.post(
            "/tokens",
            headers=header,
            json={
                "name": "Synthetic over-reach",
                "scopes": ["confidential:reveal"],
                "expires_at": "2099-01-01T00:00:00+00:00",
            },
        )
        assert granted.json()["data"]["error"]["code"] == "forbidden_scopes"
        with pytest.raises(IntegrityError), Session(admin_engine) as session, session.begin():
            token = session.scalar(select(ApiToken).where(ApiToken.org_id == org_a))
            assert token is not None
            token.scopes = [*token.scopes, "confidential:write"]

        # Only admin and bidder may reveal; every reveal is audited without the value.
        set_role(admin_engine, org_a, user_a, "technical")
        denied = await api.post(f"/confidential-values/{value['value_id']}/reveal", headers=header)
        assert denied.status_code == 403
        set_role(admin_engine, org_a, user_a, "bidder")
        shown = await api.post(f"/confidential-values/{value['value_id']}/reveal", headers=header)
        assert shown.json()["data"]["value"] == "6222 0212 3456 7890 123"
        async with app.state.db.transaction(org_a) as session:
            audits = (
                await session.scalars(
                    select(AuditLog).where(AuditLog.action.like("confidential.%"))
                )
            ).all()
            assert "confidential.value.reveal" in {row.action for row in audits}
            assert "6222" not in json.dumps([row.details for row in audits])

        # Card references must name an active field; masked text cannot be confirmed.
        content = {
            "response_kind": "commitment",
            "response_text": "账号 {{secret.missing_field}}",
            "deviation": "none",
            "deviation_note": "Synthetic correspondence note.",
        }
        unknown = await api.post(
            f"/tasks/{task}/cards",
            headers=header,
            json={
                "extraction_job_id": extraction,
                "requirement_id": requirements[0]["id"],
                "content": content,
            },
        )
        assert unknown.status_code == 422
        assert unknown.json()["data"]["error"]["code"] == "unknown_confidential_field"
        card = await create_card(
            api,
            header,
            task,
            extraction,
            # A technical requirement, so the technical reviewer below owns the decision.
            requirements[2],
            {**content, "response_text": "账号 {{secret.bank_account}}，报价 [REDACTED_AMOUNT]"},
        )
        submitted = await require_action(api, header, card, "submit")
        set_role(admin_engine, org_a, user_a, "technical")
        refused = await card_action(
            api,
            header,
            submitted,
            "confirm",
            reviewed_evidence_ids=[],
            reviewed_warning_codes=submitted["warning_codes"],
            reason="Synthetic review.",
        )
        assert refused.json()["data"]["error"]["code"] == "redacted_placeholder_in_response"

        # Tables fail closed without an org and reject rows for another org.
        async with app.state.db.transaction() as session:
            for model in (ConfidentialField, ConfidentialValue):
                assert (await session.scalars(select(model))).all() == []
        with pytest.raises(DBAPIError) as error:
            async with app.state.db.transaction(org_a) as session:
                session.add(
                    ConfidentialField(
                        org_id=org_b,
                        created_by=tenants["users"][1],
                        key="foreign",
                        label="x",
                        kind="other",
                        scope="org",
                    )
                )
        assert error.value.orig.sqlstate == "42501"  # pyright: ignore[reportAttributeAccessIssue]
        with admin_engine.connect() as connection:
            for table in ("confidential_fields", "confidential_values"):
                flags = connection.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE relname=:table"
                    ),
                    {"table": table},
                ).one()
                assert flags.relrowsecurity and flags.relforcerowsecurity
