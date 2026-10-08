"""Synthetic HTTP → immutable preparation → isolated local GM signature evidence.

Failure modes were recorded before implementation in data/work/bid-review-sig/
failure-modes.md. This suite uses the main session's PostgreSQL runtime and never
starts services. End-to-end receipts are written beneath pytest's --basetemp.
"""

import json
import re
from uuid import UUID, uuid4

import pymupdf
import pytest
from app.api.main import create_app
from app.core.config import Settings
from app.core.security import Secrets
from app.models.bid_review import BidDocumentPage
from app.models.bid_signature import BidPDFValidation, BidPreparationTrust, BidSigningCandidate
from app.models.entities import Membership, UsageRecord
from bid_signature_fixtures import (
    gm_fixture,
    modified_pdf,
    signed_pdf,
    standard_fixture,
    unsigned_pdf,
)
from conftest import FakeQueue
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_bid_review_upload_db import PDF, error, preview, result, run, submit, task, upload
from test_platform_auth import platform_settings, sign_in

QUOTE = "★ 法定代表人签字并加盖公章，填写日期，每页电子签章。"


@pytest.fixture
def application(tenants, operator, tmp_path):
    return create_app(platform_settings(tmp_path), queue=FakeQueue())


async def operator_header(api):
    response = await sign_in(api)
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["data"]["session"]}


async def anchor(api, header, content):
    response = await api.post(
        "/platform/trust-anchors",
        headers=header,
        data={"label": "Synthetic CA"},
        files={"certificate": ("ca.der", content, "application/octet-stream")},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def tender():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 60), QUOTE, fontname="china-s", fontsize=10)
        return pdf.tobytes()


def mutation(content, case):
    if case == "incremental":
        # A valid appended revision with no additional digital signature.
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            count, root = pdf.xref_length(), pdf.pdf_catalog()
            catalog = pdf.xref_object(root).rstrip()[:-2] + " /Lang (zh-CN) >>"
        previous = int(re.findall(rb"startxref\s+(\d+)", content)[-1])
        extra = f"\n{root} 0 obj\n{catalog}\nendobj\n".encode()
        offset = len(content) + 1
        xref = len(content) + len(extra)
        return (
            content
            + extra
            + f"xref\n{root} 1\n{offset:010d} 00000 n \ntrailer\n<< /Size {count} /Root {root} 0 R /Prev {previous} >>\nstartxref\n{xref}\n%%EOF\n".encode()
        )
    if case in {"gap", "overlap"}:
        pattern = rb"/ByteRange \[([^]]+)\]"
        found = re.search(pattern, content)
        assert found is not None
        values = list(map(int, found[1].split()))
        values[2] = values[1] - 1 if case == "overlap" else values[2] + 2
        replacement = b" ".join(f"{value:<10d}".encode() for value in values)
        assert len(replacement) == len(found[1])
        return content[: found.start(1)] + replacement + content[found.end(1) :]
    if case == "malformed_der":
        where = content.index(b"/Contents <") + len(b"/Contents <")
        return content[:where] + b"3080" + content[where + 4 :]
    return content


async def prepare_one(api, headers, application, tenants, content, *, files=None):
    task_id = await task(api, headers[0])
    uploaded = result(
        await upload(
            api,
            headers[0],
            task_id,
            files
            or [
                ("tender.pdf", PDF, tender()),
                ("bid.pdf", PDF, content),
            ],
        )
    )
    receipt = result(await preview(api, headers[0], task_id, uploaded["id"]))
    request_id = str(uuid4())
    accepted = result(
        await submit(api, headers[0], task_id, uploaded["id"], receipt, request_id=request_id)
    )
    await run(application, tenants["orgs"][0], accepted["job_id"])
    detail = result(await api.get(f"/v4/bid-submissions/{uploaded['id']}", headers=headers[0]))
    assert detail["preparation"]["status"] == "succeeded"
    return uploaded, detail, receipt, request_id, accepted, task_id


