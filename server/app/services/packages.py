"""Project packages (`bid-package/1`): one tender project moved between installations.

A package holds the task's rows as JSON lines, read from the live schema, and the
stored files they name. Rows keep their identifiers; references to rows a package
does not carry are cleared or, when required, the row is left out and counted in
the manifest. See docs/plan/enterprise-gateway.md#packages.
"""

import hashlib
import io
import json
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.core.security import Secrets
from app.models.confidential import ConfidentialField, ConfidentialValue
from app.models.entities import Job, Task
from app.models.members import MemberGrant, PackageImport, TaskMember
from app.models.task_events import TaskEventHead
from app.providers.storage import Storage
from app.schemas.contracts import CONTRACT_VERSION
from app.services import redaction
from app.services.auth import Identity, set_actor_context
from app.services.versioned import audit

FORMAT = "bid-package/1"
PACKAGE_KINDS = {"package_export", "package_import"}
FINAL_STATES = ("succeeded", "failed", "cancelled")
EXPORTED_JOB_KINDS = (
    "parse",
    "extract",
    "card_generate",
    "draft",
    "check",
    "score",
    "score_rubric",
)

# Rows selected by their `task_id`.
TASK_TABLES = (
    "tasks",
    "documents",
    "chunks",
    "jobs",
    "requirements",
    "requirement_review_sets",
    "requirement_reviews",
    "requirement_review_events",
    "requirement_review_requests",
    "response_cards",
    "evidence",
    "evidence_sources",
    "draft_runs",
    "card_generation_runs",
    "check_runs",
    "check_items",
    "check_findings",
    "check_finding_citations",
    "check_decisions",
    "check_certificates",
    "check_certificate_items",
    "score_rubric_sets",
    "score_rubric_sections",
    "score_rubric_items",
    "score_rubric_classifications",
    "score_rubric_coverage",
    "score_rubric_coverage_decisions",
    "score_rubric_coverage_items",
    "score_rubric_decisions",
    "score_rubric_revision_events",
    "score_reports",
    "score_report_items",
    "score_report_item_responses",
    "score_item_citations",
    "task_certificates",
    "task_features",
    "task_org_profiles",
    "task_attachments",
    "task_templates",
    "task_resources",
)
# Task rows without a `task_id`, selected by their parent: (column, parent table).
TASK_CHILDREN = {
    "response_card_revisions": ("card_id", "response_cards"),
    "response_items": ("draft_id", "draft_runs"),
    "card_evidence_links": ("revision_id", "response_card_revisions"),
}
# Library rows the task's rows refer to, with every row those refer to in turn.
LIBRARY_TABLES = (
    "certificates",
    "certificate_revisions",
    "certificate_files",
    "certificate_file_parts",
    "products",
    "product_revisions",
    "features",
    "feature_revisions",
    "org_profiles",
    "org_profile_revisions",
    "profile_attachment_links",
    "attachment_archives",
    "attachment_revisions",
    "attachment_files",
    "attachment_file_parts",
    "attachment_reviews",
    "templates",
    "template_revisions",
)
SEARCHABLE = ("certificates", "products", "features", "org_profiles", "templates")
# Library files belong to the revision they were uploaded for.
LIBRARY_CHILDREN = {
    "certificate_files": ("certificate_revision_id", "certificate_revisions"),
    "certificate_file_parts": ("certificate_file_id", "certificate_files"),
    "attachment_files": ("attachment_revision_id", "attachment_revisions"),
    "attachment_file_parts": ("attachment_file_id", "attachment_files"),
}
TABLES = (*TASK_TABLES, *TASK_CHILDREN, *LIBRARY_TABLES)
# Rows a package never carries: screenshots are not part of a package.
SEED_FILTERS = {
    "jobs": "kind = ANY(:kinds) AND status = ANY(:final)",
    "evidence": "screenshot_asset_id IS NULL",
}
# Installation-bound references: members, tokens, provider configuration, agents, queues.
SCRUBBED = (
    "actor_member_id",
    "actor_token_id",
    "provider_config_id",
    "agent_principal_id",
    "agent_session_id",
    "agent_step_id",
    "invocation_id",
    "queue_id",
    "lease_until",
)
STORED = {
    "documents": "storage_key",
    "evidence_sources": "storage_key",
    "certificate_files": "storage_key",
    "certificate_file_parts": "storage_key",
    "attachment_files": "storage_key",
    "attachment_file_parts": "storage_key",
    "template_revisions": "storage_key",
}
# Encrypted at rest: carried as plaintext and encrypted again by the importing server.
SEALED = (
    ("attachment_revisions", "label_encrypted"),
    ("attachment_files", "upload_name_encrypted"),
    ("attachment_file_parts", "upload_name_encrypted"),
    ("requirement_reviews", "snapshot_ciphertext"),
    ("requirement_review_events", "snapshot_ciphertext"),
    ("requirement_review_events", "reason_ciphertext"),
    ("requirement_review_requests", "receipt_ciphertext"),
)
# A run's input is read only while its job runs and may carry memory context, so a
# package replaces it with this marker.
RUN_INPUTS = (
    ("check_runs", "encrypted_input"),
    ("score_rubric_sets", "encrypted_input"),
    ("score_reports", "encrypted_input"),
    ("card_generation_runs", "encrypted_input"),
)
OMITTED_INPUT = json.dumps({"omitted": FORMAT})
ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def worker(job: Job) -> Identity:
    submission = job.result["submission"]
    return Identity(
        scopes=set(submission["scopes"]),
        token_id=UUID(submission["actor_token_id"]) if submission["actor_token_id"] else None,
        actor_kind="worker",
    )


