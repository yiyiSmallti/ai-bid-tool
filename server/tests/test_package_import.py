"""Project package import (`bid-package/1`) end to end.

Failure matrix before implementation: an altered file, an altered row, a row of
another task, a citation of a chunk the package lacks or a reference to a row it
lacks is written in part before the import fails; a sealed column stays readable
only under the exporting installation's key; a repeat of the same package
imports twice; a package whose project already exists overwrites it; a gateway
member without `library:edit` creates library items; the imported project is
visible to a member who did not import it; a confirmation, decision or run
changes on the way in, so the project exported again differs from the package;
the guards admit a write outside the import's own job; the CLI cannot import
with a `bid_` token. The packages and report land in data/work/package-import.
"""

import asyncio
import hashlib
import io
import json
import os
import zipfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from app.api.main import create_app
from app.core.config import Settings
from app.services import packages
from bid_cli import main as cli
from conftest import FakeQueue
from cryptography.fernet import Fernet
from sqlalchemy import text
from test_check import (  # noqa: F401
    LiveCheckClient,
    check_case,
    invoke_live_cli,
    preview_check,
    process_check,
    submit_check,
)
from test_members import GATEWAY, bootstrap_admin, code
from test_package_export import CONFIDENTIAL, entries, export
from test_project_visibility import new_member, signed_in

ARTIFACTS = Path(__file__).parents[2] / "data" / "work" / "package-import"


def wipe(engine, data_dir: Path) -> None:
    """Empty every table and the data directory's place, as a fresh installation starts."""
    data_dir.mkdir(parents=True, exist_ok=True)
    with engine.begin() as connection:
        tables = connection.scalars(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                " AND tablename <> 'alembic_version'"
            )
        ).all()
        connection.exec_driver_sql(
            "TRUNCATE " + ", ".join(f'public."{name}"' for name in tables) + " CASCADE"
        )


@asynccontextmanager
async def installation(engine, data_dir: Path, monkeypatch, *, enterprise: bool):
    """A new installation on the emptied database, with its own secrets key."""
    wipe(engine, data_dir)
    monkeypatch.setenv("BID_SECRETS_KEY", Fernet.generate_key().decode())
    if enterprise:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO public.installation (mode, company_name)"
                    " VALUES ('enterprise', 'Synthetic Company')"
                )
            )
        for name, value in GATEWAY.items():
            monkeypatch.setenv(name, value)
    application = create_app(Settings(data_dir=data_dir), queue=FakeQueue())
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client,
    ):
        yield client, application


def repack(content: bytes, change) -> bytes:
    """The package with `change(files)` applied, its manifest hashes made to match."""
    files = entries(content)
    change(files)
    manifest = json.loads(files["manifest.json"])
    for table in manifest["tables"]:
        body = files[f"data/{table['name']}.jsonl"]
        table["sha256"] = hashlib.sha256(body).hexdigest()
        table["rows"] = len(body.splitlines())
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])
    return buffer.getvalue()


def edit_rows(table: str, edit):
    def change(files):
        name = f"data/{table}.jsonl"
        rows = [json.loads(line) for line in files[name].splitlines()]
        edit(rows)
        files[name] = packages.jsonl(rows)

    return change


async def submit(api, header, content: bytes, name="project.zip"):
    return await api.post(
        "/packages/import", headers=header, files={"file": (name, content, "application/zip")}
    )


async def run(api, app, header, content: bytes) -> dict:
    started = await submit(api, header, content)
    assert started.status_code == 200, started.text
    job = started.json()["data"]
    if job["status"] == "queued":
        try:
            await app.state.processor(job["id"])
        except Exception:
            pass
    view = await api.get(f"/packages/{job['id']}", headers=header)
    assert view.status_code == 200, view.text
    return view.json()["data"]


async def tasks_here(app) -> int:
    async with app.state.db.transaction() as session:
        return await session.scalar(text("SELECT count(*) FROM public.tasks"))


def table_hashes(content: bytes) -> dict[str, str]:
    manifest = json.loads(entries(content)["manifest.json"])
    return {table["name"]: table["sha256"] for table in manifest["tables"]}