@pytest.mark.parametrize(
    "case",
    [
        "signed_attributes",
        "direct",
        "wrong_digest",
        "wrong_signature",
        "wrong_user_id",
        "gap",
        "overlap",
        "incremental",
        "later_invalid",
        "malformed_der",
        "unsupported",
        "expired",
        "not_yet_valid",
        "tampered",
        "embedded_digest",
        "embedded_wrong_digest",
        "embedded_two_signatures",
        "purpose_email",
        "purpose_client_only",
    ],
)
async def test_gm_signature_dimensions_through_http_worker(
    case, api, headers, application, tenants, tmp_path
):
    purposes = {
        "purpose_email": ("1.3.6.1.5.5.7.3.2", "1.3.6.1.5.5.7.3.4"),
        "purpose_client_only": ("1.3.6.1.5.5.7.3.2",),
    }.get(case)
    fixture = gm_fixture(
        expired=case == "expired", not_yet_valid=case == "not_yet_valid", signer_purposes=purposes
    )
    ops = await operator_header(api)
    await anchor(api, ops, fixture.root_der)
    args = {"attrs": False, "direct_digest": True} if case == "direct" else {}
    if case in {"wrong_digest", "wrong_signature", "wrong_user_id"}:
        args[case] = True
    if case.startswith("embedded_"):
        args["embedded_digest"] = True
        args["wrong_digest"] = case == "embedded_wrong_digest"
    if case == "unsupported":
        args["subfilter"] = "unrecognized.detached"
    else:
        args["subfilter"] = "GM.sm2cms.detached"
    content = signed_pdf(fixture, **args)
    if case == "later_invalid":
        content = signed_pdf(
            fixture, base=content, wrong_signature=True, subfilter="GM.sm2cms.detached"
        )
    if case == "embedded_two_signatures":
        content = signed_pdf(
            fixture, base=content, embedded_digest=True, subfilter="GM.sm2cms.detached"
        )
    content = modified_pdf(content) if case == "tampered" else mutation(content, case)
    uploaded, detail, *_ = await prepare_one(api, headers, application, tenants, content)
    doc = next(row for row in detail["submission"]["documents"] if row["role"] == "bid")
    validation = next(
        row for row in detail["signature_validations"] if row["document_id"] == doc["id"]
    )
    assert validation["validation_network"] == "disabled"
    observations = validation["signatures"]
    assert observations and all(row["revocation_status"] == "unknown" for row in observations)
    assert all(row["timestamp_status"] == "absent" for row in observations)
    if case in {"signed_attributes", "direct", "incremental", "embedded_digest", "purpose_email"}:
        assert observations[0]["crypto_status"] == "valid"
        assert observations[0]["trust_status"] == "trusted"
    elif case == "purpose_client_only":
        assert observations[0]["crypto_status"] == "valid"
        assert observations[0]["trust_status"] == "not_for_signing"
    elif case == "embedded_wrong_digest":
        assert observations[0]["content_digest_status"] == "invalid"
        assert observations[0]["signature_value_status"] == "valid"
        assert observations[0]["crypto_status"] == "invalid"
    elif case == "embedded_two_signatures":
        assert [row["crypto_status"] for row in observations] == ["valid", "valid"]
        assert observations[0]["modified_after_signing"] is True
        assert observations[0]["post_signing_changes"] == "covered_by_later_valid_signature"
        assert observations[1]["post_signing_changes"] == "none"
    elif case in {"expired", "not_yet_valid"}:
        assert observations[0]["crypto_status"] == "valid"
        assert observations[0]["certificate_validity_status"] == case
        assert observations[0]["trust_status"] != "trusted"
    elif case == "unsupported":
        assert observations[0]["crypto_status"] == "unsupported"
    elif case == "later_invalid":
        assert len(observations) == 2
        assert observations[0]["crypto_status"] == "valid"
        assert observations[1]["crypto_status"] == "invalid"
        assert validation["final_revision"]["status"] == "invalid"
    else:
        assert observations[0]["crypto_status"] != "valid"
    if case == "direct":
        assert observations[0]["verification_variant"] == "direct_sm3_content_digest"
    if case == "embedded_digest":
        assert (
            observations[0]["verification_variant"] == "gm_embedded_content_digest_sm2_default_za"
        )
    if case == "malformed_der":
        # An unreadable CMS proves nothing either way.
        assert observations[0]["crypto_status"] == "unsupported"
    if case == "wrong_digest":
        assert observations[0]["content_digest_status"] == "invalid"
        assert observations[0]["signature_value_status"] == "valid"
    if case == "incremental":
        assert observations[0]["modified_after_signing"] is True
        assert validation["final_revision"]["modified_after_last_signature"] is True
    candidates = detail["signing_candidates"]
    assert candidates and candidates[0]["applicability"] == "unknown"
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        page = await session.get(BidDocumentPage, UUID(candidates[0]["page_id"]))
        assert page is not None
        envelope = json.loads(Secrets.for_data(Settings()).decrypt(page.text_encrypted))
        candidate = candidates[0]
        assert (
            envelope["value"][candidate["start_offset"] : candidate["end_offset"]]
            == candidate["quote"]
        )
        assert await session.scalar(select(func.count()).select_from(UsageRecord)) == 0
    error(await api.get(f"/v4/bid-submissions/{uploaded['id']}", headers=headers[1]), status=404)
    error(
        await api.get(
            f"/v4/bid-submissions/{uploaded['id']}/signing-candidates", headers=headers[1]
        ),
        status=404,
    )
    (tmp_path / f"signature-{case}-receipt.json").write_text(
        json.dumps(detail, ensure_ascii=False, indent=2)
    )


