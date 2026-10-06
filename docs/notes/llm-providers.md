# LLM extraction and drafting providers

## Problem

Requirement extraction must call a real model, record what each call cost,
and never save a requirement whose citation cannot be checked against the
stored source text. Vendor outages, refusals, and truncated output must end in
a defined job state rather than partial or silent results.

## Usage

Configure tenant models and select platform catalog models through
[provider configuration](provider-config.md#usage). `BID_LLM_*` still supplies
non-secret adapter execution limits and standalone/eval configuration; credential
resolution follows [platform credential authority](platform-credentials.md#how-it-works).
These settings do not select a tenant job's fallback model. Tests inject adapters
with `httpx.MockTransport`.

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
`{"thinking": {"type": "disabled"}}`. Output-limit fields and aliases are
reserved: both adapters reject them with `invalid_provider_options` before
admission in extraction and drafting, including nested vendor options. The
platform catalog rejects such reasoning levels at creation and update. The
shared field list and spelling rules are in
[llm_options.py](../../server/app/core/llm_options.py); other adapter-owned fields
win on conflict. Set the output limit through `BID_LLM_MAX_OUTPUT_TOKENS`.

The model sees each PDF page as `<page number="N">` and each Word block as
`<block id="p37">` inside its section, and returns items that cite a page
number or block ID as `ref` with a verbatim quote. Word citations are covered
in [docx-citations.md](docx-citations.md). Output is constrained to `WIRE_SCHEMA`:
Anthropic through `output_config.format`, OpenAI-compatible services through
`response_format` (`json_schema`, or `json_object` with the schema in the
prompt). The adapter maps each known `ref` back to its chunk ID and document ID,
so the model never reproduces UUIDs. An unknown `ref` rejects only that item with
`unknown_position`; other verified items from the paid response continue through
the processor. `condition` is typed as
`param/op/value/unit` or null, because structured outputs require closed
objects. The processor then checks every quote against the stored page or
block text. Normalization is used only to locate a unique contiguous span: the
exact source characters are saved as `source.quote`, while the model's original
text is retained separately as top-level `model_quote`. No match is rejected as
`quote_not_at_position`; more than one normalized match is rejected as
`ambiguous_quote` unless exactly one is bounded by segment separators, as
described in [docx-citations.md](docx-citations.md). Gap-fill coverage locates
quotes with the same rule in original offsets. Rejected items are listed in `result.rejected` with their
position, model quote, reason, and a warning.

The prompt requires one requirement per hardware or software parameter,
including nonnumeric parameters, with that parameter's own quote and all its
source details. Heading-only items are skipped even when starred; `merge_starred`
also skips a heading ending in a colon without content after it. Star detection
splits a block on Chinese or ASCII semicolons and newlines, so only an explicitly
marked segment becomes starred. `locate_span` resolves each item's quote against
the complete stored page or block, and only an interval contained in the marked
segment becomes starred. A substring inside another parameter's name cannot
mark an adjacent item or suppress addition of a missing marked segment.

After the first pass, `HTTPExtractor.extract` performs one parameter gap-fill
sweep. `uncovered_parameters` splits the full stored block or page text on
Chinese or ASCII semicolons and newlines. A piece is a candidate when it has a
comparison (`≥`, `≤`, `>`, `<`, `≯`, `≮`, or phrases such as `不少于`, `不低于`,
`不超过`), or a `name:value` whose value contains a digit or a recognized unit.
Coverage uses only items that pass `cited`, at the same chunk and block/page. A
quote covers a parameter only when it contains exactly one detected parameter
segment; a quote spanning a whole list covers none of those segments. Leading
list numbers, stars, and terminal sentence punctuation do not affect coverage.

Only uncovered pieces are rendered, one per line, under the original block ID
or page number. They reuse the batch budget, concurrency, transient retries and
halving above. The processor still validates every returned quote against the
full stored source, so joining separated pieces or inventing a value is rejected.
There is no second model sweep. With no gaps, no extra call is made. After model
items and deterministic starred-source items are finalized, the processor scans
once more to report what is still uncovered.

`result.gap_fill` adds `segments` (uncovered candidate pieces), `calls` (actual
gap-fill attempts, including retries), and `added` (new requirements saved from
gap-fill output after citation checks and deduplication). `remaining` is the
number of candidate pieces still uncovered after all saved and rule-added items.
Existing result fields and the `extract(chunks, schema)` provider entry point are
unchanged.

Gap filling adds the input and output tokens of its calls, at the selected
model and reasoning level. For `S` uncovered segments grouped into `B` initial
gap batches, it normally adds `B` calls. Splitting can create at most `2*S-B`
batch attempts before reaching individual segments; including the two transient
retries per batch, the upper bound is `3*(2*S-B)` vendor calls per extraction
attempt. A requeued job starts a new attempt and can incur those costs again.
These are bounds on calls, not a fixed token or price cap.
The shared job execution layer additionally enforces a cumulative vendor-call
ceiling and, for platform-billed requests, a charge ceiling and per-call
reservation. All recursive and gap-fill requests, including transient retries,
pass the same admission point immediately before HTTP dispatch. The spending
bound, unknown outcomes and accounting keys are defined in
[prepaid-billing.md](prepaid-billing.md#admission-and-the-spending-bound).

On Anthropic, effort comes from `BID_LLM_EFFORT`, and a safety decline is
retried server-side on another model (`fallbacks: "default"`) unless
`BID_LLM_ANTHROPIC_FALLBACK=false`. The usage record stores the model that
actually answered.

| Vendor result | Job outcome |
| --- | --- |
| Timeout, connection error (including a TLS connection dropped mid-response), HTTP 408/409/429/5xx/529 | The call is retried after 10 and 30 seconds; if it still fails, the job is requeued with exit code 3, at most three attempts |
| Quota used up, unpaid account or expired plan: HTTP 402, `insufficient_quota`, `billing_error`, Zhipu `QUOTA_CODES` | Failed, `provider_quota_exhausted`, exit 4; reset time and payer-specific guidance follow [provider-config.md](provider-config.md#quota-and-balance) |
| Other HTTP errors, such as 400 or 401 | Failed, `provider_unavailable`, exit 4 |
| Refusal | Failed, `provider_refused` |
| Output limit reached on an unsplittable batch | Failed, `provider_output_truncated`; lower the reasoning level or raise `BID_LLM_MAX_OUTPUT_TOKENS` |
| Malformed output on an unsplittable batch | Failed, `invalid_provider_output` |
| Some items have an empty quote or text, an unknown `ref`, no unique source match, or a quote not found at the cited position | Succeeded; those items are listed in `result.rejected` with a reason and not saved |
| No item passes | Failed, `invalid_citation`; nothing saved |
| An unexpected error while assembling results | Failed, `processing_failed`; every finished call is still recorded, and the log holds the exception type and stack without its message |
| No funds for the next call, or a job ceiling reached | Failed, `insufficient_balance`, `job_charge_limit_exceeded`, or `job_call_limit_exceeded`; no partial extraction is saved, prior usage remains |
| Attempt cancelled, superseded or expired | `job_attempt_stopped` fences further calls; it cannot overwrite a newer attempt or cancellation |
| Usage cannot be persisted, or exceeds its reserved bound | Failed, `usage_accounting_failed` or `call_charge_bound_exceeded`; no further calls from the attempt |
| Missing or invalid vendor token counts | Failed, `invalid_provider_usage`; keep the reservation for reconciliation instead of inventing zero usage |

Each response with valid usage produces one `ProviderUsage`. Inside a job,
`HTTPExtractor.post` awaits immediate durable settlement through
`accounted_call` before interpreting model output. Aggregate adapter usage and
`ProviderFailure.usage` remain available to standalone consumers, but the job
processor does not insert these records again. Job costs are computed from
persisted usage across all attempts, including failed and cancelled calls.
USD is computed from configured per-million-token prices, or `null` when
either price is missing. Anthropic cached input tokens are included in input
usage at those configured prices.

### Structured response drafting

`LLMProvider.draft(requirements, materials)` is implemented by both HTTP adapters
through [drafting.py](../../server/app/providers/drafting.py). It uses the shared
tenant model resolver and `with_reasoning`, the same per-request timeout, request options,
finite transient retries, reservation and `HTTPExtractor.post` settlement. The
extraction prompt, wire format, parallel batches, gap filling and citation
post-processing retain their own entry point and behavior.

The drafting wire schema describes per-requirement response proposals. Anthropic
uses `output_config.format`; OpenAI-compatible endpoints use JSON schema or JSON
object mode with the schema in the system prompt. Every request receives only
the fixed outbound snapshot described in
[model-drafting-redaction.md](model-drafting-redaction.md). The model is instructed
to treat supplied text as data, retain negative deviations, distinguish material
declarations from proof and leave unavailable evidence empty.

`groups` budgets serialized requirements together with the complete material
text, with a budget of the reasoning level's extraction batch size times
`BID_DRAFTING_BATCH_SCALE`, because requirement quotes are far shorter than the
document pages that budget was tuned for. Whole requirements are batched in order;
a large single input gets its own batch. Up to `BID_LLM_CONCURRENCY` batches run at once, as for extraction; each
call is still admitted against the job's call ceiling and charge cap before it is
sent. Completed batches are returned in request order whichever finished first. Truncated or malformed responses halve the requirement batch;
one requirement is the terminal boundary. No field/page text is spliced, no
previous response is supplied and no drafting gap-fill pass runs. `plan_calls`
receives the planned first-pass batch count before any requests, so recursive
halves and transient retries share the correctly scaled cumulative ceiling.

Each completed batch retains its exact request-local reference map for service
validation. A provider/admission failure stops batches that have not started;
batches already in flight finish, and completed batches return with a safe
error code. An input change found at admission therefore stops later calls but
not calls already sent from the authorized snapshot; the worker applies the partial-result
and review rules in [response-cards.md](response-cards.md#model-proposals).
Immediate accounting precedes parsing for every HTTP response. Drafting's
`safe_metadata` mode suppresses arbitrary vendor error strings and untrusted
model-name echoes before persistence. It records the configured model or a
recognized Anthropic fallback identity. An unrecognized reported model is
accounted as `unverified-model` and fails with `invalid_provider_model`; no
arbitrary reported label is persisted. Sent text and raw responses never enter
usage, audit, errors or regular logs. Usage's `job_id` links each call to the
fixed reasoning level and its generation snapshot.

## Pitfalls

- Error messages keep only the HTTP status and recognized vendor error types. Response
  bodies can echo input, so they are never stored or logged.
- An empty environment variable counts as unset; a selected provider without
  its key or model stops startup.
- The configured prices apply to whichever model answered. After a refusal
  fallback, the recorded cost is an estimate at the primary model's prices.
- A rejected first-pass quote does not count as coverage and may therefore
  trigger gap filling. Rejected gap-fill items are reported without another
  repair round. If a missing item was a ★ clause, the ★ rule still adds it
  from the source. `evals/extract_tender.py` reports verified citations and
  ★ recall per run.
- Gap detection is lexical, not a completeness proof. Nonnumeric parameters
  without a comparison or recognized unit rely on the first-pass prompt.
  Whole-list quotes are retained when valid but deliberately cover no individual
  parameter segment; each parameter still needs its own saved quote.
- Reasoning models can spend the whole output budget thinking. On GLM, a
  section took about 11 times longer with thinking on, and the full reference
  tender was cut off at 32,000 output tokens before halving existed. Halving
  costs the truncated call plus the retries; disable thinking with
  `BID_LLM_REQUEST_OPTIONS` when most batches overflow.
- The cache key includes the provider, model, `PROMPT_VERSION`,
  `EXTRACTION_VERSION`, and `ADAPTER_VERSION`. Bump `PROMPT_VERSION` for prompt changes,
  `EXTRACTION_VERSION` for post-processing or citation semantics, and
  `ADAPTER_VERSION` for adapter or wire-schema compatibility changes.
- Budget exhaustion is an atomic extraction failure: already verified
  intermediate items are not saved as a partial requirement list. Consumers
  must not infer that extraction publishes partial requirements from
  drafting's separate partial-result policy. Both reuse call accounting and
  attempt guards. Lease and cancellation behavior are in
  [background-jobs.md](background-jobs.md).

## Code

- [server/app/providers/llm.py](../../server/app/providers/llm.py): `HTTPExtractor.extract`, `uncovered_parameters`, `parameter_segments`, `AnthropicExtractor`, `OpenAICompatibleExtractor`, `create_llm`, `WIRE_SCHEMA`.
- [server/app/services/extraction.py](../../server/app/services/extraction.py): exact-span location, citation rejection, starred-source merging, and fingerprints.
- [server/app/providers/base.py](../../server/app/providers/base.py): `ProviderFailure` with `code` and `usage`.
- [server/app/providers/drafting.py](../../server/app/providers/drafting.py): structured proposal prompt/schema, batches and retry boundaries.
- [server/app/jobs/processor.py](../../server/app/jobs/processor.py): usage recording and job states.
- [server/app/core/config.py](../../server/app/core/config.py): `llm_*` settings and startup validation.
- [server/tests/test_llm_providers.py](../../server/tests/test_llm_providers.py): end-to-end cases through the API and job processor.
- [server/tests/test_card_generation.py](../../server/tests/test_card_generation.py): drafting calls through both adapters with fixed inputs and per-call billing.
- [server/tests/test_parameter_extraction.py](../../server/tests/test_parameter_extraction.py): parameter coverage, original positions, rejection, deduplication and per-call accounting through the API and job processor.
- [test_adversarial_citations.py](../../server/tests/test_adversarial_citations.py): located star membership and citation continuity through human review and draft assembly.
- [evals/extract_tender.py](../../evals/extract_tender.py): real-vendor run on a public tender.
