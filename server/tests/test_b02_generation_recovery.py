"""API/worker regressions for B02 preparation scope binding.

Failures to prevent: a selected subset is compared to the whole extraction and
never reaches the provider; confirming an unchanged source discards preparation;
an explicit manual append is missed when the worker validates only selected IDs.
These scenarios use real review/manual services and preserve the worker fences.
"""

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.models.entities import Job
from task_fixtures import confirm_requirements_async
from test_card_generation import drafting_client, execute, submit
from test_response_cards import create_tender


def artifact(scenario, data):
    output = Path(__file__).resolve().parents[2] / "data/work/b02-fixes/generation"
    output.mkdir(parents=True, exist_ok=True)
    (output / f"{scenario}.json").write_text(json.dumps(data, indent=2) + "\n")


@pytest.mark.parametrize("change", ["unchanged", "confirmation_only", "manual_append"])
async def test_subset_preparation_binds_whole_scope_and_keeps_human_review_separate(
    tenants, tmp_path, change
):
    async with drafting_client(tenants, tmp_path) as (api, app, headers, vendor, _):
        header = headers[0]
        task, _, extraction, requirements = await create_tender(api, app, header, tmp_path)
        selected = requirements[2]["id"]
        receipt = await submit(api, header, task, extraction, requirement_ids=[selected])
        org = UUID(header["X-Org-Id"])
        async with app.state.db.transaction(org) as session:
            job = await session.get(Job, UUID(receipt["data"]["job_id"]))
            manifest = job.result["submission"]["input_manifest"]
            assert {item["requirement_id"] for item in manifest["requirements"]} == {selected}
            assert {
                item["requirement_id"] for item in manifest["requirement_preparation"]["entries"]
            } == {row["id"] for row in requirements}
            if change == "confirmation_only":
                await confirm_requirements_async(
                    session, org, UUID(task), settings=app.state.processor.settings
                )
        if change == "manual_append":
            review = await api.get(f"/v4/requirements/{selected}/review", headers=header)
            assert review.status_code == 200, review.text
            data = review.json()["data"]
            body = {
                "extraction_job_id": extraction,
                "expected_set_revision": data["scope"]["revision"],
                "content": {
                    **data["requirement"]["content"],
                    "text": "Synthetic manually recovered delivery interpretation",
                    "source": {
                        **data["requirement"]["content"]["source"],
                        "quote": "thirty calendar days",
                    },
                },
                "reason": "Synthetic reviewer found an omitted requirement",
            }
            preview = await api.post(
                f"/v4/tasks/{task}/requirements/manual-preview", headers=header, json=body
            )
            assert preview.status_code == 200, preview.text
            added = await api.post(
                f"/v4/tasks/{task}/requirements/manual",
                headers=header,
                json={
                    **body,
                    "request_id": str(uuid4()),
                    "expected_preview_hash": preview.json()["data"]["preview_hash"],
                },
            )
            assert added.status_code == 201, added.text
        terminal = await execute(api, app, header, receipt)
        if change == "manual_append":
            assert terminal["data"]["status"] == "failed", terminal
            assert terminal["data"]["error"]["code"] == "review_changed"
            assert not vendor.drafts
        else:
            assert terminal["data"]["status"] == "succeeded", terminal
            assert vendor.drafts
        artifact(
            change,
            {
                "scenario": change,
                "task_id": task,
                "extraction_job_id": extraction,
                "generation_job_id": receipt["data"]["job_id"],
                "state": terminal["data"]["status"],
                "provider_calls": len(vendor.drafts),
                "scope_members": len(manifest["requirement_preparation"]["entries"]),
            },
        )
