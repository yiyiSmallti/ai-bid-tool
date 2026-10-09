"""Explicit 3.0 output projection over the shared 4.0 execution paths."""

from typing import Any

LEGACY_COST_FIELDS = frozenset({"llm_tokens", "ocr_pages", "usd"})
NEW_COMMANDS = frozenset(
    {
        "review presence prepare",
        "review presence preview",
        "review presence authorize",
        "platform clef show",
        "platform clef set",
        "platform clef check",
        "platform trust-anchor list",
        "platform trust-anchor add",
        "platform trust-anchor disable",
        "evidence stamp",
        "evidence annotation list",
        "evidence annotation show",
        "evidence annotation preview",
        "evidence annotation releases",
        "evidence annotation release preview",
        "evidence annotation release retry",
        "task budget show",
        "task budget set",
        "task budget history",
        "billing alert show",
        "billing alert set",
        "billing notices",
        "product simulate",
        "req review-list",
        "req show",
        "req review-history",
        "req rejected",
        "req add",
        "req confirm",
        "req reopen",
        "req confirm-batch",
    }
)


def legacy_projection(
    value: Any, *, cost: bool = False, path: tuple[str, ...] = (), command: str | None = None
) -> Any:
    if isinstance(value, list):
        return [legacy_projection(item, path=path, command=command) for item in value]
    if not isinstance(value, dict):
        if (
            isinstance(value, str)
            and path
            and path[-1] in {"eligibility", "reasons", "gap_reasons"}
        ):
            return {
                "requirement_unconfirmed": "unconfirmed",
                "requirement_invalidated": "needs_reconfirmation",
            }.get(value, value)
        return value
    if not path:
        command = value.get("command", command)

    def added_attachment(key, item):
        if key in {
            "requirement_review",
            "requirement_reviews",
            "requirement_preparation",
            "review_requirement_id",
        }:
            return True
        if key == "agent_provenance":
            return True
        if key == "budget_preflight" and path == ("data",):
            return True
        if key != "budget":
            return False
        if path == ("data", "result") or (path == ("data",) and command == "task create"):
            return True
        # Waiting commands flatten their published domain result into data.
        return (
            path == ("data",)
            and isinstance(item, dict)
            and {"job_id", "run_id", "completion", "continuation", "cost"} <= item.keys()
        )

    return {
        key: legacy_projection(
            item,
            cost=key in {"cost", "estimated_cost", "cached_result_cost"},
            path=(*path, key),
            command=command,
        )
        for key, item in value.items()
        if (not cost or key in LEGACY_COST_FIELDS) and not added_attachment(key, item)
    }