def require_exportable(settings, task: Task) -> None:
    if settings.deployment == "enterprise" and not task.export_to_personal_allowed:
        raise ServiceError(
            "package_export_not_allowed",
            "This project does not allow export to a personal installation",
            403,
            4,
        )


async def submit_export(session: AsyncSession, actor: Identity, task_id: UUID, settings) -> Job:
    from app.services.task_access import access

    task = await access(session, actor, task_id, "package:export")
    require_exportable(settings, task)
    await set_actor_context(session, actor)
    job = Job(
        id=uuid4(),
        task_id=task_id,
        kind="package_export",
        cache_key=uuid4().hex,
        status="queued",
        result={
            "submission": {
                "actor_token_id": str(actor.token_id) if actor.token_id else None,
                "scopes": sorted(actor.scopes),
            }
        },
    )
    session.add(job)
    await session.flush()
    audit(session, actor, "package.export", job.id, {"task_id": str(task_id)})
    return job


async def package_job(session: AsyncSession, actor: Identity, job_id: UUID) -> Job:
    job = await session.get(Job, job_id)
    if job is None or job.kind not in PACKAGE_KINDS:
        raise not_found()
    await job_access(session, actor, job)
    return job


async def job_access(session: AsyncSession, actor: Identity, job: Job) -> None:
    """On a gateway a package job is its member's; an export also needs the task."""
    if actor.member_id is not None and job.actor_member_id != actor.member_id:
        raise not_found()
    if job.kind == "package_export":
        from app.services.task_access import access

        assert job.task_id is not None
        await access(session, actor, job.task_id, "package:export", bind_context=False)
    else:
        from app.services.task_access import live_actor

        (await live_actor(session, actor)).require("package:import")


def job_view(job: Job) -> dict:
    report = job.result.get("report") or {}
    return {
        "id": str(job.id),
        "kind": job.kind,
        "status": job.status,
        "task_id": str(job.task_id) if job.task_id else report.get("task_id"),
        "error": job.error,
        "report": job.result.get("report"),
    }


async def download(
    session: AsyncSession, actor: Identity, job_id: UUID, storage: Storage, settings
) -> tuple[bytes, str]:
    job = await package_job(session, actor, job_id)
    if job.kind != "package_export" or job.status != "succeeded":
        raise ServiceError("package_not_ready", "The package is not ready", 409, 4)
    task = await session.get(Task, job.task_id)
    if task is None:
        raise not_found()
    require_exportable(settings, task)
    package = job.result["package"]
    content = await storage.read(package["storage_key"])
    if hashlib.sha256(content).hexdigest() != package["sha256"]:
        raise ServiceError("package_integrity_failed", "The stored package changed", 500, 4)
    audit(session, actor, "package.download", job.id, {"sha256": package["sha256"]})
    return content, f"bid-package-{job.task_id}.zip"


