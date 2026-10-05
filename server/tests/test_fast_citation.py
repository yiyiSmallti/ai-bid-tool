"""Database citation parity and full check-publication scale regressions.

Failure inventory before implementation: exact spelling shortcuts lose normalized
ambiguity; units split combining/Hangul sequences or compatibility expansions;
boundaries use normalized offsets; empty/null inputs accidentally match; repeated
verification makes large publications quadratic; deferred commit is omitted from
timing; report rows are fast but incomplete or leak into another tenant.
"""

import ast
import json
import unicodedata
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

import pytest
from app.core.config import Settings
from app.core.db import Database
from app.core.security import Secrets
from app.jobs import score as score_job
from app.models import Base
from app.models.check import CheckFinding, CheckFindingCitation, CheckItem, CheckRun
from app.models.entities import Chunk, Document, Job, Requirement, Task
from app.models.response_cards import DraftRun, ResponseItem
from app.models.score import ScoreItemCitation, ScoreReport
from app.services import check, check_rules, drafts
from app.services.auth import ROLE_SCOPES, Identity
from app.services.check_inputs import RULE_VERSION, SCHEMA_VERSION, CheckSnapshot
from app.services.extraction import SEGMENT_SEPARATORS, locate_quote, normalize
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_check import publish_draft
from test_check_semantic_storage import semantic_report
from test_check_storage import TABLES
from test_check_storage import check_db as check_db  # noqa: F401
from test_check_storage import check_seeded as check_seeded  # noqa: F401
from test_rls import seeded as seeded  # noqa: F401
from test_score_api import rubric_case as rubric_case  # noqa: F401
from test_score_api import rubric_input_case as rubric_input_case  # noqa: F401
from test_score_report_storage import TABLES as SCORE_TABLES
from test_score_report_storage import pending_score
from test_score_report_storage import score_storage_case as score_storage_case  # noqa: F401
from test_score_run import finish_score, preview_score, submit_score
from test_score_run import score_case as score_case  # noqa: F401

SERVER = Path(__file__).resolve().parents[1]
REQUIREMENT_COUNT = 1500
CHUNK_CHARACTERS = 6000
PUBLISH_LIMIT_SECONDS = 60


def legacy_locator_sql() -> str:
    """Read the original implementation without executing its migration."""
    migration = SERVER / "migrations/versions/0019_citation_boundaries.py"
    tree = ast.parse(migration.read_text())
    template = next(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "CREATE FUNCTION response_locate_quote(" in node.value
    )
    combining = "".join(chr(code) for code in range(0x110000) if unicodedata.combining(chr(code)))
    return template.replace("__COMBINING__", combining.replace("'", "''")).replace(
        "CREATE FUNCTION response_locate_quote(",
        "CREATE FUNCTION public.response_locate_quote_0019_test(",
        1,
    )


