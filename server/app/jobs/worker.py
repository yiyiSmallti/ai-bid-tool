import asyncio
import json
import sys

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import ServiceError
from app.jobs.processor import Processor
from app.jobs.queue import Queue
from app.providers.disabled import DisabledLLM
from app.providers.llm import resolve_llm
from app.providers.local_ocr import LocalOCR
from app.providers.storage import create_storage


async def run():
    settings = Settings.load()
    db = Database(settings)
    try:
        await db.verify_role()
    except ServiceError as exc:
        await db.engine.dispose()
        print(json.dumps({"error": exc.code, "message": exc.message}), file=sys.stderr)
        raise SystemExit(exc.exit_code) from None
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
    queue.processor.queue = queue
    try:
        async with queue.app.open_async():
            await queue.recover_agent_wakes()
            await queue.recover_sandbox_jobs()
            await queue.recover_process_jobs()
            await queue.app.run_worker_async(queues=["bid"])
    finally:
        await db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
