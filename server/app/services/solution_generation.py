"""Encrypted fixed inputs for scoped, resumable solution drafting jobs."""

import json
from typing import Any, cast
from uuid import uuid4

from cryptography.fernet import InvalidToken
from sqlalchemy import select

from app.core.errors import ServiceError, not_found
from app.core.security import Secrets
from app.models.entities import Document, Job
from app.providers import llm as llm_providers
from app.providers.configured import model_identity
from app.providers.llm import with_reasoning
from app.providers.solution import (
    ADAPTER_VERSION,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    solution_provider,
)
from app.schemas.solution import SolutionDraftReceipt, SolutionDraftRequest, SolutionProviderRequest
from app.services import bid_outline, confidential, redaction, task_access
from app.services.drafts import digest
from app.services.score_generation import provider_options
from app.services.task_authorization import task_authorized
from app.services.versioned import audit
from app.services.whole_bid import tender_facts


def source_inputs(chunks: list[dict], titles: list[str] | None) -> dict:
    """Use actual chapter line ranges for PDF and Word, never matching whole pages."""
    lines = bid_outline.lines_of(chunks)
    heads = bid_outline.chapter_heads(lines)
    needs, scored = [], []
    for position, (start, title) in enumerate(heads):
        end = heads[position + 1][0] if position + 1 < len(heads) else len(lines)
        chapter = lines[start + 1 : end]
        role = bid_outline.chapter_role(title)
        if role == "needs":
            needs.extend([title, *(line.text for line in chapter)])
        elif role == "scoring":
            scored.extend(
                {"title": item.title, "score_range": item.score_range, "rule": item.rule}
                for item in bid_outline.scoring_items(chapter)
                if item.subjective and all(row["title"] != item.title for row in scored)
            )
    if titles is not None:
        if not titles or len(set(titles)) != len(titles):
            raise ServiceError(
                "invalid_solution_titles", "Select distinct scoring item titles", 400, 2
            )
        if set(titles) - {row["title"] for row in scored}:
            raise ServiceError(
                "invalid_solution_titles",
                "Titles must be subjective scoring items in this tender",
                400,
                2,
            )
        scored = [row for row in scored if row["title"] in titles]
    if not scored:
        raise ServiceError(
            "no_subjective_items", "No subjective solution scoring items were found", 400, 2
        )
    text = "\n".join(needs)
    return {"items": scored, "needs_text": text}


async def snapshot(session, actor, task_id, document_id, titles, provider, settings, reasoning):
    # The model's reasoning level is part of the fixed input, as for rubric scoring.
    provider, level, _ = with_reasoning(provider, reasoning)
    task = await task_access.access(
        session, actor, task_id, "solution:draft", write=True, bind_context=False
    )
    document = await session.get(Document, document_id)
    if document is None or document.task_id != task_id:
        raise not_found()
    if document.status != "parsed":
        raise ServiceError("not_parsed", "Parse the document before drafting solutions", 400, 2)
    chunks = await bid_outline.load_chunks(session, document_id)
    raw = source_inputs(chunks, titles)
    facts = tender_facts("\n".join(line.text for line in bid_outline.lines_of(chunks)), task, None)
    entries = await confidential.task_entries(session, task_id)
    library = (
        confidential.library(entries, Secrets.for_data(settings))
        if task.model_redaction_enabled
        else []
    )
    payload, counts = redaction.redact_tree(
        {
            "items": raw["items"],
            "needs_text": raw["needs_text"],
            "project_name": facts.project_name,
            "tenderee": facts.tenderee,
        },
        task.model_redaction_enabled,
        library,
    )
    assert isinstance(payload, dict)
    payload = cast(dict[str, Any], payload)
    # Mask complete values before cutting context: truncating raw text could cut
    # a registered confidential value in half and prevent its mask from matching.
    needs_truncated = len(payload["needs_text"]) > settings.solution_needs_max_chars
    payload["needs_text"] = payload["needs_text"][: settings.solution_needs_max_chars]
    requests = [
        SolutionProviderRequest.model_validate(
            {
                **item,
                "needs_text": payload["needs_text"],
                "project_name": payload["project_name"],
                "tenderee": payload["tenderee"],
            }
        ).model_dump(mode="json")
        for item in payload["items"]
    ]
    manifest = {
        "task_id": str(task_id),
        "document_id": str(document_id),
        "document_sha256": document.sha256,
        "chunks_sha256": digest({"chunks": chunks}),
        "titles": [row["title"] for row in raw["items"]],
        "score_ranges": [row["score_range"] for row in raw["items"]],
        "needs_max_chars": settings.solution_needs_max_chars,
        "needs_truncated": needs_truncated,
        "outbound_sha256": digest({"requests": requests}),
        "model_redaction_enabled": task.model_redaction_enabled,
        "model_redaction_revision": task.model_redaction_revision,
        "redaction_rule_version": redaction.RULE_VERSION,
        "redacted_counts": counts,
        "model": model_identity(provider),
        "reasoning": level,
        "provider_options": provider_options(provider),
        "adapter_version": ADAPTER_VERSION,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
    }
    return manifest, requests, provider


