"""Real HTTP and PostgreSQL acceptance for public CA management.

Failure modes are enumerated before implementation in
data/work/bid-review-sig/failure-modes.md. Prepared here; requires the isolated DB.
"""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.api.main import create_app
from app.core.config import Settings
from app.core.db import Database
from app.services.platform_trust_anchors import get_snapshot
from conftest import PASSWORD, FakeQueue
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_platform_auth import platform_settings, sign_in

PATH = "/platform/trust-anchors"


def certificate(*, ca=True):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic offline test CA")])
    now = datetime(2026, 10, 8, tzinfo=UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=ca,
                crl_sign=ca,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM), cert.public_bytes(
        serialization.Encoding.DER
    )


@pytest.fixture
async def console(operator, tmp_path):
    app = create_app(platform_settings(tmp_path), queue=FakeQueue())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api,
    ):
        login = await sign_in(api)
        assert login.status_code == 200, login.text
        yield api, {"Authorization": "Bearer " + login.json()["data"]["session"]}


def audit_rows(admin_engine):
    with admin_engine.connect() as connection:
        return list(
            connection.execute(
                text(
                    "SELECT action,details FROM platform_audit_logs WHERE action LIKE 'platform.trust_anchor.%' ORDER BY created_at,id"
                )
            ).mappings()
        )


