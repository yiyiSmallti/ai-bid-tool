import os
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from app.api.main import create_app
from app.core.config import Settings
from app.core.security import hash_password
from app.core.totp import generate_secret
from app.models.entities import Membership, Org, User
from app.providers.llm import HTTPExtractor
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

PASSWORD = "synthetic-test-password-only"
PASSWORD_HASH = hash_password(PASSWORD)
OPERATOR = "ops@example.test"
OPERATOR_PASSWORD = "synthetic-operator-password"
OPERATOR_SECRET = generate_secret()


@pytest.fixture(autouse=True)
def no_retry_waits(monkeypatch):
    # Transient-failure retries are covered by their own test; elsewhere one vendor
    # reply per call keeps scripted responses simple.
    monkeypatch.setattr(HTTPExtractor, "retry_delays", ())


@pytest.fixture(scope="session")
def admin_engine():
    url = os.environ.get("BID_TEST_ADMIN_URL")
    if not url:
        pytest.skip("An isolated PostgreSQL test runtime is required")
    if not (make_url(url).database or "").startswith("bid_test"):
        raise RuntimeError("Tests refuse to mutate a database without the bid_test prefix")
    engine = create_engine(url, hide_parameters=True)
    with engine.begin() as connection:
        if not connection.scalar(text("SELECT 1 FROM pg_roles WHERE rolname='bid_app'")):
            from psycopg import sql

            statement = sql.SQL(
                "CREATE ROLE bid_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
            )
            if os.environ.get("BID_DATABASE_PASSWORD"):
                statement += sql.SQL(" PASSWORD {} ").format(
                    sql.Literal(os.environ["BID_DATABASE_PASSWORD"])
                )
            connection.execute(text(statement.as_string()))
    os.environ["BID_MIGRATION_DATABASE_URL"] = url
    command.upgrade(Config("alembic.ini"), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def tenants(admin_engine):
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE platform_cards, balance_entries, org_balances, platform_audit_logs, platform_models, evidence_sources, certificate_files, task_templates, template_revisions, templates, task_org_profiles, org_profile_revisions, org_profiles, task_certificates, certificate_revisions, certificates, task_features, feature_revisions, features, audit_logs, task_resources, product_revisions, products, jobs, usage_records, requirements, chunks, documents, tasks, api_tokens, memberships, orgs, users CASCADE"
            )
        )
    orgs, users = [], []
    with Session(admin_engine) as session, session.begin():
        for label in ("A", "B"):
            org_id = uuid4()
            org = Org(id=org_id, org_id=org_id, name=f"Synthetic tenant {label}")
            user = User(
                id=uuid4(), email=f"{label.lower()}@example.test", password_hash=PASSWORD_HASH
            )
            session.add_all([org, user])
            session.flush()
            session.add(Membership(org_id=org_id, user_id=user.id, role="admin"))
            orgs.append(org_id)
            users.append(user.id)
    return {"orgs": orgs, "users": users}


class FakeQueue:
    def __init__(self):
        self.calls = []
        self.processor = None

    async def enqueue(self, org_id: str, job_id: str):
        self.calls.append((org_id, job_id))
        return len(self.calls)


@pytest.fixture
def application(tenants, tmp_path):
    settings = Settings(data_dir=tmp_path)
    return create_app(settings, queue=FakeQueue())


@pytest.fixture
async def api(application):
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client,
    ):
        yield client


@pytest.fixture
async def headers(api, tenants):
    output = []
    for label, org in zip(("a", "b"), tenants["orgs"], strict=True):
        response = await api.post(
            "/auth/login",
            json={"email": f"{label}@example.test", "password": PASSWORD, "org_id": str(org)},
        )
        assert response.status_code == 200
        output.append(
            {"Authorization": "Bearer " + response.json()["data"]["session"], "X-Org-Id": str(org)}
        )
    return output


@pytest.fixture
def pdf_bytes():
    import pymupdf

    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text(
            (40, 60),
            "Synthetic test fixture, not a real tender.\nRequired delivery within 30 days.\nMinimum memory is 64 GB.",
        )
        second = document.new_page()
        second.insert_text((40, 60), "A valid certificate must be provided.")
        return document.tobytes()


@pytest.fixture
def docx_bytes():
    from io import BytesIO

    from docx import Document

    document = Document()
    document.add_heading("Synthetic template only", level=1)
    document.add_paragraph("Synthetic content, not a real bid or proof.")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.fixture
def operator(tenants, admin_engine):
    """A platform operator identity; settings must list OPERATOR with OPERATOR_SECRET."""
    with Session(admin_engine) as session, session.begin():
        session.add(
            User(id=uuid4(), email=OPERATOR, password_hash=hash_password(OPERATOR_PASSWORD))
        )
    return OPERATOR