async def schema(session: AsyncSession) -> tuple[list[dict], dict[str, set[str]]]:
    """Foreign keys among and out of package tables, and each table's NOT NULL columns."""
    keys = await session.execute(
        text(
            """
SELECT source.relname AS source, target.relname AS target, c.condeferrable AS deferrable,
  ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY k(n, o)
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.n ORDER BY k.o)
    AS columns,
  ARRAY(SELECT a.attname FROM unnest(c.confkey) WITH ORDINALITY k(n, o)
        JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.n ORDER BY k.o)
    AS referenced
FROM pg_constraint c
JOIN pg_class source ON source.oid = c.conrelid
JOIN pg_namespace n ON n.oid = source.relnamespace AND n.nspname = 'public'
JOIN pg_class target ON target.oid = c.confrelid
WHERE c.contype = 'f' AND source.relname = ANY(:tables)
ORDER BY source.relname, c.conname
"""
        ),
        {"tables": list(TABLES)},
    )
    required = await session.execute(
        text(
            """
SELECT table_name, column_name FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = ANY(:tables) AND is_nullable = 'NO'
"""
        ),
        {"tables": list(TABLES)},
    )
    not_null: dict[str, set[str]] = defaultdict(set)
    for table, column in required:
        not_null[table].add(column)
    return [dict(row._mapping) for row in keys], not_null


def import_order(foreign_keys: list[dict]) -> list[str]:
    """Tables ordered so every immediate foreign key points to an earlier table."""
    after: dict[str, set[str]] = {table: set() for table in TABLES}
    for key in foreign_keys:
        if key["target"] in after and key["target"] != key["source"] and not key["deferrable"]:
            after[key["source"]].add(key["target"])
    ordered: list[str] = []
    while after:
        ready = sorted(table for table, needs in after.items() if not needs - set(ordered))
        if not ready:
            raise ServiceError("package_schema_cycle", "Package tables form a cycle", 500, 4)
        ordered.extend(ready)
        for table in ready:
            del after[table]
    return ordered


async def fetch(session: AsyncSession, table: str, where: str, params: dict) -> list[dict]:
    rows = await session.scalars(
        text(f'SELECT to_jsonb(t) FROM public."{table}" t WHERE {where}'), params
    )
    return list(rows)


async def fetch_keys(
    session: AsyncSession, table: str, columns: list[str], keys: list[dict]
) -> list[dict]:
    """Rows of `table` whose `columns` equal one of `keys`, compared in the column types."""
    row = ", ".join(f't."{column}"' for column in columns)
    key = ", ".join(f'k."{column}"' for column in columns)
    return await fetch(
        session,
        table,
        f"({row}) IN (SELECT {key} FROM jsonb_populate_recordset("
        f'NULL::public."{table}", CAST(:keys AS jsonb)) k)',
        {"keys": json.dumps(keys)},
    )


def key_of(row: dict, columns: list[str]) -> tuple | None:
    values = tuple(row.get(column) for column in columns)
    return None if any(value is None for value in values) else values


