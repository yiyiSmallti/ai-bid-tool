"""Project package export (`bid-package/1`) end to end.

Failure matrix before implementation: a row of another task or another task's file
enters the package; a key, token, provider credential or confidential value
appears in any byte of the archive or of an entry; a sealed column leaves as
ciphertext the importing server cannot read; a run input leaves the server; a
manifest hash or row count disagrees with its entry; a reference inside the
package points at a row the package does not carry; a stored file is missing or
differs from its hash; two exports of an unchanged task differ in data; a member
reads or downloads another member's package, or a token without
`package:export` starts one; a gateway project exports without
`export_to_personal_allowed`; the download is served before the job succeeds.
The archive and report land in data/work/package-export.
"""

import asyncio
import hashlib
import io
import json
import os
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.services import packages
from sqlalchemy import text
from app.core.config import Settings
from bid_cli import main as cli
from test_check import (  # noqa: F401
    LiveCheckClient,
    check_case,
    invoke_live_cli,
    preview_check,
    process_check,
    submit_check,
)
from test_members import bootstrap_admin, code
from test_project_visibility import gateway, new_member, signed_in  # noqa: F401
from test_response_cards import create_tender

CONFIDENTIAL = "SYNTHETIC-PACKAGE-CONFIDENTIAL-7731"
ARTIFACTS = Path(__file__).parents[2] / "data" / "work" / "package-export"


async def export(api, app, header, task) -> tuple[dict, bytes]:
    started = await api.post("/packages/export", headers=header, json={"task_id": task})
    assert started.status_code == 200, started.text
    job = started.json()["data"]
    assert job["status"] == "queued"
    early = await api.get(f"/packages/{job['id']}/file", headers=header)
    assert early.status_code == 409 and early.json()["data"]["error"]["code"] == "package_not_ready"
    await app.state.processor(job["id"])
    status = await api.get(f"/packages/{job['id']}", headers=header)
    assert status.status_code == 200, status.text
    view = status.json()["data"]
    assert view["status"] == "succeeded", view
    download = await api.get(f"/packages/{job['id']}/file", headers=header)
    assert download.status_code == 200, download.text
    assert download.headers["content-type"] == "application/zip"
    return view, download.content


