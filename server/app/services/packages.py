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
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.core.security import Secrets
from app.models.entities import Job, Task
from app.providers.storage import Storage
from app.schemas.contracts import CONTRACT_VERSION
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
    return {
        "id": str(job.id),
        "kind": job.kind,
        "status": job.status,
        "task_id": str(job.task_id) if job.task_id else None,
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
