"""Project package round trip personal → gateway → personal (slice 5 acceptance).

Failure matrix before implementation: a project imported into a gateway loses or
rewrites a requirement confirmation, a card confirmation or a citation; the
gateway exports a project whose administrator has not allowed it; a member who
does not own the project exports it; the package that comes back from the
gateway differs from the one that left the personal installation, so a second
personal installation holds another project; a citation in the returned project
names a chunk it lacks. The packages and report land in data/work/package-import.
"""

import json
import os

from test_check import check_case, preview_check, process_check, submit_check  # noqa: F401
from test_members import GATEWAY, bootstrap_admin, code
from test_package_export import entries, export
from test_package_import import ARTIFACTS, installation, run, table_hashes
from test_project_visibility import new_member, signed_in


def rows(content: bytes) -> dict[str, list[dict]]:
    return {
        name[5:-6]: [json.loads(line) for line in body.splitlines()]
        for name, body in entries(content).items()
        if name.startswith("data/")
    }


def decisions(content: bytes) -> dict:
    """The confirmations and citations a round trip must keep, by row id."""
    data = rows(content)
    chunks = {row["id"] for row in data["chunks"]}
    cited = sorted(
        {row["chunk_id"] for row in data["requirements"] if row["chunk_id"]}
        | {
            row["source"]["chunk_id"]
            for row in data["response_items"]
            if isinstance(row.get("source"), dict) and row["source"].get("chunk_id")
        }
        | {
            row["chunk_id"]
            for row in data.get("check_finding_citations", [])
            if row.get("chunk_id")
        }
    )
    assert cited and set(cited) <= chunks, "every citation names a packaged chunk"
    return {
        "requirement_reviews": sorted(
            (row["requirement_id"], row["state"], row["revision"], row["confirmed_at"])
            for row in data["requirement_reviews"]
        ),
        "card_confirmations": sorted(
            (row["card_id"], row["revision"], row["state"], row["confirmed_at"])
            for row in data["response_card_revisions"]
            if row["confirmed_at"]
        ),
        "finding_citations": sorted(
            json.dumps(row, sort_keys=True) for row in data.get("check_finding_citations", [])
        ),
        "cited_chunks": cited,
    }


async def test_package_round_trip(check_case, database, tmp_path, monkeypatch):  # noqa: F811
    case = check_case
    api, app, header, task = case["api"], case["app"], case["header"], case["task"]
    submitted = await submit_check(case, await preview_check(case))
    assert submitted.status_code == 200, submitted.text
    checked = await process_check(case, submitted.json()["data"])
    assert checked["data"]["status"] == "succeeded", checked
    _, source = await export(api, app, header, task)
    kept = decisions(source)
    assert kept["requirement_reviews"] and kept["card_confirmations"], kept
    assert kept["finding_citations"], kept
    report: dict = {
        "rerun": "pytest -q server/tests/test_package_round_trip.py -p no:cacheprovider",
        "confirmed_requirements": sum(
            state == "confirmed" for _, state, _, _ in kept["requirement_reviews"]
        ),
        "confirmed_cards": len(kept["card_confirmations"]),
        "cited_chunks": len(kept["cited_chunks"]),
        "finding_citations": len(kept["finding_citations"]),
    }
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / "round-trip-source.zip").write_bytes(source)

    async with installation(database, tmp_path / "gateway", monkeypatch, enterprise=True) as (
        gateway,
        gateway_app,
    ):
        root = bootstrap_admin("root.admin")
        admin = await signed_in(gateway, "root.admin", root["initial_password"])
        _, owner = await new_member(gateway, admin, "project.owner", ["library:edit"])
        _, other = await new_member(gateway, admin, "other.member")
        imported = await run(gateway, gateway_app, owner, source)
        assert imported["status"] == "succeeded", imported

        refused = await gateway.post("/packages/export", headers=owner, json={"task_id": task})
        report["export_before_sharing"] = code(refused)
        assert report["export_before_sharing"] == [403, "package_export_not_allowed"]
        shared = await gateway.patch(
            f"/tasks/{task}/sharing", headers=admin, json={"export_to_personal_allowed": True}
        )
        assert shared.status_code == 200, shared.text
        outsider = await gateway.post("/packages/export", headers=other, json={"task_id": task})
        report["export_by_non_member"] = code(outsider)
        assert report["export_by_non_member"] == [404, "not_found"]

        _, returned = await export(gateway, gateway_app, owner, task)
        (ARTIFACTS / "round-trip-gateway.zip").write_bytes(returned)
        assert decisions(returned) == kept
        # Only the gateway's sharing switch differs; the receiver clears it on import.
        sent, came = rows(source)["tasks"][0], rows(returned)["tasks"][0]
        assert {k for k in sent if sent[k] != came[k]} == {"export_to_personal_allowed"}
        hashes = table_hashes(returned)
        assert {k for k in hashes if hashes[k] != table_hashes(source)[k]} == {"tasks"}

    for name in GATEWAY:
        monkeypatch.delenv(name)
    async with installation(database, tmp_path / "personal", monkeypatch, enterprise=False) as (
        personal,
        personal_app,
    ):
        session = await personal.post(
            "/auth/session", json={"access_key": os.environ["BID_ACCESS_KEY"]}
        )
        assert session.status_code == 200, session.text
        local = {"Authorization": "Bearer " + session.json()["data"]["session"]}
        back = await run(personal, personal_app, local, returned)
        assert back["status"] == "succeeded", back
        assert back["report"]["source_mode"] == "enterprise", back
        _, again = await export(personal, personal_app, local, task)
        (ARTIFACTS / "round-trip-personal.zip").write_bytes(again)
        assert decisions(again) == kept
        assert table_hashes(again) == table_hashes(source)
        reviews = await personal.get(f"/tasks/{task}/requirements", headers=local)
        assert reviews.status_code == 200 and reviews.json()["items"], reviews.text
        report["round_trip"] = {"tables": len(table_hashes(source)), "identical": True}

    path = ARTIFACTS / "report.json"
    prior = json.loads(path.read_text()) if path.exists() else {}
    path.write_text(json.dumps({**prior, "round_trip": report}, indent=2, sort_keys=True) + "\n")
