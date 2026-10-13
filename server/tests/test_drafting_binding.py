"""Drafting runs bind their preview hash to unchanged inputs before dispatch."""

from app.models.entities import Job
from sqlalchemy import func, select
from test_card_generation import (  # pyright: ignore[reportMissingImports]
    drafting_client,
    execute,
    submit,
)
from test_response_cards import create_tender  # pyright: ignore[reportMissingImports]


async def generation_post(api, header, task, extraction, **body):
    return await api.post(
        f"/tasks/{task}/cards/generations",
        headers=header,
        json={"extraction_job_id": extraction, **body},
    )


async def test_preview_hash_binding_before_dispatch(database, tmp_path):
    async with drafting_client(database, tmp_path) as (api, app, headers, vendor, _):
        header = headers
        task, _, extraction, requirements = await create_tender(api, app, header, tmp_path)
        first = [requirements[2]["id"]]
        preview = (
            await submit(api, header, task, extraction, requirement_ids=first, dry_run=True)
        )["data"]
        changed = await generation_post(
            api,
            header,
            task,
            extraction,
            requirement_ids=[requirements[2]["id"], requirements[3]["id"]],
            expected_input_hash=preview["input_hash"],
        )
        assert changed.status_code == 409, changed.text
        assert changed.json()["data"]["error"]["code"] == "generation_input_changed"
        async with app.state.db.transaction() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(Job).where(Job.kind == "card_generate")
                )
                == 0
            )
        bound = await submit(
            api,
            header,
            task,
            extraction,
            requirement_ids=first,
            expected_input_hash=preview["input_hash"],
        )
        terminal = await execute(api, app, header, bound)
        assert terminal["data"]["status"] == "succeeded", terminal
        assert len(vendor.drafts) == 1
