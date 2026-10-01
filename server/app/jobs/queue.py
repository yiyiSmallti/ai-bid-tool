import asyncio
from collections.abc import Awaitable, Callable

import procrastinate
from sqlalchemy.engine import make_url

from app.core.config import Settings


class Queue:
    def __init__(self, settings: Settings):
        url = make_url(settings.database_url.get_secret_value()).set(drivername="postgresql")
        self.app = procrastinate.App(
            connector=procrastinate.PsycopgConnector(
                conninfo=url.render_as_string(hide_password=False), min_size=1, max_size=5
            )
        )
        self.processor: Callable[[str, str], Awaitable[None]] | None = None

        @self.app.task(
            name="bid.process",
            queue="bid",
            retry=procrastinate.RetryStrategy(max_attempts=3, wait=5),
        )
        async def process(org_id: str, job_id: str):
            if self.processor is None:
                raise RuntimeError("Worker processor is not configured")
            await self.processor(org_id, job_id)

        self.task = process
        self.lock = asyncio.Lock()

    async def enqueue(self, org_id: str, job_id: str) -> int:
        async with self.lock, self.app.open_async():
            return await self.task.defer_async(org_id=org_id, job_id=job_id)
