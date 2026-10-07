import base64
import hashlib
import os
import secrets
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from app.api.main import create_app
from app.core.config import Settings
from app.core.totp import generate_secret
from app.models.entities import Membership, Org, User
from app.providers.llm import HTTPExtractor
from app.services.platform_credentials import PlatformCredentialResolver as CredentialResolver
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

PASSWORD = "synthetic-test-password-only"


def cheap_password_hash(password: str) -> str:
    """A hash in the production format with few rounds; verification reads them from it.

    Production's 600,000 PBKDF2 rounds per login dominated the suite on CI's CPUs, and
    no test depends on the work factor stored for these synthetic users.
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 1000)
    return f"pbkdf2$1000${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


PASSWORD_HASH = cheap_password_hash(PASSWORD)
OPERATOR = "ops@example.test"
OPERATOR_PASSWORD = "synthetic-operator-password"
OPERATOR_SECRET = generate_secret()


def prepare_database(url: str, *, configure_logins: bool = True) -> None:
    """Create the application role if missing and migrate the database at `url` to head.

    Role passwords are cluster-wide, so concurrent xdist workers must not set them; only the
    locked `pytest_configure` path and single-process runs configure logins.
    """
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
    engine.dispose()
    os.environ["BID_MIGRATION_DATABASE_URL"] = url
    command.upgrade(Config("alembic.ini"), "head")
    # CI uses password authentication instead of the local test socket's trust policy.
    # These roles exist only after migration and use the isolated test runtime password.
    if configure_logins and os.environ.get("BID_DATABASE_PASSWORD"):
        from psycopg import sql
        from sqlalchemy.exc import SQLAlchemyError

        try:
            with engine.begin() as connection:
                for role in ("bid_platform_app", "bid_credential_reader"):
                    statement = sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(os.environ["BID_DATABASE_PASSWORD"])
                    )
                    connection.exec_driver_sql(statement.as_string())
        except SQLAlchemyError as exc:
            raise RuntimeError("Could not configure isolated credential test logins") from exc
        finally:
            engine.dispose()


def pytest_configure(config):
    """Give each pytest-xdist worker a copy of the migrated test database.

    Tests truncate shared tables, so workers cannot share one database. Roles are
    cluster-wide and migrations create them, so one worker at a time migrates the base
    database under an advisory lock taken in the maintenance database, then copies it.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    admin = os.environ.get("BID_TEST_ADMIN_URL")
    if not worker or not admin:
        return
    base = make_url(admin)
    name = f"{base.database}_{worker}"
    maintenance = create_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", hide_parameters=True
    )
    with maintenance.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(hashtext('bid_test_workers'))"))
        try:
            prepare_database(admin)
            # A database with open sessions cannot be a template; the migration's pool may
            # still hold one, and no other worker uses the base database under this lock.
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                    " WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": base.database},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            connection.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{base.database}"'))
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(hashtext('bid_test_workers'))"))
    maintenance.dispose()
    os.environ["BID_TEST_ADMIN_URL"] = base.set(database=name).render_as_string(hide_password=False)
    for variable in (
        "BID_DATABASE_URL",
        "BID_PLATFORM_DATABASE_URL",
        "BID_CREDENTIAL_DATABASE_URL",
    ):
        if os.environ.get(variable):
            app = make_url(os.environ[variable]).set(database=name)
            os.environ[variable] = app.render_as_string(hide_password=False)


@pytest.fixture(autouse=True)
def no_retry_waits(monkeypatch):
    # Transient-failure retries are covered by their own test; elsewhere one vendor
    # reply per call keeps scripted responses simple.
    monkeypatch.setattr(HTTPExtractor, "retry_delays", ())


@pytest.fixture(scope="session")
def admin_engine():
    url = os.environ.get("BID_TEST_ADMIN_URL")
    if not url:
        # Failing instead of skipping keeps a run without the database from looking green.
        pytest.fail("BID_TEST_ADMIN_URL must point at an isolated PostgreSQL test runtime")
    # Under xdist, pytest_configure already prepared this worker's copy under a lock.
    prepare_database(url, configure_logins=not os.environ.get("PYTEST_XDIST_WORKER"))
    engine = create_engine(url, hide_parameters=True)
    yield engine
    engine.dispose()