def citation_corpus() -> list[tuple[str | None, str | None]]:
    """Deterministic generated cases; common Unicode avoids version-dependent codepoints."""
    cases = [
        (None, "quote"),
        ("quote", None),
        (None, None),
        ("", ""),
        ("", "quote"),
        ("source", ""),
        ("source", " \t\n\u3000"),
        (" \t\n\u3000", " \t"),
        ("3.5mm插孔：≥2个；5mm插孔：≥2个", "5mm插孔：≥2个"),
        ("A-1;B/1;C.1", "1"),
        ("A-1;B/1;C.1;1", "1"),
        ("Memory:16 GB;Memory:16GB", "Memory:16GB"),
        ("fi;ﬁ", "fi"),
        ("xﬁ;fi", "fi"),
        ("ﬁ;f", "f"),
        ("ﬁ", "f"),
        ("ﬃ", "fi"),
        ("㍍", "メートル"),
        ("㍍", "メート"),
        ("㍍;メートル", "メートル"),
        ("e\u0301;e", "e"),
        ("e\u0301", "é"),
        ("q\u0307\u0323", "q\u0323\u0307"),
        ("\u1100\u1161\u11a8", "각"),
        ("\u1100\u1161\u11a8;각", "각"),
        ("\u1100\u1161\u11a8", "가"),
        ("①;1", "1"),
        ("甲内存≥１６ＧＢ；内存≥16GB", "内存≥16GB"),
        ("内存≥１６ＧＢ；甲内存≥16GB", "内存≥16GB"),
        ("内存≥１６ＧＢ；内存≥16GB", "内存≥16GB"),
        ("前缀“ＡＢＣ”后缀", '"ABC"'),
        ("前缀‘ＡＢＣ’后缀", "'ABC'"),
        ("甲；乙", "甲；乙"),
        ("甲；乙", "甲乙"),
        ("甲 \n乙", "甲乙"),
        ("甲；中间；乙", "甲；乙"),
        (" \n甲 \n乙\t ", "甲乙"),
        (" \n甲 \n乙\t ", " \n甲 \n乙\t "),
    ]
    tokens = ("内存：≥16 GB", "ＡＢＣ１２３", "e\u0301", "\u1100\u1161\u11a8", "ﬃ", "㍍")
    separators = sorted(SEGMENT_SEPARATORS) + [
        "\t",
        "\n",
        "\r",
        "\v",
        "\f",
        "\x1c",
        "\x1d",
        "\x1e",
        "\x1f",
        "\u0085",
        "\u00a0",
        "\u1680",
        "\u2000",
        "\u2001",
        "\u2002",
        "\u2003",
        "\u2004",
        "\u2005",
        "\u2006",
        "\u2007",
        "\u2008",
        "\u2009",
        "\u200a",
        "\u2028",
        "\u2029",
        "\u202f",
        "\u205f",
        "\u3000",
    ]
    for token in tokens:
        needle = normalize(token)
        cases.extend(
            [
                (token, needle),
                ("前" + token + "后", needle),
                (token + "；" + token, needle),
                ("前" + token + "后；" + token, needle),
                (token + "；前" + token + "后", needle),
                ("前" + token + "后；前" + token + "后", needle),
                (token, "不存在"),
            ]
        )
        for separator in separators:
            cases.append(("前" + token + "后" + separator + token + separator + "末", needle))
    # A normalized duplicate must defeat an apparently unique exact occurrence.
    for index in range(48):
        ordinary = f"参数{index:03d}:16GB"
        width = ordinary.translate(str.maketrans("0123456789:GB", "０１２３４５６７８９：ＧＢ"))
        cases.extend(
            [(ordinary + "；" + width, ordinary), ("前" + width + "；" + ordinary, ordinary)]
        )
    for token in tokens:
        prefix = "背景说明" * 750
        suffix = "附录备注" * 750
        cases.extend(
            [
                (prefix + "；" + token + "；" + suffix, normalize(token)),
                (prefix + "；" + token + "；" + suffix + "；" + token, normalize(token)),
            ]
        )
    # Some CCC=0 compatibility characters decompose into leading combining
    # marks. Whole-string NFKC can reorder across units that the old locator
    # deliberately kept separate; exact-spelling shortcuts must preserve that.
    for exceptional in ("\u0f73", "\u0f75", "\u0f81", "\uff9e", "\uff9f"):
        for base in ("a", "e", "q"):
            for accent in ("\u0301", "\u0323"):
                token = base + exceptional + accent
                cases.extend(
                    [
                        (token, token),
                        (token, normalize(token)),
                        (token, base),
                        (token, exceptional),
                        ("前；" + token + "；末", token),
                    ]
                )
    for token in ("ｶﾞ", "ﾊﾟ", "ｶﾞ\u0301", "ﾊﾟ\u0323"):
        cases.extend(
            [
                (token, token),
                (token, normalize(token)),
                (token, token[:1]),
                (token + "；" + normalize(token), normalize(token)),
            ]
        )
    very_long = "背景说明" * 8192 + "；唯一引用：≥１６ＧＢ；" + "附录备注" * 2048
    cases.extend(
        [
            (very_long, "唯一引用：≥１６ＧＢ"),
            (very_long, "唯一引用:≥16GB"),
        ]
    )
    return cases


def test_fast_locator_matches_original_sql_and_python(admin_engine, tmp_path):
    corpus = citation_corpus()
    payload = [
        {"index": index, "source": source, "quote": quote}
        for index, (source, quote) in enumerate(corpus)
    ]
    with admin_engine.connect() as connection:
        transaction = connection.begin()
        try:
            # PostgreSQL DDL is transactional: the legacy oracle never survives this test.
            connection.execute(text(legacy_locator_sql()))
            rows = (
                connection.execute(
                    text("""
                    SELECT c.index, public.response_locate_quote_0019_test(c.source,c.quote) AS old,
                      public.response_locate_quote(c.source,c.quote) AS new
                    FROM jsonb_to_recordset(CAST(:corpus AS jsonb))
                      AS c(index integer,source text,quote text)
                    ORDER BY c.index
                """),
                    {"corpus": json.dumps(payload, ensure_ascii=False)},
                )
                .mappings()
                .all()
            )
        finally:
            transaction.rollback()
    results = []
    for row in rows:
        source, quote = corpus[row["index"]]
        expected = (
            locate_quote(source, quote)[0] if source is not None and quote is not None else None
        )
        results.append(
            {**payload[row["index"]], "old": row["old"], "new": row["new"], "python": expected}
        )
    artifact = tmp_path / "fast-citation-parity.json"
    artifact.write_text(
        json.dumps(
            {"unicode_version": unicodedata.unidata_version, "cases": results},
            ensure_ascii=False,
            indent=2,
        )
    )
    assert len(results) == len(corpus)
    for row in results:
        assert row["new"] == row["old"] == row["python"], row


