"""Two-org and lifecycle acceptance for the sandbox (contract items 2 and 7) on a live instance.

Drives the running API, worker and gVisor sandbox node of a development instance with
two synthetic orgs. Org A renders labelled prototypes; org B probes every sandbox
route, job route and signed link of A's run and must see exactly what an unknown
resource returns. The runtime database role then reads and writes across org context
directly. Lifecycle cases cancel a run and SIGKILL the worker while a sandbox is
executing, and check that nothing is published, the container is reaped and the next
render succeeds.

Environment (values never printed):
  ACCEPT_ORG / ACCEPT_EMAIL / ACCEPT_PASSWORD        org A admin
  ACCEPT_B_ORG / ACCEPT_B_EMAIL / ACCEPT_B_PASSWORD  org B admin
  BID_DATABASE_URL                                   the restricted runtime role
  ACCEPT_TASK / ACCEPT_EXTRACTION / ACCEPT_TASK_FEATURE / ACCEPT_FEATURE_REVISION
                                                     A's task inputs for a prototype

    PYTHONPATH=server .venv/bin/python scripts/sandbox_two_org_acceptance.py \\
        --server http://127.0.0.1:8000 --worker-command data/dev-runtime/run-worker.sh

Writes results.json under data/work/sandbox-verification/two-org/ of --runtime-root and
exits 1 when any case fails. It restarts the worker once; run it only against a development instance.
"""

import argparse
import asyncio
import datetime
import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

ROOT = Path(__file__).resolve().parents[1]
# The checkout that runs the instance: sandbox node profile, worker log.
RUNTIME = ROOT
TABLES = (
    "sandbox_inputs",
    "sandbox_runs",
    "sandbox_attempts",
    "sandbox_artifacts",
    "sandbox_fetch_receipts",
)
COLIMA = ["/opt/homebrew/bin/colima", "ssh", "-p", "bsx", "--"]
TERMINAL = {"succeeded", "failed", "cancelled"}


def page(title: str, body: str = "") -> bytes:
    return (
        "<!doctype html><html><body><h1>SYNTHETIC SANDBOX ACCEPTANCE - NOT A REAL PRODUCT</h1>"
        f"<h2>{title}</h2>{body}</body></html>"
    ).encode()


def cpu_loop() -> bytes:
    # A unique page per run, so an identical request never reuses an earlier run.
    return page(f"cpu loop {uuid4()}", "<script>while (true) {}</script>")


