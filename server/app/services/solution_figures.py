"""Figures of a solution chapter: a model job drawing pages of the proposed system, and
human uploads of real screenshots. Both write a new draft revision of the chapter."""

import copy
import hashlib
import json
from typing import Literal, cast
from uuid import uuid4

import pymupdf
from cryptography.fernet import InvalidToken

from app.core.errors import ServiceError, not_found
from app.core.security import Secrets
from app.models.entities import Job, Task
from app.providers import llm as llm_providers
from app.providers.configured import model_identity
from app.providers.llm import with_reasoning
from app.providers.solution_figures import (
    ADAPTER_VERSION,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    figures_provider,
)
from app.schemas.solution import (
    FigureProviderRequest,
    FigureSubsection,
    SolutionFigure,
    SolutionFiguresReceipt,
    SolutionFiguresRequest,
)
from app.services import confidential, redaction, solution, task_access
from app.services.drafts import digest
from app.services.score_generation import provider_options
from app.services.task_authorization import task_authorized
from app.services.versioned import audit
from app.services.whole_bid import tender_facts

# The model reads the start of each subsection; a page needs its subject, not every detail.
TEXT_CHARS = 600
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_SIDE = 8000
EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg"}


def storage_key(task_id, figure: SolutionFigure) -> str:
    return f"solution-figure/{task_id}/{figure.sha256}.{EXTENSIONS[figure.media_type]}"


def image_figure(
    content: bytes, caption: str, origin: Literal["generated", "uploaded"]
) -> SolutionFigure:
    """A PNG or JPEG that opens as one bounded image; anything else is refused."""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        media_type = "image/png"
    elif content.startswith(b"\xff\xd8\xff"):
        media_type = "image/jpeg"
    else:
        raise ServiceError("invalid_figure", "A figure must be a PNG or JPEG image", 400, 2)
    if len(content) > MAX_IMAGE_BYTES:
        raise ServiceError("invalid_figure", "A figure is at most 15 MiB", 413, 2)
    try:
        pixmap = pymupdf.Pixmap(content)
    except Exception:
        raise ServiceError("invalid_figure", "The image cannot be read", 400, 2) from None
    if not (0 < pixmap.width <= MAX_SIDE and 0 < pixmap.height <= MAX_SIDE):
        raise ServiceError("invalid_figure", "The image is too large", 400, 2)
    return SolutionFigure(
        caption=caption,
        sha256=hashlib.sha256(content).hexdigest(),
        media_type=media_type,
        origin=origin,
    )


def with_figures(sections: list[dict], figures: dict[str, dict | None]) -> list[dict]:
    """Sections with the figure of each `section.subsection` number set or cleared."""
    sections = copy.deepcopy(sections)
    for position, section in enumerate(sections, 1):
        for sub_position, sub in enumerate(section.get("subsections", []), 1):
            number = f"{position}.{sub_position}"
            if number in figures:
                sub["figure"] = figures[number]
    return sections


