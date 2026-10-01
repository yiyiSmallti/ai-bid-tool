# LLM extraction providers

## Problem

Requirement extraction must call a real model, record what each call cost,
and never save a requirement whose citation cannot be checked against the
stored page text. Vendor outages, refusals, and truncated output must end in
a defined job state rather than partial or silent results.

## Usage

Select the platform model with `BID_LLM_PROVIDER` (`anthropic` or `openai`)
and its `BID_LLM_*` settings, then restart the API and worker. Setup steps are
in [development.md](../guides/development.md#configure-the-extraction-model).
Tests inject an adapter built with an `httpx.MockTransport`; production code
calls `create_llm(settings)`.

## How it works

Both adapters call the vendor over httpx; no vendor SDK is installed. Pages
are grouped into batches of at most `BID_LLM_BATCH_CHARS` characters, and a
page larger than the budget gets a batch of its own rather than being cut.
Batches run one after another.

The model sees each page as `<page number="N">` and returns items that cite a
page number and a verbatim quote. Output is constrained to `WIRE_SCHEMA`:
Anthropic through `output_config.format`, OpenAI-compatible services through
`response_format` (`json_schema`, or `json_object` with the schema in the
prompt). The adapter maps each page number back to its chunk ID and document
ID, so the model never reproduces UUIDs. `condition` is typed as
`param/op/value/unit` or null, because structured outputs require closed
objects. The processor then checks every quote against the stored page text
and rejects the whole result if any quote fails.

On Anthropic, effort comes from `BID_LLM_EFFORT`, and a safety decline is
retried server-side on another model (`fallbacks: "default"`) unless
`BID_LLM_ANTHROPIC_FALLBACK=false`. The usage record stores the model that
actually answered.

| Vendor result | Job outcome |
| --- | --- |
| Timeout, connection error, HTTP 408/409/429/5xx/529 | Requeued; error exit code 3; at most three attempts |
| Other HTTP errors, such as 400 or 401 | Failed, `provider_unavailable`, exit 4 |
| Refusal | Failed, `provider_refused` |
| Truncated output, malformed JSON, schema mismatch, page outside the batch | Failed, `invalid_provider_output` |
| A quote that is not verbatim | Failed, `invalid_citation`; nothing saved |

Each completed call produces one `ProviderUsage`. When a later batch fails,
`ProviderFailure.usage` carries the earlier calls and the processor records
them, because the vendor billed them. USD is computed from the configured
per-million-token prices, or `null` when either price is missing.

## Pitfalls

- Error messages keep only the HTTP status and vendor error type. Response
  bodies can echo input, so they are never stored or logged.
- An empty environment variable counts as unset; a selected provider without
  its key or model stops startup.
- The configured prices apply to whichever model answered. After a refusal
  fallback, the recorded cost is an estimate at the primary model's prices.
- Whole-result rejection means one altered quote discards a paid extraction.
  `evals/extract_tender.py` reports the verbatim rate per item so the policy
  can be judged on real tenders.
- The cache key includes the provider, model, adapter version, and
  `PROMPT_VERSION`; bump `ADAPTER_VERSION` when the prompt or schema changes.

## Code

- [server/app/providers/llm.py](../../server/app/providers/llm.py): `AnthropicExtractor`, `OpenAICompatibleExtractor`, `create_llm`, `WIRE_SCHEMA`.
- [server/app/providers/base.py](../../server/app/providers/base.py): `ProviderFailure` with `code` and `usage`.
- [server/app/jobs/processor.py](../../server/app/jobs/processor.py): usage recording and job states.
- [server/app/core/config.py](../../server/app/core/config.py): `llm_*` settings and startup validation.
- [server/tests/test_llm_providers.py](../../server/tests/test_llm_providers.py): end-to-end cases through the API and job processor.
- [evals/extract_tender.py](../../evals/extract_tender.py): real-vendor run on a public tender.
