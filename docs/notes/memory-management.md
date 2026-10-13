---
kind: reference
---

# Memory management reads

The management surface provides bounded browsing of installation memory, exact
revision detail and task feedback recovery. Memory authority, lifecycle and safety
are defined in [memory mechanisms](memory.md); management pagination and response
bounds are defined in [the management contract](../plan/management-pages.md#memory-and-feedback).

## Browse and detail

`bid memory browse --input QUERY.json --json` uses `MemoryQuery` from
[`management_pages.py`](../../server/app/schemas/management_pages.py). The only
enabled query scope is `library`. Text search applies normalized prefix tokens,
tags use all-tag matching, and expiry uses the server time fixed for the page.
Current-root search projections avoid scanning historical revisions.

Pages use signed continuations bound to the live actor, collection and filters.
Each continuation rechecks authority. Response bounds may shorten a page while
preserving its continuation; responses do not calculate an all-library total.
Exact detail includes the selected revision, current revision, effective status,
`as_of`, `revised_at` and live action hints. Historical revision detail does not
change the current entry or supply authority to mutate it.

Creation, edits and decisions use the existing memory write contracts. Action hints
express live authority; writes check it again. A disabled entry requires an edit
and another approval. A deleted entry retains history for an authorized human
management session.

## Feedback recovery

Management feedback reads select `?management=true` on the task feedback endpoint.
The default projection retains the `MemoryFeedbackView` contract. Management rows
add the saved job ID, job status, exact recorded event set and resulting candidate
link. Retry submits those recorded events through the candidate service. It does
not repeat the human card decision or invent a feedback event. Task and card read
scopes are checked independently from memory scopes.

## Implementation and verification

[`management.py`](../../server/app/memory/management.py) owns bounded reads;
[`api/management_memory.py`](../../server/app/api/management_memory.py) supplies
HTTP adapters and [`management_memory.py`](../../cli/bid_cli/management_memory.py)
supplies CLI parsing and result validation. Mutations remain in the memory services
linked from [memory mechanisms](memory.md#interfaces).

[`test_management_memory.py`](../../server/tests/test_management_memory.py)
exercises live pagination, history, action hints, projection integrity and feedback
recovery through HTTP. The fixed-scale acceptance in
[`test_management_memory_scale.py`](../../server/tests/test_management_memory_scale.py)
measures real queries and mutations and writes repeatable receipts and query plans
under `data/work/management-pages-validation/memory-scale`.