async def snapshot(session, actor, chapter_id, provider, settings, reasoning):
    provider, level, _ = with_reasoning(provider, reasoning)
    chapter, revision = await solution.load(
        session, actor, chapter_id, "solution:draft", write=True
    )
    task = await session.get_one(Task, chapter.task_id)
    uploaded = {
        number
        for number, figure in solution.figure_numbers(revision.sections).items()
        if figure["origin"] == "uploaded"
    }
    subsections = [
        {
            "number": f"{position}.{sub_position}",
            "section": section["heading"],
            "heading": sub["heading"],
            "text": "\n".join(sub["paragraphs"])[:TEXT_CHARS],
        }
        for position, section in enumerate(revision.sections, 1)
        for sub_position, sub in enumerate(section.get("subsections", []), 1)
        if f"{position}.{sub_position}" not in uploaded
    ]
    if not subsections:
        raise ServiceError("no_figure_subsections", "Every subsection has a figure", 400, 2)
    from app.services import bid_outline

    chunks = await bid_outline.load_chunks(session, chapter.document_id)
    facts = tender_facts("\n".join(line.text for line in bid_outline.lines_of(chunks)), task, None)
    entries = await confidential.task_entries(session, task.id)
    library = (
        confidential.library(entries, Secrets.for_data(settings))
        if task.model_redaction_enabled
        else []
    )
    payload, counts = redaction.redact_tree(
        {"title": chapter.title, "project_name": facts.project_name, "subsections": subsections},
        task.model_redaction_enabled,
        library,
    )
    fields = cast(dict, payload)
    request = FigureProviderRequest(
        title=fields["title"],
        project_name=fields["project_name"],
        subsections=[FigureSubsection(**row) for row in fields["subsections"]],
    ).model_dump(mode="json")
    manifest = {
        "task_id": str(task.id),
        "document_id": str(chapter.document_id),
        "chapter_id": str(chapter.id),
        "revision": revision.revision,
        "uploaded": sorted(uploaded),
        "outbound_sha256": digest({"request": request}),
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
    return manifest, request, provider, chapter


@task_authorized("solution:draft", parent=("chapter_id", "solution_chapters"), write=True)
async def submit(session, actor, chapter_id, body: SolutionFiguresRequest, provider, settings):
    figures_provider(provider)
    if not settings.preview_renderer:
        raise ServiceError(
            "figure_renderer_unavailable", "Drawing figures needs the desktop renderer", 409, 4
        )
    manifest, request, _, chapter = await snapshot(
        session, actor, chapter_id, provider, settings, body.reasoning
    )
    solution.expected(chapter, body.expected_revision)
    job_id = uuid4()
    job = Job(
        id=job_id,
        kind="solution_figures",
        command="solution figures",
        status="queued",
        task_id=chapter.task_id,
        document_id=chapter.document_id,
        cache_key=digest({"kind": "solution_figures", "job_id": str(job_id), "manifest": manifest}),
        provider_config_id=getattr(provider, "provider_config_id", None),
        provider_identity=model_identity(provider),
        result={
            "submission": {
                "input_manifest": manifest,
                "input_hash": digest(manifest),
                "encrypted_input": Secrets.for_data(settings).encrypt(
                    json.dumps({"job_id": str(job_id), "request": request}, ensure_ascii=False)
                ),
            }
        },
    )
    session.add(job)
    await session.flush()
    audit(
        session,
        actor,
        "solution.figures.submitted",
        job_id,
        {"chapter_id": str(chapter.id), "revision": chapter.current_revision},
    )
    receipt = SolutionFiguresReceipt(
        job_id=job_id, chapter_id=chapter.id, revision=chapter.current_revision
    )
    return receipt.model_dump(mode="json"), job


async def current_snapshot(session, actor, job, settings):
    """The fixed input, refused when the chapter or the redaction changed since submission."""
    submission = job.result["submission"]
    expected = submission["input_manifest"]
    provider = await llm_providers.resolve_llm(session, settings, job=job)
    manifest, request, provider, chapter = await snapshot(
        session, actor, expected["chapter_id"], provider, settings, expected.get("reasoning")
    )
    if manifest != expected or digest(manifest) != submission["input_hash"]:
        raise ServiceError(
            "solution_figures_input_changed",
            "The chapter, redaction or figure rules changed; submit again",
            409,
            4,
        )
    try:
        saved = json.loads(Secrets.for_data(settings).decrypt(submission["encrypted_input"]))
    except (InvalidToken, ValueError, TypeError):
        raise ServiceError(
            "solution_figures_input_integrity", "Encrypted figure input cannot be verified", 409, 4
        ) from None
    if saved != {"job_id": str(job.id), "request": request}:
        raise ServiceError(
            "solution_figures_input_integrity", "Figure input does not match its manifest", 409, 4
        )
    return provider, FigureProviderRequest.model_validate(request), chapter


async def publish(session, actor, chapter, figures: dict[str, SolutionFigure], *, job_id):
    """Replace the generated figures with `figures`; uploaded ones stay."""
    _, revision = await solution.load(session, actor, chapter.id, "solution:draft", write=True)
    changes: dict[str, dict | None] = {
        number: None
        for number, figure in solution.figure_numbers(revision.sections).items()
        if figure["origin"] == "generated"
    }
    changes |= {number: figure.model_dump(mode="json") for number, figure in figures.items()}
    return await solution.append(
        session,
        actor,
        chapter,
        with_figures(revision.sections, changes),
        origin="model",
        job_id=job_id,
    )


@task_authorized("solution:edit", parent=("chapter_id", "solution_chapters"), write=True)
async def upload(session, actor, chapter_id, expected_revision, number, caption, content, storage):
    chapter, revision = await solution.load(session, actor, chapter_id, "solution:edit", write=True)
    actor.require("solution:edit")
    solution.expected(chapter, expected_revision)
    if number not in {
        f"{position}.{sub_position}"
        for position, section in enumerate(revision.sections, 1)
        for sub_position, _ in enumerate(section.get("subsections", []), 1)
    }:
        raise ServiceError("invalid_figure", "No such subsection in this chapter", 400, 2)
    figure = image_figure(content, caption, "uploaded")
    await storage.put(storage_key(chapter.task_id, figure), content)
    return await solution.append(
        session,
        actor,
        chapter,
        with_figures(revision.sections, {number: figure.model_dump(mode="json")}),
        origin="human",
    )


async def job_access(session, actor, job, *, cancel=False):
    if job.kind != "solution_figures" or job.task_id is None:
        raise not_found()
    await task_access.access(
        session,
        actor,
        job.task_id,
        "solution:draft" if cancel else "solution:read",
        write=cancel,
    )