def entries(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def secret_values(app, extra: list[str]) -> list[str]:
    settings = app.state.processor.settings
    values = list(extra)
    for name in type(settings).model_fields:
        value = getattr(settings, name)
        for item in value if isinstance(value, list) else [value]:
            if hasattr(item, "get_secret_value") and item.get_secret_value():
                values.append(item.get_secret_value())
    url = settings.database_url.get_secret_value()
    if "@" in url and ":" in url.split("@")[0].split("//")[-1]:
        values.append(url.split("@")[0].split(":")[-1])
    values.append(os.environ["BID_ACCESS_KEY"])
    return [value for value in values if len(value) >= 8]


async def verify(app, content: bytes, task: str, secrets: list[str]) -> dict:
    files = entries(content)
    manifest = json.loads(files["manifest.json"])
    assert manifest["format"] == "bid-package/1" and manifest["task"]["id"] == task
    data = {}
    for table in manifest["tables"]:
        body = files[f"data/{table['name']}.jsonl"]
        assert hashlib.sha256(body).hexdigest() == table["sha256"], table["name"]
        data[table["name"]] = [json.loads(line) for line in body.splitlines()]
        assert len(data[table["name"]]) == table["rows"], table["name"]
    assert set(files) == {
        "manifest.json",
        *(f"data/{table['name']}.jsonl" for table in manifest["tables"]),
        *(f"files/{item['sha256']}" for item in manifest["files"]),
    }
    for item in manifest["files"]:
        body = files[f"files/{item['sha256']}"]
        assert hashlib.sha256(body).hexdigest() == item["sha256"] and len(body) == item["size"]

    # Only this task's rows; every stored file a row names is carried.
    for table, rows in data.items():
        for row in rows:
            if "task_id" in row and table != "tasks":
                assert row["task_id"] == task, table
            column = packages.STORED.get(table)
            if column and row.get(column):
                assert row[column] in {item["key"] for item in manifest["files"]}, table
    assert [row["id"] for row in data["tasks"]] == [task]

    # Every reference inside the package resolves inside the package.
    async with app.state.db.transaction() as session:
        foreign_keys, _ = await packages.schema(session)
    dangling = []
    for key in foreign_keys:
        if key["source"] not in data:
            continue
        present = {
            tuple(row.get(c) for c in key["referenced"]) for row in data.get(key["target"], [])
        }
        for row in data[key["source"]]:
            values = tuple(row.get(c) for c in key["columns"])
            if None not in values and values not in present:
                dangling.append((key["source"], key["target"], key["columns"]))
    assert dangling == []

    # Nothing installation-bound, sealed or secret leaves the server.
    for table, rows in data.items():
        for row in rows:
            for column in packages.SCRUBBED:
                assert row.get(column) is None, (table, column)
            for value in row.values():
                assert not (isinstance(value, str) and value.startswith("gAAAA")), table
    for table, column in packages.RUN_INPUTS:
        for row in data.get(table, []):
            assert row[column] == packages.OMITTED_INPUT, table
    for row in data["jobs"]:
        assert row["kind"] in packages.EXPORTED_JOB_KINDS
        assert "encrypted_input" not in json.dumps(row["result"])
    leaks = sorted(
        {
            f"{name}:{index}"
            for index, value in enumerate(secrets)
            for name, body in [("archive", content), *files.items()]
            if value.encode() in body
        }
    )
    assert leaks == []
    return {"manifest": manifest, "data": data}


async def test_package_export_personal(check_case, database, tmp_path, monkeypatch):  # noqa: F811
    case = check_case
    api, app, header, task = case["api"], case["app"], case["header"], case["task"]
    report: dict = {
        "rerun": "pytest -q server/tests/test_package_export.py -p no:cacheprovider",
    }

    # A confidential value, a finished check run and a token are on the task.
    field = await api.post(
        "/confidential-fields",
        headers=header,
        json={
            "key": "package_value",
            "label": "Synthetic package value",
            "kind": "other",
            "scope": "task",
        },
    )
    assert field.status_code == 200, field.text
    value = await api.post(
        f"/confidential-fields/{field.json()['data']['id']}/values",
        headers=header,
        json={"value": CONFIDENTIAL, "task_id": task},
    )
    assert value.status_code == 200, value.text
    submitted = await submit_check(case, await preview_check(case))
    assert submitted.status_code == 200, submitted.text
    checked = await process_check(case, submitted.json()["data"])
    assert checked["data"]["status"] == "succeeded", checked
    expires = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    limited = await api.post(
        "/tokens",
        headers=header,
        json={"name": "read only", "scopes": ["task:read", "job:read"], "expires_at": expires},
    )
    assert limited.status_code == 200, limited.text
    limited_secret = limited.json()["data"]["token"]
    exporter = await api.post(
        "/tokens",
        headers=header,
        json={
            "name": "exporter",
            "scopes": ["task:read", "job:read", "package:export"],
            "expires_at": expires,
        },
    )
    assert exporter.status_code == 200, exporter.text
    exporter_secret = exporter.json()["data"]["token"]

    # Another task, whose rows and files must stay out.
    other, *_ = await create_tender(api, app, header, tmp_path, suffix="other")

    refused = await api.post(
        "/packages/export",
        headers={"Authorization": f"Bearer {limited_secret}"},
        json={"task_id": task},
    )
    report["token_without_scope"] = code(refused)
    assert report["token_without_scope"] == [403, "forbidden"]

    view, content = await export(api, app, header, task)
    secrets = secret_values(app, [CONFIDENTIAL, limited_secret, exporter_secret])
    checked_package = await verify(app, content, task, secrets)
    manifest = checked_package["manifest"]
    assert other not in json.dumps(checked_package["data"])
    assert manifest["source_mode"] == "personal"
    assert view["report"]["tables"] == {t["name"]: t["rows"] for t in manifest["tables"]}
    for table in ("documents", "chunks", "requirements", "response_cards", "draft_runs"):
        assert view["report"]["tables"].get(table, 0) > 0, table
    assert view["report"]["tables"].get("check_runs") == 1
    assert view["report"]["tables"].get("certificate_files", 0) > 0

    # A token holding the scope exports the same data; the package is the token's.
    token_view, again = await export(api, app, {"Authorization": f"Bearer {exporter_secret}"}, task)
    same = await verify(app, again, task, secrets)
    assert [t["sha256"] for t in same["manifest"]["tables"]] == [
        t["sha256"] for t in manifest["tables"]
    ]
    assert same["manifest"]["files"] == manifest["files"]
    # One person owns a personal installation: a token with the scope reads the
    # session's package, a token without it does not.
    shown = await api.get(
        f"/packages/{view['id']}", headers={"Authorization": f"Bearer {exporter_secret}"}
    )
    assert shown.status_code == 200 and token_view["id"] != view["id"]
    report["token_without_scope_status"] = code(
        await api.get(
            f"/packages/{view['id']}/file", headers={"Authorization": f"Bearer {limited_secret}"}
        )
    )
    assert report["token_without_scope_status"] == [403, "forbidden"]

    # The CLI exports, reads the status and saves the package with a token.
    monkeypatch.setenv("BID_ACCESS_KEY", exporter_secret)
    cli_settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(
        cli, "Client", lambda mode, server: LiveCheckClient(mode, server, cli_settings)
    )
    exit_code, started = await asyncio.to_thread(
        invoke_live_cli, ["package", "export", "--task", task]
    )
    assert exit_code == 0 and started["data"]["status"] == "queued", started
    saved = tmp_path / "cli-package.zip"
    exit_code, early = await asyncio.to_thread(
        invoke_live_cli,
        ["package", "download", "--job", started["data"]["id"], "--output", str(saved)],
    )
    assert exit_code == 4 and early["data"]["error"]["code"] == "package_not_ready", early
    await app.state.processor(started["data"]["id"])
    exit_code, status = await asyncio.to_thread(
        invoke_live_cli, ["package", "status", "--job", started["data"]["id"]]
    )
    assert exit_code == 0 and status["data"]["status"] == "succeeded", status
    exit_code, receipt = await asyncio.to_thread(
        invoke_live_cli,
        ["package", "download", "--job", started["data"]["id"], "--output", str(saved)],
    )
    assert exit_code == 0 and receipt["data"]["sha256"] == status["data"]["report"]["sha256"]
    await verify(app, saved.read_bytes(), task, secrets)
    report["cli"] = {
        "export": 0,
        "status": status["data"]["status"],
        "download": receipt["data"]["size"],
    }

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / "personal.zip").write_bytes(content)
    report["personal"] = {
        "sha256": view["report"]["sha256"],
        "tables": view["report"]["tables"],
        "files": view["report"]["files"],
        "omitted_rows": manifest["omitted_rows"],
        "cleared": manifest["cleared"],
        "secrets_scanned": len(secrets),
        "deterministic_data": True,
    }
    path = ARTIFACTS / "report.json"
    prior = json.loads(path.read_text()) if path.exists() else {}
    path.write_text(json.dumps({**prior, **report}, indent=2, sort_keys=True) + "\n")


