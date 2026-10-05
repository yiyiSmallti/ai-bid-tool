# Model drafting inputs, redaction and evidence references

## Problem

A model needs enough selected material to propose a response without receiving
unrelated tenant data or private values that the task has not authorized for
outbound use. Its citations must describe material it actually saw. A model
response is neither an authenticated document nor a human review.

## Usage

Use the preview and generation steps in
[the CLI guide](../guides/cli.md#generate-model-response-proposals). Review and
confirmation follow [response-cards.md](response-cards.md); the reasons for
separating these actions are in [ADR 0005](../adr/0005-human-confirmed-responses.md).

## How it works

### Submission snapshot

`snapshot` and `material_inputs` in
[card_generation.py](../../server/app/services/card_generation.py) read only the
selected requirements' original quotes and locations, scalar string values of
allowed fields in the task's active fixed resource selections, and locally
extractable text of archived certificate pages tied to those selections. The
allowlist is `RESOURCE_FIELD_PATHS` in
[response_card_contracts.py](../../server/app/schemas/response_card_contracts.py).
Every field has its own local `ref`. Selection and revision IDs, original file
hashes, preview hashes and page numbers remain in the server manifest; the vendor
gets local refs, field/page positions and text. Requirement IDs identify the
requested proposals. No other pages, PDF/image bytes, prior card text, storage
paths, credentials, templates or memory are attached.

Original PDF bytes are checked against their stored hash and length before local
page text extraction. A selected page with no text is listed by source ID as
`page_text_unavailable`; it never triggers OCR or image upload. Snapshot text is
encrypted with `Secrets` and protected by tenant RLS. The public manifest and
dry-run contain IDs, text/location hashes, redaction state and revision, rule
version, model/catalog/reasoning identity and hit counts. They contain no text
values. Preview performs the same read authorization and no writes or calls.

### Outbound rules

`RULE_VERSION`, `RULES` and `redact_tree` in
[redaction.py](../../server/app/services/redaction.py) define the versioned rule
set. Matching uses NFKC text with offsets mapped back to the original string.
The external copy uses `[REDACTED_…]` placeholders; source files, stored field
values and tender quotes remain unchanged.

| Category | Detection boundary |
| --- | --- |
| amount | Price, quote and budget labels followed by a separator and any text, or by a numeric, currency-marked or written-out (壹贰叁…) value; unlabelled numbers with supported currency symbols/codes or Chinese currency units |
| contact | Contact-name labels followed by a colon or equals sign; phone labels followed by a number of at least seven digits; supported unlabelled phone-number patterns |
| identity | Identity labels followed by a number of at least six digits, spaces, hyphens, `X` or `*`; unlabelled Chinese identity-number patterns |
| bank_account | Account labels followed by a number of at least eight digits, spaces, hyphens or `*`; labelled IBANs; unlabelled long account-number patterns |

A label word alone is never a detection, so tender wording such as "刷身份证登录"
or "管理员账号" is sent unchanged. English labels must be whole words.

Every business-text leaf is processed, including requirement quotes, Word heading
paths and location labels, resource field values and certificate page text.
Overlapping matches are masked as one union so a shorter numeric match cannot
expose the remainder of a labelled value. Counts are rule detections before
overlap merging, grouped by category; they are not counts of unique people or
accounts. The same detection counts are reported when masking is disabled.

The task switch defaults on. Only a human org admin can change it through the
revision-checked, audited task setting. The job fixes that revision at submission
and checks it before every admitted call, including retries and halves. A changed
revision stops further calls and requires a fresh submission. A disabled switch
produces an explicit warning that the manifest text will be sent unmasked.

### Reference validation

Each completed batch retains exactly the requirements and local refs sent in
that request. The worker resolves refs against this map; model-supplied selection
IDs, revision IDs, URLs and source claims cannot create new material bindings.
Unknown refs are reported as `unknown-` plus a truncated SHA-256 digest, avoiding
echoes of arbitrary sensitive strings supplied as a ref.

For evidence responses, placeholders are rejected first. `locate_quote` in
[extraction.py](../../server/app/services/extraction.py) then locates one unique
contiguous span in both the actual sent field/page and its fixed original text.
The same normalization and ambiguity rules as
[requirement citations](docx-citations.md#how-it-works) apply. The saved quote is
the exact original span. Unsent text, joined quotations, unknown/ambiguous
matches, stale selections and changed revision bindings are dropped with a
requirement/ref/reason report. There is no repair, reverse substitution or
inferred source. Material resolution repeats against current stored parents at
publication; PDF quotes remain `unreviewed_page` until a human inspects them.

An evidence proposal with no accepted references remains an unconfirmed evidence
draft with `review_hint=needs_material`. A commitment has no Evidence; extra refs
are discarded with `commitment_evidence_dropped`. The response text remains a
proposal for human review. These checks do not verify its semantic claims.

### Cost and reporting

Dry-run reports a conservative first-pass token allowance and a charge/cost bound
where configured prices are available. It labels the estimate
`first_pass_upper_bound`; halving and retries can cost more. Missing prices remain
null with `cost_basis=unknown` and a reason. Insufficient available balance for
the first call is reported as `admission_blocker=insufficient_balance`; a first
call beyond the job charge ceiling reports `job_charge_limit_exceeded`. These
are read-only checks, and every real call still requires live reservation.
Duration is unknown until execution.
Estimation and admission use the same output-token bound. Both adapters build
draft requests through the [reserved output-limit contract](llm-providers.md#how-it-works),
so environment or legacy reasoning options cannot inject a competing limit.
Actual request cost, settlement and unresolved reservations follow
[prepaid-billing.md](prepaid-billing.md).

Usage, audit, job results and errors contain no sent text or raw model output.
Only validated response content is stored in authorized card revisions. Invalid
quotes are never copied to Evidence or diagnostics. Drafting suppresses untrusted
vendor error strings at the HTTP boundary before they can reach job errors.

## Pitfalls

- Pattern masking still masks free text after a labelled separator ("价格：见附件")
  and cannot recognize every unlabelled or unconventional sensitive value, such
  as a contact name written after a label without a colon. Review the selected
  source data and counts before submitting; the switch is not an authenticity or
  secrecy classifier.
- Each batch includes the complete selected material text. Batching and halving
  split requirements, never splice/truncate fields or pages. A single requirement
  that still produces malformed/truncated output fails explicitly. Large selected
  materials may require choosing a smaller resource set.
- A masked quote cannot become evidence of the original private value. Turning
  masking off is a separate human admin decision, not automatic retry behavior.
- Already admitted calls can finish after cancellation or a setting change and
  still incur usage. Their accounting and publication fences are separate.

## Code

- [card_generation.py](../../server/app/services/card_generation.py): snapshot, submission, access and reference validation.
- [redaction.py](../../server/app/services/redaction.py): versioned masking and counts.
- [test_redaction.py](../../server/tests/test_redaction.py): kept wording and masked value cases.
- [providers/drafting.py](../../server/app/providers/drafting.py): wire schema, prompt, complete batches and partial output.
- [test_card_generation.py](../../server/tests/test_card_generation.py): synthetic API/worker acceptance scenarios and repeatable external artifacts.