def scale_source(index: int) -> tuple[str, str]:
    quote = f"第{index:04d}项：内存≥１６ＧＢ"
    prefix = "背景说明" * 750 + "；" + quote + "；"
    source = prefix + ("附录备注" * 1500)[: CHUNK_CHARACTERS - len(prefix)]
    return source, quote


async def worker_context(session, user, *, kind="worker"):
    from task_fixtures import actor_context_async

    org = await session.scalar(
        text("SELECT nullif(current_setting('app.current_org', true), '')::uuid")
    )
    assert org is not None
    await actor_context_async(session, org, user, kind=kind)


async def prepare_scale_input(db, actor, settings):
    """Prepare a valid persisted draft without disabling any production gate."""
    async with db.transaction(actor.org_id) as session:
        await worker_context(session, actor.user_id, kind="session")
        task = Task(
            id=uuid4(),
            org_id=actor.org_id,
            created_by=actor.user_id,
            name="Synthetic citation publication scale",
        )
        from task_fixtures import seed_task_async

        await seed_task_async(session, task)
        document = Document(
            id=uuid4(),
            org_id=actor.org_id,
            task_id=task.id,
            name="synthetic-scale.pdf",
            sha256="a" * 64,
            storage_key=f"org/{actor.org_id}/synthetic-scale.pdf",
            media_type="application/pdf",
        )
        session.add(document)
        await session.flush()
        extraction = Job(
            id=uuid4(),
            org_id=actor.org_id,
            task_id=task.id,
            document_id=document.id,
            kind="extract",
            cache_key=uuid4().hex * 2,
            status="succeeded",
        )
        generation = Job(
            id=uuid4(),
            org_id=actor.org_id,
            task_id=task.id,
            document_id=document.id,
            kind="draft",
            cache_key=uuid4().hex * 2,
            status="running",
            run_id=uuid4(),
        )
        session.add_all([extraction, generation])
        await session.flush()
        requirements = []
        for index in range(REQUIREMENT_COUNT):
            if index % 3 == 2:
                # Shared source pairs exercise dedup without reducing the workload
                # to one short page; the other 1000 requirements have distinct pages.
                quote = requirements[-1].quote
                chunk_id, page = requirements[-1].chunk_id, requirements[-1].page
            else:
                source, quote = scale_source(index)
                chunk = Chunk(
                    id=uuid4(),
                    org_id=actor.org_id,
                    task_id=task.id,
                    document_id=document.id,
                    page=index + 1,
                    seq=index + 1,
                    text=source,
                )
                session.add(chunk)
                chunk_id, page = chunk.id, chunk.page
            requirement = Requirement(
                id=uuid4(),
                org_id=actor.org_id,
                task_id=task.id,
                document_id=document.id,
                chunk_id=chunk_id,
                page=page,
                text=quote,
                quote=quote,
                category="technical",
                starred=True,
                condition={},
                fingerprint=sha256(f"{index}:{quote}".encode()).hexdigest(),
                job_id=extraction.id,
            )
            requirements.append(requirement)
        await session.flush()
        session.add_all(requirements)
        await session.flush()
        draft_manifest = {
            "task_id": str(task.id),
            "extraction_job_id": str(extraction.id),
            "requirements": [
                {"requirement_id": str(requirement.id)} for requirement in requirements
            ],
        }
        draft = DraftRun(
            id=uuid4(),
            org_id=actor.org_id,
            task_id=task.id,
            extraction_job_id=extraction.id,
            generation_job_id=generation.id,
            generation_run_id=generation.run_id,
            input_hash=drafts.digest(draft_manifest),
            input_manifest=draft_manifest,
            actor_user_id=actor.user_id,
            actor_kind="session",
            completion="partial",
            summary={"gap_requirements": REQUIREMENT_COUNT},
        )
        session.add(draft)
        await session.flush()
        items, fixed_items = [], []
        for requirement in requirements:
            source = {
                "document_id": str(document.id),
                "chunk_id": str(requirement.chunk_id),
                "page": requirement.page,
                "location": None,
                "quote": requirement.quote,
            }
            response = ResponseItem(
                id=uuid4(),
                org_id=actor.org_id,
                draft_id=draft.id,
                requirement_id=requirement.id,
                category=requirement.category,
                starred=True,
                kind="gap",
                source=source,
                location_label=f"Synthetic scale · page {requirement.page}",
                gap_reasons=["missing_card"],
            )
            session.add(response)
            item = {
                "requirement_id": str(requirement.id),
                "response_item_id": str(response.id),
                "card_revision_id": None,
                "partition": "gap",
                "source": source,
                "category": "technical",
                "starred": True,
                "review_domain": None,
                "gap_reasons": ["missing_card"],
                "evidence": [],
            }
            items.append(item)
            fixed_items.append(
                {
                    **{
                        key: item[key]
                        for key in (
                            "requirement_id",
                            "response_item_id",
                            "card_revision_id",
                            "partition",
                            "review_domain",
                            "gap_reasons",
                            "evidence",
                        )
                    },
                    "card_id": None,
                    "document_id": str(document.id),
                    "chunk_id": str(requirement.chunk_id),
                    "generation_dependencies": None,
                }
            )
        await session.flush()
    secret = {"items": items, "certificates": []}
    manifest = {
        "org_id": str(actor.org_id),
        "task_id": str(task.id),
        "draft_id": str(draft.id),
        "extraction_job_id": str(extraction.id),
        "document_id": str(document.id),
        "document_sha256": document.sha256,
        "draft_input_hash": draft.input_hash,
        "assessment_date": "2026-10-04",
        "mode": "rules",
        "rule_version": RULE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "prompt_version": None,
        "model_redaction_enabled": True,
        "model_redaction_revision": 1,
        "confidential": [],
        "items": fixed_items,
        "certificates": [],
    }
    fixed = CheckSnapshot(
        draft=draft,
        extraction=extraction,
        manifest=manifest,
        secret=secret,
        input_hash=drafts.digest(manifest),
        limitations=["Synthetic citation performance fixture"],
    )
    encrypted = Secrets.for_data(settings).encrypt(json.dumps(secret, ensure_ascii=False))
    async with db.transaction(actor.org_id) as session:
        job = Job(
            id=uuid4(),
            org_id=actor.org_id,
            task_id=task.id,
            document_id=document.id,
            kind="check",
            cache_key=uuid4().hex * 2,
            status="running",
            run_id=uuid4(),
            lease_until=datetime.now(UTC) + timedelta(minutes=10),
            result={
                "submission": {
                    "input_hash": fixed.input_hash,
                    "input_manifest": manifest,
                    "encrypted_input": encrypted,
                }
            },
        )
        session.add(job)
        await session.flush()
    return fixed, job


