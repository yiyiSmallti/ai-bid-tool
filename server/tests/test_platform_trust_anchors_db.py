"""Real HTTP and PostgreSQL acceptance for public CA management.

Failure modes are enumerated before implementation in
data/work/bid-review-sig/failure-modes.md. Prepared here; requires the isolated DB.
"""

import json
import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.api.main import create_app
from app.core.config import Settings
from app.core.db import Database
from app.services.platform_trust_anchors import get_snapshot
from conftest import FakeQueue
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy import text

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
async def console(database, tmp_path):
    app = create_app(Settings(data_dir=tmp_path), queue=FakeQueue())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api,
    ):
        yield api, {"Authorization": "Bearer " + os.environ["BID_ACCESS_KEY"]}


def audit_rows(admin_engine):
    with admin_engine.connect() as connection:
        return list(
            connection.execute(
                text(
                    "SELECT action,details FROM audit_logs WHERE action LIKE 'platform.trust_anchor.%' ORDER BY created_at,id"
                )
            ).mappings()
        )


async def test_preview_add_duplicate_disable_and_pinned_snapshot(
    console, admin_engine, database, tmp_path
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
    listed = await api.get(PATH, headers=headers)
    assert listed.status_code == 200 and len(listed.json()["items"]) == 1
    assert "Synthetic offline test CA" not in json.dumps(
        [dict(row) for row in audit_rows(admin_engine)]
    )


async def test_invalid_ca_and_token_authority_rejected(console, database, admin_engine):
    api, human_headers = console
    pem, _ = certificate(ca=False)
    for payload in (pem, b"garbage", b"x" * 65537):
        response = await api.post(
            PATH,
            headers=human_headers,
            data={"label": "CA"},
            files={"certificate": ("ca.pem", payload)},
        )
        assert (
            response.status_code == 400
            and response.json()["data"]["error"]["code"] == "invalid_trust_anchor"
        )
    assert audit_rows(admin_engine) == []
    token = await api.post(
        "/tokens",
        headers=human_headers,
        json={
            "name": "trust boundary",
            "scopes": ["task:read"],
            "expires_at": "2030-01-01T00:00:00Z",
        },
    )
    header = {"Authorization": "Bearer " + token.json()["data"]["token"]}
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
        assert response.status_code in {401, 403}, response.text