async def test_snapshot_disable_retry_and_restricted_projection(
    api, headers, application, tenants, admin_engine, caplog, tmp_path
):
    fixture = gm_fixture()
    ops = await operator_header(api)
    root = await anchor(api, ops, fixture.root_der)
    content = signed_pdf(fixture, subfilter="GM.sm2cms.detached")
    uploaded, detail, receipt, request_id, accepted, task_id = await prepare_one(
        api, headers, application, tenants, content
    )
    replay = result(
        await submit(api, headers[0], task_id, uploaded["id"], receipt, request_id=request_id)
    )
    assert replay["job_id"] == accepted["job_id"]
    disabled = await api.post(f"/platform/trust-anchors/{root['id']}/disable", headers=ops)
    assert disabled.status_code == 200
    assert (
        result(await api.get(f"/v4/bid-submissions/{uploaded['id']}", headers=headers[0])) == detail
    )
    _, later, *_ = await prepare_one(api, headers, application, tenants, content)

    def signed(data):
        return next(row for row in data["signature_validations"] if row["signatures"])

    assert signed(detail)["trust_store_sha256"] != signed(later)["trust_store_sha256"]
    assert signed(later)["signatures"][0]["trust_status"] == "unknown"
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        for model in (BidPreparationTrust, BidPDFValidation, BidSigningCandidate):
            rows = list((await session.scalars(select(model))).all())
            assert rows
            if hasattr(rows[0], "details_encrypted"):
                assert "SYNTHETIC GM Signer" not in rows[0].details_encrypted
    async with application.state.db.transaction(tenants["orgs"][1]) as session:
        for model in (BidPreparationTrust, BidPDFValidation, BidSigningCandidate):
            assert await session.scalar(select(func.count()).select_from(model)) == 0
    for table in ("bid_preparation_trust", "bid_pdf_validations", "bid_signing_candidates"):
        with pytest.raises(DBAPIError):
            async with application.state.db.transaction(tenants["orgs"][0]) as session:
                await session.execute(text(f"DELETE FROM {table}"))
    issued = result(
        await api.post(
            "/v4/tokens",
            headers=headers[0],
            json={
                "name": "Synthetic signature advisory token",
                "expires_at": "2030-01-01T00:00:00Z",
                "scopes": ["task:read", "bid-review:read"],
            },
        )
    )
    token_header = {**headers[0], "Authorization": "Bearer " + issued["token"]}
    token_response = await api.get(f"/v4/bid-submissions/{uploaded['id']}", headers=token_header)
    token_detail = result(token_response)
    assert (
        "SYNTHETIC GM Signer" not in token_response.text
        and "SYNTHETIC GM Root" not in token_response.text
    )
    assert all(row["quote"] is None for row in token_detail["signing_candidates"])
    token_candidates = result(
        await api.get(
            f"/v4/bid-submissions/{uploaded['id']}/signing-candidates", headers=token_header
        )
    )
    assert all(row["quote"] is None for row in token_candidates["items"])
    with Session(admin_engine) as session, session.begin():
        member = session.scalar(
            select(Membership).where(
                Membership.org_id == tenants["orgs"][0], Membership.user_id == tenants["users"][0]
            )
        )
        member.role = "technical"
    restricted_response = await api.get(f"/v4/bid-submissions/{uploaded['id']}", headers=headers[0])
    restricted = result(restricted_response)
    assert "SYNTHETIC GM Signer" not in restricted_response.text
    assert "SYNTHETIC GM Root" not in restricted_response.text
    assert all(row["quote"] is None for row in restricted["signing_candidates"])
    assert "SYNTHETIC GM Signer" not in caplog.text and QUOTE not in caplog.text
    (tmp_path / "restricted-signature-receipt.json").write_text(json.dumps(restricted, indent=2))