async def test_large_check_publish_includes_deferred_commit(tenants, tmp_path):
    settings = Settings(data_dir=tmp_path)
    actor = Identity(
        user_id=tenants["users"][0],
        org_id=tenants["orgs"][0],
        scopes=set(ROLE_SCOPES["admin"]),
        role="admin",
        actor_kind="worker",
    )
    db = Database(settings)
    try:
        await db.verify_role()
        prepared_at = perf_counter()
        fixed, job = await prepare_scale_input(db, actor, settings)
        evaluated = check_rules.evaluate(fixed.secret, str(fixed.draft.id))
        preparation_seconds = perf_counter() - prepared_at
        started = perf_counter()
        async with db.transaction(actor.org_id) as session:
            await worker_context(session, actor.user_id)
            result = await check.publish(session, actor, job, fixed, evaluated, settings)
            flushed_seconds = perf_counter() - started
        elapsed = perf_counter() - started
        report_id = UUID(result["report_id"])
        counts = {}
        async with db.transaction(actor.org_id) as session:
            stored = await session.get(CheckRun, report_id)
            assert stored is not None
            assert stored.summary == {
                "item_count": REQUIREMENT_COUNT,
                "finding_count": REQUIREMENT_COUNT,
                "unassessed_count": 0,
            }
            for model in (CheckItem, CheckFinding, CheckFindingCitation):
                counts[model.__tablename__] = await session.scalar(
                    select(func.count()).select_from(model).where(model.report_id == report_id)
                )
            assert (
                await session.scalar(
                    text("SELECT check_current_inputs(:org,:report)"),
                    {"org": actor.org_id, "report": report_id},
                )
                is True
            )
        for org in (None, tenants["orgs"][1]):
            async with db.transaction(org) as session:
                assert await session.get(CheckRun, report_id) is None
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(CheckFindingCitation)
                        .where(CheckFindingCitation.report_id == report_id)
                    )
                    == 0
                )
        measurement = {
            "requirements": REQUIREMENT_COUNT,
            "distinct_source_quote_pairs": REQUIREMENT_COUNT * 2 // 3,
            "chunk_characters": CHUNK_CHARACTERS,
            "stored_counts": counts,
            "preparation_seconds": preparation_seconds,
            "publish_seconds_including_commit": elapsed,
            "publish_seconds_before_commit": flushed_seconds,
            "commit_seconds": elapsed - flushed_seconds,
            "ceiling_seconds": PUBLISH_LIMIT_SECONDS,
            "mode": "rules",
            "draft_partition": "missing_card gaps",
            "source_generator": "scale_source(index), index in range(1500), deterministic 6000-character CJK pages; every third requirement reuses the preceding page and quote",
            "source_example": scale_source(0),
            "input_hash": fixed.input_hash,
            "result": result,
        }
        artifact = tmp_path / "large-check-publish-performance.json"
        artifact.write_text(json.dumps(measurement, ensure_ascii=False, indent=2))
        print(
            json.dumps(
                {
                    "artifact": str(artifact),
                    "publish_seconds_including_commit": elapsed,
                    "stored_counts": counts,
                }
            )
        )
        assert set(counts.values()) == {REQUIREMENT_COUNT}, measurement
        assert elapsed < PUBLISH_LIMIT_SECONDS, measurement
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize("mutation", ["source_missing", "source_ambiguous", "requirement_quote"])
async def test_check_deferred_gate_rejects_changed_citation(check_seeded, check_db, mutation):
    with pytest.raises(DBAPIError) as error:
        async with check_db.transaction(check_seeded["orgs"][0]) as session:
            report, item = await semantic_report(session, check_seeded)
            requirement = await session.get(Requirement, item.requirement_id)
            assert requirement is not None
            chunk = await session.get(Chunk, requirement.chunk_id)
            assert chunk is not None
            if mutation == "source_missing":
                chunk.text = "Changed source that no longer contains the requirement quote"
            elif mutation == "source_ambiguous":
                chunk.text += "；" + chunk.text
            else:
                requirement.quote = "Synthetic"
            await session.flush()
            # A final COMMIT must observe the changed live source, even if all
            # coverage and citation children already passed their insert gates.
            assert report.id is not None
    assert error.value.orig.sqlstate == "23514"


