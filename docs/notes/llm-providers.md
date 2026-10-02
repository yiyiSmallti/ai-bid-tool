# LLM extraction providers

## Problem

Requirement extraction must call a real model, record what each call cost,
and never save a requirement whose citation cannot be checked against the
stored source text. Vendor outages, refusals, and truncated output must end in
a defined job state rather than partial or silent results.

## Usage

Select the platform model with `BID_LLM_PROVIDER` (`anthropic` or `openai`)
and its `BID_LLM_*` settings, then restart the API and worker. Setup steps are
in [development.md](../guides/development.md#configure-the-extraction-model).
Tests inject an adapter built with an `httpx.MockTransport`; production code
calls `create_llm(settings)`.

## How it works

Both adapters call the vendor over httpx; no vendor SDK is installed. Chunks
(PDF pages or Word sections) are grouped into batches of at most
`BID_LLM_BATCH_CHARS` characters (default 8,000), and a chunk larger than the
budget gets a batch of its own rather than being cut. When the model hits its
output limit or answers with text that does not parse as the schema, the
batch is halved and both halves are sent again: by chunk
first, then a Word section by blocks, then a long page or block by lines. A
part keeps its page number or block ID, and its quotes are still checked
against the whole stored page or block. Only a single line that still
overflows fails the job. Up to
`BID_LLM_CONCURRENCY` batches (default 4) run at once; after a failure, batches
not yet started are skipped. Each vendor call has a total deadline of
`BID_LLM_TIMEOUT_SECONDS`, so a response that keeps the connection alive
without finishing still ends. `BID_LLM_REQUEST_OPTIONS` is a JSON object merged
into every request body, for vendor switches such as
`{"thinking": {"type": "disabled"}}`; the adapter's own fields win on conflict.

The model sees each PDF page as `<page number="N">` and each Word block as
`<block id="p37">` inside its section, and returns items that cite a page
number or block ID as `ref` with a verbatim quote. Word citations are covered
in [docx-citations.md](docx-citations.md). Output is constrained to `WIRE_SCHEMA`:
Anthropic through `output_config.format`, OpenAI-compatible services through
`response_format` (`json_schema`, or `json_object` with the schema in the
prompt). The adapter maps each `ref` back to its chunk ID and document ID, so the
model never reproduces UUIDs. `condition` is typed as
`param/op/value/unit` or null, because structured outputs require closed
objects. The processor then checks every quote against the stored page or
block text, saves the items that pass, and lists the others in
`result.rejected` with their position, quote, and a warning.

On Anthropic, effort comes from `BID_LLM_EFFORT`, and a safety decline is
retried server-side on another model (`fallbacks: "default"`) unless
`BID_LLM_ANTHROPIC_FALLBACK=false`. The usage record stores the model that
actually answered.

| Vendor result | Job outcome |
| --- | --- |
| Timeout, connection error (including a TLS connection dropped mid-response), HTTP 408/409/429/5xx/529 | The call is retried after 10 and 30 seconds; if it still fails, the job is requeued with exit code 3, at most three attempts |
| Quota used up, unpaid account or expired plan: HTTP 402, `insufficient_quota`, `billing_error`, Zhipu `QUOTA_CODES` | Failed, `provider_quota_exhausted`, exit 4; the message names the reset time when the vendor gives one and asks the user to contact the system administrator |
| Other HTTP errors, such as 400 or 401 | Failed, `provider_unavailable`, exit 4 |
| Refusal | Failed, `provider_refused` |
| Truncated or malformed output on a single line, `ref` outside the batch | Failed, `invalid_provider_output` |
| Some items with an empty quote or text, or a quote not found at the cited position | Succeeded; those items are listed in `result.rejected` with a reason and not saved |
| No item passes | Failed, `invalid_citation`; nothing saved |
| An unexpected error while assembling results | Failed, `processing_failed`; every finished call is still recorded, and the log holds the exception type and stack without its message |

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
- A rejected item is lost, not repaired. If it was a ★ clause, the ★ rule
  still adds it from the source. `evals/extract_tender.py` reports verified
  citations and ★ recall per run.
- Reasoning models can spend the whole output budget thinking. On GLM, a
  section took about 11 times longer with thinking on, and the full reference
  tender was cut off at 32,000 output tokens before halving existed. Halving
  costs the truncated call plus the retries; disable thinking with
  `BID_LLM_REQUEST_OPTIONS` when most batches overflow.
- The cache key includes the provider, model, adapter version, and
  `PROMPT_VERSION`; bump `ADAPTER_VERSION` when the prompt or schema changes.

## Code

- [server/app/providers/llm.py](../../server/app/providers/llm.py): `AnthropicExtractor`, `OpenAICompatibleExtractor`, `create_llm`, `WIRE_SCHEMA`.
- [server/app/providers/base.py](../../server/app/providers/base.py): `ProviderFailure` with `code` and `usage`.
- [server/app/jobs/processor.py](../../server/app/jobs/processor.py): usage recording and job states.
- [server/app/core/config.py](../../server/app/core/config.py): `llm_*` settings and startup validation.
- [server/tests/test_llm_providers.py](../../server/tests/test_llm_providers.py): end-to-end cases through the API and job processor.
- [evals/extract_tender.py](../../evals/extract_tender.py): real-vendor run on a public tender.
