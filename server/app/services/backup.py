"""Installation backups: a database dump and a file storage snapshot, verified by restore.

A backup is a directory `bid-backup-<UTC time>` holding `database.dump`
(`pg_dump -Fc`), `files/` (stored objects as written, still encrypted) and
`manifest.json` with the dump's sha256, the migration revisions, the row count of
every table and each file's size and sha256. Row counts are read in the snapshot
the dump uses, so a later restore must reproduce them exactly. Only the operator
runs these, with the migration owner's connection; nothing here is reachable
from request handling or the worker.
"""

import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from app.core.errors import ServiceError

FORMAT = "bid-backup/1"
PREFIX = "bid-backup-"
STAMP = "%Y%m%dT%H%M%SZ"
COUNTED_SCHEMAS = ("public", "procrastinate")


class BackupError(ServiceError):
    """A refusal with what the operator needs to see, never row content or secrets."""

    def __init__(self, code: str, message: str, status=422, exit_code=4, details=None):
        super().__init__(code, message, status, exit_code)
        self.details = details or {}


def failure(code: str, message: str, details: dict | None = None) -> BackupError:
    return BackupError(code, message, details=details)


def tool(name: str) -> str:
    """A PostgreSQL client from BID_PG_BIN, else PATH; it must match the server's major."""
    directory = os.environ.get("BID_PG_BIN")
    path = str(Path(directory) / name) if directory else shutil.which(name)
    if not path or not Path(path).is_file():
        raise BackupError("pg_tools_missing", f"{name} is not installed", 503, 3)
    return path


def libpq(url: URL) -> tuple[list[str], dict[str, str]]:
    """Connection arguments without the password, which goes in PGPASSWORD."""
    environment = dict(os.environ)
    if url.password:
        environment["PGPASSWORD"] = str(url.password)
    host = url.query.get("host") or url.host
    port = url.query.get("port") or url.port
    arguments = ["--username", str(url.username)]
    if host:
        arguments += ["--host", str(host)]
    if port:
        arguments += ["--port", str(port)]
    return arguments, environment


def run(arguments: list[str], environment: dict[str, str]) -> None:
    result = subprocess.run(arguments, env=environment, capture_output=True, text=True)
    if result.returncode != 0:
        # Client errors name objects, never row content; keep the tail for the operator.
        raise BackupError(
            "pg_tool_failed",
            f"{Path(arguments[0]).name} failed",
            503,
            3,
            details={"stderr": result.stderr.strip().splitlines()[-3:]},
        )


def counts(connection) -> dict[str, int]:
    tables = connection.execute(
        text(
            "SELECT schemaname, tablename FROM pg_tables WHERE schemaname = ANY(:schemas)"
            " ORDER BY schemaname, tablename"
        ),
        {"schemas": list(COUNTED_SCHEMAS)},
    ).all()
    return {
        f"{schema}.{table}": connection.scalar(
            text(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(schema, table)).as_string()
            )
        )
        for schema, table in tables
    }


def revisions(connection) -> list[str]:
    return sorted(connection.scalars(text("SELECT version_num FROM public.alembic_version")))


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(block)
    return hasher.hexdigest()


def snapshot_local(root: Path, target: Path) -> None:
    root = root.resolve()
    for path in sorted(root.rglob("*")) if root.is_dir() else []:
        if not path.is_file() or path.name.endswith(".tmp"):
            continue
        destination = target / path.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(path, destination)


def snapshot_s3(settings, target: Path) -> None:
    from app.providers.storage import S3Storage

    storage = S3Storage(settings)
    pages = storage.client.get_paginator("list_objects_v2").paginate(Bucket=storage.bucket)
    for page in pages:
        for item in page.get("Contents", []):
            destination = target / item["Key"]
            if not destination.resolve().is_relative_to(target.resolve()):
                raise failure("backup_object_key_invalid", "An object key leaves the snapshot")
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            body = storage.client.get_object(Bucket=storage.bucket, Key=item["Key"])["Body"]
            with destination.open("wb") as handle:
                shutil.copyfileobj(body, handle)


