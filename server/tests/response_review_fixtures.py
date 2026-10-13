"""Saved response inputs and installation-human reviews for API acceptance."""

from hashlib import sha256
from uuid import UUID, uuid4

from app.models.entities import Chunk, Document, Job, Requirement
from app.services.auth import HUMAN_SCOPES
from sqlalchemy.orm import Session
from task_access_fixtures import new_task
from task_fixtures import actor_context, confirm_requirements


async def seed_scope(api, headers, admin_engine, *, count=1, quote=None):
    task_id = UUID(await new_task(api, headers))
    document_id, job_id, chunk_id = uuid4(), uuid4(), uuid4()
    requirements = [uuid4() for _ in range(count)]
    quotes = [quote or f"Synthetic requirement {index + 1}." for index in range(count)]
    with Session(admin_engine) as session, session.begin():
        actor_context(session)
        session.add(
            Document(
                id=document_id,
                task_id=task_id,
                name="Synthetic extraction.pdf",
                sha256=sha256(str(task_id).encode()).hexdigest(),
                storage_key=f"synthetic-extraction/{document_id}.pdf",
                media_type="application/pdf",
                page_count=1,
                status="parsed",
                citation_mode="page",
            )
        )
        session.flush()
        session.add(
            Chunk(
                id=chunk_id,
                task_id=task_id,
                document_id=document_id,
                page=1,
                seq=1,
                text="\n".join(quotes),
            )
        )
        session.add(
            Job(
                id=job_id,
                task_id=task_id,
                document_id=document_id,
                kind="extract",
                cache_key=sha256(str(job_id).encode()).hexdigest(),
                status="succeeded",
                actor_kind="session",
                actor_scopes=sorted(HUMAN_SCOPES),
                result={},
            )
        )
        session.flush()
        session.add_all(
            Requirement(
                id=identifier,
                task_id=task_id,
                document_id=document_id,
                chunk_id=chunk_id,
                page=1,
                quote=quote,
                text=quote,
                category="technical",
                starred=False,
                condition={},
                fingerprint=sha256(f"{job_id}:{index}".encode()).hexdigest(),
                job_id=job_id,
            )
            for index, (identifier, quote) in enumerate(zip(requirements, quotes, strict=True))
        )
    return {
        "task_id": task_id,
        "document_id": document_id,
        "job_id": job_id,
        "chunk_id": chunk_id,
        "requirement_ids": requirements,
        "headers": headers,
    }


async def response_scope(api, headers, admin_engine, *, count=1, quote=None):
    scope = await seed_scope(api, headers, admin_engine, count=count, quote=quote)
    with Session(admin_engine) as session, session.begin():
        confirm_requirements(session, scope["task_id"])
    created = await api.post(
        f"/tasks/{scope['task_id']}/cards",
        headers=headers,
        json={
            "extraction_job_id": str(scope["job_id"]),
            "requirement_id": str(scope["requirement_ids"][0]),
            "content": {
                "response_kind": "commitment",
                "response_text": "Synthetic delivery commitment",
                "deviation": "negative",
                "deviation_note": "Synthetic one-day delivery difference",
            },
        },
    )
    assert created.status_code == 200, created.text
    scope["card"] = created.json()["data"]
    return scope


async def submit(api, headers, scope):
    card = scope["card"]
    response = await api.post(
        f"/cards/{card['id']}/actions",
        headers=headers,
        json={"expected_revision": card["revision"], "action": "submit"},
    )
    assert response.status_code == 200, response.text
    scope["card"] = response.json()["data"]
    return scope["card"]


async def refresh_card(api, auth, scope):
    response = await api.get(f"/cards/{scope['card']['id']}", headers=auth)
    assert response.status_code == 200, response.text
    scope["card"] = response.json()["data"]
    return scope["card"]


async def attach_material(api, owner, scope, tmp_path):
    from test_response_cards import CERTIFICATE_PAGE, select_real_materials

    _, _, _, _, source = await select_real_materials(api, owner, str(scope["task_id"]), tmp_path)
    updated = await api.put(
        f"/cards/{scope['card']['id']}",
        headers=owner,
        json={
            "expected_revision": scope["card"]["revision"],
            "content": {
                "response_kind": "evidence",
                "response_text": "Synthetic material is attached",
                "deviation": "negative",
                "deviation_note": "Synthetic reviewed difference",
                "evidence": [
                    {
                        "kind": "certificate_pdf_page",
                        "evidence_source_id": source["id"],
                        "quote": CERTIFICATE_PAGE,
                    }
                ],
            },
        },
    )
    assert updated.status_code == 200, updated.text
    scope["card"] = updated.json()["data"]
    assert scope["card"]["evidence"] and all(
        e["confirmed_at"] is None for e in scope["card"]["evidence"]
    )


async def confirm(api, scope, **changes):
    card = scope["card"]
    return await api.post(
        f"/cards/{card['id']}/actions",
        headers=scope["headers"],
        json={
            "expected_revision": card["revision"],
            "action": "confirm",
            "reviewed_evidence_ids": [row["id"] for row in card["evidence"]],
            "reviewed_warning_codes": card["warning_codes"],
            "reason": "Synthetic human inspected all materials and warnings.",
            **changes,
        },
    )


async def complete(api, owner, scope):
    await submit(api, owner, scope)
    result = await confirm(api, scope)
    assert result.status_code == 200, result.text
    scope["card"] = result.json()["data"]


async def revoke(api, owner, scope, mutation):
    from test_response_cards import require_action

    if mutation == "card_reopen":
        scope["card"] = await require_action(
            api,
            scope["headers"],
            scope["card"],
            "reopen",
            reason="Synthetic review reopened",
        )
    else:
        from test_requirement_confirmation import decision, post_decision, review

        current = await review(api, owner, scope["card"]["requirement_id"])
        response = await post_decision(
            api, owner, scope["card"]["requirement_id"], decision(current, "reopen")
        )
        assert response.status_code == 200, response.text