async def test_check_closed_after_early_deferred_validation(check_seeded, check_db):
    with pytest.raises(DBAPIError) as error:
        async with check_db.transaction(check_seeded["orgs"][0]) as session:
            report, item = await semantic_report(session, check_seeded)
            await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            await session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            original = await session.scalar(
                select(CheckFindingCitation).where(
                    CheckFindingCitation.report_id == report.id,
                    CheckFindingCitation.check_item_id == item.id,
                    CheckFindingCitation.kind == "tender",
                )
            )
            assert original is not None
            values = {
                column.name: getattr(original, column.name)
                for column in CheckFindingCitation.__table__.columns
            }
            values["id"] = uuid4()
            session.add(CheckFindingCitation(**values))
            await session.flush()
    assert error.value.orig.sqlstate == "42501"


@contextmanager
def counted_locator(admin_engine, data):
    """Instrument one transaction, including real publication, then roll back DDL."""
    with admin_engine.connect() as connection:
        transaction = connection.begin()
        try:
            original = connection.scalar(
                text(
                    "SELECT pg_get_functiondef('public.response_locate_quote(text,text)'::regprocedure)"
                )
            )
            connection.execute(
                text(
                    original.replace(
                        "FUNCTION public.response_locate_quote(",
                        "FUNCTION public.response_locate_quote_counter_original(",
                        1,
                    )
                )
            )
            connection.execute(
                text(
                    "CREATE TEMP TABLE citation_locator_calls(source_text text, quote text) ON COMMIT DROP"
                )
            )
            connection.execute(text("GRANT SELECT, INSERT ON citation_locator_calls TO bid_app"))
            connection.execute(
                text("""
                CREATE OR REPLACE FUNCTION public.response_locate_quote(source_text text, quote text)
                RETURNS text LANGUAGE plpgsql VOLATILE STRICT PARALLEL UNSAFE
                SET search_path=pg_catalog AS $$
                BEGIN
                  INSERT INTO pg_temp.citation_locator_calls VALUES(source_text,quote);
                  RETURN public.response_locate_quote_counter_original(source_text,quote);
                END $$
            """)
            )
            connection.execute(text("SET LOCAL ROLE bid_app"))
            connection.execute(
                text(
                    "SELECT set_config('app.current_org',:org,true),set_config('app.actor_user_id',:user,true),set_config('app.actor_kind','worker',true),set_config('app.actor_token_id','',true)"
                ),
                {"org": str(data["orgs"][0]), "user": str(data["users"][0])},
            )
            yield connection
        finally:
            transaction.rollback()


