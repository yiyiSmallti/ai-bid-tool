---
kind: plan
---

# Uploaded-bid review and score estimate

Status: **Approved; phase 1 implemented (slices 1–5). Clef triage is on by default.**

The first phase is divided into five implementation slices: upload/preparation;
signature checklist and approved local validation; rule/LLM compliance and human
dismissal; console/Word report; Clef triage with fixed per-call billing. Slice 3a
implements exact human-authorized sanitized native-text review, cited tender
obligations and signing-clause applicability with unresolved location inventories.
Slice 3b adds authorized bid-text compliance findings, deterministic rules and
append-only classified human decisions within that same run. Slice 4 adds the
immutable console/Word snapshot and human-only report artifacts. Slice 5 adds
platform Clef configuration, exact human-cleared presence JPEGs and fixed per-call
billing. Database/browser acceptance remains pending for these implementation slices.
Upload/preparation and local signature evidence are implemented. PostgreSQL and
browser acceptance remain pending; static checks do not establish those gates.
The delivered boundaries are in [slice 3a](#slice-3a-authorized-native-text-review),
[slice 3b](#slice-3b-native-text-compliance-findings-and-human-decisions) and
[slice 4](#slice-4-console-report-and-word-artifacts) and
[slice 5](#slice-5-default-on-presence-triage); their mechanisms are in
[the mechanism note](../notes/bid-review.md).

This contract adds an independent uploaded-bid review flow to B09/B10 in the
[roadmap](roadmap.md), with B04/B05 evidence, P03 vision and E01/E02 evaluation
dependencies. The importable [Pydantic v2 contract](bid-review/bid_review_contracts.py)
defines payloads and service/Provider Protocols. The document contract itself
registers no runtime components. Implementation follows the approved
[decisions](#open-decisions) under [agent.md](../../agent.md#workflow).

## Objective and input boundary

A user uploads a tender document (招标文件) and an already-written bid (投标文件),
possibly split into qualification, commercial/technical, price and declaration
PDF/DOCX files. An authenticated task provides the org, permissions and budget;
creating response cards or using the drafting flow is not a prerequisite. The result
is an advisory review report (检验报告) in the console and Word, with rejection risks,
signature completeness, evidence checks, remediation and, when requested, score ranges.

All new inputs and runs fix `scope=uploaded_bid`. Existing
[check](check.md#goals-and-boundaries) and [score](score.md#scoring-input) continue to
accept only a current internal confirmed `DraftRun`. Do not add uploaded-file unions
to those requests, synthesize a DraftRun, read a released export through agent grants,
or reinterpret an uploaded bid as confirmed response cards. Uploaded statements are
unverified assertions even when their text can be cited exactly.

The new flow reuses tender parse/extraction mechanisms, source verification, provider
configuration and budgets through explicit authorized stages. Its upload receives the
tender and bid files together and records their distinct roles. Requirement coverage
must identify the pinned extraction and unparsed tender areas. An optional existing
extraction must belong to the selected tender/task and pass the same integrity gates;
it is never silently replaced with the latest extraction. Uploaded-bid mapping is a
new service boundary, independent of confirmed-draft input builders.

Included: complete submitted-file inventory, clause-to-response mapping, ★/▲ clauses,
qualification/commercial/technical compliance, quotation defects visible in the bid,
two distinct visual validation logics, local PDF signature validation and cited
remediation. No automatic bid editing, signature creation, procurement-platform
integration, certificate fabrication, vendor crawling or pricing strategy is included.
Local validation of existing PDF signatures does not sign or submit a bid. Arithmetic
evaluation of supplied opening prices is limited to the tender's explicit scoring
rule; competitor research and strategic recommendations remain excluded by
[design non-goals](../design.md#background-goals-and-non-goals).

## Verified code foundations and required additions

| Existing code anchor | Reuse and boundary |
| --- | --- |
| `AssessmentRequest`, `AssessmentInput`, `VerifiedCitation`, `AssessmentJobAccepted` in [check_contracts.py](../../server/app/schemas/check_contracts.py); `access`/snapshot in [check_inputs.py](../../server/app/services/check_inputs.py); `fixed_rows`/`snapshot` in [score_run_inputs.py](../../server/app/services/score_run_inputs.py) | Requests require `draft_id`, the input fixes `confirmed_draft`, and services read confirmed draft partitions. Reuse citations and job receipts, never these input loaders for arbitrary uploads. |
| `Contract`, `Source`, `Location`, `Result`, `Cost`, `CONTRACT_VERSION` in [contracts.py](../../server/app/schemas/contracts.py) | Use Result 4.0 and original PDF-page or DOCX-block citations. Add an uploaded-bid citation branch without changing the existing `VerifiedCitation` union. |
| `Storage.read_bounded`, `FileCipher`, `validate_key` in [storage.py](../../server/app/providers/storage.py) | Encrypted immutable objects bound to `org/{org_id}/`, bounded reads then hash/size checks. The Protocol has no deletion method; retention/purge needs separate implementation, not an assumed storage API. |
| `run_pdf_operation_async` in [pdf_process.py](../../server/app/core/pdf_process.py), raster bounds in [pdf_raster.py](../../server/app/core/pdf_raster.py), `parse_docx` in [docx_blocks.py](../../server/app/services/docx_blocks.py) | Reuse isolated PDF work and structural Word locations. Existing parsing does not establish seal coverage, stable Word pages or cryptographic signature validity. New preparation must preserve those distinctions. |
| `resolve_llm` in [llm.py](../../server/app/providers/llm.py), [configured.py](../../server/app/providers/configured.py), [provider configuration](provider-config.md) | Existing org revision/platform default resolution supplies extraction, mapping and semantic LLMs. Clef needs an explicit optional adapter/capability; it is not an implicit LLM fallback. |
| `BudgetCallQuote`, `BudgetPreflightData`, `BudgetJobResult` in [budget_contracts.py](../../server/app/schemas/budget_contracts.py), `attach` in [budget_preflight.py](../../server/app/services/budget_preflight.py), [JobExecution](../../server/app/jobs/execution.py) | Reuse shared admission, accounting and terminal budget attachment. The [budget contract](budget.md#result-cost-and-compatibility) governs newer 4.0 cost semantics even where older check/score prose describes zero-cost previews or predates task budgets. |
| `redact`, `redact_tree`, `library_value` in [redaction.py](../../server/app/services/redaction.py) | Existing patterns and registered values protect text. Bid-derived name lists, page classification, pixel privacy, outbound grants and restricted report projections are additions, not existing guarantees. |
| `Identity.require`, `HUMAN_ONLY_SCOPES`, `SCOPES`, `ROLE_SCOPES` in [auth.py](../../server/app/services/auth.py); [task_workflow.py](../../server/app/services/task_workflow.py) | Preserve session-only actions, live Membership/task authority and token ceilings in API, workers and DB. Proposed scopes below are declarations only. |
| [Screenshot evidence](../notes/screenshot-evidence.md), [annotation](annotation.md), [attachment archives](attachment-archive.md) | Reuse exact pixels, source/rendition hashes, mappings, privacy lineage and separate human confirmation. Archive approval, annotation and image similarity do not prove a response claim. Uploaded-bid pages require an explicit new source branch before any later reuse in B04/B05. |

The [local PDF validator](../../server/app/services/bid_pdf_signatures.py) and
[trust-store decision](#open-decisions) provide independent cryptographic evidence.
Merely reading signature widgets or detecting their image appearance cannot satisfy
this contract.

## Two independent validation logics

### Signature and seal completeness

Derive a checklist from all available tender signing clauses and template instructions,
combining deterministic keyword/symbol scans with cited LLM proposals. Record each
requirement's original quote, applicability, required mark, owner role, date obligation,
location rule and professional review domain. Expand “every page” to the full pinned
page inventory; expand a seam seal to the relevant ordered page group. Unknown
applicability or an unmapped required location stays unresolved, not not-applicable.

Each required occurrence records its mapped document/page/region and independently
answers: mark present; correct owner; date filled and valid where required; correct
location; coverage of all required pages. Distinguish company seal (公章), legal
representative signature/private seal, authorized-agent signature, date, seam seal
(骑缝章), and every-page electronic seal. Do not deduplicate repeated obligations
merely because one nearby seal exists. A seal elsewhere on the page does not satisfy
a prescribed box, and one visible seam fragment does not establish complete coverage.

Presence is a visual observation. Owner matching uses locally extracted seal text and
the bid's fixed bidder identity or human inspection, not the blurred external image.
An unreadable owner/date/position remains `unknown`; it must not inherit the presence
probability. Handwritten signatures cannot establish the signer's identity by visual
similarity alone. Record tender-authorized alternatives as alternatives, not cumulative
requirements invented by the checker.

For each digitally signed PDF, validate the **unchanged original bytes locally** before
conversion or rasterization. Retain document hash, signature field/revision, byte-range
coverage, cryptographic validity, changes after signing (including incremental updates),
certificate fingerprint/subject/issuer and validity period, local trust-chain result,
claimed signing time versus trusted timestamp, and revocation status. Certificate
names are encrypted restricted data. Multi-signature files require a result per
signature and the final document revision; an earlier valid signature cannot hide a
later modification. Permitted certification changes and additional signatures must be
reported separately from “unmodified,” then evaluated against the tender.

Offline validation uses pinned local trust anchors and embedded validation material.
Missing trust, missing/stale revocation proof, unsupported algorithms or unsupported
signature formats produce `unknown`/`unsupported`, never valid. No automatic OCSP/CRL
network access or model call is made. Visible electronic seal artwork without a PDF
signature is visual-only; a valid PDF signature does not prove every visible seal/date
requirement was met. DOCX and scans explicitly report digital validation not applicable.

The primary quality metric is **recall of missing required marks**, measured per
required location. Any unresolved occurrence remains in the report even if Clef
assigns high presence probability. Triage cannot suppress required locations or turn
a skipped page into a pass.

The approved reference profile is 点聚 Filter `/DJ.GMPkiLite`, SubFilter
`/GM.sm2cms.detached`: GM/T 0010 CMS SignedData uses OIDs
`1.2.156.10197.6.1.4.2.1` / `.2`, SM3, SM2 signature OID `1.2.156.10197.1.501` or
its GM/T 0006 form `1.2.156.10197.1.301.1`, and curve OID `1.2.156.10197.1.301`.
Real 点聚 signatures are not detached despite the SubFilter: eContent holds
SM3(ByteRange bytes), there are no signed attributes, and the standard ZA-bound SM2
signature covers that embedded digest. Their signer certificates mark extended key
usage critical with client authentication and email protection; email protection,
any purpose and document-signing purposes qualify a certificate for signing. The owner-reported
政采云 bid embeds only its provincial-CA signer certificate, so uploaded local
intermediates and roots are required to establish a chain. pyHanko cannot verify
this SM2 profile; the approved pure-Python BSD dependency is gmssl (with its
pycryptodomex dependency). Signed attributes use DER SET OF and standard SM2 Z with
user ID `1234567812345678`; a signature over the direct SM3 content digest is also
accepted without signed attributes, with the matched variant recorded. The reference
bid is descriptive input only; implementation acceptance uses synthetic fixtures.

### Images as response evidence

Evaluate a specific tender requirement and bid response claim against a specific
certificate, screenshot or report region. Record claim type and subject; parameter
name/operator/value/unit; exact covered product model/version/configuration; holder,
manufacturer or issuer as applicable; and validity inclusive of the explicit bid date.
Separate what the bid asserts from what the image actually supports. “Certificate
present” is not “certificate supports this model/value,” and seal completeness does
not establish certificate authenticity.

The outcome is `supports`, `contradicts`, `insufficient` or `unknown`, with independent
dimension outcomes and locally verified text or an exact page/region observation.
Semantic LLM/OCR output remains a proposal. A human in the proper review domain must
inspect the actual pinned image and record confirm/reject/reopen with a reason before
the report labels it human-reviewed support. Pending evidence can appear as unresolved
review work; it cannot silently earn confirmed-evidence points. If privacy masks hide
the model, value, holder or validity date being judged, that dimension must be checked
locally or by a human, or remain unknown.

The primary metric is accuracy on deliberately mutated claims, especially wrong
model/value, with false acceptance reported separately. Exact source and privacy
lineage follows [B05](annotation.md#pixel-provenance-and-renderer-contract) and
[attachment source binding](attachment-archive.md#b05-source-binding-and-invalidation).
A review decision does not set `Evidence.confirmed_by`, approve a response card, or
authorize draft/export. Any later adoption into B04/B05 must enter those existing
human confirmation gates with a separately accepted uploaded-page adapter.

## Sources, pages and findings

Retain every original file unchanged, ordered with its declared role and lot. File
hashes, sizes, document IDs and parse/render versions form an immutable submission
manifest. Replacement makes a new submission/version; reports and decisions remain
bound to their original input. The submitted list defines coverage: absent companion
files required by the tender become missing-document work, not assumed attachments.

PDF pages use one-based original page numbers. DOCX text retains `Source.location`
under [Word citations](../notes/docx-citations.md). A fixed conversion produces a
separate page view with converter/font/profile identity, rendered PDF hash, page count
and block-to-page mappings. Report labels distinguish “original PDF page” from
“rendered DOCX page.” Unmapped Word blocks and omitted headers/text boxes/images remain
explicit gaps under [PDF parsing](../notes/pdf-parsing.md) and Word parsing limits.
No successful report may claim complete visual coverage without that page mapping.

Pages are read by kind, not OCR'd by default. A page with a usable text layer uses that
text for retrieval, rules and verbatim citations. An image page (certificate, license,
screenshot, seal or signature page) gets no full-page OCR; it is sent, redacted, to a
multimodal model only for the specific questions its checks ask, and findings on it
anchor to the page image and region rather than to quoted text, subject to human review
like other visual evidence. Only a submission whose pages lack text layers throughout
needs transcription for retrieval and citations. That uses local OCR (the existing
Tesseract provider) or a multimodal transcription that is labelled unverified model
text: it can locate pages but cannot serve as a verbatim citation, because a model
cannot verify its own transcription. Local OCR language data is a deployment
prerequisite for that fallback only.

Every finding carries a tender citation and bid citation or visual anchor: original
page/block and verbatim quote, or pinned page PNG plus bounding box and observation.
Missing text/marks instead retain the tender obligation and an explicit searched-bid
page/region inventory, with complete/partial search coverage and reason. An absent
file is anchored to the exact submission inventory; do not fabricate an absent quote
or page. Unmapped/partial search yields uncertainty, not definitive absence.

Reuse `Source` and the applicable tender/evidence branches of `VerifiedCitation`;
uploaded text and page regions use additive types. Reject draft references in this
scope. A model returns only server-supplied local refs and bounded excerpts. Resolve
within the current batch, verify a unique continuous span in sent and pinned original
text, and store the exact original excerpt as in
[check citation validation](check.md#input-manifest-rules-and-citations). Image
observations are not verbatim text. Privacy placeholders cannot be reverse-restored
into new model citations. Protected originals and safe external projections remain
distinct; token reads never receive original sensitive quotes via report serialization.

Each finding records rule/model/human-reviewed basis, rule version or model identity,
model confidence when model-derived, impact (`rejection`, `lost_points`, or both),
severity `fatal/high/medium`, and remediation. Confidence is not calibrated accuracy.
Fatal means an apparent mandatory rejection trigger under a cited clause; actual
rejection remains the evaluation committee's decision. Missing interpretation stays
unresolved rather than borrowing a legal rule or model background knowledge.

## Report, scoring and human decisions

Word and console read the same immutable report and decision snapshot, carrying the
same input hash, coverage, findings and score status. The report follows this order:

| Section | Required content |
| --- | --- |
| 一、总体结论 | Rejection items found/no items found/undetermined, biggest risk, available score range and its scope, unresolved coverage and remediation summary. “No items found” never means guaranteed acceptance. |
| 二、基本信息 | Tender/submission identity, lots, ordered file inventory, explicit bid date, preparation versions, run and decision snapshot; bidder identity only in authorized human views. |
| 三、废标判定 | One row per compliance item and ★/▲ clause: response/deviation/missing/unknown, tender/bid anchors and basis. Quotation defects include missing fields, inconsistent totals/currency, arithmetic/rounding, validity or signature requirements when determinable locally. |
| 签章校验 | Separate unnumbered section between 三 and 四: requirement-location checklist, missing/uncertain marks, owner/date/location results and independent PDF signature results. |
| 四、高风险缺陷 | Fatal/high/medium findings, rejection or lost-points impact, exact basis, original machine result and current human disposition. |
| 五、得分预估 | Each technical/commercial/price part: maximum, nullable estimate/range, rule and bid basis with verbatim citations, deduction reasons, unassessable items and assessed subtotal distinguished from a total. First slice explicitly says scoring not requested/not implemented. |
| 六、证据核对 | Claim-to-image comparisons, model/value/owner/validity dimension results, actual page regions, privacy limits and pending/accepted/rejected human reviews. |
| 七、补救清单 | One actionable entry per issue, linked finding, responsible role, expected impact and deadline only if known from a cited tender date or explicit human input. No invented deadline or fabricated supporting material. |
| 八、检验说明 | Input scope, excluded/unparsed pages, checks not run, models/versions and local rules used, incurred cost and unresolved holds, run/decision time, and “评标委员会决定最终评审结果；本报告仅供辅助审查。” |

Console lists are paginated and show linked source pages on demand. Word generation
is deterministic local rendering of validated content, with no model rewrite at export.
Report artifacts pin report/decision hashes, renderer version, hash/size and format;
rerendering after a decision creates a new artifact, never rewrites an older Word file.
Machine findings remain immutable. A visible stale-artifact indicator directs a human
to a new snapshot. No unpublished or partially written DOCX is downloadable.

Scoring is the second slice. Reuse the existing human-confirmed rubric and supported
aggregation rules from [score](score.md#first-version-aggregation-algorithms), bound
to the same tender/extraction. Map uploaded text/image evidence through a new scoring
Provider request, not a fake `ResponseItem`. Unconfirmed or contradicted evidence,
thin promises, ambiguous rules and subjective/live-demonstration criteria remain
unassessable. Compute finite Decimal bounds with the existing rounding policy; missing
items never become zero or full marks merely to produce a total.

Price scoring and any multi-bidder comparison require **explicit user-supplied opening
prices**, opening time, bid validity/inclusion decisions, source and all inputs required
by the cited tender formula. Without these, estimate only technical/commercial parts;
`price` is unavailable and the overall total is null. Do not infer competitor prices
or forecast rank from public data. Availability of prices alone does not make an
unsupported formula executable. Formula evaluation requires a reviewed bounded local
algorithm ID/version and confirmed parameters; arbitrary formula text is never `eval`ed.
No new price algorithm is approved by this contract. Until separately enabled, price
inputs are retained as supplied and price scores stay unsupported. Comparative results
mean arithmetic under supplied data, not predictions about the actual committee.

Human decisions append `dismiss`, `reopen` or `confirm`, retaining actor, time,
nonblank reason, expected finding revision, prior decision ID and run input hash.
Only the stored domain's authorized human reviewer can act; admin role alone is not
a technical/commercial review grant. Unclassified findings require classification
before a decision. Stale input or concurrent revision conflicts with no partial write.
Dismissal changes current disposition only, preserving the machine finding, original
severity and score output. New runs do not inherit dismissals. A corrected file needs
a new submission/review; no “fixed” button silently clears a risk. Evidence-confirmation
history is separate and equally append-only. Confirm records agreement with the
advisory finding; it does not confirm Evidence or satisfy export gates. Confirm and
dismiss apply to an open disposition; reopen restores either closed disposition.
Classification and decisions share the finding revision and predecessor event ID;
reclassification is permitted only while open.

## Providers, Clef and charging

Existing configurable LLMs perform extraction, location mapping, semantic compliance
and cited reasoning. Only implementations under `server/app/providers/` may call
external APIs. The contract's Provider methods accept bounded redacted contexts and
return candidates; services independently verify sources, coverage and human gates.
Provider failure never silently selects a different model or treats missing output as
success. Documents are untrusted data, never tool instructions.

[Cloudflare's Clef model reference](https://developers.cloudflare.com/workers-ai/models/clef/)
describes `@cf/cloudflare/clef` as a 27B multimodal decision model, with a 65,536-token
context, text/JSON state, typed `noul`/`choice`/`score` questions and probability
outputs. It accepts at most four embedded images and is priced per input token.
This contract uses only page-level triage: seal/signature presence, date filled,
document/page type, certificate-or-not and image-supports-claim. Clef supplies **no
reasons or citations**; a triage answer cannot be a finding, final pass, evidence
confirmation or score. Basis-requiring conclusions go to cited LLM/OCR/local rules
and human review. Escalate ambiguous and all rejection-critical missing-mark checks;
no confidence threshold alone closes them.

Clef is enabled by default. Every review includes Clef triage once the platform has
configured the adapter, both gateway credentials, capability validation, a versioned
fixed **per-call sale quote**, request bounds and pricing policy. The submitter may turn
it off for a single review in preflight, which shows the planned Clef calls and their
fixed price under the same budget admission. When that platform configuration is missing
or invalid, or the gateway check fails, the review runs without Clef and the report states
that triage was unavailable; local checks, cited LLM paths and human review still cover
every required location. Initial Clef has no org BYOK path. LLM BYOK remains governed
by the existing budget contract. Provider image limits are stricter than general page
rendering: at most 4 MiB and 16 million pixels per image, 8 MiB total decoded image
bytes and 13 MiB complete serialized request; no remote URLs. Questions are bounded
to 64 and local request sizing must prevent vendor context truncation. Any deliberate
smaller image derivation must have its own hash and coordinate mapping; never silently
downsample an authoritative source or crop away a required seal location.

Returned `usage.input_tokens` has been observed to vary for identical Clef requests.
Retain it only as provider telemetry, not the org charge or task liability. Each
actual dispatch obtains a `BudgetCallQuote` whose platform charge/task amount is the
fixed configured call price; the completed call settles exactly that quote once.
Changing images/questions within the allowed envelope cannot change that call's sale
price after admission. Retries are separate admitted calls. Vendor USD remains
independent, nullable unless verifiable; aggregate experiment tokens do not establish
a vendor bill. A malformed answer with proven completed dispatch still follows the
fixed-price settlement policy; a timeout/unknown completion retains the hold until
reconciled, never assumes free or retries without admission. Missing fixed pricing
blocks Clef, with no token-based fallback. This is a required extension of the current
accounting adapter, not a claim that token-based adapters already implement it.

Clef traffic goes only through a configured Cloudflare AI Gateway, using its Workers AI
route (`gateway.ai.cloudflare.com/v1/{account}/{gateway}/workers-ai/@cf/cloudflare/clef`),
never the direct Workers AI REST endpoint. It needs two separate platform credentials
under [platform credentials](platform-credentials.md): the Workers AI model token
(`Authorization`) and the gateway authentication token (`cf-aig-authorization`); a gateway
with authentication enabled rejects either alone. Neither is org-visible, and a missing
one blocks Clef. Configuration and connection tests read the gateway through its API and
record that authentication is on, log collection and log push are off, caching is
disabled, gateway retries are off and a rate limit is set; any other state blocks
dispatch. Each request also sends `cf-aig-collect-log: false`. The gateway may be shared
with other Cloudflare capabilities, so its rate limit bounds their combined traffic.
Workers AI usage is billed to the platform through the gateway's unified billing; that
vendor bill is reconciled separately and never replaces the fixed per-call sale quote.
HTTP 429 from the gateway rate limit means the call was not completed: back off per
`Retry-After`, keep or release the hold under the admission rules, and re-admit before
retrying. Gateway request and trace IDs are kept as provider telemetry for reconciliation.

The first Clef image profile excludes price pages even after a general external-price
grant; that grant may authorize only an enabled cited LLM/OCR path. Clef's probabilities
remain normalized internal answer types, not a claim that the vendor returns a reason,
an abstention probability or an independently calibrated confidence score.

All paid calls use [budget admission](budget.md#admission-and-concurrent-settlement):
Task → Job → OrgBalance locks, fixed request/provider/price hashes, live grants,
per-call reservation and idempotent settlement. LLM BYOK has zero platform charge but
may have positive/unknown task liability. `max_charge` bounds platform charges only.
Local PDF signature checks and report assembly make no model call and no fabricated
usage. Local OCR retains the existing free-call accounting and its own page count.
Cancellation, failed citations and model refusal never erase incurred usage.

## Confidentiality, roles and lifecycle

Original bids, page images, OCR, identity lists, quotations, certificate subjects and
human reasons are encrypted org business content. Use existing file permissions plus
the stricter source-origin gates below. Storage keys are server-generated beneath
`org/{org_id}/bid-review/{submission_id}/`; public views carry IDs/hashes, never keys.
Read bounded ciphertext/plaintext and validate descriptor/hash before parse/use.
Neutral filenames, no-store responses and signed application links with live
authorization follow [attachment downloads](attachment-archive.md#privacy-permissions-and-signed-downloads).
No public S3 links or browser persistent storage of report content are introduced.

Before **any external** model/OCR/visual call, perform local redaction: existing pattern
detection, registered confidential values, and bidder/staff-name lists derived locally
from the bid and human-supplemented where needed. Names cannot be discovered by first
sending the unredacted bid to an external model. Pin list hashes, value revisions,
redaction policy, sanitized text/image hashes and exact reviewed outbound scope.
Short names, obfuscated scans and unrecognized sensitive areas need human review;
patterns alone do not certify privacy. Original citations remain encrypted; redact
all outbound text leaves including filenames, headings, metadata and model reasons.
Disabled text redaction blocks external work, with no per-request bypass.

Image redaction uses opaque removal/re-encoding and the existing screenshot privacy
workflow with exact reviewed pixels and reversible geometry metadata. Remove identifying
QR codes, ID-card content, accounts, people/identity data and image metadata. For a
presence-only question blur/obscure seal text while preserving sufficient mark shape;
use irreversible masks where blurring cannot safely hide text. Do not transmit raw
seal identity merely to check presence. Persist the redacted image hash and privacy
review, not just a mask proposal. Owner/holder checks needing hidden identity use
local processing or a human; they cannot be inferred from anonymized pixels.

Price pages, including mixed pages and uncertain price-page classifications, are
excluded from external calls by default. A human task owner with admin/bidder authority
may explicitly enable specified price pages for a pinned submission/provider/purpose,
using a revision-checked, audited grant. This permits only sanitized page transmission,
never removal of mandatory value redaction or external competitor-price comparison.
Remaining price-dependent judgments run locally/human or stay unknown. Revocation or
changed pixels/settings stops later calls and invalidates the preflight; in-flight
usage still settles. Human clearance for a page/purpose cannot widen to all files.

| Proposed scope | Role/task intersection and token boundary |
| --- | --- |
| `bid-review:read` | All four org roles with live task read; explicitly issued tokens receive only safe IDs/counts/status and redacted advisory summaries. No raw text, identity, price, pixel, human reason or artifact link in token projections. |
| `bid-review:run` | Admin/bidder/technical task owner or contributor; explicit tokens may preview/submit only an exact already human-authorized sanitized scope. No upload, raw-source authority, new page disclosure or built-in-agent tool expansion is implied. |
| `bid-review:upload`, `bid-review:prepare`, `bid-review:original:read` | Human admin/bidder task owner/contributor for upload/prepare; original reads recheck live task access. Technical/viewer gets only explicitly privacy-cleared human views, not raw bid files. |
| `bid-review:source:read`, `bid-review:report:read` | Human task readers; admin/bidder may read originals/protected details with original-read authority, others only a purpose-built privacy-cleared projection. Never serialize the full protected report to a reader who lacks original-read authority. |
| `bid-review:decide`, `bid-review:evidence:review` | Human bidder for commercial and technical for technical, intersected with stored domain/task review rights and authorized evidence pixels. No admin cross-domain override. |
| `bid-review:outbound:authorize`, `bid-review:price:release` | Human task owner with admin/bidder authority; pins exact sanitized page/submission and provider/purpose. Agents/workers cannot originate a grant. Pixel clearance uses the existing human screenshot privacy workflow. The price scope also permits local opening-price input, whose separate human receipt does not imply an external-page grant. |
| `bid-review:classify` | Human admin with task management authority records a finding's professional domain with expected revision/hash and reason; classification grants no finding/evidence decision authority. |
| `bid-review:report:render`, `bid-review:report:download` | Human admin/bidder with protected report access and task access; Word and full report JSON are protected report artifacts, never an agent download channel or bid `export` grant. |

Add the human-only scopes to `auth.HUMAN_ONLY_SCOPES`, role/task ceilings and DB token
constraints together during implementation, preserving `evidence:confirm`, `export`
and every existing exclusion. Deny mixed forbidden token grants atomically. Existing
tokens gain nothing. A worker carries the verified initiator and a fixed job scope;
it cannot acquire human scopes, impersonate a session or disclose raw snapshots to
an advisory token. The internal gate may inspect exactly authorized originals to
validate integrity; it may return only the permitted projection. Apply these gates
through generic document/chunk/source/rendition/job/SSE/history/download routes too.

Retention/deletion remains an [open decision](#open-decisions). Recommended first
slice follows attachment preservation: no automatic purge, cascading content delete
or public deletion route; deactivation blocks new use while protected history remains.
Define any later org-requested purge across originals, OCR, renders, reports, caches,
model retention and backups before claiming erasure. Plaintext processing directories
are attempt-owned and removed after success/failure/cancel; unreachable encrypted
objects are reconciled only after proving they are unreferenced. Do not erase audit
or shared ledger history, nor assume the storage Protocol implements deletion.

## Preflight, HTTP and CLI

Proposed metadata routes use `/v4`; none are registered by this contract. A standalone
console wizard atomically uploads the tender and bid files as one immutable submission,
then explicitly previews/submits preparation and review. Upload validates bounded bytes
without model calls; local preparation parses/renders, discovers redaction candidates
and validates PDF signatures. Exact outbound privacy clearance is a human action after
preparation. Paid requirement extraction occurs only in the explicitly admitted review
or a separately authorized existing extraction job, never secretly during upload.
Before review preflight, local preparation must have produced a complete immutable
page/parse manifest and applicable human outbound grants. Missing preparation is an
explicit blocker, not permission to OCR or render during dry-run. Local OCR preparation
uses free per-page accounting; its preview estimates those calls without running OCR.

`BidReviewRequest` fixes submission, bid/assessment date, selected slice, optional
confirmed rubric/opening-price input and supported reasoning preference. Provider
configuration and deployment limits are server-resolved, not caller-selectable endpoints
or prices. Submission requires the exact `expected_input_hash`, signed preflight receipt
and request identity from a successful preview; preview expires after 15 minutes.
The manifest pins file/page/order hashes,
tender extraction/source/rule coverage, local preparation/trust versions, redaction
and human outbound grants, model/prompt/schema/rule/price versions, scoring inputs
and limits. Transient balances and task budget revisions are excluded from semantic
cache identity and rechecked live. A signed preflight receipt binds actor/org/task,
options/hash and expiry without persisting a preview or trusting client estimates.

Preview is call-free and write-free: no OCR/LLM/vision/conversion, Job, object, audit,
reservation or usage. It reports limits, first-pass planned calls/quotes, maximum
calls, unpriced/unknown work, next-call blocker and `full_run_guaranteed=false` using
`BudgetPreflightData`. Unknown future mappings do not justify guessed exact request
bytes: provide conservative stage envelopes and uncertainty, then quote actual bytes
before each call. Findings and maps generated during execution belong to the run's
output manifest, not an impossible preflight snapshot. A blocker still permits preview
exit 0; submit independently rejects it. Old bytes/price/privacy/configuration require
a new preview, not automatic rebinding or downgrade to a cheaper mode.

| Proposed HTTP | CLI (`bid … --json`) | Typed payload/result |
| --- | --- | --- |
| `POST /tasks/{task_id}/bid-submissions` | `review upload --task UUID --input META.json --file FILE [--file FILE] [--dry-run]` | `BidSubmissionCreate` plus bounded multipart PDF/DOCX → `BidSubmissionUploaded` or `BidUploadPreview`. Tender and bid roles are explicit; 20 files includes both roles. |
| `GET /tasks/{task_id}/bid-submissions`; `GET /bid-submissions/{id}` | `review submission list/show` | Paged safe submission metadata; original access separately gated. |
| `POST /tasks/{task_id}/bid-submissions/{id}/prepare` | `review prepare --task UUID --input PREPARE.json --dry-run`; same with `--expected-input-hash HASH --preflight-token RECEIPT [--retry] [--wait]` | `BidPrepareRequest` → `BidPreparePreview` or `AssessmentJobAccepted`; completed preparation → `BidSubmissionView`. |
| `POST /tasks/{task_id}/bid-reviews` with `dry_run=true` or explicit submit | `review run --task UUID --input REVIEW.json --dry-run`; same with `--expected-input-hash HASH --preflight-token RECEIPT [--retry] [--wait]` | `BidReviewRequest` → `BidReviewPreview` or reused `AssessmentJobAccepted`; wait → `BidReviewJobResult`. |
| `GET /tasks/{task_id}/bid-reviews`; `GET /bid-reviews/{id}` | `review list/show` | Paged run summaries; authorized report sections/findings, or safe token projection. |
| `GET /bid-reviews/{id}/findings` | `review findings --id REVIEW` | Bounded findings with `severity`, `state`, `outcome` filters and event-snapshot-bound cursors; tokens receive metadata only. |
| `POST /bid-reviews/{id}/findings/{finding_id}/decisions`; `GET` same | `review decide/history` | `BidReviewDecisionRequest` → immutable decision; separately paged history. |
| `POST /bid-reviews/{id}/findings/{finding_id}/classification` | `review classify --input CLASSIFY.json` | Human classification with expected revision/hash and reason; no self-assigned domain in a dismissal request. |
| `POST /bid-reviews/{id}/evidence/{check_id}/decisions`; `GET` same | `review evidence decide/history` | `EvidenceReviewDecisionRequest` → `EvidenceReviewDecision`; no Evidence confirmation side effect. |
| `POST /bid-submissions/{id}/outbound-authorizations` | `review outbound authorize --submission UUID --input GRANT.json` | `OutboundAuthorizationRequest` → `OutboundAuthorizationView`; exact prepared sanitized scope, provider and purpose, including revocation. |
| `POST /bid-submissions/{id}/price-releases` | `review price release --submission UUID --input RELEASE.json` | `PriceReleaseRequest` → `PriceReleaseView`; human external-price-page grant or revocation. |
| `POST /bid-submissions/{id}/opening-prices` | `review opening-prices add --submission UUID --input PRICES.json` | `OpeningPriceCreate` → protected `OpeningPriceInput`; local scoring input only. |
| `POST /bid-reviews/{id}/artifacts` (preview then submit) | `review report --id UUID --input REPORT.json --dry-run`; same with `--expected-input-hash HASH --preflight-token RECEIPT` | `BidReportRenderRequest` → `BidReportRenderPreview` or existing job receipt; completed output → `BidReviewReportArtifact` pair. |
| `GET /bid-review-artifacts/{id}/download-link`; authenticated byte handler | `review report download --artifact UUID --output PATH` | Short-lived human-only link, validated file descriptor/hash and local receipt; no raw file in Result. |
| Existing job status/wait/cancel, extended for proposed kinds | `job status/wait/cancel --id UUID` | Existing Job receipt/result filtering plus budget attachment and new origin gates. |

The source redaction/privacy stage reuses upstream exact-pixel review before outbound
authorization. Paths remain proposed until approval; their service boundaries are
defined in the contract module.
All commands are noninteractive; absent arguments fail. Local and remote CLI modes
retain server, PostgreSQL and identity gates. Pagination defaults to 50, maximum 100;
actor/org/task/filter-bound cursors expire at 15 minutes. A large report is assembled
from bounded sections/pages, not one unrestricted list response.

Result retains exactly `ok`, `command`, `data`, `items`, `warnings`, `cost`,
`duration_ms`. Dry-run `cost` is the conservative estimate with its basis; reads and
admission have zero current cost; wait/status reports cumulative actual job cost.
Vendor USD is distinct from Decimal-string platform/task amounts and may be null.
No new Result version or legacy 3.0 command is introduced. Structured errors retain
safe `code`/`message` and available job ID, never source content or vendor bodies.

Illustrative `BidReviewRequest` dry-run input; identifiers are synthetic:

```json
{
  "request_id": "00000000-0000-4000-8000-000000000101",
  "submission_id": "00000000-0000-4000-8000-000000000102",
  "assessment_date": "2026-10-07",
  "scope": "uploaded_bid",
  "review_slice": "compliance",
  "dry_run": true
}
```

Illustrative `Result` admission with `AssessmentJobAccepted` in `data`; it claims no
completed report and no incurred model cost:

```json
{
  "ok": true,
  "command": "review run",
  "data": {
    "job_id": "00000000-0000-4000-8000-000000000103",
    "status": "queued",
    "cached": false
  },
  "items": [],
  "warnings": [],
  "cost": {
    "llm_tokens": 0,
    "ocr_pages": 0,
    "usd": 0.0,
    "basis": "zero",
    "charge": "0",
    "billing_currency": "USD",
    "task_amount": "0",
    "unpriced_calls": 0,
    "unresolved_calls": 0
  },
  "duration_ms": 18
}
```

The example currency is illustrative; actual currency comes from deployment
configuration. The CLI overlays the returned hash/receipt only for deliberate submit,
and never echoes receipt strings into logs or shared test artifacts.

| Exit | Meaning |
| --- | --- |
| 0 | Valid preview, accepted job, authorized read/decision or complete selected scope; finding a rejection risk is not a command failure. |
| 2 | Invalid input/limits or 409 changed hash/revision/privacy/preflight expiry; refresh/review input, no silent truncation. |
| 3 | Retryable storage/queue/provider capacity or wait timeout; waiting timeout does not cancel or release holds. |
| 4 | Identity/access/integrity, unsupported enabled capability, nonretryable provider/budget or publication failure without a safe report. Missing/inaccessible resources use uniform 404. |
| 5 | Retained partial report with explicit unknown/uncovered items or failed independent batches; `ok=false`, `completion=partial`; report show/wait agree. |

Deliberately unrequested scoring and unavailable competitor prices are declared scope
limits, not failed compliance execution. An included requested score item that cannot
be assessed makes scoring partial; no complete total is manufactured. Receipt success
only proves admission, not a finished review or a downloadable Word file.

## Jobs, bounds and data model

Use existing Job/Procrastinate execution, transactionally enqueue after persistent
authorized input, and retain leases, attempt `run_id`, heartbeat, cancellation and
live publication gates. Proposed kinds `bid_review_prepare`, `bid_review` and
`bid_review_report` separate preparation, review and report rendering; each binds the
real tender `Document` and task, plus exact bid submission
parents. No `provider_test` exception or invented document can carry uploaded work.
Scoring and Clef are stages within the review run with separate pinned capability
usage; their enablement does not widen the confirmed-draft job kinds.

| Recommended bound | Enforcement |
| --- | --- |
| 20 files total, including tender and bid; PDF/DOCX only; 100 MiB/file, 500 MiB/submission; 1,000 combined pages | Check bounded upload and complete prepared page inventory. Apply lower deployment ceilings: current `Settings.max_upload_bytes` is 40 MiB, so 100 MiB is a proposed ceiling, not currently supported upload behavior. No ZIP, DOC, DOCM, arbitrary path/URL, password-protected or repaired PDF. |
| 200 external dispatches/review including retries: LLM ≤100, OCR ≤60, visual ≤40 | Aggregate stage/child jobs to one run ceiling; inherited execution limits may be lower. Local free OCR separately ≤1,000 pages and remains accounted. Preparation cannot evade review limits via hidden paid extraction. |
| 2,000 extracted obligations; 20 findings/obligation; 10,000 findings or required-location checks/run; 20 tender and 20 bid anchors/finding | Reject limit overflow, retain explicit incomplete coverage; do not discard low-confidence clauses to fit. |
| 128 KiB mutation JSON; 1 MiB list/section output | Use bounded section pagination/continuations; reject oversized indivisible items. Provider serialized envelopes use separate adapter limits. |
| PDF/page raster/process limits from existing settings; one active preparation/render per job | Preserve [PDF isolation](../notes/pdf-parsing.md), bounded Office conversion and cleanup; fail if an original page cannot be inspected within the active limits. No unsafe parent-process fallback. |

Deterministic preflight uses available metadata and never consumes a run call. Retry
counts/charges carry forward, and a paid continuation cannot reset the parent run cap.
Cache identity includes org/task/actor/submission/hash/date/slice/providers/privacy/
rules/rubric; reuse only after fresh authorization. Partial reports are immutable
terminal cache entries. A new deliberately scoped continuation records its parent
and unfinished targets without overwriting history or claiming whole-run completeness.

The module defines submissions/documents/pages, manifests and previews, review runs,
clause coverage/findings, signature requirements/checks/PDF results, claim-evidence
checks, scoring parts, human decisions and report artifacts. Pydantic enforces shape;
source existence, authority, containment, quote validity and relational integrity
remain independent service/DB gates.

| Proposed persistence | Content and migration obligations |
| --- | --- |
| `bid_submissions`, `bid_submission_documents` | Immutable ordered version, tender/task/lot, document roles, encrypted names, source hashes/lengths, uploader and request replay identity. Bid roles must never appear in tender-only extraction lists. |
| `bid_document_pages`, preparation bindings | Original/rendered page identity, structural map, parse/OCR warnings and encrypted text; reference existing org storage/source/rendition objects through explicit uploaded-bid lineage, not another unrestricted image store. |
| `bid_review_runs`, coverage and input bindings | Exact prepared submission, request/preflight/output hashes, scope, limits, rule/model/trust/privacy versions, Job and completion/stop reason; source-to-requirement mappings are normalized task-bound links. |
| `bid_review_findings`, citation/search links | Immutable machine findings and normalized tender/bid source parents; searched absence inventory and coverage are first-class, never fabricated quotation rows. |
| Signature requirements/checks and PDF validation rows | Requirement-to-required-location expansion, page-group links, per-dimension result, original PDF revision/certificate validation evidence and encrypted identity detail. |
| Claim evidence checks, scoring parts/support links | Exact claim/image/rubric and per-dimension outcome; human review is separate, numeric results nullable. Explicit local opening-price inputs encrypted and human-bound. |
| Review decisions and outbound privacy/price grants | Append-only human actor/time/reason, previous decision/revision/hash and exact page/purpose. Authoritative allowlists derived from current decisions; no agent-written confirmation flags. |
| Report artifacts | Immutable report/decision snapshot, renderer, Word/console data hash/size, storage reference and human publication authority; no overwrite on later decisions. |

Every business row requires `org_id NOT NULL`, task binding, `ENABLE` and `FORCE RLS`,
current-org `USING`/`WITH CHECK`, org/task composite FKs and runtime non-owner grants.
Actor references use org Membership, not bare User. Document/page/finding/citation/
job/decision joins include all required same-task/submission parents. JSON IDs alone
do not enforce relationships. Add parent composite keys first, then tables/indexes,
RLS, immutability/actor/state guards, token exclusions and source-origin checks;
enable routes only after two-org gate acceptance. No content backfill or confirmed
draft migration is needed. Preserve existing source discriminators; add explicit
uploaded source branches with exclusive-kind CHECKs only where required. If sharing
Document/Chunk tables, all older APIs/jobs must reject uploaded-bid origin unless
they enforce its stronger privacy policy.

Reuse Job, UsageRecord, VendorCall, AuditLog and task events, not a new money ledger
or queue. Disable new admissions and repair forward on rollback; preserve encrypted
files, original reports, decisions and charges. Object storage and DB commit are not
atomic: publication failure exposes no successful artifact, and reconciliation must
not delete an object with an ambiguous committed reference.

## Audit and failure modes

Audit submission/preparation/review/report publication, human decisions, privacy/price
grants/revocations, original/report link issuance and actual authorized byte delivery.
Reuse fixed event prefixes `bid_review.*`; store org/task/object/job/run/actor IDs,
hashes, counts, model/price revisions, fixed codes and times. Keep human reasons only
in encrypted authorized history; audits carry hashes. No company names, per-item bid
text, prices, personal identifiers, raw prompts, QR/seal pixels, signed URLs or vendor
errors enter operational logs, traces or events. Dry-run writes no audit. Replays
return the same authorized receipt without duplicated decisions/charges.

| Failure | Required behavior |
| --- | --- |
| Corrupt/encrypted/unsupported file, DOCX external relationships/active content, decompression or page bounds | Refuse safely in isolated preparation; no execution/fetch of embedded content, no partial file accepted as complete. |
| Incomplete parse, missing expected document, unmapped clause or signature position | Preserve exact gap and scope; partial/unknown, never “all clear.” |
| Invalid PDF signature, changed signed revision, unknown chain/revocation | Independent deterministic result and cited risk/limitation; no visual/model override. |
| Redaction missing, privacy review stale, price page excluded, identity required but hidden | Block external dispatch for that scope; local/human work or explicit unknown. Never fall back to raw pages. |
| Wrong org/task/source, changed membership or token | Uniform 404 or existing identity error before content disclosure; recheck each call, read and publication. |
| Wrong/ambiguous/unsent quote, forged bounding box or changed image hash | Reject conclusion, retain fixed reason and unresolved coverage; no source repair or nearest-page substitution. |
| Clef turned off or unavailable, quote absent, malformed probability or high-confidence error | No implicit fallback billing or final verdict; retain triage failure and route to the configured cited/human path within budget. |
| Budget/call ceiling reached or later independent Provider batch fails | Stop new calls; retain safely validated partial coverage and full usage where permitted. Unknown sends keep holds. |
| Cancellation, lease/input change, accounting mismatch or bound overrun | Hard publication fence; no partial-report bypass. Prior completed artifacts/history remain intact. |
| Word render failure or stale decision snapshot | Console report remains available under access; artifact job fails, no partial Word; explicit retry/current snapshot creates a new artifact. |

## Evaluation basis and acceptance plan

Feasibility evidence is restricted to **aggregates from one redacted winning-bid
sample**. Text experiments dated 2026-10-04 are already described with their aggregate
source files in [B09 evaluation basis](check.md#stage-two-evaluation-basis); their
relevance scores are not tender scores. Do not copy bid content, company names or
per-item results into documentation, fixtures or shared verification artifacts.

The supplied 2026-10-07 image experiment reports 11 redacted pages and 28/29 judgments:
document type 10/11, certificate-or-not 9/9, QR present 5/5, and validity-covers-date
4/4 including post-expiry negatives; approximately 1 second/page and 1.3–1.7k input
tokens/image. The seal experiment compared 10 pages with seals (seal text blurred)
and those pages with seals removed: 20/20, seal probability 0.91–0.99 and no-seal
probability ≤0.03. These are supplied feasibility signals, not independently rerun
measurements, general accuracy, calibrated confidence, pricing evidence or an SLA.

A 2026-10-09 rerun sent the same 20 pages as production presence derivatives
(`presence-gaussian-v1`: whole page at most 256 px, Gaussian blur, text unreadable).
Seal probability was 0.81–0.97 with seals and 0.02–0.08 without, all on the correct
side of 0.5; at the production cut-offs 18/20 were classified and 2 seal pages stayed
uncertain for human review. A 512 px variant with proportionally stronger blur did not
improve separation. The signature-ink question did not separate these pages and is
weak triage. The same coverage limits below still apply.

Faint/greyscale scans, partial/misplaced seals, wrong-company seals, required-position
checks and seam seals are **not yet covered**. A page-level presence result does not
measure end-to-end required-location recall. Freeze those cases in the acceptance set
before default-on triage ships. Repeat identical requests to measure answer/token
variation, but never charge from aggregate or nondeterministic token observations.

| Acceptance track | Required scenario and repeatable artifact |
| --- | --- |
| Standalone flow | New task/tender + multiple bid PDFs/DOCX → explicit prepare/preflight/submit → console/Word, with no DraftRun/cards. Same report IDs, findings, source anchors and section order in both artifacts. A role-based browser flow exercises original/cleared views and remediation. |
| Signature completeness | Synthetic required locations with removed seals/signatures/dates; faint, greyscale, partial, displaced, wrong-owner and seam variants; every-page/group coverage. Primary missing-mark recall and false-positive rate separately; initial curated critical-negative gate: no known missing required mark may be silently passed. Unknown/triage escalation rates are reported, not counted as correct detections. |
| Local PDF cryptography | Locally generated signed fixtures: intact, modified content/incremental update, multiple signatures, untrusted/expired certificate, missing/stale revocation proof, timestamp absence, unsigned visual seal and unsupported format. Assert no external/model calls and distinct crypto/trust/coverage results. |
| Claim evidence | Mutate model, numeric value/unit, holder/manufacturer and validity around the bid date while retaining plausible document appearance. Report mutation accuracy, false acceptance and abstentions separately; require no silently accepted curated wrong-model/value cases and source-bound human confirmation. |
| Human-report benchmark | With separately authorized reference access, compare the generated report against the existing human review report for the reference bid. Freeze reviewer adjudication and matching criteria; publish only aggregate agreement on rejection items, high-risk defects and per-part score-range overlap/containment. Investigate unmatched human findings; do not treat a winning bid or human report as infallible ground truth. Owner-approved tolerances follow baseline measurement. |
| Scope/scoring | Existing check/score continue to reject uploads. No competitor input → only technical/commercial estimate and null total; supplied prices still require complete formula/input authority. Ambiguous rubric/partial evidence never earns default marks. Compliance-only completion distinguishes intentionally unrequested scoring. |
| Confidentiality and permissions | Two orgs and same-org wrong task for every route/table/source/job/history/download; no-context FORCE RLS; token projections, generic legacy-route bypass, live revocation, raw/cleared pixels, price grants, bid-derived names and sentinel secrets absent from all fake external requests/logs/artifacts. Human/worker/agent decisions rejected at API and DB boundaries. |
| Accounting/preflight | No writes/calls on previews; expiry/hash/provider/privacy races; exact fixed Clef quote despite varied reported tokens; duplicate settlement, timeout holds, retries and shared call ceilings; LLM BYOK liability, finite budgets and partial exit 5. No actual Provider calls in CI. |
| Citations/history/recovery | PDF and DOCX original/render mapping, absence anchors, changed hashes, joined/ambiguous quotes, boxes outside content, dismiss/reopen conflicts, immutable old Word snapshots; lease/cancel/orphan/render/storage failures and bounded pagination. |

Before implementation, enumerate these failure scenarios and drive end-to-end
API → worker → console/CLI tests using synthetic Providers and documents. Store
replay commands, fixture hashes, sanitized Result snapshots, Word/PNG outputs and
JUnit/browser evidence under ignored `data/work/bid-review-acceptance/`, never
`docs/`. Real sample evaluation is opt-in, under org permissions, with aggregate-only
export. Contract drafting checks are ruff/format, pyright, import/model JSON Schemas,
document JSON validation, local links and `git diff --check`; they do not establish
database, cryptographic-validator, visual-model or runtime acceptance.

## Phased slices

1. Upload and immutable local preparation, privacy gates, signature checklist and
   local PDF signature validation, rule/LLM compliance checks, append-only dismissal,
   and the complete console/Word report, with Clef seal/signature presence, date-filled
   and page-type triage on by default once fixed-call billing and the frozen triage
   acceptance set pass. Scoring is explicitly unavailable; evidence review can be
   human/local. The signing checklist must work without Clef.
2. Confirmed rubric plus uploaded-evidence scoring, bounded supported local price
   formulas only after explicit opening inputs, and default-on Clef
   image-supports-claim triage after its benchmark gate. Retain all human evidence
   confirmation and no-citation restrictions.
3. Broader scan/seam-seal coverage, larger measured limits and optional B04/B05
   adoption adapter. Retention/purge or online certificate revocation requires its
   own approved policy and acceptance; no implicit enablement.

### Slice 3a: Authorized native-text review

This slice provides the first accounted LLM path over prepared uploads. It includes
human task-owner cleared-text inspection and exact page/hash authorization,
registered-value and bid-derived identity masking, append-only grants/revocations,
live provider/privacy/input fences, write-free preflight and explicit durable
submission. Token execution requires a prior current human grant; tokens receive
safe review/job metadata without quotes or cleared text.

The worker extracts tender obligations with verified original citations and
classifies signing candidates, including unknown applicability and additional
cited clauses. Required every-page or seam locations are expanded against the
fixed bid inventory and remain unresolved. Price, mixed, image and uncertain
pages cannot enter the external text snapshot. Missing preparation, authorization
or provider configuration blocks admission. Shared task/prepaid accounting retains
usage and unresolved holds; provider/budget stops retain explicit partial coverage,
while cancellation, input/privacy changes and accounting failures fence publication.

[Runtime schemas](../../server/app/schemas/bid_review_run.py) and
[privacy schemas](../../server/app/schemas/bid_review_privacy.py) define this bounded
subset of the proposed full contract. It does not include clause-to-bid compliance
judgments, visible mark presence, finding decisions, evidence-image validation,
score estimates, Word rendering or Clef triage. Those stages remain required for
the complete first phase. The implemented interfaces and code pointers are in
[authorized native-text review](../notes/bid-review.md#authorized-native-text-review).

The [synthetic HTTP-to-worker scenarios](../../server/tests/test_bid_review_run_db.py)
cover disclosure, citations, authority, stale grants, cancellation, deduplication
and accounting. PostgreSQL execution and real-browser acceptance have not been
performed for this slice. Captured request/result receipts belong under ignored
`data/work/bid-review-run`, outside documentation.

### Slice 3b: Native-text compliance findings and human decisions

The same authorized review run now compares extracted obligations with sanitized
bid pages. It retains response/deviation/missing/unknown outcomes, verified original
offsets on both sides, separate searched-absence inventories, document-kind rules,
local invalid/modified/non-signing signature rules and mandatory-response gaps.
Budget, cancellation, authorization and call ceilings remain those of slice 3a.
Preflight identifies exact first-stage quotes and an explicitly dependent compliance
envelope; unresolved price-dependent and unsearched work remains unknown.

Immutable findings and normalized sources are published with the review. Human
classification and dismiss/reopen/confirm use an append-only shared revision chain,
with responsible-domain task authority and no admin decision override. The CLI and
existing review page expose findings, filters, source quotes and human history.
The bounded runtime subset is defined by
[finding schemas](../../server/app/schemas/bid_review_findings.py) and the
[mechanism note](../notes/bid-review.md#bid-compliance-and-human-decisions).

The [HTTP/worker acceptance suite](../../server/tests/test_bid_review_findings_db.py)
and [mocked browser suite](../../web/e2e/bid-review-findings.spec.js) are implemented.
Their database/browser execution remains pending in the main session. Generated
verification artifacts belong under ignored `data/work/bid-review-findings`.
Visible mark presence, image evidence review, scoring, Word rendering and Clef
remain outside this slice.

### Slice 4: Console report and Word artifacts

The report captures one published review and the append-only human events current
at render admission. Its decision hash and encrypted content remain immutable;
later decisions require a new render and are shown as a stale-snapshot indicator.
Published partial reviews remain partial, with explicit unknowns and uncovered
scope. Unpublished reviews cannot enter report rendering.

The console reads bounded sections from the same snapshot used by the local Word
renderer. Original-read authority controls protected details; other human readers
receive a purpose-built cleared projection and tokens receive safe metadata.
Human admin/bidder render admission uses a call-free, write-free preview and signed
hash receipt. The report job publishes verified encrypted console/Word artifacts
together; download links and authenticated byte reads repeat live authority.
The [runtime schemas](../../server/app/schemas/bid_review_report.py) define this
bounded subset; [the mechanism note](../notes/bid-review.md#immutable-console-and-word-reports)
links its implementation.

The [HTTP/worker suite](../../server/tests/test_bid_review_report_db.py) and
[mocked browser suite](../../web/e2e/bid-review-report.spec.js) cover snapshot drift,
projections, sections, failures and downloads. Database and browser execution remain
pending in the main session; static checks and CLI tests do not establish those
gates. Repeatable acceptance artifacts belong under ignored
`data/work/bid-review-report`. Scoring, claim-to-image evidence verification,
visible mark presence and Clef remain outside this slice.

## Slice 5: default-on presence triage

Phase 1 implements page-level company-seal and signature-presence questions only.
Date-filled, owner identity, required-box position, seam completeness, page-type
classification, claim-to-image assessment and scoring remain local/human or explicit
future scope. The visible state is always 初筛 / 需人工确认, never confirmed presence.

Required locations first become available in a published native-text review. A human
admin/bidder task owner then explicitly prepares presence derivatives from that review,
inspects the exact JPEGs, authorizes their separate `bid_review_presence` purpose,
and previews a new review run. Newly extracted required pages must intersect those
exact authorized source pages; other locations remain uncovered. The first run and
unconfigured/blocked runs continue with an explicit triage-unavailable coverage code.
Per-run `clef_enabled=false` remains bound to the preflight and job identity.

The [presence mechanism](../notes/bid-review.md#presence-only-clef-triage) defines
pixel lineage, approval invalidation and runtime boundaries. Platform operators manage
`bid platform clef show/set/check`; human owners use
`bid review presence prepare/preview/authorize`. The latter authorization command also
records revocation with `allow_external=false`; its receipt cannot authorize text.
[Runtime image contracts](../../server/app/schemas/bid_review_presence.py) and
[Clef contracts](../../server/app/schemas/clef.py) define the concrete interfaces.

Presence preparation rejects more than forty eligible pages instead of silently
truncating the inventory. Such submissions retain local/text review and explicit
unavailable triage coverage; selecting a smaller image subset before derivation
is not part of this image-authorization interface.

The application labels probability at or below 0.1 as `triage_absent`, at or above
0.9 as `triage_present`, and intermediate observations as `triage_uncertain`.
These thresholds organize human review and are not calibrated accuracy claims;
multiple required mark types use the lowest relevant probability, and seam groups
remain uncertain. All locations retain human confirmation, including high-presence
results. Missing/uncertain marks carry an explicit escalation.

The [HTTP/worker acceptance suite](../../server/tests/test_bid_review_clef_db.py),
[HTTPS mocked browser suite](../../web/e2e/bid-review-clef.spec.js) and
[synthetic blur experiment](../../scripts/check_bid_presence_blur.py) cover the slice.
PostgreSQL and browser execution remain pending; numeric blur checks alone establish
neither real-document privacy nor missing-mark recall. Artifacts belong under ignored
`data/work/bid-review-clef`, outside documentation.

## Open decisions

| Topic | Recommended default | Approval consequence |
| --- | --- | --- |
| Input/command scope | Independent `uploaded_bid`, `bid review …`, immutable multi-file submission; no DraftRun prerequisite | Keeps confirmed-draft services and agent permissions unchanged. |
| First slice | Upload + local preparation + signature completeness including local PDF validation + default-on Clef presence/page-type triage + rule/LLM compliance + console/Word | Score and Clef image-supports-claim triage remain next-slice capabilities, visibly unavailable. |
| Limits | 20 files, 100 MiB/file, 500 MiB/submission, 1,000 combined pages; lower deployment limits win; 200 external calls with 100/60/40 stage ceilings | Benchmark maximum accepted inputs before raising actual deployment settings. |
| PDF validator/trust | **Approved:** `gmssl==3.2.2` for GM/T 0010 SM2/SM3; bounded strict DER; straightforward RSA/ECDSA through existing `cryptography`; pyHanko deferred | Platform operators upload public root/intermediate CA certificates on 信任根证书 under [ADR 0010](../adr/0010-offline-signature-trust.md). Pin each preparation to the enabled store hash; keep crypto, coverage, modification, certificate validity, trust, timestamp and revocation independent. No OCSP/CRL/network checks or automatic trust. |
| Clef | **On by default** for every review once the platform adapter, gateway credentials and versioned fixed per-call sale price are configured; per-review opt-out in preflight; no initial BYOK | Missing configuration or a failed gateway check runs the review without Clef and reports triage unavailable; no dispatch until accounting and capability bounds pass; provider token telemetry never sets user charges. |
| Clef transport | Only through an authenticated Cloudflare AI Gateway with log collection, log push, caching and gateway retries off and a rate limit set; unified billing; separate model and gateway credentials | A gateway in any other state blocks Clef; 429 is a retryable non-completion. |
| Confidentiality | Mandatory external redaction including bid-derived names and exact reviewed image derivatives | Local/human handling when necessary identity/value is masked. |
| Price pages and opening data | Price pages excluded unless human task owner explicitly permits exact sanitized pages; opening prices processed locally | No full score or competitor comparison from missing/guessed input; no strategic pricing. |
| Human authority | Raw files/reports restricted; responsible domain decides findings/evidence; token advisory grants require prior exact human outbound authorization | No token access through quotations, generic sources, jobs or report links. |
| Reports | Common console/Word snapshot, advisory statement, unknowns and original machine conclusions retained | Human dismissal never overwrites history or silently changes scores. |
| Preflight | Write-free, 15-minute actor-bound hash receipt, explicit submission and live per-call admission | No paid discovery on dry-run or automatic retries after changed inputs. |
| Retention/deletion | Preserve encrypted originals/reports/decisions without automatic expiry for first slice; cleanup transient plaintext | Decide retention duration, org erasure, backups and provider retention before adding any purge promise or operation. |
| Release metrics | Zero silent passes on curated critical missing-mark/wrong-model/value negatives; general thresholds after broader baseline and human-report comparison | Small feasibility samples cannot serve as release accuracy claims. |
