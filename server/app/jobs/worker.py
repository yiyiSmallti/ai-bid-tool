import asyncio

from app.core.config import Settings
from app.core.db import Database
from app.jobs.processor import Processor
from app.jobs.queue import Queue
from app.providers.disabled import DisabledLLM
from app.providers.llm import resolve_llm
from app.providers.local_ocr import LocalOCR
from app.providers.storage import create_storage
from app.services import billing


async def run():
    settings = Settings.load()
    from app.core.credential_db import get_connections

    await get_connections(settings).reader.verify()
    db = Database(settings)
    await db.verify_role()
    await billing.verify_currency(db, settings.billing_currency)
    queue = Queue(settings)

    async def resolve(session, job=None):
        return await resolve_llm(session, settings, job=job)

    queue.processor = Processor(
        settings,
        db,
        create_storage(settings),
        DisabledLLM(),
        LocalOCR(settings.ocr_language, settings.ocr_data_dir),
        resolve,
    )
    try:
        async with queue.app.open_async():
            await queue.app.run_worker_async(queues=["bid"])
    finally:
        from app.core.credential_db import close_connections

        await close_connections(settings)
        await db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