async def test_unsigned_pdf_is_not_applicable(api, headers, application, tenants):
    _, detail, *_ = await prepare_one(
        api, headers, application, tenants, unsigned_pdf(empty_widget=True)
    )
    assert all(
        row["status"] == "not_applicable" and not row["signatures"]
        for row in detail["signature_validations"]
    )


@pytest.mark.parametrize("algorithm", ["rsa", "ecdsa"])
async def test_standard_detached_through_http_worker(algorithm, api, headers, application, tenants):
    root, builder = standard_fixture(algorithm)
    ops = await operator_header(api)
    await anchor(api, ops, root)
    content = signed_pdf(subfilter="adbe.pkcs7.detached", cms_builder=builder)
    _, detail, *_ = await prepare_one(api, headers, application, tenants, content)
    signature = next(row for row in detail["signature_validations"] if row["signatures"])[
        "signatures"
    ][0]
    assert signature["crypto_status"] == "valid"
    assert signature["trust_status"] == "trusted"


async def test_local_uploaded_intermediate_is_required(api, headers, application, tenants):
    from dataclasses import replace

    fixture = gm_fixture(chain=True)
    assert fixture.intermediate_der is not None
    # Match the owner-described profile: the PDF embeds only the signer cert.
    content = signed_pdf(replace(fixture, intermediate_der=None), subfilter="GM.sm2cms.detached")
    ops = await operator_header(api)
    await anchor(api, ops, fixture.root_der)
    _, missing, *_ = await prepare_one(api, headers, application, tenants, content)
    await anchor(api, ops, fixture.intermediate_der)
    _, complete, *_ = await prepare_one(api, headers, application, tenants, content)
    for detail, trust in ((missing, "unknown"), (complete, "trusted")):
        signature = next(row for row in detail["signature_validations"] if row["signatures"])[
            "signatures"
        ][0]
        assert signature["crypto_status"] == "valid" and signature["trust_status"] == trust


