import asyncio

from app.core.config import Settings
from app.core.db import Database
from app.jobs.processor import Processor
from app.jobs.queue import Queue
from app.providers.llm import create_llm, resolve_llm
from app.providers.local_ocr import LocalOCR
from app.providers.storage import create_storage


async def run():
    settings = Settings.load()
    db = Database(settings)
    await db.verify_role()
    queue = Queue(settings)
    fallback = create_llm(settings)

    async def resolve(session):
        return await resolve_llm(session, settings, fallback)

    queue.processor = Processor(
        settings,
        db,
        create_storage(settings),
        fallback,
        LocalOCR(settings.ocr_language, settings.ocr_data_dir),
        resolve,
    )
    try:
        async with queue.app.open_async():
            await queue.app.run_worker_async(queues=["bid"])
    finally:
        await db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
