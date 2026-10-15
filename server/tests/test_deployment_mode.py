"""Deployment mode through the real init-db, API and worker entry points.

Failure matrix before implementation: an enterprise database initializes without
a company name; init-db, the API or the worker runs a database in the other
mode; an existing personal database without the installation row is claimed by
an enterprise gateway, or migrated before the refusal; the stored mode changes
in place; an enterprise gateway accepts the access key over HTTP. Artifacts are
synthetic and reproducible with --basetemp=data/work/deployment-mode.
"""

import json
import os
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

ENTERPRISE = {
    "BID_DEPLOYMENT": "enterprise",
    "BID_PUBLIC_URL": "https://gateway.example.test",
    "BID_COMPANY_NAME": "Synthetic Company",
}
PERSONAL = {"BID_DEPLOYMENT": "personal"}


@pytest.fixture
def scratch_databases(admin_engine):
    """Create empty databases on demand; return (migration URL, runtime URL)."""
    admin = make_url(os.environ["BID_TEST_ADMIN_URL"])
    maintenance = create_engine(
        admin.set(database="postgres"), isolation_level="AUTOCOMMIT", hide_parameters=True
    )
    created = []

    def create():
        name = f"{admin.database}_mode_{uuid4().hex[:8]}"
        with maintenance.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        created.append(name)
        runtime = make_url(os.environ["BID_DATABASE_URL"]).set(database=name)
        return (
            admin.set(database=name).render_as_string(hide_password=False),
            runtime.render_as_string(hide_password=False),
        )

    yield create
    with maintenance.connect() as connection:
        for name in created:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    maintenance.dispose()


def environment(migration, runtime, mode, tmp_path):
    env = {
        name: value
        for name, value in os.environ.items()
        if name not in {"BID_DEPLOYMENT", "BID_PUBLIC_URL", "BID_COMPANY_NAME"}
    }
    return (
        env
        | mode
        | {
            "BID_MIGRATION_DATABASE_URL": migration,
            "BID_DATABASE_URL": runtime,
            "BID_DATA_DIR": str(tmp_path / "files"),
        }
    )


def init_db(env):
    done = subprocess.run(
        [sys.executable, "-m", "app.admin", "init-db"],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    error = json.loads(done.stderr.strip().splitlines()[-1])["error"] if done.returncode else None
    return done.returncode, error


def free_port():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


def api_command(port):
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "app.api.main:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
        "--no-access-log",
    ]


def refused_start(command, env):
    """Exit status and whether the output names the mismatch, for a start that must fail."""
    done = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120)
    return done.returncode, "deployment_mode_mismatch" in done.stdout + done.stderr


def stored(migration):
    engine = create_engine(migration, hide_parameters=True)
    try:
        with engine.connect() as connection:
            version = None
            if connection.scalar(text("SELECT to_regclass('public.alembic_version')")):
                version = connection.scalar(text("SELECT version_num FROM public.alembic_version"))
            row = None
            if version and version >= "0004":
                row = (
                    connection.execute(text("SELECT mode, company_name FROM public.installation"))
                    .mappings()
                    .first()
                )
    finally:
        engine.dispose()
    return version, dict(row) if row else None


