"""Token budget for long agent sessions.

A decision request sends the latest stored summary and the messages after it.
When those exceed the recent budget, the older ones are summarized first and
only the newest messages up to the kept budget stay verbatim, so a summary is
not needed again on the very next step. Originals stay stored either way.
"""

import math
from dataclasses import dataclass

REPLY_RESERVE_TOKENS = 16_384
RECENT_TOKENS = 20_000
KEEP_TOKENS = 12_000
# The smallest context window a pinned model must have. The request byte
# ceiling estimates below it with the reply reserve, so a request that fits the
# ceiling also leaves room for the reply. Tool result schemas alone take about
# a third of the ceiling.
CONTEXT_TOKENS = 128_000
WIRE_BYTES = 256 * 1024
assert WIRE_BYTES // 3 + REPLY_RESERVE_TOKENS <= CONTEXT_TOKENS
TOOL_RESULT_TOKENS = 8_000
# Message counts bound the decision's input references, which also carry the
# extraction's requirements and cards.
RECENT_MESSAGES = 40
KEEP_MESSAGES = 24


def estimate(text: str) -> int:
    """Conservative token estimate: about one token per CJK character, three
    ASCII characters per token; real tokenizers produce fewer."""
    return math.ceil(len(text.encode()) / 3)


def cut_to_tokens(text: str, tokens: int, hint: str) -> str:
    """Text within `tokens`, ending with `hint` when anything was cut."""
    if estimate(text) <= tokens:
        return text
    budget = tokens * 3 - len(hint.encode())
    kept = text.encode()[:budget].decode(errors="ignore")
    return kept + hint


@dataclass(frozen=True)
class ContextPlan:
    summary: object | None
    recent: list
    # Ordinal range to summarize before the next decision, or None.
    covers: tuple[int, int] | None
    covered: list


def plan(stored) -> ContextPlan:
    """Split stored messages, in ordinal order, into summary, recent and to-cover."""
    summaries = [message for message in stored if message.role == "summary"]
    summary = summaries[-1] if summaries else None
    after = summary.covers_to if summary is not None else 0
    candidates = [
        message for message in stored if message.role != "summary" and message.ordinal > after
    ]
    if (
        len(candidates) <= RECENT_MESSAGES
        and sum(estimate(message.content) for message in candidates) <= RECENT_TOKENS
    ):
        return ContextPlan(summary, candidates, None, [])
    kept, used = [], 0
    for message in reversed(candidates):
        cost = estimate(message.content)
        if kept and (used + cost > KEEP_TOKENS or len(kept) >= KEEP_MESSAGES):
            break
        kept.insert(0, message)
        used += cost
    older = candidates[: len(candidates) - len(kept)]
    if not older:
        return ContextPlan(summary, candidates, None, [])
    start = summary.covers_from if summary is not None else older[0].ordinal
    covered = ([summary] if summary is not None else []) + older
    return ContextPlan(summary, kept, (start, older[-1].ordinal), covered)