async def target_revisions(session, document_id, titles, score_ranges):
    from app.models.solution import SolutionChapter, SolutionRevision

    chapters = (
        await session.scalars(
            select(SolutionChapter).where(
                SolutionChapter.document_id == document_id, SolutionChapter.title.in_(titles)
            )
        )
    ).all()
    result = {
        title: {"revision": 0, "job_id": None, "origin": None, "state": None} for title in titles
    }
    selected_ranges = dict(zip(titles, score_ranges, strict=True))
    for chapter in chapters:
        if chapter.score_range != selected_ranges[chapter.title]:
            continue
        revision = await session.scalar(
            select(SolutionRevision).where(
                SolutionRevision.chapter_id == chapter.id,
                SolutionRevision.revision == chapter.current_revision,
            )
        )
        if revision is None:
            raise ServiceError(
                "solution_input_integrity", "Current chapter revision is missing", 409, 4
            )
        result[chapter.title] = {
            "revision": revision.revision,
            "job_id": str(revision.job_id) if revision.job_id else None,
            "origin": revision.origin,
            "state": revision.state,
        }
    return result


@task_authorized("solution:draft", write=True)
async def submit(session, actor, task_id, body: SolutionDraftRequest, provider, settings):
    solution_provider(provider)
    manifest, requests, _ = await snapshot(
        session, actor, task_id, body.document_id, body.titles, provider, settings, body.reasoning
    )
    targets = await target_revisions(
        session, body.document_id, manifest["titles"], manifest["score_ranges"]
    )
    if any(current["state"] == "confirmed" for current in targets.values()):
        raise ServiceError(
            "solution_confirmed",
            "Reopen confirmed chapters by editing before regenerating",
            409,
            4,
        )
    job_id = uuid4()
    # A submission is explicit regeneration, whereas attempt retries resume this
    # same job. A completed job cannot be reused over a newer human revision.
    job = Job(
        id=job_id,
        kind="solution_draft",
        command="solution draft",
        status="queued",
        task_id=task_id,
        document_id=body.document_id,
        cache_key=digest({"kind": "solution_draft", "job_id": str(job_id), "manifest": manifest}),
        provider_config_id=getattr(provider, "provider_config_id", None),
        provider_identity=model_identity(provider),
        result={
            "submission": {
                "input_manifest": manifest,
                "input_hash": digest(manifest),
                "target_revisions": {key: value["revision"] for key, value in targets.items()},
                "encrypted_input": Secrets.for_data(settings).encrypt(
                    json.dumps(
                        {
                            "job_id": str(job_id),
                            "requests": requests,
                        },
                        ensure_ascii=False,
                    )
                ),
            }
        },
    )
    session.add(job)
    await session.flush()
    audit(
        session,
        actor,
        "solution.draft.submitted",
        job_id,
        {
            "task_id": str(task_id),
            "document_id": str(body.document_id),
            "input_hash": digest(manifest),
            "chapter_count": len(requests),
        },
    )
    return SolutionDraftReceipt(
        job_id=job_id, task_id=task_id, document_id=body.document_id, titles=manifest["titles"]
    ).model_dump(mode="json"), job


async def current_snapshot(session, actor, job, settings):
    submission = job.result["submission"]
    expected = submission["input_manifest"]
    provider = await llm_providers.resolve_llm(session, settings, job=job)
    manifest, requests, provider = await snapshot(
        session,
        actor,
        job.task_id,
        job.document_id,
        expected["titles"],
        provider,
        settings,
        expected.get("reasoning"),
    )
    if manifest != expected or digest(manifest) != submission["input_hash"]:
        raise ServiceError(
            "solution_input_changed",
            "Tender inputs, redaction or drafting rules changed; submit again",
            409,
            4,
        )
    try:
        saved = json.loads(Secrets.for_data(settings).decrypt(submission["encrypted_input"]))
    except (InvalidToken, ValueError, TypeError):
        raise ServiceError(
            "solution_input_integrity", "Encrypted drafting input cannot be verified", 409, 4
        ) from None
    if saved != {"job_id": str(job.id), "requests": requests}:
        raise ServiceError(
            "solution_input_integrity", "Drafting input does not match its manifest", 409, 4
        )
    targets = await target_revisions(
        session, job.document_id, expected["titles"], expected["score_ranges"]
    )
    for title, current in targets.items():
        if current["revision"] == submission["target_revisions"][title]:
            continue
        if (
            current["job_id"] == str(job.id)
            and current["origin"] == "model"
            and current["state"] == "draft"
        ):
            continue
        raise ServiceError(
            "solution_revision_conflict",
            "A chapter was edited or confirmed after submission",
            409,
            4,
        )
    return provider, [SolutionProviderRequest.model_validate(row) for row in requests], targets


async def job_access(session, actor, job, *, cancel=False):
    if job.kind != "solution_draft" or job.task_id is None:
        raise not_found()
    await task_access.access(
        session,
        actor,
        job.task_id,
        "solution:draft" if cancel else "solution:read",
        write=cancel,
    )
    document = await session.get(Document, job.document_id)
    if document is None or document.task_id != job.task_id:
        raise not_found()