def test_requirement_batch_locates_distinct_actual_pairs_once(check_seeded, admin_engine, tmp_path):
    org = check_seeded["orgs"][0]
    with admin_engine.connect() as connection:
        foreign = connection.scalar(
            select(Requirement.id).where(Requirement.org_id == check_seeded["orgs"][1])
        )
    assert foreign is not None
    with counted_locator(admin_engine, check_seeded) as connection, Session(connection) as session:
        original = session.scalar(select(Requirement).where(Requirement.org_id == org))
        assert original is not None
        values = {
            column.name: getattr(original, column.name) for column in Requirement.__table__.columns
        }
        original_chunk = session.get(Chunk, original.chunk_id)
        assert original_chunk is not None
        chunk_values = {
            column.name: getattr(original_chunk, column.name) for column in Chunk.__table__.columns
        }
        duplicate_chunk = Chunk(**{**chunk_values, "id": uuid4(), "seq": 2, "page": 2})
        session.add(duplicate_chunk)
        session.flush()
        duplicate = Requirement(
            **{
                **values,
                "id": uuid4(),
                "chunk_id": duplicate_chunk.id,
                "page": duplicate_chunk.page,
                "fingerprint": uuid4().hex * 2,
            }
        )
        session.add(duplicate)
        session.flush()
        requested = [original.id, original.id, duplicate.id]
        rows = (
            connection.execute(
                text(
                    "SELECT requirement_id,is_valid FROM public.response_requirement_citations_valid(:org,CAST(:requirements AS uuid[]))"
                ),
                {"org": org, "requirements": requested},
            )
            .mappings()
            .all()
        )
        assert {row["requirement_id"]: row["is_valid"] for row in rows} == {
            original.id: True,
            duplicate.id: True,
        }
        calls = connection.scalar(text("SELECT count(*) FROM citation_locator_calls"))
        assert calls == 1
        denied = (
            connection.execute(
                text(
                    "SELECT requirement_id,is_valid FROM public.response_requirement_citations_valid(:org,CAST(:requirements AS uuid[]))"
                ),
                {"org": org, "requirements": [uuid4(), foreign]},
            )
            .mappings()
            .all()
        )
        assert not any(row["is_valid"] for row in denied)
        assert connection.scalar(text("SELECT count(*) FROM citation_locator_calls")) == calls
        (tmp_path / "citation-batch-call-count.json").write_text(
            json.dumps(
                {
                    "requested_requirements": len(requested),
                    "distinct_requirements": len({row["requirement_id"] for row in rows}),
                    "locator_calls": calls,
                    "foreign_or_missing_valid": False,
                },
                indent=2,
            )
        )


def test_check_publish_locates_requirement_once_across_all_gates(
    check_seeded, admin_engine, tmp_path
):
    org = check_seeded["orgs"][0]
    with counted_locator(admin_engine, check_seeded) as connection, Session(connection) as session:
        original = session.get(CheckRun, check_seeded["checks"][org]["run"])
        assert original is not None
        job = Job(
            id=uuid4(),
            org_id=org,
            task_id=original.task_id,
            document_id=original.document_id,
            kind="check",
            cache_key=uuid4().hex * 2,
            status="running",
            run_id=uuid4(),
            lease_until=datetime.now(UTC) + timedelta(minutes=10),
            result={
                "submission": {
                    "input_hash": original.input_hash,
                    "input_manifest": original.input_manifest,
                    "encrypted_input": original.encrypted_input,
                }
            },
        )
        session.add(job)
        session.flush()
        values = {
            column.name: getattr(original, column.name) for column in CheckRun.__table__.columns
        }
        report = CheckRun(**{**values, "id": uuid4(), "job_id": job.id, "run_id": job.run_id})
        session.add(report)
        session.flush()
        remapped = {}
        counts = {}
        for name in TABLES[1:-1]:
            table = Base.metadata.tables[name]
            old = dict(
                session.execute(select(table).where(table.c.report_id == original.id))
                .mappings()
                .one()
            )
            new_id = uuid4()
            remapped[old["id"]] = new_id
            child = {
                key: remapped.get(value, value)
                if key in {"check_item_id", "certificate_id", "finding_id"}
                else value
                for key, value in old.items()
            }
            child.update(id=new_id, report_id=report.id)
            session.execute(table.insert().values(child))
            counts[name] = 1
        # Execute the exact deferred publication boundary within the rollback
        # transaction so the instrumentation never escapes into another test.
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        calls = connection.scalar(text("SELECT count(*) FROM citation_locator_calls"))
        (tmp_path / "check-publication-call-count.json").write_text(
            json.dumps(
                {
                    "report_children": counts,
                    "locator_calls": calls,
                    "boundary": "SET CONSTRAINTS ALL IMMEDIATE",
                    "instrumentation_rolled_back": True,
                },
                indent=2,
            )
        )
        assert calls == 1


