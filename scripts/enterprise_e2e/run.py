"""Enterprise gateway end-to-end acceptance (docs/plan/enterprise-gateway.md#end-to-end-acceptance).

Starts deploy/gateway/compose.yml, with the test-only compose.e2e.yml beside
this file, on an isolated Docker daemon: a gateway, and personal installations
from the same files. A synthetic HTTPS model (vendor.py) answers every model
call; nothing leaves the Docker network. The administrator and members A, B and
C act only through Caddy over HTTPS.

Failure matrix before implementation: B misses A's certificate or public memory,
or sees A's private memory or personal model; B's drafting uses the company key
or A's model; the extraction uses a member's model instead of the company one;
a non-member learns a project object exists from any route; a card confirmation
records someone other than B; an agent gains a confirm scope; an imported
project loses a requirement confirmation, a card confirmation or a citation;
the gateway exports before the administrator allows it; the package B brings
back differs from the one A brought in; a disabled B keeps a session, a token or
a running agent; a password or key reaches the report or a container log.
report.json is rewritten after every step; it holds no password, session or key.
"""

import argparse
import asyncio
import json
import os
import secrets
import ssl
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
sys.path[:0] = [str(REPOSITORY / "server"), str(REPOSITORY / "server" / "tests")]

from test_package_import import table_hashes  # noqa: E402
from test_package_round_trip import decisions  # noqa: E402
from test_project_visibility import sweep  # noqa: E402
from test_response_cards import TENDER_LINES, labelled_pdf  # noqa: E402

PASSWORD = "synthetic-e2e-password-2026"
VENDOR = "https://vendor:8443/v1"
AGENT_SCOPES = [
    "task:read",
    "job:read",
    "card:read",
    "card:generate",
    "draft:read",
    "draft:run",
    "resource:read",
    "certificate:read",
    "certificate:file:read",
    "profile:read",
    "evidence:source:read",
]
COUNT_SQL = (
    "SELECT table_name || '=' || (xpath('/row/c/text()', query_to_xml(format("
    "'SELECT count(*) AS c FROM public.%I', table_name), false, true, '')))[1]::text"
    " FROM information_schema.tables WHERE table_schema = 'public'"
    " AND table_type = 'BASE TABLE' ORDER BY 1"
)