async def test_failed_preparation_retry_keeps_admitted_trust(
    api, headers, application, tenants, monkeypatch
):
    from app.core.errors import ServiceError

    fixture = gm_fixture()
    ops = await operator_header(api)
    root = await anchor(api, ops, fixture.root_der)
    task_id = await task(api, headers[0])
    uploaded = result(
        await upload(
            api,
            headers[0],
            task_id,
            [
                ("tender.pdf", PDF, tender()),
                ("bid.pdf", PDF, signed_pdf(fixture)),
            ],
        )
    )
    receipt = result(await preview(api, headers[0], task_id, uploaded["id"]))
    accepted = result(await submit(api, headers[0], task_id, uploaded["id"], receipt))
    real_put = application.state.storage.put

    async def failing_put(org, key, content):
        if "/pages/" in key:
            raise ServiceError("synthetic_storage_failure", "Synthetic storage failure", 500, 4)
        await real_put(org, key, content)

    monkeypatch.setattr(application.state.storage, "put", failing_put)
    await run(application, tenants["orgs"][0], accepted["job_id"])
    failed = result(await api.get(f"/v4/bid-submissions/{uploaded['id']}", headers=headers[0]))
    assert failed["preparation"]["status"] == "failed" and not failed["signature_validations"]
    assert (
        await api.post(f"/platform/trust-anchors/{root['id']}/disable", headers=ops)
    ).status_code == 200
    monkeypatch.setattr(application.state.storage, "put", real_put)
    new_receipt = result(await preview(api, headers[0], task_id, uploaded["id"]))
    assert new_receipt["input_hash"] == receipt["input_hash"]
    request_id = str(uuid4())
    retried = result(
        await submit(
            api, headers[0], task_id, uploaded["id"], new_receipt, retry=True, request_id=request_id
        )
    )
    assert retried["job_id"] == accepted["job_id"]
    await run(application, tenants["orgs"][0], accepted["job_id"])
    detail = result(await api.get(f"/v4/bid-submissions/{uploaded['id']}", headers=headers[0]))
    assert detail["preparation"]["status"] == "succeeded"
    signature = next(row for row in detail["signature_validations"] if row["signatures"])[
        "signatures"
    ][0]
    assert signature["trust_status"] == "trusted"
    assert (
        result(
            await submit(
                api,
                headers[0],
                task_id,
                uploaded["id"],
                new_receipt,
                retry=True,
                request_id=request_id,
            )
        )["job_id"]
        == accepted["job_id"]
    )
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        assert await session.scalar(select(func.count()).select_from(BidPreparationTrust)) == 1
        assert await session.scalar(select(func.count()).select_from(BidPDFValidation)) == 2


async def test_signature_relational_and_immutable_boundaries(api, headers, application, tenants):
    from app.services.auth import ROLE_SCOPES, Identity, set_actor_context

    for i in (0, 1):
        local_headers = [headers[i], headers[1 - i]]
        local_tenants = {"orgs": [tenants["orgs"][i], tenants["orgs"][1 - i]]}
        await prepare_one(api, local_headers, application, local_tenants, signed_pdf())
    actor = Identity(tenants["users"][0], tenants["orgs"][0], set(ROLE_SCOPES["admin"]), "admin")
    for model in (BidPreparationTrust, BidPDFValidation, BidSigningCandidate):
        async with application.state.db.transaction(tenants["orgs"][1]) as session:
            row = await session.scalar(select(model).limit(1))
            values = {column.name: getattr(row, column.name) for column in model.__table__.columns}
        values |= {"id": uuid4(), "org_id": actor.org_id}
        if "created_by" in values:
            values["created_by"] = actor.user_id
        with pytest.raises(DBAPIError) as rejected:
            async with application.state.db.transaction(actor.org_id) as session:
                await set_actor_context(session, actor)
                await session.execute(model.__table__.insert().values(**values))
        assert rejected.value.orig.sqlstate == "23503"
        with pytest.raises(DBAPIError):
            async with application.state.db.transaction(actor.org_id) as session:
                await session.execute(text(f"UPDATE {model.__tablename__} SET id=id"))