async def collect(
    session: AsyncSession, task_id: UUID, foreign_keys: list[dict], not_null: dict[str, set[str]]
) -> tuple[dict[str, dict[str, dict]], dict]:
    """The task's rows and the library rows they need, keyed by table then id."""
    rows: dict[str, dict[str, dict]] = {table: {} for table in TABLES}

    def add(table: str, found: list[dict]) -> int:
        added = 0
        for row in found:
            if row["id"] not in rows[table]:
                rows[table][row["id"]] = row
                added += 1
        return added

    params = {"task": task_id, "kinds": list(EXPORTED_JOB_KINDS), "final": list(FINAL_STATES)}
    add("tasks", await fetch(session, "tasks", "id = :task", params))
    for table in TASK_TABLES[1:]:
        where = " AND ".join(["task_id = :task", *filter(None, [SEED_FILTERS.get(table)])])
        add(table, await fetch(session, table, where, params))
    for table, (column, parent) in TASK_CHILDREN.items():
        add(
            table,
            await fetch(
                session,
                table,
                f'"{column}"::text = ANY(:ids)',
                {"ids": list(rows[parent])},
            ),
        )
    library = set(LIBRARY_TABLES)
    while True:
        added = 0
        for key in foreign_keys:
            if key["target"] not in library:
                continue
            wanted = {key_of(row, key["columns"]) for row in rows[key["source"]].values()}
            have = {key_of(row, key["referenced"]) for row in rows[key["target"]].values()}
            missing = [
                dict(zip(key["referenced"], values, strict=True))
                for values in sorted(wanted - have, key=str)
                if values is not None
            ]
            if missing:
                added += add(
                    key["target"],
                    await fetch_keys(session, key["target"], key["referenced"], missing),
                )
        for table, (column, parent) in LIBRARY_CHILDREN.items():
            added += add(
                table,
                await fetch(
                    session, table, f'"{column}"::text = ANY(:ids)', {"ids": list(rows[parent])}
                ),
            )
        if not added:
            break
    return rows, prune(rows, foreign_keys, not_null)


def prune(
    rows: dict[str, dict[str, dict]], foreign_keys: list[dict], not_null: dict[str, set[str]]
) -> dict:
    """Clear references to rows the package leaves out; leave out rows that require them."""
    omitted: dict[str, int] = defaultdict(int)
    cleared: dict[str, int] = defaultdict(int)
    changed = True
    while changed:
        changed = False
        for key in foreign_keys:
            source, target = key["source"], key["target"]
            present = (
                {key_of(row, key["referenced"]) for row in rows[target].values()}
                if target in rows
                else set()
            )
            for row_id, row in list(rows[source].items()):
                values = key_of(row, key["columns"])
                if values is None or values in present:
                    continue
                nullable = [c for c in key["columns"] if c not in not_null[source]]
                if not nullable:
                    del rows[source][row_id]
                    omitted[source] += 1
                else:
                    for column in nullable:
                        row[column] = None
                    cleared[f"{source}.{','.join(nullable)}"] += 1
                changed = True
    return {"omitted_rows": dict(sorted(omitted.items())), "cleared": dict(sorted(cleared.items()))}


def scrub(table: str, row: dict, secrets: Secrets) -> None:
    for column in SCRUBBED:
        if column in row:
            row[column] = None
    if table == "jobs":
        if row.get("initiated_by") in {"builtin_agent", "external_agent"}:
            row["initiated_by"] = "legacy_unknown"
        submission = row["result"].get("submission")
        if isinstance(submission, dict):
            submission.pop("encrypted_input", None)
            submission.pop("actor_token_id", None)
    for sealed_table, column in SEALED:
        if sealed_table == table and row.get(column) is not None:
            row[column] = secrets.decrypt(row[column])
    for input_table, column in RUN_INPUTS:
        if input_table == table and row.get(column) is not None:
            row[column] = OMITTED_INPUT


def jsonl(rows: list[dict]) -> bytes:
    return b"".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        for row in rows
    )


def entry(archive: zipfile.ZipFile, name: str, content: bytes) -> None:
    info = zipfile.ZipInfo(name, ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, content)


