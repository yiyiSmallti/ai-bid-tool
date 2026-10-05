"""Check pages preserve risk/source order before bounded narrative hydration.

Failure modes: UUID-only pages scramble severity and saved requirement order;
per-row finding queries grow with the page; returning raw limitation codes omits
localized instructions. API traversal must retain all rows without duplicates.
"""

from test_check import check_case as check_case
from test_console_assessments_db import published


async def test_console_check_pages_preserve_risk_and_fixed_requirement_order(check_case):
    case = check_case
    report_id = await published(case)
    full = (await case["api"].get(f"/checks/{report_id}", headers=case["header"])).json()
    severity = {"disqualification_risk": 0, "deduction_risk": 1, "info": 2}
    domain = {"commercial": 0, "technical": 1, None: 2}
    expected_findings = [
        row["id"]
        for row in sorted(
            full["items"],
            key=lambda row: (severity[row["severity"]], domain[row["review_domain"]], row["id"]),
        )
    ]
    expected_coverage = [row["id"] for row in full["data"]["coverage"]]
    for part, expected in (("findings", expected_findings), ("coverage", expected_coverage)):
        seen = []
        cursor = None
        while True:
            response = await case["api"].get(
                f"/v4/checks/{report_id}",
                params={
                    "view": "console",
                    "part": part,
                    "limit": 2,
                    **({"cursor": cursor} if cursor else {}),
                },
                headers=case["header"],
            )
            assert response.status_code == 200, response.text
            body = response.json()
            seen.extend(row["id"] for row in body["items"])
            cursor = body["data"]["next_cursor"]
            if cursor is None:
                break
        assert seen == expected
        assert len(seen) == len(set(seen))


async def test_console_finding_page_does_not_hydrate_via_per_row_legacy_reader(
    check_case, monkeypatch
):
    from app.services import check

    case = check_case
    report_id = await published(case)

    async def legacy_row_reader(*args, **kwargs):
        raise AssertionError("Console finding hydration must batch citations and review metadata")

    monkeypatch.setattr(check, "finding_view", legacy_row_reader)
    response = await case["api"].get(
        f"/v4/checks/{report_id}?view=console&part=findings", headers=case["header"]
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"]
    assert all(row["revision"] >= 1 for row in response.json()["items"])
