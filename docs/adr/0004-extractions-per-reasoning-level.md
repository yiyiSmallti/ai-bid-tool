---
kind: adr
---

# 0004 Use official reasoning levels and retain each extraction separately

Date: 2026-10-02. Status: accepted.

## Context

Reasoning effort affects extraction completeness, latency, and usage, with differences of up to tenfold. Thinking switches and batch sizes were deployment-wide environment variables, unavailable for user selection. Requirements merged by “same task, same fingerprint,” mixing entries at different granularities when a document was extracted twice at different effort levels.

## Decision

- Use the provider's published official values for the model (for example, Zhipu GLM-5.3 `low`, `high`, `max`), without inventing “fast/deep” levels. Providers have no endpoint listing levels, so platform administrators register them in the model catalog from documentation. Each level includes request parameters, batch size, and Anthropic effort.
- Use the provider's official default level when the user omits one.
- Persist the level on the job and include it in the cache key: changing levels creates a new job; repeating the same level returns the original job.
- Requirements belong to the extraction job that produced them. By default, `req list` shows the latest successful extraction per document. Retain older results for job-specific viewing; `req history` lists all extractions.
- `req list` no longer merges extractions. Its returned content changes, so upgrade the Result contract from 1.1 to 1.2.

## Tradeoffs

- Users see provider-specific level names that differ between models, avoiding a maintained mapping and a misleading compression into two levels.
- The official default is often the highest level: most complete, slowest, and highest usage.
- Zhipu's Coding Plan endpoint accepts “disable thinking,” but the documentation says GLM-5.3 cannot disable it, so it is not registered as a level.
- Merging helped retries but mixed entries at different granularities. Retries now stay within one job.

See [reasoning-levels.md](../notes/reasoning-levels.md) for the mechanism.
