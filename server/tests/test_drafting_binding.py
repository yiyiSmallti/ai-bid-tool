"""Paid drafting runs bound to their preview hash and a per-job charge cap.

Failure modes enumerated before implementation:
- a paid run whose inputs, model or price changed since the preview must not create a job;
- a cap below the first call's reservation must be reported by the preview;
- a running job must stop admitting calls once the next reservation would pass the cap,
  keep valid partial results, and never charge more than the cap;
- a queued job with a higher or no cap must not be reused by a lower-cap request;
- retrying a failed job applies the new cap to its cumulative charge.
"""

import asyncio
import json
from decimal import Decimal
from pathlib import Path

from app.models.entities import Job, VendorCall
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


async def test_preview_hash_binding_and_cap_preflight(tenants, tmp_path):
    async with drafting_client(tenants, tmp_path) as (api, app, headers, vendor, _):
        header = headers[0]
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
            max_charge="1",
        )
        assert changed.status_code == 409, changed.text
        assert changed.json()["data"]["error"]["code"] == "generation_input_changed"
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(Job).where(Job.kind == "card_generate")
                )
                == 0
            )

        tiny = Decimal(preview["estimated_charge"]) / 2
        capped = (
            await submit(
                api,
                header,
                task,
                extraction,
                requirement_ids=first,
                dry_run=True,
                max_charge=str(tiny),
            )
        )["data"]
        assert capped["admission_blocker"] == "spend_cap_below_first_call"
        assert Decimal(capped["max_charge"]) == tiny
        assert capped["input_hash"] == preview["input_hash"]

        bound = await submit(
            api,
            header,
            task,
            extraction,
            requirement_ids=first,
            expected_input_hash=preview["input_hash"],
            max_charge=str(preview["estimated_charge"]),
        )
        terminal = await execute(api, app, header, bound)
        assert terminal["data"]["status"] == "succeeded", terminal
        assert len(vendor.drafts) == 1


async def test_cap_stops_admission_keeps_partial_and_bounds_charge(tenants, tmp_path):
    async with drafting_client(tenants, tmp_path, llm_batch_chars=1) as (
        api,
        app,
        headers,
        vendor,
        _,
    ):
        header = headers[0]
        task, _, extraction, requirements = await create_tender(api, app, header, tmp_path)
        selected = [row["id"] for row in requirements[2:5]]
        first = (
            await submit(api, header, task, extraction, requirement_ids=selected[:1], dry_run=True)
        )["data"]
        full = (
            await submit(api, header, task, extraction, requirement_ids=selected, dry_run=True)
        )["data"]
        # The synthetic vendor bills 1,000 tokens (0.001) per call, far below a reservation;
        # this margin admits exactly the first call and refuses the second.
        cap = Decimal(first["estimated_charge"]) + Decimal("0.0005")
        receipt = await submit(
            api,
            header,
            task,
            extraction,
            requirement_ids=selected,
            expected_input_hash=full["input_hash"],
            max_charge=str(cap),
        )
        terminal = await execute(api, app, header, receipt)
        result = terminal["data"]["result"]
        assert terminal["data"]["status"] == "succeeded", terminal
        assert result["completion"] == "partial" and result["stop_reason"] == "spend_cap_reached"
        assert len(vendor.drafts) == 1 and len(result["created_revision_ids"]) == 1
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            charged = await session.scalar(
                select(func.coalesce(func.sum(VendorCall.charge), 0)).where(
                    VendorCall.job_id == receipt["data"]["job_id"]
                )
            )
        assert charged is not None and Decimal(charged) <= cap

        artifact = Path("data/work/drafting-binding")
        await asyncio.to_thread(artifact.mkdir, parents=True, exist_ok=True)
        (artifact / "cap-stop.json").write_text(
            json.dumps(
                {
                    "cap": str(cap),
                    "charged": str(charged),
                    "vendor_calls": len(vendor.drafts),
                    "stop_reason": result["stop_reason"],
                    "synthetic": True,
                },
                indent=2,
            )
        )


async def test_queued_job_cap_conflict_and_retry_applies_new_cap(tenants, tmp_path):
    async with drafting_client(tenants, tmp_path) as (api, app, headers, vendor, _):
        header = headers[0]
        task, _, extraction, requirements = await create_tender(api, app, header, tmp_path)
        selected = [requirements[2]["id"]]
        uncapped = await submit(api, header, task, extraction, requirement_ids=selected)
        conflict = await generation_post(
            api, header, task, extraction, requirement_ids=selected, max_charge="5"
        )
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["data"]["error"]["code"] == "generation_cap_conflict"

        # A tiny cap on retry refuses the first call: the job fails without any vendor call.
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            job = await session.get(Job, uncapped["data"]["job_id"])
            assert job is not None
            job.status = "failed"
        retried = await submit(
            api,
            header,
            task,
            extraction,
            requirement_ids=selected,
            retry=True,
            max_charge="0.00000001",
        )
        assert retried["data"]["job_id"] == uncapped["data"]["job_id"]
        terminal = await execute(api, app, header, retried)
        assert terminal["data"]["status"] == "failed", terminal
        assert terminal["data"]["error"]["code"] == "spend_cap_reached"
        assert not vendor.drafts
        higher = await submit(
            api, header, task, extraction, requirement_ids=selected, retry=True, max_charge="5"
        )
        terminal = await execute(api, app, header, higher)
        assert terminal["data"]["status"] == "succeeded", terminal
        assert len(vendor.drafts) == 1