@pytest.fixture
def tenants(admin_engine, monkeypatch):
    # Dedicated logins use the same isolated test database, never the org connection.
    runtime_url = make_url(os.environ["BID_DATABASE_URL"])
    for variable, role in (
        ("BID_PLATFORM_DATABASE_URL", "bid_platform_app"),
        ("BID_CREDENTIAL_DATABASE_URL", "bid_credential_reader"),
    ):
        if not os.environ.get(variable):
            monkeypatch.setenv(
                variable, runtime_url.set(username=role).render_as_string(hide_password=False)
            )
    if not os.environ.get("BID_SECRETS_KEY"):
        monkeypatch.setenv("BID_SECRETS_KEY", Fernet.generate_key().decode())
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE platform_credentials, platform_cards, balance_entries, org_balances, platform_audit_logs, platform_models, evidence_sources, certificate_files, task_templates, template_revisions, templates, task_org_profiles, org_profile_revisions, org_profiles, task_certificates, certificate_revisions, certificates, task_features, feature_revisions, features, audit_logs, task_resources, product_revisions, products, jobs, usage_records, requirements, chunks, documents, tasks, api_tokens, memberships, orgs, users CASCADE"
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
        self.wakes = []
        self.annotation_cleanups = []
        self.attachment_invalidations = []

    async def enqueue(self, org_id: str, job_id: str):
        self.calls.append((org_id, job_id))
        return len(self.calls)

    async def enqueue_in_transaction(self, session, org_id: str, job_id: str):
        return await self.enqueue(org_id, job_id)

    async def enqueue_attachment_invalidation_in_transaction(self, session, **arguments):
        self.attachment_invalidations.append(arguments)
        return len(self.attachment_invalidations)

    async def enqueue_agent_wake(self, session, org_id: str, session_id: str, *, delay=30):
        self.wakes.append((org_id, session_id))
        return len(self.wakes)

    async def enqueue_annotation_cleanup_in_transaction(
        self, session, org_id: str, job_id: str, *, delay=300
    ):
        self.annotation_cleanups.append((org_id, job_id, max(300, delay)))
        return len(self.annotation_cleanups)

    async def ensure_process_delivery(self, session, job):
        if job.queue_id is None:
            job.queue_id = await self.enqueue(str(job.org_id), str(job.id))


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
            User(id=uuid4(), email=OPERATOR, password_hash=cheap_password_hash(OPERATOR_PASSWORD))
        )
    return OPERATOR


async def seed_platform_credential(
    settings,
    *,
    name="main",
    provider="anthropic",
    endpoint="https://api.anthropic.com",
    key="synthetic-platform-key",
    active=True,
    purpose="catalog_llm",
):
    """Persist a synthetic credential through the restricted management connection."""
    from datetime import UTC, datetime, timedelta

    from app.schemas.platform_credentials import CredentialCreate, PlatformOperator
    from app.services.platform_credentials import PlatformCredentialService
    from pydantic import SecretStr

    actor = PlatformOperator("fixture@example.test", datetime.now(UTC) + timedelta(minutes=5))
    return (
        await PlatformCredentialService(settings).create(
            actor,
            CredentialCreate(
                name=name,
                purpose=purpose,
                provider=provider,
                endpoint=endpoint,
                api_key=SecretStr(key),
                active=active,
            ),
        )
    ).credential


async def credential_app(settings, *, llm: HTTPExtractor, queue):
    """Use stored platform credentials with an explicitly injected MockTransport adapter.

    The adapter remains injectable for unknown-price, reasoning and stale-identity cases.
    Its billing flags and request settings are untouched; only authentication moves to the
    same encrypted store and per-call catalog resolver used by production API/worker calls.
    """
    from app.models.entities import PlatformModel
    from app.schemas.platform_credentials import CatalogResolveTarget, CredentialSpec

    key = llm.settings.llm_api_key
    assert key is not None and isinstance(llm.transport, httpx.MockTransport)
    provider = "anthropic" if llm.name == "anthropic" else "openai"
    endpoint = CredentialSpec.safe_endpoint(
        llm.settings.llm_base_url
        or ("https://api.anthropic.com" if llm.name == "anthropic" else "https://api.openai.com/v1")
    )
    clean_settings = settings.model_copy(update={"llm_api_key": None})
    llm.settings = llm.settings.model_copy(update={"llm_api_key": None, "llm_base_url": endpoint})
    app = create_app(clean_settings, llm=llm, queue=queue)
    credential_name = "fixture_" + uuid4().hex
    await seed_platform_credential(
        clean_settings,
        name=credential_name,
        provider=provider,
        endpoint=endpoint,
        key=key.get_secret_value(),
    )
    model_id = llm.platform_model_id or "fixture-" + uuid4().hex
    revision = llm.model_revision or 1
    async with app.state.db.transaction() as session:
        session.add(
            PlatformModel(
                id=model_id,
                capability="llm_extract",
                provider=provider,
                model=llm.model,
                base_url=endpoint,
                credential=credential_name,
                revision=revision,
                # These rows authorize credential resolution. Deliberate pricing faults stay
                # on the injected adapter, rather than inventing nullable catalog prices.
                vendor_input_usd_per_mtok=llm.settings.llm_input_usd_per_mtok or 0,
                vendor_output_usd_per_mtok=llm.settings.llm_output_usd_per_mtok or 0,
                sale_input_per_mtok=llm.sale[0] if llm.sale else 0,
                sale_output_per_mtok=llm.sale[1] if llm.sale else 0,
                enabled=True,
                is_default=False,
                reasoning=[],
                updated_by="fixture@example.test",
            )
        )
    llm.credential_resolver = CredentialResolver(clean_settings)
    llm.credential_target = CatalogResolveTarget(
        model_id=model_id, expected_model_revision=revision
    )
    return app