async def test_package_import_round_trip(check_case, database, tmp_path, monkeypatch):  # noqa: F811
    case = check_case
    api, app, header, task = case["api"], case["app"], case["header"], case["task"]
    report: dict = {"rerun": "pytest -q server/tests/test_package_import.py -p no:cacheprovider"}

    # The source project: a confidential value, a finished check run.
    field = await api.post(
        "/confidential-fields",
        headers=header,
        json={"key": "package_value", "label": "Synthetic value", "kind": "other", "scope": "task"},
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
    _, exported_source = await export(api, app, header, task)
    # A card names the value, which a package never carries.
    package = repack(
        exported_source,
        edit_rows(
            "response_card_revisions",
            lambda rows: rows[0].update(
                response_text=rows[0]["response_text"] + " {{secret.package_value}}"
            ),
        ),
    )
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / "personal.zip").write_bytes(package)
    data = {
        name[5:-6]: [json.loads(line) for line in body.splitlines()]
        for name, body in entries(package).items()
        if name.startswith("data/")
    }

    # A second personal installation with another secrets key.
    async with installation(database, tmp_path / "second", monkeypatch, enterprise=False) as (
        second,
        second_app,
    ):
        session = await second.post(
            "/auth/session", json={"access_key": os.environ["BID_ACCESS_KEY"]}
        )
        assert session.status_code == 200, session.text
        owner = {"Authorization": "Bearer " + session.json()["data"]["session"]}

        def altered_file(files):
            key = next(name for name in files if name.startswith("files/"))
            files[key] = files[key] + b"tampered"

        def altered_row(files):
            edit_rows("requirements", lambda rows: rows[0].update(page=rows[0]["page"] + 1))(files)

        chunk = data["chunks"][0]["id"]
        foreign = "00000000-0000-4000-8000-000000000001"
        refused = {}
        refused["altered_file"] = code(
            await submit(second, owner, repack_raw(package, altered_file))
        )
        refused["altered_row"] = code(await submit(second, owner, repack_raw(package, altered_row)))
        for name, change in {
            "dangling_citation": edit_rows(
                "response_items", lambda rows: rows[0]["source"].update(chunk_id=foreign)
            ),
            "dangling_reference": edit_rows(
                "response_cards", lambda rows: rows[0].update(requirement_id=foreign)
            ),
            "foreign_task_row": edit_rows(
                "requirements", lambda rows: rows[0].update(task_id=foreign)
            ),
        }.items():
            altered = repack(package, change)
            started = await submit(second, owner, altered)
            if started.status_code != 200:
                refused[name] = code(started)
                continue
            view = await run(second, second_app, owner, altered)
            refused[name] = [view["status"], (view["error"] or {}).get("code")]
        assert refused == {
            "altered_file": [422, "package_integrity_failed"],
            "altered_row": [422, "package_integrity_failed"],
            "dangling_citation": ["failed", "package_citation_unresolved"],
            "dangling_reference": ["failed", "package_reference_unresolved"],
            "foreign_task_row": [422, "package_integrity_failed"],
        }, refused
        assert chunk in json.dumps(data["requirements"])
        assert await tasks_here(second_app) == 0
        report["refused"] = refused

        imported = await run(second, second_app, owner, package)
        assert imported["status"] == "succeeded", imported
        result = imported["report"]
        assert result["task_id"] == task and result["source_mode"] == "personal"
        assert result["tables"] == {name: len(rows) for name, rows in data.items()}
        assert result["needs_value"] == ["package_value"], result
        report["import"] = {key: result[key] for key in ("tables", "files", "needs_value")}

        # A repeat returns the first import; another copy of the same project is refused.
        again = await submit(second, owner, package)
        assert again.status_code == 200 and again.json()["data"]["id"] == imported["id"]
        copy = rezip(package)
        assert copy != package
        duplicate = await run(second, second_app, owner, copy)
        report["existing_task"] = [duplicate["status"], duplicate["error"]["code"]]
        assert report["existing_task"] == ["failed", "package_task_exists"]
        assert await tasks_here(second_app) == 1

        # The imported project is the package: exporting it again yields the same rows,
        # its sealed columns read under this installation's key.
        _, exported = await export(second, second_app, owner, task)
        (ARTIFACTS / "reexported.zip").write_bytes(exported)
        assert table_hashes(exported) == table_hashes(package)
        assert entries(exported).keys() == entries(package).keys()
        async with second_app.state.db.transaction() as db:
            audit = await db.scalar(
                text(
                    "SELECT details FROM public.audit_logs"
                    " WHERE action = 'package.imported' AND object_id = :task"
                ),
                {"task": task},
            )
        assert audit["imported_from"]["source_mode"] == "personal", audit
        report["round_trip"] = {"tables": len(table_hashes(package)), "identical": True}

        # The CLI imports with a token; a repeat answers with the same job.
        expires = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        token = await second.post(
            "/tokens",
            headers=owner,
            json={
                "name": "importer",
                "scopes": ["task:create", "job:read", "package:import"],
                "expires_at": expires,
            },
        )
        assert token.status_code == 200, token.text
        monkeypatch.setenv("BID_ACCESS_KEY", token.json()["data"]["token"])
        cli_settings = Settings(data_dir=tmp_path / "second")
        monkeypatch.setattr(
            cli, "Client", lambda mode, server: LiveCheckClient(mode, server, cli_settings)
        )
        saved = tmp_path / "cli-package.zip"
        saved.write_bytes(package)
        exit_code, answer = await asyncio.to_thread(
            invoke_live_cli, ["package", "import", "--file", str(saved)]
        )
        assert exit_code == 0 and answer["data"]["id"] == imported["id"], answer
        report["cli"] = {"import": exit_code, "same_job": True}

    path = ARTIFACTS / "report.json"
    prior = json.loads(path.read_text()) if path.exists() else {}
    path.write_text(json.dumps({**prior, "personal": report}, indent=2, sort_keys=True) + "\n")