async def test_score_publish_locates_shared_draft_and_rubric_pairs_once(
    score_storage_case, admin_engine, tmp_path
):
    case = score_storage_case
    org = case["tenants"]["orgs"][0]
    async with case["app"].state.db.transaction(org) as session:
        original = await pending_score(session, case)
        original_id = original.id
    with (
        counted_locator(admin_engine, case["tenants"]) as connection,
        Session(connection) as session,
    ):
        original = session.get(ScoreReport, original_id)
        assert original is not None
        expected = set()
        requirements = session.scalars(
            select(Requirement).where(
                Requirement.org_id == org,
                Requirement.task_id == original.task_id,
                Requirement.job_id == original.extraction_job_id,
            )
        ).all()
        for requirement in requirements:
            chunk = session.get(Chunk, requirement.chunk_id)
            assert chunk is not None and chunk.blocks is None
            expected.add((chunk.text, requirement.quote))
        job = Job(
            id=uuid4(),
            org_id=org,
            task_id=original.task_id,
            document_id=original.document_id,
            kind="score",
            cache_key=uuid4().hex * 2,
            status="running",
            run_id=uuid4(),
            lease_until=datetime.now(UTC) + timedelta(minutes=10),
            result={
                "submission": {
                    "input_hash": original.input_hash,
                    "input_manifest": original.input_manifest,
                    "encrypted_input": original.encrypted_input,
                }
            },
        )
        session.add(job)
        session.flush()
        values = {
            column.name: getattr(original, column.name) for column in ScoreReport.__table__.columns
        }
        report = ScoreReport(**{**values, "id": uuid4(), "job_id": job.id, "run_id": job.run_id})
        session.add(report)
        session.flush()
        remapped, counts = {}, {}
        for name in SCORE_TABLES[1:]:
            table = Base.metadata.tables[name]
            children = (
                session.execute(select(table).where(table.c.report_id == original.id))
                .mappings()
                .all()
            )
            for old in children:
                new_id = uuid4()
                remapped[old["id"]] = new_id
                child = {
                    key: remapped.get(value, value) if key == "score_item_id" else value
                    for key, value in old.items()
                }
                child.update(id=new_id, report_id=report.id)
                session.execute(table.insert().values(child))
            counts[name] = len(children)
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        calls = connection.execute(
            text("SELECT source_text,quote FROM citation_locator_calls")
        ).all()
        measurement = {
            "draft_requirements": len(requirements),
            "distinct_source_quote_pairs": len(expected),
            "locator_calls": len(calls),
            "report_children": counts,
            "boundary": "SET CONSTRAINTS ALL IMMEDIATE",
            "instrumentation_rolled_back": True,
        }
        (tmp_path / "score-publication-call-count.json").write_text(
            json.dumps(measurement, indent=2)
        )
        assert set(calls) == expected, measurement
        assert len(calls) == len(expected), measurement


async def test_score_closed_after_early_deferred_validation(score_storage_case):
    case = score_storage_case
    with pytest.raises(DBAPIError) as error:
        async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
            report = await pending_score(session, case)
            await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            await session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            original = await session.scalar(
                select(ScoreItemCitation).where(
                    ScoreItemCitation.report_id == report.id, ScoreItemCitation.kind == "tender"
                )
            )
            assert original is not None
            values = {
                column.name: getattr(original, column.name)
                for column in ScoreItemCitation.__table__.columns
            }
            values["id"] = uuid4()
            session.add(ScoreItemCitation(**values))
            await session.flush()
    assert error.value.orig.sqlstate == "42501"


@pytest.mark.parametrize("mutation", ["source_missing", "source_ambiguous", "requirement_quote"])
async def test_score_deferred_gate_rejects_changed_rubric_citation(score_storage_case, mutation):
    case = score_storage_case
    with pytest.raises(DBAPIError) as error:
        async with case["app"].state.db.transaction(case["tenants"]["orgs"][0]) as session:
            await pending_score(session, case)
            requirement = await session.get(
                Requirement, UUID(case["confirmed"]["items"][0]["requirement_id"])
            )
            assert requirement is not None
            chunk = await session.get(Chunk, requirement.chunk_id)
            assert chunk is not None
            if mutation == "source_missing":
                chunk.text = "Changed source with no scoring clause"
            elif mutation == "source_ambiguous":
                chunk.text += "；" + chunk.text
            else:
                requirement.quote = "Synthetic"
            await session.flush()
    assert error.value.orig.sqlstate == "23514"