def file_list(root: Path) -> list[dict]:
    return [
        {
            "key": path.relative_to(root).as_posix(),
            "size": path.stat().st_size,
            "sha256": digest(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def prune(directory: Path, keep_days: int, now: datetime, newest: Path) -> list[str]:
    """Remove finished backups older than keep_days and any unfinished one, never the newest."""
    removed = []
    for path in sorted(directory.glob(PREFIX + "*")):
        if path == newest or not path.is_dir():
            continue
        name = path.name.removeprefix(PREFIX)
        try:
            taken = datetime.strptime(name.removesuffix(".partial"), STAMP).replace(tzinfo=UTC)
        except ValueError:
            continue
        if name.endswith(".partial") or now - taken > timedelta(days=keep_days):
            shutil.rmtree(path)
            removed.append(path.name)
    return removed


def backup(settings, directory: Path, keep_days: int = 14) -> dict:
    url = make_url(os.environ["BID_MIGRATION_DATABASE_URL"])
    now = datetime.now(UTC)
    if settings.storage != "s3" and directory.resolve().is_relative_to(settings.data_dir.resolve()):
        raise failure("backup_inside_storage", "Write backups outside the data directory")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    final = directory / (PREFIX + now.strftime(STAMP))
    partial = final.with_name(final.name + ".partial")
    if final.exists() or partial.exists():
        raise failure("backup_exists", "A backup for this second already exists")
    partial.mkdir(mode=0o700)
    arguments, environment = libpq(url)
    engine = create_engine(url, hide_parameters=True, isolation_level="REPEATABLE READ")
    try:
        with engine.connect() as connection:
            # Counts and the dump read one snapshot, held open until pg_dump has it.
            snapshot = connection.scalar(text("SELECT pg_export_snapshot()"))
            tables = counts(connection)
            current = revisions(connection)
            run(
                [
                    tool("pg_dump"),
                    *arguments,
                    "--dbname",
                    str(url.database),
                    "--format=custom",
                    f"--snapshot={snapshot}",
                    "--file",
                    str(partial / "database.dump"),
                ],
                environment,
            )
            connection.rollback()
        files = partial / "files"
        files.mkdir(mode=0o700)
        if settings.storage == "s3":
            snapshot_s3(settings, files)
        else:
            snapshot_local(settings.data_dir, files)
        manifest = {
            "format": FORMAT,
            "created_at": now.isoformat(),
            "deployment": settings.deployment,
            "storage": settings.storage,
            "revisions": current,
            "database": {"sha256": digest(partial / "database.dump"), "tables": tables},
            "files": file_list(files),
        }
        (partial / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        partial.rename(final)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    finally:
        engine.dispose()
    return {
        "backup": str(final),
        "tables": len(tables),
        "rows": sum(tables.values()),
        "files": len(manifest["files"]),
        "revisions": current,
        "pruned": prune(directory, keep_days, now, final),
    }


def verify(path: Path) -> dict:
    """Restore a backup into a scratch database, compare it with the manifest, drop it."""
    try:
        manifest = json.loads((path / "manifest.json").read_text())
    except (OSError, ValueError) as exc:
        raise failure("backup_manifest_invalid", "The backup has no readable manifest") from exc
    if manifest.get("format") != FORMAT:
        raise failure("backup_format_unsupported", "Unknown backup format")
    problems: dict[str, list] = {}
    dump = path / "database.dump"
    if not dump.is_file() or digest(dump) != manifest["database"]["sha256"]:
        raise failure("backup_dump_altered", "The database dump does not match its manifest")
    files = path / "files"
    expected = {item["key"]: item for item in manifest["files"]}
    found = {item["key"]: item for item in file_list(files)} if files.is_dir() else {}
    if missing := sorted(set(expected) - set(found)):
        problems["missing_files"] = missing
    if altered := sorted(key for key in set(expected) & set(found) if expected[key] != found[key]):
        problems["altered_files"] = altered
    if extra := sorted(set(found) - set(expected)):
        problems["unlisted_files"] = extra

    url = make_url(os.environ["BID_MIGRATION_DATABASE_URL"])
    scratch = "bid_verify_" + uuid4().hex[:12]
    maintenance = create_engine(url, hide_parameters=True, isolation_level="AUTOCOMMIT")
    arguments, environment = libpq(url)
    try:
        with maintenance.connect() as connection:
            connection.execute(
                text(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(scratch)).as_string())
            )
        try:
            # Owners and grants name cluster roles; the scratch copy only proves content.
            run(
                [
                    tool("pg_restore"),
                    *arguments,
                    "--dbname",
                    scratch,
                    "--no-owner",
                    "--no-privileges",
                    "--exit-on-error",
                    str(dump),
                ],
                environment,
            )
            restored = create_engine(url.set(database=scratch), hide_parameters=True)
            try:
                with restored.connect() as connection:
                    tables = counts(connection)
                    current = revisions(connection)
            finally:
                restored.dispose()
        finally:
            with maintenance.connect() as connection:
                connection.execute(
                    text(
                        sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)")
                        .format(sql.Identifier(scratch))
                        .as_string()
                    )
                )
    finally:
        maintenance.dispose()
    recorded = manifest["database"]["tables"]
    if differing := sorted(
        name for name in set(recorded) | set(tables) if recorded.get(name) != tables.get(name)
    ):
        problems["row_counts"] = [
            {"table": name, "manifest": recorded.get(name), "restored": tables.get(name)}
            for name in differing
        ]
    if current != manifest["revisions"]:
        problems["revisions"] = [manifest["revisions"], current]
    report = {
        "backup": str(path),
        "tables": len(tables),
        "rows": sum(tables.values()),
        "files": len(found),
        "revisions": current,
    }
    if problems:
        raise failure("backup_verify_failed", "The restored backup differs", {**report, **problems})
    return {**report, "verified": True}