def rezip(content: bytes) -> bytes:
    """The same files in another archive, so another package digest."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in sorted(entries(content).items()):
            archive.writestr(name, body)
    return buffer.getvalue()


def repack_raw(content: bytes, change) -> bytes:
    """The package with `change(files)` applied and the manifest left as it was."""
    files = entries(content)
    change(files)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])
    return buffer.getvalue()


async def test_package_import_gateway(check_case, database, tmp_path, monkeypatch):  # noqa: F811
    case = check_case
    api, app, header, task = case["api"], case["app"], case["header"], case["task"]
    _, package = await export(api, app, header, task)
    library = [
        name
        for name in entries(package)
        if name.startswith("data/") and name[5:-6] in {"certificates", "products"}
    ]
    assert library, "the source project binds library items"

    async with installation(database, tmp_path / "gateway", monkeypatch, enterprise=True) as (
        gateway,
        gateway_app,
    ):
        root = bootstrap_admin("root.admin")
        admin = await signed_in(gateway, "root.admin", root["initial_password"])
        _, plain = await new_member(gateway, admin, "plain.member")
        _, editor = await new_member(gateway, admin, "library.editor", ["library:edit"])
        _, outsider = await new_member(gateway, admin, "outsider", ["library:edit"])
        report: dict = {}

        forbidden = await run(gateway, gateway_app, plain, package)
        report["without_library_edit"] = [forbidden["status"], forbidden["error"]["code"]]
        assert report["without_library_edit"] == ["failed", "package_library_forbidden"]
        assert await tasks_here(gateway_app) == 0

        imported = await run(gateway, gateway_app, editor, package)
        assert imported["status"] == "succeeded", imported
        async with gateway_app.state.db.transaction() as session:
            owners = (
                await session.execute(
                    text(
                        "SELECT m.login, t.role FROM public.task_members t"
                        " JOIN public.members m ON m.id = t.member_id WHERE t.task_id = :task"
                    ),
                    {"task": task},
                )
            ).all()
        assert [tuple(row) for row in owners] == [("library.editor", "owner")]
        report["owner"] = "library.editor"
        report["outsider_project"] = code(await gateway.get(f"/tasks/{task}", headers=outsider))
        report["outsider_job"] = code(
            await gateway.get(f"/packages/{imported['id']}", headers=outsider)
        )
        assert report["outsider_project"] == report["outsider_job"] == [404, "not_found"]
        requirements = await gateway.get(f"/tasks/{task}/requirements", headers=editor)
        assert requirements.status_code == 200 and requirements.json()["items"], requirements.text

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS / "report.json"
    prior = json.loads(path.read_text()) if path.exists() else {}
    path.write_text(json.dumps({**prior, "gateway": report}, indent=2, sort_keys=True) + "\n")