async def test_package_export_gateway_policy(gateway, tmp_path):  # noqa: F811
    api, app = gateway
    root = bootstrap_admin("root.admin")
    admin = await signed_in(api, "root.admin", root["initial_password"])
    _, owner = await new_member(api, admin, "owner.one")
    _, outsider = await new_member(api, admin, "outsider")
    task, *_ = await create_tender(api, app, owner, tmp_path)

    blocked = await api.post("/packages/export", headers=owner, json={"task_id": task})
    report = {"blocked": code(blocked)}
    assert report["blocked"] == [403, "package_export_not_allowed"]
    report["outsider"] = code(
        await api.post("/packages/export", headers=outsider, json={"task_id": task})
    )
    assert report["outsider"] == [404, "not_found"]
    allowed = await api.patch(
        f"/tasks/{task}/sharing", headers=admin, json={"export_to_personal_allowed": True}
    )
    assert allowed.status_code == 200, allowed.text

    view, content = await export(api, app, owner, task)
    async with app.state.db.transaction() as session:
        member_rows = await session.scalar(
            text("SELECT count(*) FROM public.task_members WHERE task_id = :t"), {"t": task}
        )
    assert member_rows == 1
    checked = await verify(app, content, task, secret_values(app, []))
    assert checked["manifest"]["source_mode"] == "enterprise"
    assert "task_members" not in checked["data"]
    report["outsider_status"] = code(await api.get(f"/packages/{view['id']}", headers=outsider))
    report["outsider_file"] = code(await api.get(f"/packages/{view['id']}/file", headers=outsider))
    assert report["outsider_status"] == report["outsider_file"] == [404, "not_found"]

    # Withdrawing the permission stops downloads of packages already built.
    withdrawn = await api.patch(
        f"/tasks/{task}/sharing", headers=admin, json={"export_to_personal_allowed": False}
    )
    assert withdrawn.status_code == 200, withdrawn.text
    report["withdrawn_file"] = code(await api.get(f"/packages/{view['id']}/file", headers=owner))
    assert report["withdrawn_file"] == [403, "package_export_not_allowed"]

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS / "report.json"
    prior = json.loads(path.read_text()) if path.exists() else {}
    path.write_text(json.dumps({**prior, "gateway": report}, indent=2, sort_keys=True) + "\n")