async def test_preview_add_duplicate_disable_and_pinned_snapshot(
    console, admin_engine, tenants, tmp_path
):
    api, headers = console
    pem, der = certificate()
    preview = await api.post(
        PATH + "/preview",
        headers=headers,
        data={"label": "Synthetic CA"},
        files={"certificate": ("ca.pem", pem)},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["is_ca"] is True
    assert audit_rows(admin_engine) == []
    added = await api.post(
        PATH,
        headers=headers,
        data={"label": "Synthetic CA"},
        files={"certificate": ("ca.pem", pem)},
    )
    assert added.status_code == 200, added.text
    anchor = added.json()["data"]
    assert anchor["created"] and anchor["enabled"] and anchor["revision"] == 1
    assert "certificate_der" not in anchor
    duplicate = await api.post(
        PATH,
        headers=headers,
        data={"label": "Ignored rename"},
        files={"certificate": ("ca.der", der)},
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["data"]["id"] == anchor["id"]
    assert duplicate.json()["data"]["label"] == "Synthetic CA"
    assert duplicate.json()["data"]["created"] is False
    db = Database(Settings(data_dir=tmp_path))
    try:
        async with db.transaction() as session:
            pinned = await get_snapshot(session)
        assert len(pinned["anchors"]) == 1
        disabled = await api.post(f"{PATH}/{anchor['id']}/disable", headers=headers)
        assert disabled.status_code == 200, disabled.text
        assert not disabled.json()["data"]["enabled"]
        assert disabled.json()["data"]["revision"] == 2
        async with db.transaction() as session:
            current = await get_snapshot(session)
        assert current["anchors"] == [] and current["sha256"] != pinned["sha256"]
        assert pinned["anchors"][0]["fingerprint_sha256"] == anchor["fingerprint_sha256"]
        assert (
            await api.post(f"{PATH}/{anchor['id']}/disable", headers=headers)
        ).status_code == 200
        again = await api.post(
            PATH, headers=headers, data={"label": "CA"}, files={"certificate": ("ca.pem", pem)}
        )
        assert again.status_code == 200 and not again.json()["data"]["enabled"]
    finally:
        await db.engine.dispose()
    assert [row["action"] for row in audit_rows(admin_engine)] == [
        "platform.trust_anchor.add",
        "platform.trust_anchor.disable",
    ]
    listed = await api.get(PATH, headers=headers)
    assert listed.status_code == 200 and len(listed.json()["items"]) == 1
    assert "Synthetic offline test CA" not in json.dumps(
        [dict(row) for row in audit_rows(admin_engine)]
    )


async def test_invalid_ca_and_org_authority_rejected(console, tenants, admin_engine):
    api, operator_headers = console
    pem, _ = certificate(ca=False)
    for payload in (pem, b"garbage", b"x" * 65537):
        response = await api.post(
            PATH,
            headers=operator_headers,
            data={"label": "CA"},
            files={"certificate": ("ca.pem", payload)},
        )
        assert (
            response.status_code == 400
            and response.json()["data"]["error"]["code"] == "invalid_trust_anchor"
        )
    assert audit_rows(admin_engine) == []
    for org, email in zip(tenants["orgs"], ("a@example.test", "b@example.test"), strict=True):
        login = await api.post(
            "/auth/login", json={"email": email, "password": PASSWORD, "org_id": str(org)}
        )
        assert login.status_code == 200, login.text
        header = {
            "Authorization": "Bearer " + login.json()["data"]["session"],
            "X-Org-Id": str(org),
        }
        for method, path in (
            ("GET", PATH),
            ("POST", PATH),
            ("POST", PATH + "/preview"),
            ("POST", PATH + "/00000000-0000-0000-0000-000000000001/disable"),
        ):
            response = await api.request(
                method,
                path,
                headers=header,
                data={"label": "CA"},
                files={"certificate": ("ca.pem", pem)},
            )
            assert response.status_code == 401, response.text


def test_fixed_functions_and_no_direct_org_runtime_access(admin_engine, tenants):
    with admin_engine.connect() as connection:
        role = connection.execute(
            text(
                "SELECT rolcanlogin,rolsuper,rolbypassrls,rolinherit FROM pg_roles WHERE rolname='bid_trust_anchors_fn'"
            )
        ).one()
        assert tuple(role) == (False, False, False, False)
        assert not connection.scalar(
            text(
                "SELECT has_table_privilege('bid_app','platform_trust_anchors','SELECT,INSERT,UPDATE,DELETE')"
            )
        )
        assert not connection.scalar(
            text("SELECT pg_has_role('bid_app','bid_trust_anchors_fn','MEMBER')")
        )
        rows = connection.execute(
            text(
                "SELECT p.proname,pg_get_userbyid(p.proowner),p.prosecdef,has_function_privilege('bid_app',p.oid,'EXECUTE'),has_function_privilege('public',p.oid,'EXECUTE'),p.proconfig FROM pg_proc p WHERE p.proname LIKE 'platform_trust_anchor_%'"
            )
        ).all()
        assert len(rows) == 4
        assert all(row[1:5] == ("bid_trust_anchors_fn", True, True, False) for row in rows)
        assert all("search_path=pg_catalog" in row[5] for row in rows)
        assert not connection.scalar(
            text("SELECT has_schema_privilege('bid_trust_anchors_fn','public','CREATE')")
        )
        assert not connection.scalar(
            text("SELECT EXISTS(SELECT 1 FROM pg_policies WHERE 'bid_trust_anchors_fn'=ANY(roles))")
        )
    for statement in (
        "SELECT * FROM public.platform_trust_anchors",
        "INSERT INTO public.platform_trust_anchors(id) VALUES(gen_random_uuid())",
        "UPDATE public.platform_trust_anchors SET enabled=false",
        "DELETE FROM public.platform_trust_anchors",
    ):
        with pytest.raises(DBAPIError), admin_engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE bid_app"))
            connection.execute(text(statement))


async def test_admission_and_audit_rollback_together(console, tenants, admin_engine):
    api, headers = console
    pem, _ = certificate()
    with admin_engine.begin() as connection:
        connection.execute(
            text("""
        CREATE FUNCTION public.test_reject_anchor_audit() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF NEW.action='platform.trust_anchor.add' THEN
        RAISE EXCEPTION 'Synthetic audit sink unavailable'; END IF; RETURN NEW; END $$;
        CREATE TRIGGER test_reject_anchor_audit BEFORE INSERT ON public.platform_audit_logs
        FOR EACH ROW EXECUTE FUNCTION public.test_reject_anchor_audit();
        """)
        )
    try:
        response = await api.post(
            PATH,
            headers=headers,
            data={"label": "Rollback CA"},
            files={"certificate": ("ca.pem", pem)},
        )
        assert response.status_code == 503, response.text
        with admin_engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT count(*) FROM public.platform_trust_anchors")) == 0
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM public.platform_audit_logs WHERE action='platform.trust_anchor.add'"
                    )
                )
                == 0
            )
    finally:
        with admin_engine.begin() as connection:
            connection.execute(
                text("DROP TRIGGER test_reject_anchor_audit ON public.platform_audit_logs")
            )
            connection.execute(text("DROP FUNCTION public.test_reject_anchor_audit()"))
