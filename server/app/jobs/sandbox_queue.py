"""Bounded dispatch retries while an organization waits for a sandbox slot."""

import procrastinate


def register_sandbox_task(app: procrastinate.App, processor):
    # Capacity waits do not claim a business attempt or start a container. Keep
    # redispatching past the queue deadline so the processor can record expiry.
    @app.task(
        name="bid.sandbox", queue="bid", retry=procrastinate.RetryStrategy(max_attempts=125, wait=5)
    )
    async def process(org_id: str, job_id: str):
        active = processor()
        if active is None:
            raise RuntimeError("Worker processor is not configured")
        await active(org_id, job_id)

    return process