def test_mode_mismatch_both_ways(scratch_databases, tmp_path):
    report = {
        "rerun": "pytest -q server/tests/test_deployment_mode.py --basetemp=data/work/deployment-mode"
    }
    worker = [sys.executable, "-m", "app.jobs.worker"]

    # Enterprise: the company name is required before anything is migrated.
    migration, runtime = scratch_databases()
    gateway = environment(migration, runtime, ENTERPRISE, tmp_path)
    nameless = {key: value for key, value in gateway.items() if key != "BID_COMPANY_NAME"}
    assert init_db(nameless) == (2, "company_name_required")
    assert stored(migration) == (None, None)
    assert init_db(gateway) == (0, None)
    assert init_db(gateway) == (0, None)
    assert stored(migration) == (
        "0004",
        {"mode": "enterprise", "company_name": "Synthetic Company"},
    )
    personal_on_gateway = environment(migration, runtime, PERSONAL, tmp_path)
    report["enterprise_database"] = {
        "init_db_personal": init_db(personal_on_gateway),
        "api_personal": refused_start(api_command(free_port()), personal_on_gateway),
        "worker_personal": refused_start(worker, personal_on_gateway),
    }
    assert report["enterprise_database"] == {
        "init_db_personal": (4, "deployment_mode_mismatch"),
        "api_personal": (3, True),
        "worker_personal": (4, True),
    }

    # The matching mode starts; the gateway refuses the access key over HTTP.
    port = free_port()
    api = subprocess.Popen(
        api_command(port), env=gateway, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    runner = subprocess.Popen(
        worker, env=gateway, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}") as http:
            for _ in range(200):
                try:
                    if http.get("/health").status_code == 200:
                        break
                except httpx.NetworkError:
                    pass
                time.sleep(0.1)
            else:
                raise AssertionError("Enterprise API did not start")
            key = os.environ["BID_ACCESS_KEY"]
            session = http.post("/auth/session", json={"access_key": key})
            tasks = http.get("/tasks", headers={"Authorization": f"Bearer {key}"})
        report["enterprise_access_key"] = [
            [session.status_code, session.json()["data"]["error"]["code"]],
            [tasks.status_code, tasks.json()["data"]["error"]["code"]],
        ]
        assert report["enterprise_access_key"] == [
            [401, "access_key_not_accepted"],
            [401, "access_key_not_accepted"],
        ]
        time.sleep(2)
        assert runner.poll() is None, "Enterprise worker exited on its own database"
    finally:
        for process in (api, runner):
            process.terminate()
            process.wait(timeout=30)

    # The stored mode cannot change in place, even for the owner.
    engine = create_engine(migration, hide_parameters=True)
    try:
        for statement in (
            "UPDATE public.installation SET mode='personal', company_name=NULL",
            "DELETE FROM public.installation",
        ):
            with pytest.raises(DBAPIError, match="immutable"), engine.begin() as connection:
                connection.execute(text(statement))
    finally:
        engine.dispose()

    # Personal: an enterprise gateway refuses it.
    migration, runtime = scratch_databases()
    personal = environment(migration, runtime, PERSONAL, tmp_path)
    assert init_db(personal) == (0, None)
    assert stored(migration) == ("0004", {"mode": "personal", "company_name": None})
    gateway_on_personal = environment(migration, runtime, ENTERPRISE, tmp_path)
    report["personal_database"] = {
        "init_db_enterprise": init_db(gateway_on_personal),
        "api_enterprise": refused_start(api_command(free_port()), gateway_on_personal),
        "worker_enterprise": refused_start(worker, gateway_on_personal),
    }
    assert report["personal_database"] == {
        "init_db_enterprise": (4, "deployment_mode_mismatch"),
        "api_enterprise": (3, True),
        "worker_enterprise": (4, True),
    }

    # An existing database from before the installation row is personal, and an
    # enterprise init-db refuses it before migrating anything.
    migration, runtime = scratch_databases()
    legacy = environment(migration, runtime, PERSONAL, tmp_path)
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "0003"],
        env=legacy,
        check=True,
        capture_output=True,
        timeout=300,
    )
    gateway_on_legacy = environment(migration, runtime, ENTERPRISE, tmp_path)
    report["legacy_database"] = {
        "init_db_enterprise": init_db(gateway_on_legacy),
        "after_refusal": stored(migration),
        "init_db_personal": init_db(legacy),
        "after_upgrade": stored(migration),
    }
    assert report["legacy_database"] == {
        "init_db_enterprise": (4, "deployment_mode_mismatch"),
        "after_refusal": ("0003", None),
        "init_db_personal": (0, None),
        "after_upgrade": ("0004", {"mode": "personal", "company_name": None}),
    }
    (tmp_path / "deployment-mode.json").write_text(json.dumps(report, indent=2))
