"""Output limits belong to the adapter, never to vendor request options."""

from typing import Any

# Include common endpoint aliases and spelling variants, including nested
# generation options. Reasoning budgets (e.g. budget_tokens) are not output limits.
OUTPUT_LIMIT_NAMES = frozenset(
    {
        "maxtokens",
        "maxcompletiontokens",
        "maxoutputtokens",
        "maxnewtokens",
        "maxtokenstosample",
        "maxgenlen",
        "maxlength",
        "numpredict",
    }
)


def output_limits(value: Any, *, nested: bool = True) -> list[Any]:
    limits = []
    if isinstance(value, dict):
        for key, option in value.items():
            if key.replace("_", "").replace("-", "").casefold() in OUTPUT_LIMIT_NAMES:
                limits.append(option)
            elif nested:
                limits.extend(output_limits(option))
    elif nested and isinstance(value, list):
        for option in value:
            limits.extend(output_limits(option))
    return limits


def validate_request_options(options: dict) -> dict:
    if output_limits(options):
        raise ValueError("request_options cannot set adapter-owned output limits or aliases")
    return options
