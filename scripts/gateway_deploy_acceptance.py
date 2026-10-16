"""Slice 6 acceptance: fresh gateway install, backup, restore verify and one upgrade.

Runs deploy/gateway against an isolated Docker daemon only (an explicit unix
socket) in a new directory under data/work. An image built from --from-ref is
installed fresh, bootstrapped and given synthetic data over HTTPS through Caddy;
deploy/gateway/upgrade.sh then moves it to an image of this checkout, and the
data, sign-in and a backup with `restore --verify` are checked afterwards.

Failure matrix before implementation: the API or database is reachable without
Caddy; the access key works over HTTPS; the first administrator's one-time
password reaches a log; the upgrade migrates the live database without a
verified backup or rehearsal, or leaves the rehearsal database or its roles
behind; members, projects or documents change across the upgrade; the upgraded
gateway serves the old contract; a backup of the upgraded gateway does not
verify; a secret appears in any container log. report.json is written after
each check; no password, session or key is written anywhere.
"""

import argparse
import io
import json
import os
import secrets
import ssl
import subprocess
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pymupdf
from cryptography.fernet import Fernet

REPOSITORY = Path(__file__).resolve().parents[1]
PASSWORD = "synthetic-gateway-password-2026"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True, help="unix socket of an isolated test Docker")
    parser.add_argument("--from-ref", default="6b0a3ea", help="git ref of the installed release")
    parser.add_argument("--port", type=int, default=48943)
    parser.add_argument("--compose-plugins", default="/opt/homebrew/lib/docker/cli-plugins")
    parser.add_argument("--keep", action="store_true", help="keep the project and volumes")
    args = parser.parse_args()
    if not args.host.startswith("unix://"):
        raise SystemExit("Use an explicit local Docker socket")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    root = REPOSITORY / "data" / "work" / "gateway-deploy" / stamp
    root.mkdir(mode=0o700, parents=True)
    report: dict = {"from_ref": args.from_ref, "synthetic_only": True, "checks": []}

    def checked(name: str, **details):
        report["checks"].append({"check": name, **details})
        (root / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print("passed:", name, flush=True)

    values = {
        "BID_IMAGE": "bid-gateway:old",
        "BID_COMPANY_NAME": "合成测试公司",
        "BID_GATEWAY_HOST": "localhost",
        "BID_PUBLIC_URL": f"https://localhost:{args.port}",
        "BID_TLS": "internal",
        "BID_HTTPS_BIND": f"127.0.0.1:{args.port}",
        "BID_HTTP_BIND": f"127.0.0.1:{args.port + 1}",
        "BID_OWNER_PASSWORD": secrets.token_hex(24),
        "BID_DATABASE_PASSWORD": secrets.token_hex(24),
        "BID_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "BID_TOKEN_KEY": Fernet.generate_key().decode(),
        "BID_SECRETS_KEY": Fernet.generate_key().decode(),
        "BID_ACCESS_KEY": secrets.token_urlsafe(32),
        "BID_STORAGE": "local",
    }
    env_file = root / "gateway.env"
    with os.fdopen(os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
        handle.write("".join(f"{name}={value}\n" for name, value in values.items()))
    docker_config = root / "docker"
    docker_config.mkdir(mode=0o700)
    (docker_config / "config.json").write_text(
        json.dumps({"cliPluginsExtraDirs": [args.compose_plugins]})
    )
    project = "bid-gw-" + stamp.lower()
    environment = {
        **{k: v for k, v in os.environ.items() if not k.startswith("BID_")},
        "DOCKER_HOST": args.host,
        "DOCKER_CONFIG": str(docker_config),
        "COMPOSE_PROJECT_NAME": project,
        "BID_GATEWAY_ENV": str(env_file),
    }
    gateway = REPOSITORY / "deploy" / "gateway"
    compose = ["docker", "compose", "-f", str(gateway / "compose.yml"), "--env-file", str(env_file)]
    log = (root / "commands.log").open("w")

    def run(arguments, capture=False, check=True, **extra):
        result = subprocess.run(
            arguments,
            env={**environment, **extra},
            stdout=subprocess.PIPE if capture else log,
            stderr=log,
            text=True,
        )
        if check and result.returncode != 0:
            raise RuntimeError(f"{' '.join(arguments[:4])} exited {result.returncode}")
        return result

    secrets_seen = [values[name] for name in values if "PASSWORD" in name or "KEY" in name]
    try:
        tree = root / "release"
        tree.mkdir()
        archive = subprocess.run(
            ["git", "-C", str(REPOSITORY), "archive", args.from_ref],
            check=True,
            capture_output=True,
        ).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as handle:
            handle.extractall(tree, filter="data")
        print("building images; log:", root / "commands.log", flush=True)
        run(["docker", "build", "-f", "deploy/Dockerfile", "-t", "bid-gateway:old", str(tree)])
        run(
            ["docker", "build", "-f", "deploy/Dockerfile", "-t", "bid-gateway:new", str(REPOSITORY)]
        )
        checked("images built", installed=args.from_ref, upgrade="working tree")

        run([*compose, "up", "-d"])
        published = run([*compose, "ps", "--format", "json"], capture=True).stdout.strip()
        services = (
            json.loads(published)
            if published.startswith("[")
            else [json.loads(line) for line in published.splitlines()]
        )
        ports = sorted(
            {
                f"{item['Service']}:{port['PublishedPort']}"
                for item in services
                for port in item.get("Publishers") or []
                if port.get("PublishedPort")
            }
        )
        assert ports == sorted([f"caddy:{args.port}", f"caddy:{args.port + 1}"]), ports
        checked("fresh install started; only Caddy publishes ports", ports=ports)

        for _ in range(30):
            authority = run(
                [
                    *compose,
                    "exec",
                    "-T",
                    "caddy",
                    "cat",
                    "/data/caddy/pki/authorities/local/root.crt",
                ],
                capture=True,
                check=False,
            ).stdout
            if authority:
                break
            time.sleep(2)
        else:
            raise RuntimeError("Caddy did not create its local authority")
        context = ssl.create_default_context(cadata=authority)
        base = values["BID_PUBLIC_URL"]
        http = httpx.Client(base_url=base, verify=context, timeout=30)
        for _ in range(90):
            try:
                if http.get("/health").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(2)
        else:
            raise RuntimeError("the gateway did not answer over HTTPS")
        installation = http.get("/installation").json()["data"]
        assert installation["mode"] == "enterprise", installation
        refused = http.post("/auth/session", json={"access_key": values["BID_ACCESS_KEY"]})
        assert refused.json()["data"]["error"]["code"] == "access_key_not_accepted", refused.text
        checked("HTTPS through Caddy; access key refused", installed=installation)

        created = run(
            [
                *compose,
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "server",
                "python",
                "-m",
                "app.admin",
                "member",
                "create",
                "--role",
                "admin",
                "--login",
                "root.admin",
            ],
            capture=True,
        ).stdout
        initial = json.loads(created.strip().splitlines()[-1])["initial_password"]
        secrets_seen.append(initial)

        def sign_in(login: str, password: str) -> dict:
            response = http.post("/auth/session", json={"login": login, "password": password})
            assert response.status_code == 200, response.text
            return {"Authorization": "Bearer " + response.json()["data"]["session"]}

        admin = sign_in("root.admin", initial)
        changed = http.post(
            "/account/password", headers=admin, json={"current": initial, "new": PASSWORD}
        )
        assert changed.status_code == 200, changed.text
        member = http.post(
            "/members",
            headers=admin,
            json={"login": "synthetic.member", "display_name": "合成成员", "grants": []},
        )
        assert member.status_code == 200, member.text
        secrets_seen.append(member.json()["data"]["initial_password"])
        task = http.post(
            "/tasks",
            headers=admin,
            json={"name": "合成部署验收项目", "tender_number": "SYN-GW-2026"},
        )
        assert task.status_code == 200, task.text
        task_id = task.json()["data"]["id"]
        document = pymupdf.open()
        document.new_page().insert_text((72, 72), "Synthetic tender requirement for deployment.")
        upload = http.post(
            f"/tasks/{task_id}/documents",
            headers=admin,
            files={"file": ("synthetic.pdf", document.tobytes(), "application/pdf")},
        )
        assert upload.status_code == 200, upload.text
        before = {
            "members": len(http.get("/members", headers=admin).json()["items"]),
            "tasks": len(http.get("/tasks", headers=admin).json()["items"]),
            "documents": len(
                http.get(f"/tasks/{task_id}/documents", headers=admin).json()["items"]
            ),
        }
        checked("bootstrap and synthetic data", counts=before)

        def revisions() -> list[str]:
            query = "SELECT version_num FROM alembic_version"
            return run(
                [
                    *compose,
                    "exec",
                    "-T",
                    "postgres",
                    "psql",
                    "-U",
                    "bid_owner",
                    "-d",
                    "bid",
                    "-Atc",
                    query,
                ],
                capture=True,
            ).stdout.split()

        installed_revisions = revisions()
        started = time.monotonic()
        upgrade = run([str(gateway / "upgrade.sh"), "bid-gateway:new"], check=False)
        assert upgrade.returncode == 0, f"upgrade exited {upgrade.returncode}"
        databases = run(
            [
                *compose,
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "bid_owner",
                "-d",
                "postgres",
                "-Atc",
                "SELECT datname FROM pg_database WHERE datname LIKE 'bid_%' ORDER BY 1",
            ],
            capture=True,
        ).stdout.split()
        assert databases == [], databases
        assert "BID_IMAGE=bid-gateway:new" in env_file.read_text().splitlines()
        upgraded_revisions = revisions()
        assert upgraded_revisions != installed_revisions, upgraded_revisions
        checked(
            "upgrade.sh backed up, verified, rehearsed and migrated",
            seconds=round(time.monotonic() - started),
            leftover_databases=databases,
            revisions=[installed_revisions, upgraded_revisions],
        )

        for _ in range(90):
            try:
                if http.get("/health").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(2)
        upgraded = http.get("/installation").json()["data"]
        admin = sign_in("root.admin", PASSWORD)
        after = {
            "members": len(http.get("/members", headers=admin).json()["items"]),
            "tasks": len(http.get("/tasks", headers=admin).json()["items"]),
            "documents": len(
                http.get(f"/tasks/{task_id}/documents", headers=admin).json()["items"]
            ),
        }
        assert after == before, (before, after)
        packages = http.post("/packages/import", headers=admin)
        assert packages.status_code != 404, "the upgraded gateway serves package import"
        checked("data and sign-in survive the upgrade", installed=upgraded, counts=after)

        taken = run([*compose, "run", "--rm", "-T", "backup"], capture=True).stdout
        backup = json.loads(taken.strip().splitlines()[-1])
        verified = run(
            [
                *compose,
                "run",
                "--rm",
                "-T",
                "backup",
                "python",
                "-m",
                "app.admin",
                "restore",
                "--verify",
                backup["backup"],
            ],
            capture=True,
        ).stdout
        verify = json.loads(verified.strip().splitlines()[-1])
        assert verify["verified"] and verify["rows"] == backup["rows"], verify
        checked(
            "backup and restore --verify of the upgraded gateway",
            backup={k: backup[k] for k in ("tables", "rows", "files", "revisions")},
        )

        logs = run([*compose, "logs", "--no-color"], capture=True).stdout
        leaked = sum(value in logs for value in secrets_seen)
        assert leaked == 0, f"{leaked} secrets in container logs"
        checked("no password or key in container logs", lines=len(logs.splitlines()))
    finally:
        if not args.keep:
            run([*compose, "--profile", "ops", "down", "-v"], check=False)
            env_file.unlink()
        log.close()
    print("report:", root / "report.json")


if __name__ == "__main__":
    main()
