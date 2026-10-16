"""Installation backup and `restore --verify` through the operator CLI.

Failure matrix before implementation: the dump and the recorded row counts come
from different moments, so a faithful restore fails verification; an altered
dump, an altered, missing or unlisted file or an edited row count verifies; a
backup written inside the data directory copies itself; a failed backup leaves
a directory that later counts as finished; retention removes the newest backup
or keeps one older than 14 days; verification leaves its scratch database
behind, also after a failure; the migration password reaches the output or the
manifest. The backups and report land in data/work/backup.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from test_check import check_case, preview_check, process_check, submit_check  # noqa: F401

ROOT = Path(__file__).parents[2]
ARTIFACTS = ROOT / "data" / "work" / "backup"


def admin(*arguments: str, data_dir: Path) -> tuple[int, dict, str]:
    environment = {
        **os.environ,
        "BID_MIGRATION_DATABASE_URL": os.environ["BID_TEST_ADMIN_URL"],
        "BID_DATA_DIR": str(data_dir),
    }
    result = subprocess.run(
        [sys.executable, "-m", "app.admin", *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    output = result.stdout if result.returncode == 0 else result.stderr
    lines = [line for line in output.splitlines() if line.startswith("{")]
    return result.returncode, json.loads(lines[-1]) if lines else {}, result.stdout + result.stderr


def scratch_databases(engine) -> list[str]:
    with engine.connect() as connection:
        return list(
            connection.scalars(
                text("SELECT datname FROM pg_database WHERE datname LIKE 'bid_verify_%'")
            )
        )


def copy(backup: Path, name: str, change) -> Path:
    altered = backup.parent / name
    shutil.copytree(backup, altered)
    change(altered)
    return altered


async def test_backup_and_verify(check_case, database, tmp_path):  # noqa: F811
    case = check_case
    app = case["app"]
    submitted = await submit_check(case, await preview_check(case))
    assert submitted.status_code == 200, submitted.text
    checked = await process_check(case, submitted.json()["data"])
    assert checked["data"]["status"] == "succeeded", checked
    await asyncio.to_thread(
        exercise, database, Path(app.state.processor.settings.data_dir), tmp_path
    )


def exercise(database, data_dir: Path, tmp_path: Path) -> None:
    assert any(path.is_file() for path in Path(data_dir).rglob("*")), "the case stores files"
    password = str(database.url.password or "")
    report: dict = {"rerun": "pytest -q server/tests/test_backup.py -p no:cacheprovider"}
    leftovers = scratch_databases(database)

    inside = admin("backup", "--dir", str(Path(data_dir) / "backups"), data_dir=data_dir)
    report["inside_data_dir"] = [inside[0], inside[1].get("error")]
    assert report["inside_data_dir"] == [4, "backup_inside_storage"], inside[2]
    assert not list((Path(data_dir) / "backups").glob("bid-backup-*"))

    # The application's data directory is tmp_path itself.
    backups = tmp_path.parent / (tmp_path.name + "-backups")
    backups.mkdir()
    now = datetime.now(UTC)
    old = backups / ("bid-backup-" + (now - timedelta(days=15)).strftime("%Y%m%dT%H%M%SZ"))
    recent = backups / ("bid-backup-" + (now - timedelta(days=13)).strftime("%Y%m%dT%H%M%SZ"))
    unfinished = backups / ("bid-backup-" + now.strftime("%Y%m%dT000000Z") + ".partial")
    for path in (old, recent, unfinished):
        path.mkdir()

    code, taken, output = admin("backup", "--dir", str(backups), data_dir=data_dir)
    assert code == 0, output
    backup = Path(taken["backup"])
    assert sorted(taken["pruned"]) == sorted([old.name, unfinished.name]), taken
    assert sorted(path.name for path in backups.iterdir()) == sorted([recent.name, backup.name])
    manifest = json.loads((backup / "manifest.json").read_text())
    with database.connect() as connection:
        live = {
            name: connection.scalar(text(f"SELECT count(*) FROM {name}"))
            for name in manifest["database"]["tables"]
        }
    assert manifest["database"]["tables"] == live
    assert live["public.tasks"] == 1 and live["public.check_runs"] == 1, live
    assert manifest["files"] and taken["files"] == len(manifest["files"])
    report["backup"] = {key: taken[key] for key in ("tables", "rows", "files")}

    code, verified, output = admin("restore", "--verify", str(backup), data_dir=data_dir)
    assert code == 0 and verified["verified"], output
    assert verified["rows"] == taken["rows"] and verified["files"] == taken["files"], verified
    report["verify"] = {key: verified[key] for key in ("tables", "rows", "files", "verified")}

    def altered_dump(path: Path):
        with (path / "database.dump").open("ab") as handle:
            handle.write(b"tampered")

    def altered_file(path: Path):
        target = path / "files" / manifest["files"][0]["key"]
        target.write_bytes(target.read_bytes() + b"tampered")

    def missing_file(path: Path):
        (path / "files" / manifest["files"][0]["key"]).unlink()

    def unlisted_file(path: Path):
        (path / "files" / "unlisted.bin").write_bytes(b"synthetic")

    def edited_count(path: Path):
        edited = json.loads((path / "manifest.json").read_text())
        edited["database"]["tables"]["public.tasks"] += 1
        (path / "manifest.json").write_text(json.dumps(edited))

    refused = {}
    for name, change in {
        "altered_dump": altered_dump,
        "altered_file": altered_file,
        "missing_file": missing_file,
        "unlisted_file": unlisted_file,
        "edited_count": edited_count,
    }.items():
        code, answer, output = admin(
            "restore", "--verify", str(copy(backup, name, change)), data_dir=data_dir
        )
        problems = sorted(
            key
            for key in ("altered_files", "missing_files", "unlisted_files", "row_counts")
            if key in answer
        )
        refused[name] = [code, answer.get("error"), *problems]
        assert not password or password not in output
    assert refused == {
        "altered_dump": [4, "backup_dump_altered"],
        "altered_file": [4, "backup_verify_failed", "altered_files"],
        "missing_file": [4, "backup_verify_failed", "missing_files"],
        "unlisted_file": [4, "backup_verify_failed", "unlisted_files"],
        "edited_count": [4, "backup_verify_failed", "row_counts"],
    }, refused
    report["refused"] = refused
    assert scratch_databases(database) == leftovers, "verification drops its scratch database"
    if password:
        assert password not in (backup / "manifest.json").read_text()
        assert password not in json.dumps([taken, verified])

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (ARTIFACTS / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