async def test_score_worker_keeps_admission_validation_and_defers_final_duplicate(
    score_case, monkeypatch, tmp_path
):
    case = score_case
    measured = {
        "deferred_snapshot_calls": 0,
        "admission_sql_calls": 0,
        "deferred_snapshot_sql_calls": 0,
    }
    phase = {"deferred_snapshot": False}
    original_snapshot = score_job.current_snapshot

    async def snapshot(*args, **kwargs):
        deferred = kwargs.get("defer_database_validation", False)
        if deferred:
            measured["deferred_snapshot_calls"] += 1
        phase["deferred_snapshot"] = deferred
        try:
            return await original_snapshot(*args, **kwargs)
        finally:
            phase["deferred_snapshot"] = False

    def observe_sql(connection, cursor, statement, parameters, context, executemany):
        if "score_inputs_current(" in statement:
            key = (
                "deferred_snapshot_sql_calls"
                if phase["deferred_snapshot"]
                else "admission_sql_calls"
            )
            measured[key] += 1

    monkeypatch.setattr(score_job, "current_snapshot", snapshot)
    engine = case["app"].state.db.engine.sync_engine
    event.listen(engine, "before_cursor_execute", observe_sql)
    try:
        preview = await preview_score(case)
        accepted = await submit_score(case, preview)
        assert accepted.status_code == 200, accepted.text
        terminal = await finish_score(case, accepted.json()["data"])
        assert terminal["status"] == "succeeded", terminal
        assert terminal["result"]["completion"] == "complete"
        assert len(case["score_vendor"].requests) == 1
        measurement = {
            **measured,
            "worker_status": terminal["status"],
            "report_id": terminal["result"]["report_id"],
            "vendor_calls": len(case["score_vendor"].requests),
        }
        (tmp_path / "score-worker-publication-validation.json").write_text(
            json.dumps(measurement, indent=2)
        )
        assert measured["admission_sql_calls"] > 0, measurement
        assert measured["deferred_snapshot_calls"] == 1, measurement
        assert measured["deferred_snapshot_sql_calls"] == 0, measurement
    finally:
        event.remove(engine, "before_cursor_execute", observe_sql)


async def test_score_preserves_explicit_invalid_citation_gap_outside_rubric(score_storage_case):
    case = score_storage_case
    org = case["tenants"]["orgs"][0]
    requirement_id = UUID(case["requirements"][3]["id"])
    rubric_requirements = {UUID(item["requirement_id"]) for item in case["confirmed"]["items"]}
    assert requirement_id not in rubric_requirements
    async with case["app"].state.db.transaction(org) as session:
        requirement = await session.get(Requirement, requirement_id)
        assert requirement is not None
        chunk = await session.get(Chunk, requirement.chunk_id)
        assert chunk is not None
        # Changing only Chunk.text leaves a missing-card draft's source hash and
        # eligibility unchanged, so submit_draft reuses the fixture's old job.
        # Change the citation itself to request a fresh, explicitly invalid gap.
        requirement.quote = "Synthetic non-scoring quotation absent from parsed text"
        assert requirement.quote not in chunk.text
        await session.flush()
        assert (
            await session.scalar(
                text("SELECT response_citation_valid(:org,:requirement)"),
                {"org": org, "requirement": requirement_id},
            )
            is False
        )
    assembled = await publish_draft(
        case["api"], case["app"], case["header"], case["task"], case["extraction"]
    )
    changed_case = {**case, "draft_id": UUID(assembled["draft_id"])}
    assert changed_case["draft_id"] != case["draft_id"]
    async with case["app"].state.db.transaction(org) as session:
        gap = await session.scalar(
            select(ResponseItem).where(
                ResponseItem.draft_id == changed_case["draft_id"],
                ResponseItem.requirement_id == requirement_id,
            )
        )
        assert gap is not None and gap.kind == "gap" and gap.card_id is None
        assert gap.gap_reasons == ["missing_card", "invalid_citation"]
        # The confirmed rubric remains strict; only the explicitly recorded,
        # unrelated draft gap may remain unassessable without blocking the report.
        report = await pending_score(session, changed_case)
        report_id = report.id
    async with case["app"].state.db.transaction(org) as session:
        assert await session.get(ScoreReport, report_id) is not None
