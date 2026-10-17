"""Model review of a tender's scoring table, with an encrypted, fixed input.

The model reads the scoring region as numbered lines and lists the items; only the
items verified against those lines are kept. The outline uses the newest succeeded
review whose chunks still match.
"""

import json
import re
from typing import cast
from uuid import uuid4

from cryptography.fernet import InvalidToken

from app.core.errors import ServiceError, not_found
from app.core.security import Secrets
from app.models.entities import Document, Job
from app.providers import llm as llm_providers
from app.providers.configured import model_identity
from app.providers.scoring_review import (
    ADAPTER_VERSION,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    scoring_review_provider,
)
from app.schemas.whole_bid import ScoringReviewReceipt
from app.services import bid_outline, confidential, redaction, task_access
from app.services.drafts import digest
from app.services.score_generation import provider_options
from app.services.task_authorization import task_authorized
from app.services.versioned import audit


async def snapshot(session, actor, document_id, provider, settings):
    document = await session.get(Document, document_id)
    if document is None or document.task_id is None:
        raise not_found()
    task = await task_access.access(
        session, actor, document.task_id, "solution:draft", write=True, bind_context=False
    )
    if document.status != "parsed":
        raise ServiceError("not_parsed", "Parse the document before reviewing it", 400, 2)
    chunks = await bid_outline.load_chunks(session, document_id)
    lines = bid_outline.lines_of(chunks)
    region = bid_outline.review_region(lines)
    if region is None:
        raise ServiceError("no_scoring_region", "No scoring table was found to review", 400, 2)
    start, end, truncated = region
    entries = await confidential.task_entries(session, document.task_id)
    library = (
        confidential.library(entries, Secrets.for_data(settings))
        if task.model_redaction_enabled
        else []
    )
    sent, counts = redaction.redact_tree(
        {"lines": bid_outline.review_lines(lines, start, end)},
        task.model_redaction_enabled,
        library,
    )
    assert isinstance(sent, dict)
    text = "\n".join(cast(list[str], sent["lines"]))
    manifest = {
        "task_id": str(document.task_id),
        "document_id": str(document_id),
        "document_sha256": document.sha256,
        "chunks_sha256": bid_outline.chunks_digest(chunks),
        "region": {"start": start, "end": end, "truncated": truncated},
        "outbound_sha256": digest({"text": text}),
        "model_redaction_enabled": task.model_redaction_enabled,
        "model_redaction_revision": task.model_redaction_revision,
        "redaction_rule_version": redaction.RULE_VERSION,
        "redacted_counts": counts,
        "model": model_identity(provider),
        "provider_options": provider_options(provider),
        "adapter_version": ADAPTER_VERSION,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
    }
    return manifest, text, document


def shown_lines(text: str) -> dict[int, str]:
    """The lines the model was shown, by number, without their L-number prefix."""
    rows = (re.match(r"L(\d+)\|(.*)", row) for row in text.split("\n"))
    return {int(row.group(1)): row.group(2) for row in rows if row}


@task_authorized("solution:draft", parent=("document_id", "documents"), write=True)
async def submit(session, actor, document_id, provider, settings):
    scoring_review_provider(provider)
    manifest, text, document = await snapshot(session, actor, document_id, provider, settings)
    job_id = uuid4()
    job = Job(
        id=job_id,
        kind="scoring_review",
        command="tender outline review",
        status="queued",
        task_id=document.task_id,
        document_id=document_id,
        cache_key=digest({"kind": "scoring_review", "job_id": str(job_id), "manifest": manifest}),
        provider_config_id=getattr(provider, "provider_config_id", None),
        provider_identity=model_identity(provider),
        result={
            "submission": {
                "input_manifest": manifest,
                "input_hash": digest(manifest),
                "encrypted_input": Secrets.for_data(settings).encrypt(
                    json.dumps({"job_id": str(job_id), "text": text}, ensure_ascii=False)
                ),
            }
        },
    )
    session.add(job)
    await session.flush()
    audit(
        session,
        actor,
        "tender.scoring_review.submitted",
        job_id,
        {
            "task_id": str(document.task_id),
            "document_id": str(document_id),
            "input_hash": digest(manifest),
        },
    )
    receipt = ScoringReviewReceipt(job_id=job_id, document_id=document_id)
    return receipt.model_dump(mode="json"), job


async def current_snapshot(session, actor, job, settings):
    submission = job.result["submission"]
    provider = await llm_providers.resolve_llm(session, settings, job=job)
    manifest, text, _ = await snapshot(session, actor, job.document_id, provider, settings)
    if manifest != submission["input_manifest"] or digest(manifest) != submission["input_hash"]:
        raise ServiceError(
            "scoring_review_input_changed",
            "Tender inputs, redaction or review rules changed; submit again",
            409,
            4,
        )
    try:
        saved = json.loads(Secrets.for_data(settings).decrypt(submission["encrypted_input"]))
    except (InvalidToken, ValueError, TypeError):
        raise ServiceError(
            "scoring_review_input_integrity", "Encrypted review input cannot be verified", 409, 4
        ) from None
    if saved != {"job_id": str(job.id), "text": text}:
        raise ServiceError(
            "scoring_review_input_integrity", "Review input does not match its manifest", 409, 4
        )
    return provider, text


async def job_access(session, actor, job, *, cancel=False):
    if job.kind != "scoring_review" or job.task_id is None:
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
