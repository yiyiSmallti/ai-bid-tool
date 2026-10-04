import asyncio

from app import admin
from app.providers.storage import FileCipher
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_response_cards import create_tender  # pyright: ignore[reportMissingImports]
from test_vendor_screenshots import (  # pyright: ignore[reportMissingImports]
    capture,
    download,
    select_product,
    vendor_client,
)


async def test_rotation_moves_fields_and_objects_to_the_current_key(
    tenants, tmp_path, admin_engine, monkeypatch
):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    objects = tmp_path / "objects"
    monkeypatch.setenv("BID_ENCRYPTION_KEY", old)
    async with vendor_client(tenants, tmp_path, monkeypatch) as (api, app, headers):
        task, _, extraction, _ = await create_tender(api, app, headers[0], tmp_path)
        product, selection = await select_product(api, headers[0], task)
        artifacts, _ = await capture(
            api, app, headers[0], task, extraction, product, selection, archive="bundle"
        )

    monkeypatch.setenv("BID_ENCRYPTION_KEY", new)
    monkeypatch.setenv("BID_ENCRYPTION_KEY_PREVIOUS", old)
    monkeypatch.setenv("BID_DATA_DIR", str(objects))
    first = await asyncio.to_thread(admin.rotate_encryption)
    assert first["orgs"] == 2 and first["fields_checked"] >= 2
    assert first["fields_rewritten"] == first["fields_checked"]
    assert first["objects_rewritten"] == first["objects_checked"] > 0
    # Everything already uses the current key, so a repeated run changes nothing.
    second = await asyncio.to_thread(admin.rotate_encryption)
    assert second["fields_rewritten"] == second["objects_rewritten"] == 0
    assert second["fields_checked"] == first["fields_checked"]

    current = Fernet(new.encode())
    with Session(admin_engine) as session, session.begin():
        session.execute(
            text("SELECT set_config('app.current_org', :org, true)"),
            {"org": str(tenants["orgs"][0])},
        )
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

    # With the retired key removed, the application still serves what it stored before.
    monkeypatch.delenv("BID_ENCRYPTION_KEY_PREVIOUS")
    async with vendor_client(tenants, tmp_path, monkeypatch) as (api, app, headers):
        await download(api, headers[0], artifacts["capture_png"])