def inventory() -> list[str]:
    environment = {
        **os.environ,
        "COLIMA_HOME": str(RUNTIME / "data/work/bsx"),
        "PATH": "/opt/homebrew/bin:" + os.environ["PATH"],
    }
    done = subprocess.run(
        [*COLIMA, "sudo", "docker", "ps", "-a", "--filter", "label=bid.sandbox=1"]
        + ["--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        env=environment,
        timeout=60,
    )
    if done.returncode != 0:
        raise RuntimeError("sandbox node inventory unavailable")
    return [line for line in done.stdout.splitlines() if line]


class Org:
    def __init__(self, http: httpx.AsyncClient, org: str, email: str, password: str):
        self.http, self.org, self.email, self.password = http, org, email, password
        self.headers: dict[str, str] = {}

    async def login(self) -> None:
        response = await self.http.post(
            "/auth/login", json={"email": self.email, "password": self.password, "org_id": self.org}
        )
        response.raise_for_status()
        self.headers = {
            "Authorization": "Bearer " + response.json()["data"]["session"],
            "X-Org-Id": self.org,
        }

    async def get(self, path: str, **kwargs) -> httpx.Response:
        return await self.http.get(path, headers=self.headers, **kwargs)

    async def post(self, path: str, **kwargs) -> httpx.Response:
        return await self.http.post(path, headers=self.headers, **kwargs)


def spec(html: bytes) -> dict:
    return {
        "purpose": "prototype_offline",
        "extraction_job_id": os.environ["ACCEPT_EXTRACTION"],
        "task_feature_id": os.environ["ACCEPT_TASK_FEATURE"],
        "expected_feature_revision_id": os.environ["ACCEPT_FEATURE_REVISION"],
        "html_sha256": hashlib.sha256(html).hexdigest(),
        "html_size_bytes": len(html),
    }


async def submit(org: Org, task: str, html: bytes, *, wait: bool = True) -> dict:
    path = f"/tasks/{task}/sandbox-runs"
    files = {"html": ("prototype.html", html, "text/html; charset=utf-8")}
    preview = await org.post(
        path, data={"submit": json.dumps({"spec": spec(html), "dry_run": True})}, files=files
    )
    assert preview.status_code == 200, preview.text
    request_hash = preview.json()["data"]["request_hash"]
    submitted = await org.post(
        path,
        data={"submit": json.dumps({"spec": spec(html), "expected_request_hash": request_hash})},
        files=files,
    )
    assert submitted.status_code == 200, submitted.text
    run = submitted.json()["data"]
    return await settle(org, run["id"]) if wait else run


async def show(org: Org, run_id: str) -> dict:
    response = await org.get(f"/sandbox-runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def settle(org: Org, run_id: str, seconds: float = 240) -> dict:
    deadline = time.monotonic() + seconds
    while True:
        run = await show(org, run_id)
        if run["state"] in TERMINAL or time.monotonic() > deadline:
            return run
        await asyncio.sleep(2)


async def until_running(org: Org, run_id: str, seconds: float = 120) -> tuple[dict, list[str]]:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        run = await show(org, run_id)
        containers = await asyncio.to_thread(inventory)
        if run["state"] == "running" and containers:
            return run, containers
        if run["state"] in TERMINAL:
            return run, containers
        await asyncio.sleep(1)
    return await show(org, run_id), []


async def reaped(seconds: float = 60) -> float | None:
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        if not await asyncio.to_thread(inventory):
            return round(time.monotonic() - started, 1)
        await asyncio.sleep(1)
    return None


def shape(response: httpx.Response) -> dict:
    body = (
        response.json()
        if response.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    error = (body.get("data") or {}).get("error") or {}
    return {"status": response.status_code, "code": error.get("code"), "ok": body.get("ok")}


async def two_org_cases(a: Org, b: Org, run: dict) -> list[dict[str, Any]]:
    """B gets for A's resources exactly what it gets for unknown ones."""
    task = os.environ["ACCEPT_TASK"]
    artifact = run["artifacts"][0]["id"]
    link = (await a.get(f"/sandbox-artifacts/{artifact}/download-link")).json()["data"]["url"]
    unknown = str(uuid4())
    html = page("cross-org submit")
    files = {"html": ("prototype.html", html, "text/html; charset=utf-8")}
    probes = {
        "run detail": (f"/sandbox-runs/{run['id']}", f"/sandbox-runs/{unknown}", "get", {}),
        "task run list": (
            f"/tasks/{task}/sandbox-runs",
            f"/tasks/{unknown}/sandbox-runs",
            "get",
            {},
        ),
        "artifact link": (
            f"/sandbox-artifacts/{artifact}/download-link",
            f"/sandbox-artifacts/{unknown}/download-link",
            "get",
            {},
        ),
        "job status": (f"/jobs/{run['job_id']}", f"/jobs/{unknown}", "get", {}),
        "job cancel": (f"/jobs/{run['job_id']}/cancel", f"/jobs/{unknown}/cancel", "post", {}),
        "submit on A's task": (
            f"/tasks/{task}/sandbox-runs",
            f"/tasks/{unknown}/sandbox-runs",
            "post",
            {"data": {"submit": json.dumps({"spec": spec(html), "dry_run": True})}, "files": files},
        ),
    }
    results = []
    for name, (foreign, missing, method, kwargs) in probes.items():
        call = b.get if method == "get" else b.post
        seen, absent = shape(await call(foreign, **kwargs)), shape(await call(missing, **kwargs))
        results.append(
            {
                "case": f"B: {name}",
                "passed": seen == absent and seen["status"] == 404,
                "foreign": seen,
                "unknown": absent,
            }
        )
    # A's own signed link, presented with B's session.
    signed = shape(await b.get(link))
    own = await a.get(link)
    results.append(
        {
            "case": "B: A's signed artifact link",
            "passed": signed["status"] == 404 and own.status_code == 200,
            "with_b_session": signed,
            "with_a_session": own.status_code,
        }
    )
    listed = (await b.get("/tasks")).json()["items"]
    results.append(
        {
            "case": "B: task list shows nothing of A",
            "passed": all(item["org_id"] == b.org for item in listed)
            and task not in {item["id"] for item in listed},
            "items": len(listed),
        }
    )
    # A's session cannot switch into B by header.
    switched = shape(await a.http.get("/tasks", headers={**a.headers, "X-Org-Id": b.org}))
    results.append(
        {
            "case": "A's session with B's org header",
            "passed": switched["status"] in (401, 403, 404) and switched["ok"] is False,
            "response": switched,
        }
    )
    return results


def database_cases(a_org: str, b_org: str, run: dict) -> list[dict[str, Any]]:
    engine = create_engine(os.environ["BID_DATABASE_URL"], hide_parameters=True)
    results = []

    def scoped(connection, org: str | None):
        if org is not None:
            connection.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": org})

    with engine.connect() as connection, connection.begin():
        counts = {t: connection.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in TABLES}
    with engine.connect() as connection, connection.begin():
        scoped(connection, a_org)
        # Control: the same tables do hold A's rows.
        own = {t: connection.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in TABLES}
    results.append(
        {
            "case": "DB: no org context reads no rows",
            "passed": not any(counts.values()) and own["sandbox_runs"] > 0,
            "rows": counts,
            "rows_in_a_context": own,
        }
    )
    attempt = run["attempt_id"]
    with engine.connect() as connection, connection.begin():
        scoped(connection, b_org)
        seen = {
            t: connection.execute(
                text(f"SELECT count(*) FROM {t} WHERE org_id = :o"), {"o": a_org}
            ).scalar()
            for t in TABLES
        }
        updated = connection.execute(
            text("UPDATE sandbox_attempts SET cleanup_state = 'failed' WHERE id = :id"),
            {"id": attempt},
        ).rowcount
        connection.rollback()
    results.append(
        {
            "case": "DB: B context sees and updates none of A",
            "passed": not any(seen.values()) and updated == 0,
            "rows_of_a": seen,
            "updated": updated,
        }
    )
    # Sandbox rows pass BEFORE INSERT gates that reject even a same-org copy, so they
    # cannot isolate the tenant checks. Plain tenant tables can: each pair has a
    # control that must succeed, so a rejection is attributable to isolation alone.
    a_task = os.environ["ACCEPT_TASK"]

    def attempt(statements: list[tuple[str, dict]]) -> str | None:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                scoped(connection, b_org)
                for sql, values in statements:
                    connection.execute(text(sql), values)
                return None
            except DBAPIError as exc:
                return getattr(exc.orig, "sqlstate", None) or type(exc.orig).__name__
            finally:
                transaction.rollback()

    with engine.connect() as connection, connection.begin():
        scoped(connection, b_org)
        b_user = connection.execute(
            text("SELECT user_id FROM memberships WHERE org_id = :o LIMIT 1"), {"o": b_org}
        ).scalar_one()
    task_sql = (
        "INSERT INTO tasks (id, org_id, created_by, name) VALUES (:id, :org, :user, 'SYNTHETIC')"
    )
    document_sql = (
        "INSERT INTO documents (id, org_id, task_id, name, sha256, storage_key, media_type, status) "
        "VALUES (:id, :org, :task, 'synthetic.pdf', :sha, :key, 'application/pdf', 'uploaded')"
    )
    b_task = uuid4()

    def document(task) -> tuple[str, dict]:
        return document_sql, {
            "id": uuid4(),
            "org": b_org,
            "task": task,
            "sha": "0" * 64,
            "key": f"org/{b_org}/task/{task}/synthetic.pdf",
        }

    pairs = (
        (
            "task row carrying A's org",
            [(task_sql, {"id": b_task, "org": b_org, "user": b_user})],
            [(task_sql, {"id": uuid4(), "org": a_org, "user": b_user})],
            {"42501"},
        ),
        (
            "B document pointing at A's task",
            [(task_sql, {"id": b_task, "org": b_org, "user": b_user}), document(b_task)],
            [document(a_task)],
            {"23503"},
        ),
    )
    for name, control, forbidden, expected in pairs:
        allowed, error = attempt(control), attempt(forbidden)
        results.append(
            {
                "case": f"DB: insert {name}",
                "passed": allowed is None and error in expected,
                "control_error": allowed,
                "sqlstate": error,
            }
        )
    engine.dispose()
    return results


def worker_pids() -> list[int]:
    done = subprocess.run(["pgrep", "-f", "app.jobs.worker"], capture_output=True, text=True)
    return [int(pid) for pid in done.stdout.split()]


def start_worker(command: str) -> None:
    log = (RUNTIME / "data/dev-runtime/worker.log").open("a")
    subprocess.Popen([command], cwd=RUNTIME, stdout=log, stderr=log, start_new_session=True)


async def lifecycle_cases(a: Org, worker_command: str) -> list[dict[str, Any]]:
    task = os.environ["ACCEPT_TASK"]
    results = []

    # Cancel while the container is executing.
    run = await submit(a, task, cpu_loop(), wait=False)
    running, containers = await until_running(a, run["id"])
    cancelled = await a.post(f"/jobs/{run['job_id']}/cancel")
    final = await settle(a, run["id"])
    cleared = await reaped()
    results.append(
        {
            "case": "cancel during execution",
            "passed": running["state"] == "running"
            and bool(containers)
            and cancelled.status_code == 200
            and final["state"] == "cancelled"
            and not final["artifacts"]
            and cleared is not None,
            "state_seen": running["state"],
            "containers_during": len(containers),
            "final_state": final["state"],
            "artifacts": len(final["artifacts"]),
            "reaped_after_seconds": cleared,
        }
    )

    # SIGKILL the worker while the container is executing, then start a new one.
    run = await submit(a, task, cpu_loop(), wait=False)
    running, containers = await until_running(a, run["id"])
    killed = worker_pids()
    for pid in killed:
        os.kill(pid, signal.SIGKILL)
    cleared = await reaped()
    await asyncio.to_thread(start_worker, worker_command)
    # The orphaned job keeps its lease until it expires; it must never publish artifacts.
    after = await show(a, run["id"])
    await asyncio.sleep(10)
    later = await show(a, run["id"])
    results.append(
        {
            "case": "worker SIGKILL during execution",
            "passed": running["state"] == "running"
            and bool(containers)
            and bool(killed)
            and cleared is not None
            and not after["artifacts"]
            and not later["artifacts"]
            and bool(worker_pids()),
            "containers_during": len(containers),
            "killed_workers": len(killed),
            "reaped_after_seconds": cleared,
            "state_after_kill": after["state"],
            "state_after_restart": later["state"],
            "artifacts": len(later["artifacts"]),
            "worker_running_again": bool(worker_pids()),
        }
    )
    # Cancel the orphan so its lease cannot make it run again later.
    if later["state"] not in TERMINAL:
        await a.post(f"/jobs/{run['job_id']}/cancel")

    baseline = await submit(a, task, page(f"baseline after lifecycle faults {uuid4()}"))
    results.append(
        {
            "case": "render succeeds after the faults",
            "passed": baseline["state"] == "succeeded" and bool(baseline["artifacts"]),
            "state": baseline["state"],
            "artifacts": len(baseline["artifacts"]),
        }
    )
    return results


async def main(server: str, worker_command: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(base_url=server, timeout=120) as http:
        a = Org(
            http,
            os.environ["ACCEPT_ORG"],
            os.environ["ACCEPT_EMAIL"],
            os.environ["ACCEPT_PASSWORD"],
        )
        b = Org(
            http,
            os.environ["ACCEPT_B_ORG"],
            os.environ["ACCEPT_B_EMAIL"],
            os.environ["ACCEPT_B_PASSWORD"],
        )
        await a.login()
        await b.login()
        run = await submit(a, os.environ["ACCEPT_TASK"], page(f"two-org {uuid4()}"))
        assert run["state"] == "succeeded", run
        cases = [
            {
                "case": "A renders a prototype through the real worker and sandbox",
                "passed": run["state"] == "succeeded" and run["cleanup_state"] == "complete",
                "artifacts": sorted(item["kind"] for item in run["artifacts"]),
            }
        ]
        cases += await two_org_cases(a, b, run)
        cases += await asyncio.to_thread(database_cases, a.org, b.org, run)
        cases += await lifecycle_cases(a, worker_command)
    return cases


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--worker-command", required=True)
    parser.add_argument("--runtime-root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    RUNTIME = args.runtime_root.resolve()
    results = asyncio.run(main(args.server, args.worker_command))
    args.output = args.output or RUNTIME / "data/work/sandbox-verification/two-org"
    args.output.mkdir(parents=True, exist_ok=True)
    report = {
        "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "passed": all(item["passed"] for item in results),
        "cases": results,
    }
    (args.output / "results.json").write_text(json.dumps(report, indent=2, default=str))
    for item in results:
        print(("PASS " if item["passed"] else "FAIL ") + item["case"])
    print(f"{sum(i['passed'] for i in results)}/{len(results)} passed")
    raise SystemExit(0 if report["passed"] else 1)