def vendor_certificate(directory: Path) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "vendor")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("vendor")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    (directory / "vendor.crt").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    (directory / "vendor.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


TEST_IMAGE = """FROM bid-gateway:new
USER root
COPY vendor.py vendor.crt vendor.key /e2e/
RUN cat /e2e/vendor.crt >> "$(python -c 'import certifi; print(certifi.where())')" \\
    && chown bid /e2e/vendor.key && chmod 0400 /e2e/vendor.key
USER bid
"""


def code(response: httpx.Response) -> list:
    if not response.headers.get("content-type", "").startswith("application/json"):
        return [response.status_code, None]
    body = response.json()
    data = body.get("data") if isinstance(body, dict) else None
    error = data.get("error") if isinstance(data, dict) else None
    return [response.status_code, error.get("code") if isinstance(error, dict) else None]


class Run:
    def __init__(self, args):
        self.args = args
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.stamp = stamp.lower()
        self.root = REPOSITORY / "data" / "work" / "enterprise-e2e" / stamp
        self.root.mkdir(mode=0o700, parents=True)
        self.log = (self.root / "commands.log").open("w")
        self.report: dict = {"synthetic_only": True, "steps": []}
        self.secrets: list[str] = []
        docker_config = self.root / "docker"
        docker_config.mkdir(mode=0o700)
        (docker_config / "config.json").write_text(
            json.dumps({"cliPluginsExtraDirs": [args.compose_plugins]})
        )
        self.environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("BID_")},
            "DOCKER_HOST": args.host,
            "DOCKER_CONFIG": str(docker_config),
        }
        self.step: dict | None = None

    def run(self, arguments, capture=False, check=True, **extra):
        result = subprocess.run(
            arguments,
            env={**self.environment, **extra},
            stdout=subprocess.PIPE if capture else self.log,
            stderr=self.log,
            text=True,
        )
        if check and result.returncode != 0:
            raise RuntimeError(f"{' '.join(arguments[:5])} exited {result.returncode}")
        return result

    def save(self):
        (self.root / "report.json").write_text(
            json.dumps(self.report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        )

    def begin(self, number: int, name: str, stack: "Stack"):
        self.step = {
            "step": number,
            "name": name,
            "requests": [],
            "assertions": [],
            "_stack": stack,
            "_before": stack.counts(),
        }

    def check(self, assertion: str, condition: bool, observed=None):
        assert self.step is not None
        self.step["assertions"].append({"assertion": assertion, "observed": observed})
        if not condition:
            self.step["failed"] = assertion
            self.end()
            raise AssertionError(f"{assertion}: {observed}")

    def end(self):
        step = self.step
        assert step is not None
        before, after = step.pop("_before"), step.pop("_stack").counts()
        step["rows"] = {
            table: [before.get(table, 0), after.get(table, 0)]
            for table in sorted(set(before) | set(after))
            if before.get(table, 0) != after.get(table, 0)
        }
        self.report["steps"].append(step)
        self.save()
        print("step", step["step"], "failed" if "failed" in step else "passed", flush=True)
        self.step = None


class Stack:
    """One Compose project from deploy/gateway, reached over HTTPS through Caddy."""

    def __init__(self, run: Run, name: str, port: int, deployment: str):
        self.owner = run
        self.name = name
        self.port = port
        values = {
            "BID_IMAGE": "bid-e2e:test",
            "BID_E2E_DEPLOYMENT": deployment,
            "BID_COMPANY_NAME": "合成测试公司",
            "BID_GATEWAY_HOST": "localhost",
            "BID_PUBLIC_URL": f"https://localhost:{port}",
            "BID_TLS": "internal",
            "BID_HTTPS_BIND": f"127.0.0.1:{port}",
            "BID_HTTP_BIND": f"127.0.0.1:{port + 1}",
            "BID_OWNER_PASSWORD": secrets.token_hex(24),
            "BID_DATABASE_PASSWORD": secrets.token_hex(24),
            "BID_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "BID_TOKEN_KEY": Fernet.generate_key().decode(),
            "BID_SECRETS_KEY": Fernet.generate_key().decode(),
            "BID_ACCESS_KEY": secrets.token_urlsafe(32),
            "BID_STORAGE": "local",
        }
        self.access_key = values["BID_ACCESS_KEY"]
        run.secrets += [v for k, v in values.items() if "PASSWORD" in k or "KEY" in k]
        self.env_file = run.root / f"{name}.env"
        with os.fdopen(
            os.open(self.env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
        ) as handle:
            handle.write("".join(f"{k}={v}\n" for k, v in values.items()))
        self.compose = [
            "docker",
            "compose",
            "-p",
            f"bid-e2e-{run.stamp}-{name}",
            "-f",
            str(REPOSITORY / "deploy" / "gateway" / "compose.yml"),
            "-f",
            str(HERE / "compose.e2e.yml"),
            "--env-file",
            str(self.env_file),
        ]
        self.http: httpx.Client | None = None
        # Container logs, kept when the project goes down for the secret scan.
        self.collected = ""
        self.context: ssl.SSLContext | None = None

    def up(self) -> httpx.Client:
        self.owner.run([*self.compose, "up", "-d"])
        for _ in range(60):
            authority = self.owner.run(
                [*self.compose, "exec", "-T", "caddy", "cat"]
                + ["/data/caddy/pki/authorities/local/root.crt"],
                capture=True,
                check=False,
            ).stdout
            if authority:
                break
            time.sleep(2)
        else:
            raise RuntimeError(f"{self.name}: Caddy did not create its local authority")
        self.context = ssl.create_default_context(cadata=authority)
        self.http = httpx.Client(
            base_url=f"https://localhost:{self.port}", verify=self.context, timeout=60
        )
        for _ in range(90):
            try:
                if self.http.get("/health").status_code == 200:
                    return self.http
            except httpx.HTTPError:
                pass
            time.sleep(2)
        raise RuntimeError(f"{self.name}: no answer over HTTPS")

    def down(self):
        if not self.env_file.exists():
            return
        if self.http is not None:
            self.collected = self.logs()
            self.http.close()
        self.owner.run([*self.compose, "down", "-v"], check=False)
        self.env_file.unlink(missing_ok=True)

    def sql(self, query: str) -> list[str]:
        return self.owner.run(
            [*self.compose, "exec", "-T", "postgres", "psql", "-U", "bid_owner", "-d", "bid"]
            + ["-Atc", query],
            capture=True,
        ).stdout.split()

    def counts(self) -> dict[str, int]:
        return {name: int(value) for name, value in (row.split("=") for row in self.sql(COUNT_SQL))}

    def vendor(self, *command: str) -> str:
        return self.owner.run(
            [*self.compose, "exec", "-T", "vendor", *command], capture=True
        ).stdout

    def calls(self) -> list[dict]:
        return [json.loads(line) for line in self.vendor("cat", "/tmp/vendor.log").splitlines()]

    def logs(self) -> str:
        return self.owner.run([*self.compose, "logs", "--no-color"], capture=True).stdout


class Actor:
    """A signed-in human; every call lands in the current step's request list."""

    def __init__(self, run: Run, stack: Stack, name: str, header: dict):
        self.owner, self.stack, self.name, self.header = run, stack, name, header

    def call(self, method: str, path: str, *, expect=200, header=None, **kwargs):
        assert self.stack.http is not None
        response = self.stack.http.request(method, path, headers=header or self.header, **kwargs)
        observed = code(response)
        step = self.owner.step
        if step is not None:
            step["requests"].append(
                {"actor": self.name, "method": method, "path": path, "status": observed}
            )
        if expect is not None and response.status_code != expect:
            raise AssertionError(f"{self.name} {method} {path}: {observed} {response.text[:500]}")
        return response

    def data(self, method: str, path: str, **kwargs):
        return self.call(method, path, **kwargs).json()["data"]

    def job(self, job_id: str, path: str = "/jobs/{}") -> dict:
        for _ in range(300):
            view = self.data("GET", path.format(job_id))
            if view["status"] in {"succeeded", "failed", "cancelled"}:
                return view
            time.sleep(1)
        raise AssertionError(f"job {job_id} did not finish")


def sign_in(run: Run, stack: Stack, name: str, login: str, password: str) -> Actor:
    """Sign in and replace a one-time password, as every new member must."""
    assert stack.http is not None
    run.secrets.append(password)
    response = stack.http.post("/auth/session", json={"login": login, "password": password})
    assert response.status_code == 200, response.text
    header = {"Authorization": "Bearer " + response.json()["data"]["session"]}
    actor = Actor(run, stack, name, header)
    actor.call("POST", "/account/password", json={"current": password, "new": PASSWORD})
    return actor


def personal_actor(run: Run, stack: Stack, name: str) -> Actor:
    assert stack.http is not None
    response = stack.http.post("/auth/session", json={"access_key": stack.access_key})
    assert response.status_code == 200, response.text
    return Actor(
        run, stack, name, {"Authorization": "Bearer " + response.json()["data"]["session"]}
    )


def model(name: str, **changes) -> dict:
    return {
        "capability": "llm_extract",
        "provider": "openai",
        "model": name,
        "base_url": VENDOR,
        "json_mode": "json_object",
        "api_key": f"synthetic-{name}-key",
        "reasoning": [{"name": "low", "request_options": {"reasoning_effort": "low"}}],
        "default_reasoning": "low",
        **changes,
    }


def tender(actor: Actor, name: str) -> dict:
    """A project with a parsed, extracted and confirmed synthetic tender."""
    task = actor.data("POST", "/tasks", json={"name": name})["id"]
    document = actor.data(
        "POST",
        f"/tasks/{task}/documents",
        files={"file": ("synthetic-tender.pdf", labelled_pdf(TENDER_LINES), "application/pdf")},
    )["id"]
    jobs = {}
    for action in ("parse", "extract"):
        queued = actor.data("POST", f"/documents/{document}/{action}", json={})
        view = actor.job(queued["job_id"])
        assert view["status"] == "succeeded", view
        jobs[action] = queued["job_id"]
    extraction = jobs["extract"]
    requirements = actor.call(
        "GET", f"/tasks/{task}/requirements", params={"job": extraction}
    ).json()["items"]
    reviews = [actor.data("GET", f"/requirements/{row['id']}/review") for row in requirements]
    actor.call(
        "POST",
        f"/tasks/{task}/extractions/{extraction}/requirement-confirmations",
        json={
            "request_id": str(uuid4()),
            "expected_set_revision": reviews[-1]["scope"]["revision"],
            "items": [
                {
                    "requirement_id": review["requirement"]["requirement_id"],
                    "expected_revision": review["requirement"]["revision"],
                    "expected_review_hash": review["requirement"]["review_hash"],
                }
                for review in reviews
            ],
            "reviewed_each": True,
            "reason": "Inspected each synthetic source.",
        },
    )
    return {
        "task": task,
        "document": document,
        "extraction": extraction,
        "requirements": [row["id"] for row in requirements],
    }


def generate(actor: Actor, project: dict) -> list[dict]:
    queued = actor.data(
        "POST",
        f"/tasks/{project['task']}/cards/generations",
        json={"extraction_job_id": project["extraction"]},
    )
    view = actor.job(queued["job_id"])
    assert view["status"] == "succeeded", view
    return cards(actor, project)


def cards(actor: Actor, project: dict) -> list[dict]:
    return [
        slot["card"]
        for slot in actor.call(
            "GET", f"/tasks/{project['task']}/cards", params={"job": project["extraction"]}
        ).json()["items"]
        if slot.get("card")
    ]


def confirm(actor: Actor, card: dict) -> dict:
    pending = actor.data(
        "POST",
        f"/cards/{card['id']}/actions",
        json={"expected_revision": card["revision"], "action": "submit"},
    )
    extra: dict = {"reviewed_evidence_ids": [item["id"] for item in pending["evidence"]]}
    if pending["warning_codes"]:
        extra |= {
            "reviewed_warning_codes": pending["warning_codes"],
            "reason": "Reviewed the synthetic proposal.",
        }
    return actor.data(
        "POST",
        f"/cards/{card['id']}/actions",
        json={"expected_revision": pending["revision"], "action": "confirm", **extra},
    )


def package(actor: Actor, task: str) -> bytes:
    queued = actor.data("POST", "/packages/export", json={"task_id": task})
    view = actor.job(queued["id"], "/packages/{}")
    assert view["status"] == "succeeded", view
    return actor.call("GET", f"/packages/{queued['id']}/file").content


def bring_in(actor: Actor, content: bytes) -> dict:
    queued = actor.data(
        "POST",
        "/packages/import",
        files={"file": ("project.bidpkg.zip", content, "application/zip")},
    )
    return actor.job(queued["id"], "/packages/{}")


def start_agent(actor: Actor, project: dict, scopes=AGENT_SCOPES, expect=200):
    return actor.call(
        "POST",
        f"/v4/tasks/{project['task']}/agent-sessions",
        expect=expect,
        json={
            "extraction_job_id": project["extraction"],
            "message": "Prepare proposals, pause, then assemble.",
            "requested_scopes": list(scopes),
            "idempotency_key": str(uuid4()),
            "limits": {},
        },
    )


def agent_state(stack: Stack, session_id: str) -> str:
    [state] = stack.sql(f"SELECT state FROM agent_sessions WHERE id = '{session_id}'")
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True, help="unix socket of an isolated test Docker")
    parser.add_argument("--port", type=int, default=48963)
    parser.add_argument("--compose-plugins", default="/opt/homebrew/lib/docker/cli-plugins")
    parser.add_argument(
        "--keep", action="store_true", help="keep the projects and volumes after a failure"
    )
    parser.add_argument("--reuse-image", action="store_true", help="skip building bid-gateway:new")
    args = parser.parse_args()
    if not args.host.startswith("unix://"):
        raise SystemExit("Use an explicit local Docker socket")
    run = Run(args)
    gateway = Stack(run, "gateway", args.port, "enterprise")
    personal = Stack(run, "personal-a", args.port + 10, "personal")
    returned = Stack(run, "personal-b", args.port + 20, "personal")
    try:
        context = run.root / "image"
        context.mkdir()
        (context / "vendor.py").write_bytes((HERE / "vendor.py").read_bytes())
        vendor_certificate(context)
        (context / "Dockerfile").write_text(TEST_IMAGE)
        print("building images; log:", run.root / "commands.log", flush=True)
        if not args.reuse_image:
            run.run(
                ["docker", "build", "-f", "deploy/Dockerfile", "-t", "bid-gateway:new"]
                + [str(REPOSITORY)]
            )
        run.run(["docker", "build", "-t", "bid-e2e:test", str(context)])
        for path in context.glob("vendor.*"):
            path.unlink()
        exercise(run, gateway, personal, returned)
        gateway.down()
        logs = "".join(stack.collected for stack in (gateway, personal, returned))
        leaked = sum(value in logs for value in run.secrets)
        run.report["container_logs"] = {"lines": len(logs.splitlines()), "secrets_found": leaked}
        assert leaked == 0, f"{leaked} secrets in container logs"
        report_text = json.dumps(run.report)
        assert not any(value in report_text for value in run.secrets), "a secret in the report"
        run.report["passed"] = True
        run.save()
    finally:
        if not args.keep:
            for stack in (gateway, personal, returned):
                stack.down()
        run.log.close()
    print("report:", run.root / "report.json")


def exercise(run: Run, gateway: Stack, personal: Stack, returned: Stack):
    gateway.up()
    created = run.run(
        [*gateway.compose, "run", "--rm", "--no-deps", "-T", "server", "python", "-m"]
        + ["app.admin", "member", "create", "--role", "admin", "--login", "root.admin"],
        capture=True,
    ).stdout
    admin = sign_in(
        run,
        gateway,
        "admin",
        "root.admin",
        json.loads(created.splitlines()[-1])["initial_password"],
    )

    def member(name: str, login: str, grants: list[str]) -> tuple[str, Actor]:
        made = admin.data(
            "POST", "/members", json={"login": login, "display_name": name, "grants": grants}
        )
        return made["member"]["id"], sign_in(run, gateway, name, login, made["initial_password"])

    # 1. Members, a grant and a quota.
    run.begin(1, "administrator creates A and B, grants A library edit, sets B's quota", gateway)
    a_id, a = member("A", "member.a", ["library:edit", "memory:edit"])
    b_id, b = member("B", "member.b", [])
    quota = admin.data("PUT", f"/members/{b_id}/quota", json={"monthly_company_tokens": 200000})
    company = admin.data("POST", "/providers", json=model("company-model"))
    shown = {row["id"]: row for row in admin.call("GET", "/members").json()["items"]}
    run.check(
        "A holds library:edit; B's quota is set; the company model is the default",
        "library:edit" in shown[a_id]["grants"]
        and quota["monthly_company_tokens"] == 200000
        and company["owner"] == "company"
        and company["is_default"],
        {"a_grants": shown[a_id]["grants"], "b_quota": quota["monthly_company_tokens"]},
    )
    run.end()

    # 2. A's library, memory and project.
    run.begin(
        2, "A adds a certificate and memory, extracts with the company model, adds B", gateway
    )
    certificate = a.data(
        "POST",
        "/resources/certificates",
        json={
            "data": {
                "kind": "qualification",
                "name": "Synthetic quality certificate",
                "number": "QMS-E2E-2026",
                "valid_from": "2026-01-01",
                "valid_until": "2027-12-31",
            }
        },
    )["certificate_id"]

    def memory(scope: str, key: str, words: str) -> str:
        made = a.data(
            "POST",
            "/memories",
            json={
                "target": {"scope": scope},
                "content": {"kind": "rule", "conflict_key": key, "text": words, "tags": []},
            },
        )["memory"]
        a.call(
            "POST",
            f"/memories/{made['id']}/decisions",
            json={"expected_revision": 1, "action": "approve", "reason": "Synthetic review"},
        )
        return made["id"]

    public_memory = memory("library", "e2e.public", "Synthetic public response wording")
    private_memory = memory("private", "e2e.private", "Synthetic private response wording")
    a_model = a.data(
        "POST", "/providers", json={**model("a-personal-model", owner="personal"), "new": True}
    )
    project = tender(a, "合成企业验收项目")
    extracted = [call for call in gateway.calls() if call["kind"] == "extract"]
    a.call("POST", f"/tasks/{project['task']}/members", json={"member_ids": [b_id]})
    roles = {
        row["member_id"]: row["role"]
        for row in a.call("GET", f"/tasks/{project['task']}/members").json()["items"]
    }
    run.check(
        "extraction ran on the company model; B joined A's project",
        {call["model"] for call in extracted} == {"company-model"}
        and roles == {a_id: "owner", b_id: "member"},
        {"extract_models": sorted({c["model"] for c in extracted}), "roles": roles},
    )
    run.end()

    # 3. B's view of the library and drafting with B's own model.
    run.begin(
        3, "B sees shared library, drafts with a personal model, not A's private data", gateway
    )
    b_model = b.data(
        "POST", "/providers", json={**model("b-personal-model", owner="personal"), "new": True}
    )
    b.call("PUT", "/account/default-model", json={"slot": b_model["slot"]})
    certificates = [
        row["certificate_id"] for row in b.call("GET", "/resources/certificates").json()["items"]
    ]
    seen = {
        "certificate": certificate in certificates,
        "public_memory": code(b.call("GET", f"/memories/{public_memory}", expect=None)),
        "private_memory": code(b.call("GET", f"/memories/{private_memory}", expect=None)),
        "models": sorted(row["owner"] for row in b.call("GET", "/providers").json()["items"]),
    }
    seen["a_model_listed"] = a_model["slot"] in {
        row["slot"] for row in b.call("GET", "/providers").json()["items"]
    }
    before = len(gateway.calls())
    drafted = generate(b, project)
    drafting = [call["model"] for call in gateway.calls()[before:] if call["kind"] == "draft"]
    run.check(
        "B sees the certificate and public memory, not A's private memory or model;"
        " B's drafting used B's personal model",
        seen
        == {
            "certificate": True,
            "public_memory": [200, None],
            "private_memory": [404, "not_found"],
            "models": ["company", "personal"],
            "a_model_listed": False,
        }
        and drafting
        and set(drafting) == {"b-personal-model"}
        and len(drafted) == len(project["requirements"]),
        {**seen, "drafting_models": drafting, "cards": len(drafted)},
    )
    run.end()

    # 4. A non-member's route sweep, with A's agent among the swept objects.
    run.begin(4, "non-member C gets not_found on every project route", gateway)
    _, c = member("C", "member.c", ["library:edit", "memory:edit", "confidential:reveal"])
    a_agent = start_agent(a, project).json()["data"]["session"]["id"]
    objects = {
        "task_id": project["task"],
        "document_id": project["document"],
        "job_id": project["extraction"],
        "requirement_id": project["requirements"][0],
        "session_id": a_agent,
    }
    coverage = asyncio.run(outsider_sweep(gateway, c.header, objects))
    statuses = sorted({line.split(" ", 2)[2] for line in coverage["routes"]})
    (run.root / "route-sweep.json").write_text(json.dumps(coverage, indent=2))
    run.step["requests"].append(  # type: ignore[index]
        {"actor": "C", "sweep": coverage["compared"], "answers": statuses}
    )
    run.check(
        "every project route answers C as for a random identifier, never 2xx",
        coverage["compared"] > 100,
        {"compared": coverage["compared"], "answers": statuses},
    )
    run.end()

    # 5. B confirms; A's agent cannot.
    run.begin(5, "B confirms a card and is recorded; A's agent cannot confirm", gateway)
    card = next(row for row in drafted if row["state"] == "draft")
    confirmed = confirm(b, card)
    [recorded] = gateway.sql(
        "SELECT actor_member_id FROM response_card_revisions"
        f" WHERE card_id = '{card['id']}' AND confirmed_at IS NOT NULL"
    )
    refused = start_agent(a, project, [*AGENT_SCOPES, "evidence:confirm"], expect=None)
    for _ in range(60):
        if agent_state(gateway, a_agent) not in {"queued", "running"}:
            break
        time.sleep(2)
    [agent_confirmations] = gateway.sql(
        "SELECT count(*) FROM response_card_revisions WHERE actor_kind = 'agent'"
        " AND confirmed_at IS NOT NULL"
    )
    run.check(
        "the confirmation records B; an agent asking for a confirm scope is refused;"
        " A's agent confirmed nothing",
        confirmed["state"] == "confirmed"
        and recorded == b_id
        and code(refused)[0] == 422
        and agent_confirmations == "0",
        {
            "card_state": confirmed["state"],
            "recorded_member_is_b": recorded == b_id,
            "agent_with_confirm_scope": code(refused),
            "a_agent_state": agent_state(gateway, a_agent),
            "agent_confirmations": int(agent_confirmations),
        },
    )
    run.end()

    # 6. Personal → gateway → personal.
    run.begin(6, "personal package imported by A, exported by B after sharing", gateway)
    personal.up()
    owner = personal_actor(run, personal, "personal A")
    owner.data("POST", "/providers", json=model("personal-install-model"))
    source_project = tender(owner, "合成个人项目")
    for row in generate(owner, source_project):
        confirm(owner, row)
    assembled = owner.data(
        "POST",
        f"/tasks/{source_project['task']}/drafts",
        json={"extraction_job_id": source_project["extraction"]},
    )
    assert owner.job(assembled["job_id"])["status"] == "succeeded"
    source = package(owner, source_project["task"])
    kept = decisions(source)
    personal.down()
    imported = bring_in(a, source)
    task = source_project["task"]
    gateway_reviews = a.call("GET", f"/tasks/{task}/requirements").json()["items"]
    before_sharing = code(a.call("POST", "/packages/export", json={"task_id": task}, expect=None))
    admin.call("PATCH", f"/tasks/{task}/sharing", json={"export_to_personal_allowed": True})
    a.call("POST", f"/tasks/{task}/members", json={"member_ids": [b_id]})
    back = package(b, task)
    returned.up()
    receiver = personal_actor(run, returned, "personal B")
    landed = bring_in(receiver, back)
    again = package(receiver, task)
    changed = {
        name
        for name, digest in table_hashes(back).items()
        if digest != table_hashes(source).get(name)
    }
    run.check(
        "A's import keeps every confirmation and citation; export waits for the administrator;"
        " B's package lands unchanged in a second personal installation",
        imported["status"] == "succeeded"
        and len(gateway_reviews) == len(source_project["requirements"])
        and before_sharing == [403, "package_export_not_allowed"]
        and decisions(back) == kept
        and changed == {"tasks"}
        and landed["status"] == "succeeded"
        and table_hashes(again) == table_hashes(source),
        {
            "import": imported["status"],
            "confirmed_requirements": len(kept["requirement_reviews"]),
            "confirmed_cards": len(kept["card_confirmations"]),
            "cited_chunks": len(kept["cited_chunks"]),
            "export_before_sharing": before_sharing,
            "gateway_package_tables_changed": sorted(changed),
            "second_personal_import": landed["status"],
            "round_trip_identical": table_hashes(again) == table_hashes(source),
        },
    )
    run.end()
    returned.down()

    # 7. Disabling B.
    run.begin(7, "disabling B ends B's session, token and running agent", gateway)
    token = b.data(
        "POST",
        "/tokens",
        json={
            "name": "Synthetic e2e token",
            "scopes": ["task:read"],
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )["token"]
    run.secrets.append(token)
    token_header = {"Authorization": "Bearer " + token}
    b.call("GET", "/tasks", header=token_header)
    gateway.vendor("touch", "/tmp/hold")
    held_before = len(gateway.calls())
    b_agent = start_agent(b, project).json()["data"]["session"]["id"]
    for _ in range(120):
        if any(
            call["kind"] == "decision" and call["held"] for call in gateway.calls()[held_before:]
        ):
            break
        time.sleep(1)
    in_flight = agent_state(gateway, b_agent)
    admin.call("PATCH", f"/members/{b_id}", json={"status": "disabled"})
    after = {
        "session": code(b.call("GET", "/tasks", expect=None)),
        "token": code(b.call("GET", "/tasks", header=token_header, expect=None)),
    }
    gateway.vendor("rm", "-f", "/tmp/hold")
    for _ in range(90):
        if agent_state(gateway, b_agent) == "cancelled":
            break
        time.sleep(2)
    after["agent"] = agent_state(gateway, b_agent)
    [reason] = gateway.sql(
        "SELECT details->>'reason' FROM audit_logs WHERE action = 'agent.session.cancelled'"
        f" AND object_id = '{b_agent}'"
    )
    run.check(
        "after B is disabled the session and token are refused and the running agent is cancelled",
        in_flight == "running"
        and after
        == {
            "session": [401, "invalid_session"],
            "token": [401, "invalid_token"],
            "agent": "cancelled",
        }
        and reason == "member_removed",
        {"agent_before": in_flight, **after, "cancel_reason": reason},
    )
    run.end()


async def outsider_sweep(stack: Stack, header: dict, objects: dict) -> dict:
    from app.api.main import create_app
    from app.core.config import Settings

    # Only the route table is read; this application never starts or connects.
    settings = Settings(
        data_dir=REPOSITORY / "data" / "work" / "enterprise-e2e" / "routes",
        database_url="postgresql+psycopg://unused@localhost/unused",
        deployment="enterprise",
        public_url="https://localhost",
        company_name="unused",
        access_key=secrets.token_urlsafe(32),
        encryption_key=Fernet.generate_key().decode(),
        token_key=Fernet.generate_key().decode(),
        secrets_key=Fernet.generate_key().decode(),
    )
    application = create_app(settings)
    async with httpx.AsyncClient(
        base_url=f"https://localhost:{stack.port}", verify=stack.context, timeout=60
    ) as client:
        return await sweep(client, application, header, objects)


if __name__ == "__main__":
    main()