async def build(
    session: AsyncSession, task_id: UUID, storage: Storage, settings
) -> tuple[bytes, dict]:
    foreign_keys, not_null = await schema(session)
    rows, pruned = await collect(session, task_id, foreign_keys, not_null)
    secrets = Secrets.for_data(settings)
    files: dict[str, dict] = {}
    contents: dict[str, bytes] = {}
    tables = []
    data: dict[str, bytes] = {}
    for table in import_order(foreign_keys):
        ordered = sorted(rows[table].values(), key=lambda row: (row.get("created_at"), row["id"]))
        for row in ordered:
            scrub(table, row, secrets)
            column = STORED.get(table)
            if column and row.get(column) and row[column] not in files:
                content = await storage.read(row[column])
                digest = hashlib.sha256(content).hexdigest()
                files[row[column]] = {"key": row[column], "sha256": digest, "size": len(content)}
                contents[digest] = content
        if not ordered:
            continue
        data[table] = jsonl(ordered)
        tables.append(
            {
                "name": table,
                "rows": len(ordered),
                "sha256": hashlib.sha256(data[table]).hexdigest(),
            }
        )
    task = rows["tasks"][str(task_id)]
    manifest = {
        "format": FORMAT,
        "contract_version": CONTRACT_VERSION,
        "source_mode": settings.deployment,
        "created_at": datetime.now(UTC).isoformat(),
        "task": {"id": task["id"], "name": task["name"], "tender_number": task["tender_number"]},
        "files": sorted(files.values(), key=lambda item: item["key"]),
        "tables": tables,
        "sealed_columns": [f"{table}.{column}" for table, column in SEALED],
        "omitted_inputs": [f"{table}.{column}" for table, column in RUN_INPUTS],
        **pruned,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        entry(
            archive,
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode(),
        )
        for digest in sorted(contents):
            entry(archive, f"files/{digest}", contents[digest])
        for table, content in data.items():
            entry(archive, f"data/{table}.jsonl", content)
    return buffer.getvalue(), manifest


async def run_export(execution, storage: Storage) -> None:
    from app.services.task_access import access

    async with execution.db.transaction() as session:
        job = await execution.owned_job(session)
        assert job.task_id is not None
        task = await access(session, worker(job), job.task_id, "package:export")
        require_exportable(execution.settings, task)
        content, manifest = await build(session, job.task_id, storage, execution.settings)
    digest = hashlib.sha256(content).hexdigest()
    key = f"packages/{job.id}.zip"
    await storage.put(key, content)
    async with execution.db.transaction() as session:
        job = await execution.owned_job(session)
        job.status, job.error, job.finished_at = "succeeded", None, datetime.now(UTC)
        job.result = {
            **job.result,
            "package": {"storage_key": key, "sha256": digest, "size": len(content)},
            "report": {
                "sha256": digest,
                "size": len(content),
                "files": len(manifest["files"]),
                "tables": {table["name"]: table["rows"] for table in manifest["tables"]},
                "omitted_rows": manifest["omitted_rows"],
                "cleared": manifest["cleared"],
            },
        }


# Imports: the package is checked whole before any row is written, then written in
# one transaction under the import authority of ADR 0014.
IMPORT_KEY = "packages/imports/{}.zip"
# Uncompressed entries may not exceed this many upload limits together.
EXPANSION_LIMIT = 20


def integrity(message: str) -> ServiceError:
    return ServiceError("package_integrity_failed", message, 422, 4)


@dataclass
class Package:
    manifest: dict
    rows: dict[str, list[dict]]
    files: dict[str, bytes]

    @property
    def task_id(self) -> str:
        return self.manifest["task"]["id"]


def read_package(content: bytes, max_bytes: int) -> Package:
    """The package's manifest, rows and files, each checked against the manifest."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if sum(info.file_size for info in infos) > EXPANSION_LIMIT * max_bytes:
                raise integrity("The package expands beyond the size limit")
            entries = {info.filename: archive.read(info) for info in infos}
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, ValueError) as exc:
        raise integrity("The package is not a readable archive") from exc
    try:
        manifest = json.loads(entries["manifest.json"])
    except (KeyError, ValueError) as exc:
        raise integrity("The package manifest is missing or unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ServiceError("package_format_unsupported", f"Only {FORMAT} is read", 422, 4)
    version = str(manifest.get("contract_version", ""))
    if version.split(".")[0] != CONTRACT_VERSION.split(".")[0]:
        raise ServiceError(
            "package_contract_unsupported",
            f"Contract {version or 'unknown'} cannot be read by contract {CONTRACT_VERSION}",
            422,
            4,
        )
    try:
        tables = {table["name"]: table for table in manifest["tables"]}
        listed = {item["sha256"]: item for item in manifest["files"]}
        task_id = str(UUID(manifest["task"]["id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise integrity("The package manifest is incomplete") from exc
    expected = {
        "manifest.json",
        *(f"data/{name}.jsonl" for name in tables),
        *(f"files/{digest}" for digest in listed),
    }
    if set(entries) != expected or not set(tables) <= set(TABLES):
        raise integrity("The package entries differ from its manifest")
    rows: dict[str, list[dict]] = {}
    for name, table in tables.items():
        body = entries[f"data/{name}.jsonl"]
        if hashlib.sha256(body).hexdigest() != table.get("sha256"):
            raise integrity(f"Table {name} differs from its manifest hash")
        try:
            rows[name] = [json.loads(line) for line in body.splitlines()]
        except ValueError as exc:
            raise integrity(f"Table {name} is unreadable") from exc
        if len(rows[name]) != table.get("rows") or not all(
            isinstance(row, dict) and isinstance(row.get("id"), str) for row in rows[name]
        ):
            raise integrity(f"Table {name} differs from its manifest row count")
    files: dict[str, bytes] = {}
    for digest, item in listed.items():
        body = entries[f"files/{digest}"]
        if hashlib.sha256(body).hexdigest() != digest or len(body) != item.get("size"):
            raise integrity("A package file differs from its manifest hash")
        files[digest] = body
    if [row["id"] for row in rows.get("tasks", [])] != [task_id]:
        raise integrity("The package must hold exactly its own task")
    for name, found in rows.items():
        if name in TASK_TABLES[1:] and any(row.get("task_id") != task_id for row in found):
            raise integrity(f"Table {name} holds rows of another task")
        column = STORED.get(name)
        keys = {item["key"] for item in manifest["files"]}
        if column and any(row.get(column) and row[column] not in keys for row in found):
            raise integrity(f"Table {name} names a file the package does not carry")
    return Package(manifest, rows, files)


async def submit_import(
    session: AsyncSession, actor: Identity, content: bytes, storage: Storage, settings
) -> Job:
    from app.services.task_access import live_actor

    live = await live_actor(session, actor)
    live.require("task:create")
    live.require("package:import")
    digest = hashlib.sha256(content).hexdigest()
    previous = await session.scalar(
        select(PackageImport).where(
            PackageImport.package_sha256 == digest,
            PackageImport.member_id.is_not_distinct_from(live.member_id),
            PackageImport.state != "failed",
        )
    )
    if previous is not None:
        job = await session.get(Job, previous.job_id)
        assert job is not None
        return job
    read_package(content, settings.max_upload_bytes)
    await set_actor_context(session, live)
    job_id = uuid4()
    key = IMPORT_KEY.format(job_id)
    await storage.put(key, content)
    job = Job(
        id=job_id,
        task_id=None,
        kind="package_import",
        cache_key=uuid4().hex,
        status="queued",
        result={
            "submission": {
                "actor_token_id": str(live.token_id) if live.token_id else None,
                "scopes": sorted(live.scopes),
            },
            "package": {"storage_key": key, "sha256": digest, "size": len(content)},
        },
    )
    session.add(job)
    await session.flush()
    session.add(PackageImport(job_id=job.id, member_id=live.member_id, package_sha256=digest))
    await session.flush()
    audit(session, live, "package.import", job.id, {"sha256": digest})
    return job


def walk(value) -> list:
    """Every value nested in a row's JSON, for the citation and placeholder scans."""
    found = [value]
    for item in found:
        if isinstance(item, dict):
            found.extend(item.items())
        elif isinstance(item, list | tuple):
            found.extend(item)
    return found


def cited_chunks(rows: dict[str, list[dict]]) -> set[str]:
    cited = set()
    for found in rows.values():
        for item in walk(found):
            if isinstance(item, tuple) and len(item) == 2:
                key, value = item
                if key == "chunk_id" and isinstance(value, str):
                    cited.add(value)
                elif key == "chunk_ids" and isinstance(value, list):
                    cited.update(v for v in value if isinstance(v, str))
    return cited


def placeholders(rows: dict[str, list[dict]]) -> set[str]:
    return {
        key
        for found in rows.values()
        for item in walk(found)
        if isinstance(item, str)
        for key in redaction.secret_keys(item)
    }


async def existing(session: AsyncSession, table: str, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    found = await session.scalars(
        text(f'SELECT id::text FROM public."{table}" WHERE id::text = ANY(:ids)'), {"ids": ids}
    )
    return set(found)


async def generated_columns(session: AsyncSession) -> dict[str, set[str]]:
    result = await session.execute(
        text(
            """
SELECT c.relname, a.attname FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
WHERE c.relname = ANY(:tables) AND a.attgenerated <> '' AND a.attnum > 0
"""
        ),
        {"tables": list(TABLES)},
    )
    columns: dict[str, set[str]] = defaultdict(set)
    for table, column in result:
        columns[table].add(column)
    return columns


async def needs_value(session: AsyncSession, keys: set[str], task_id: str) -> list[str]:
    """Placeholders the project names that this installation cannot fill."""
    if not keys:
        return []
    filled = set(
        (
            await session.scalars(
                select(ConfidentialField.key)
                .join(ConfidentialValue, ConfidentialValue.field_id == ConfidentialField.id)
                .where(
                    ConfidentialField.key.in_(keys),
                    ConfidentialField.archived.is_(False),
                    ConfidentialValue.task_id.is_(None)
                    | (ConfidentialValue.task_id == UUID(task_id)),
                )
            )
        ).all()
    )
    return sorted(keys - filled)


def unresolved(rows: dict[str, list[dict]], foreign_keys: list[dict]) -> str | None:
    """The first reference that no package row satisfies; a package is closed, as export made it."""
    for key in foreign_keys:
        present = {key_of(row, key["referenced"]) for row in rows.get(key["target"], [])}
        for row in rows.get(key["source"], []):
            values = key_of(row, key["columns"])
            if values is not None and values not in present:
                return f"{key['source']}.{','.join(key['columns'])}"
    return None


def rejected(table: str, exc: DBAPIError) -> ServiceError:
    cause = getattr(exc.orig, "__cause__", None) or exc.orig
    message = str(getattr(cause, "message", None) or cause).splitlines()[0]
    return ServiceError("package_rejected", f"{table}: {message}", 422, 4)


async def write(
    session: AsyncSession, execution, job: Job, record: PackageImport, package: Package, storage
) -> dict:
    task_id = package.task_id
    if await session.get(Task, UUID(task_id)) is not None:
        raise ServiceError(
            "package_task_exists",
            "This project already exists here; delete it before importing again",
            409,
            4,
        )
    reused: dict[str, set[str]] = {}
    for table, found in package.rows.items():
        present = await existing(session, table, [row["id"] for row in found])
        if present and table not in LIBRARY_TABLES:
            raise ServiceError(
                "package_row_conflict", f"Rows of {table} already exist here", 409, 4
            )
        reused[table] = present
    created = sum(
        len(found) - len(reused[table])
        for table, found in package.rows.items()
        if table in LIBRARY_TABLES
    )
    if created and record.member_id is not None and execution.settings.deployment == "enterprise":
        from app.models.members import Member

        member = await session.get(Member, record.member_id)
        grant = await session.get(MemberGrant, (record.member_id, "library:edit"))
        if member is None or (member.role != "admin" and grant is None):
            raise ServiceError(
                "package_library_forbidden",
                "Creating library items needs the library:edit grant",
                403,
                4,
            )
    foreign_keys, _ = await schema(session)
    if dangling := unresolved(package.rows, foreign_keys):
        raise ServiceError(
            "package_reference_unresolved", f"{dangling} names a row the package lacks", 422, 4
        )
    chunks = {row["id"] for row in package.rows.get("chunks", [])}
    if not cited_chunks(package.rows) <= chunks:
        raise ServiceError(
            "package_citation_unresolved", "A citation names a chunk the package lacks", 422, 4
        )

    actor = Identity(
        scopes=set(job.actor_scopes or []),
        token_id=job.actor_token_id,
        actor_kind="worker",
        job_id=job.id,
        run_id=job.run_id,
        member_id=record.member_id,
    )
    await set_actor_context(session, actor)
    record.task_id, record.state = UUID(task_id), "importing"
    await session.flush()
    await session.execute(
        text("SELECT set_config('app.package_import_id', :id, true)"), {"id": str(record.id)}
    )
    await session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    generated = await generated_columns(session)
    secrets = Secrets.for_data(execution.settings)
    by_key = {item["key"]: item["sha256"] for item in package.manifest["files"]}
    counts: dict[str, int] = {}
    for table in import_order(foreign_keys):
        found = [row for row in package.rows.get(table, []) if row["id"] not in reused[table]]
        if not found:
            continue
        for row in found:
            if table == "tasks":
                # Sharing is the receiving installation's decision, not the sender's.
                row["export_to_personal_allowed"] = False
            for sealed_table, column in SEALED:
                if sealed_table == table and row.get(column) is not None:
                    row[column] = secrets.encrypt(row[column])
            column = STORED.get(table)
            if column and row.get(column):
                await storage.put(row[column], package.files[by_key[row[column]]])
        columns = ", ".join(
            f'"{column}"' for column in sorted(found[0]) if column not in generated[table]
        )
        # No savepoint: guards compare a parent row's xmin with the top transaction.
        try:
            await session.execute(
                text(
                    f'INSERT INTO public."{table}" ({columns}) SELECT {columns} FROM '
                    f'jsonb_populate_recordset(NULL::public."{table}", CAST(:rows AS jsonb))'
                ),
                {"rows": json.dumps(found)},
            )
        except DBAPIError as exc:
            raise rejected(table, exc) from exc
        counts[table] = len(found)
        if table == "tasks":
            session.add(TaskEventHead(task_id=UUID(task_id)))
            if record.member_id is not None:
                session.add(
                    TaskMember(task_id=UUID(task_id), member_id=record.member_id, role="owner")
                )
            await session.flush()
    audit(
        session,
        actor,
        "package.imported",
        UUID(task_id),
        {
            "imported_from": {
                "source_mode": package.manifest.get("source_mode"),
                "exported_at": package.manifest.get("created_at"),
                "package_sha256": record.package_sha256,
            }
        },
    )
    # A new library item arrives with an empty search vector; its root guard
    # rebuilds the vector from the current revision on update.
    for table in SEARCHABLE:
        created_ids = [
            row["id"] for row in package.rows.get(table, []) if row["id"] not in reused[table]
        ]
        if created_ids:
            await session.execute(
                text(f'UPDATE public."{table}" SET id = id WHERE id::text = ANY(:ids)'),
                {"ids": created_ids},
            )
    try:
        await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    except DBAPIError as exc:
        raise rejected("deferred", exc) from exc
    return {
        "task_id": task_id,
        "source_mode": package.manifest.get("source_mode"),
        "tables": counts,
        "reused": {table: len(ids) for table, ids in reused.items() if ids},
        "files": len(package.files),
        "needs_value": await needs_value(session, placeholders(package.rows), task_id),
    }


async def run_import(execution, storage: Storage) -> None:
    async with execution.db.transaction() as session:
        job = await execution.owned_job(session)
        record = await session.scalar(select(PackageImport).where(PackageImport.job_id == job.id))
        assert record is not None
        record_id, stored = record.id, job.result["package"]
    try:
        content = await storage.read(stored["storage_key"])
        if hashlib.sha256(content).hexdigest() != stored["sha256"]:
            raise integrity("The stored package changed")
        package = read_package(content, execution.settings.max_upload_bytes)
        async with execution.db.transaction() as session:
            job = await execution.owned_job(session)
            record = await session.get(PackageImport, record_id, populate_existing=True)
            assert record is not None
            report = await write(session, execution, job, record, package, storage)
            record.state = "succeeded"
            job.status, job.error, job.finished_at = "succeeded", None, datetime.now(UTC)
            job.result = {**job.result, "report": report}
    except Exception:
        async with execution.db.transaction() as session:
            await session.execute(
                update(PackageImport)
                .where(PackageImport.id == record_id)
                .values(state="failed", task_id=None)
            )
        raise
