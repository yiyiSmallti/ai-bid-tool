import asyncio
import json
import os
from uuid import UUID, uuid4

import httpx
from app import admin
from app.api.main import create_app
from app.core.config import Settings
from app.core.security import Secrets
from app.models.confidential import ConfidentialField, ConfidentialValue
from app.providers.storage import FileCipher
from app.services.auth import HUMAN_SCOPES, Identity, set_actor_context
from conftest import FakeQueue
from cryptography.fernet import Fernet
from sqlalchemy import select, text
from sqlalchemy.orm import Session


async def test_rotation_moves_fields_and_objects_to_the_current_key(
    database, tmp_path, admin_engine, monkeypatch, pdf_bytes
):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    objects = tmp_path / "objects"
    monkeypatch.setenv("BID_ENCRYPTION_KEY", old)
    monkeypatch.setenv("BID_DATA_DIR", str(objects))
    headers = {"Authorization": "Bearer " + os.environ["BID_ACCESS_KEY"]}
    settings = Settings(data_dir=objects)
    app = create_app(settings, queue=FakeQueue())
    value_id, field_id = uuid4(), uuid4()
    plaintext = "Synthetic encryption rotation fixture"
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api,
    ):
        created = await api.post("/tasks", headers=headers, json={"name": "Rotation fixture"})
        assert created.status_code == 200, created.text
        task_id = created.json()["data"]["id"]
        uploaded = await api.post(
            f"/tasks/{task_id}/documents",
            headers=headers,
            files={"file": ("fixture.pdf", pdf_bytes)},
        )
        assert uploaded.status_code == 200, uploaded.text
        document_id = uploaded.json()["data"]["id"]
        submitted = await api.post(
            f"/documents/{document_id}/parse", headers=headers, json={"dry_run": False}
        )
        assert submitted.status_code == 200, submitted.text
        job_id = submitted.json()["data"]["job_id"]
        await app.state.processor(job_id)
        parsed = await api.get(f"/jobs/{job_id}", headers=headers)
        assert parsed.json()["data"]["status"] == "succeeded", parsed.text
        async with app.state.db.transaction() as session:
            await set_actor_context(session, Identity(scopes=set(HUMAN_SCOPES)))
            session.add(
                ConfidentialField(
                    id=field_id,
                    key="rotation_fixture",
                    label="Synthetic rotation fixture",
                    kind="other",
                    scope="task",
                )
            )
            await session.flush()
            session.add(
                ConfidentialValue(
                    id=value_id,
                    field_id=field_id,
                    task_id=UUID(task_id),
                    version=1,
                    encrypted_value=Secrets.for_data(settings).encrypt(plaintext),
                )
            )

    monkeypatch.setenv("BID_ENCRYPTION_KEY", new)
    monkeypatch.setenv("BID_ENCRYPTION_KEY_PREVIOUS", old)
    first = await asyncio.to_thread(admin.rotate_encryption)
    assert first["fields_checked"] >= 1
    assert first["fields_rewritten"] == first["fields_checked"]
    assert first["objects_rewritten"] == first["objects_checked"] > 0
    second = await asyncio.to_thread(admin.rotate_encryption)
    assert second["fields_rewritten"] == second["objects_rewritten"] == 0
    assert second["fields_checked"] == first["fields_checked"]
    assert second["objects_checked"] == first["objects_checked"]

    current = Fernet(new.encode())
    with Session(admin_engine) as session, session.begin():
        for table, column in admin.ENCRYPTED_COLUMNS:
            for value in session.scalars(
                text(f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL")
            ):
                current.decrypt(value.encode())
    cipher = FileCipher(new)
    stored = [path for path in objects.rglob("*") if path.is_file()]
    assert len(stored) == first["objects_checked"]
    for path in stored:
        cipher.decrypt(path.relative_to(objects).as_posix(), path.read_bytes())

    monkeypatch.delenv("BID_ENCRYPTION_KEY_PREVIOUS")
    fresh_settings = Settings(data_dir=objects)
    fresh = create_app(fresh_settings, queue=FakeQueue())
    async with (
        fresh.router.lifespan_context(fresh),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=fresh), base_url="http://test") as api,
    ):
        link = await api.get(f"/documents/{document_id}/download-link", headers=headers)
        assert link.status_code == 200, link.text
        downloaded = await api.get(link.json()["data"]["url"], headers=headers)
        assert downloaded.status_code == 200 and downloaded.content == pdf_bytes
        async with fresh.state.db.transaction() as session:
            ciphertext = await session.scalar(
                select(ConfidentialValue.encrypted_value).where(ConfidentialValue.id == value_id)
            )
            assert Secrets.for_data(fresh_settings).decrypt(ciphertext) == plaintext
    (tmp_path / "rotation-report.json").write_text(
        json.dumps({"first": first, "repeated": second, "retired_key_removed": True}, indent=2)
    )
