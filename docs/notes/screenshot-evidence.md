---
kind: reference
---

# Screenshot evidence and delivery decisions

## Problem

An image can disclose private data before it becomes evidence. A human's privacy
release, a claim about the visible pixels, and a decision to deliver an interface
are different actions. Reusing a file must not transfer one card's confirmation
to another card or allow changed pixels to inherit an earlier decision.

The product boundary and source classifications are defined in the
[screenshot contract](../plan/screenshots.md). Human response review follows
[response cards](response-cards.md); model reservations and settlement follow
[prepaid billing](prepaid-billing.md).

## Usage

The screenshot CLI is registered by
[`register`](../../cli/bid_cli/screenshots.py). `screenshot prepare` reads a
PNG/JPEG or an authorized certificate page into memory, applies a fixed local
plan, and publishes a new PNG and receipt together. `screenshot add` requires a
human session and the exact reviewed upload hash in that receipt. An empty
redaction plan still requires human release.

`screenshot annotate` submits a job against one exact parent rendition. The
result remains an unconfirmed material. `card create/update` can link an
`image_region` with an observation and a claim scope. The existing per-card
`card confirm` action is the only path that confirms the resulting Evidence.

`screenshot analyze --dry-run` fixes the selected requirement citations,
redacted text, image hashes, pixel mappings, model identity and image-price
revision. A non-dry submission must provide the returned input hash. Its
suggestions remain unreviewed and cannot populate a legacy exact-text quote.

`ui mock --dry-run` fixes one requirement, one selected feature revision, the
model identity and the prompt version, and prices the single planned call. The
paid run must carry that input hash; its job result is the prototype run.
`screenshot prepare --prototype-run` then downloads the run's source PNG and
applies the same local plan as any upload, and `screenshot add` ingests it as an
`origin=prototype` asset.

When a product revision lacks a source URL, `evidence search --dry-run` shows the
queries, only the revision's vendor and model, and returns their input hash. The
submitted run is a `screenshot_search` job; `evidence candidates` lists its ranked
candidates. `evidence adopt` records one candidate as the product's `official_url`
or `whitepaper_url` in a new revision and reselects that revision on the task.

A vendor page starts as a `sandbox capture` of the selected product revision's
`official_url` or `whitepaper_url`. A web page must use `archive=bundle`; a PDF
always retains its original bytes. `screenshot prepare --sandbox-artifact`
downloads one `capture_png` or `pdf_page_png` through the sandbox's signed link
and applies the local plan. The `screenshot add` input names that page in a
`vendor_web` or `vendor_pdf` source and must set `reviewed_archive_sha256` to
the run's `capture_archive` or `source_pdf` hash, as listed by `sandbox show`.

`screenshot prototype-decisions preview` fixes an explicit feature group and
its current confirmed prototype Evidence. An optional `evidence_ids` subset
supports an individual decision when one feature appears on several cards.
`apply` must submit every target in that preview exactly once. A change to an
existing decision requires its previous ID and a reason.

## How it works

### Local pixels and immutable storage

[`ScreenshotRenderer`](../../server/app/providers/screenshot_renderer.py)
passes input bytes and a bounded request through subprocess pipes. The Rust
[`run`](../../stamp/src/main.rs) implementation decodes PNG/JPEG, normalizes
orientation, applies opaque redaction, crops and draws inside borders. It
encodes RGB PNG without ancillary metadata. Python validates the result,
including its CRCs, decoded row bounds, dimensions, hash and content mapping.

The renderer's privacy profile emits only content pixels. A prototype retains
that clean presentation. Other sources receive a fixed provenance footer with
the embedded licensed font. Footer and padding are never valid evidence regions.
Initial mappings use normalized source coordinates; each later mapping uses its
parent's content coordinates, so the immutable parent chain composes back to the
source. The footer's provenance plan hash can describe the preceding local
privacy operation; the renderer's own plan hash always describes its actual
request.

The API parses upload multipart bodies directly into bounded memory instead of
using framework upload files. It recomputes the PNG and plan hashes. Certificate
sources and retained prototype renders are replayed from authorized fixed bytes;
user-uploaded source hashes remain client declarations. The service renders and
writes the encrypted object before taking the task lock, then rechecks source
selections and publishes the asset, first rendition, privacy review and audit
record together. Attempt-owned unreferenced objects are removed only after a
database check proves they were not published.

All image keys are server-generated below `org/<org_id>/screenshots/`. A preview
signature binds organization, rendition, hash and purpose. Actual content access
still requires a current membership and every underlying material permission,
returns `Cache-Control: no-store`, and rejects withdrawn assets. CLI downloads
check the exact same-origin path, forbid redirects and verify bytes before
publishing a new file.

### Database and human gates

[`screenshots.py`](../../server/app/models/screenshots.py) defines append-only
tenant records. Migration
[`0023_screenshots.py`](../../server/migrations/versions/0023_screenshots.py)
enables and forces RLS with `WITH CHECK`, adds compound parent relationships and
withholds runtime update/delete privileges. Insert triggers validate task and
successful extraction scope, exclusive source branches, fixed selections,
parent privacy lineage, pixel bounds and human actor context.

A privacy review binds both the received upload hash and the first stored image
hash. Descendants inherit that review through their parent chain; they do not
pretend their new image hashes equal the first image hash. Withdrawing an asset
invalidates all descendants and all current evidence references. A replacement
requires a new asset and review.

`image_region` stores an observation separately from the nullable image-branch
quote. Other Evidence branches retain their exact-text requirements. Confirmation
checks the responsible professional role, the complete reviewed Evidence set,
current source selection, withdrawal state and actual stored image bytes. Draft
submission, processing and later reads revalidate the same dependencies.

Prototype decisions are independent append-only records. A batch stores its
exact target manifest; a deferred database trigger requires a matching item for
every target. Each decision also has typed foreign keys to its card revision,
Evidence, rendition and selected feature revision. The task lock serializes
changes and expected-previous checks. Reopening a card, replacing a selection or
changing a bound image invalidates the earlier decision. Deactivated feature,
product and certificate selections cannot be reactivated; choosing an older
revision creates a new selection identity. `keep` preserves
prototype provenance and does not alter the feature library's delivery status.

### Model calls and publication

[`screenshot_vision.py`](../../server/app/providers/screenshot_vision.py) uses
the existing OpenAI-compatible HTTP adapter, reasoning selection, safe metadata
handling and accounted call boundary. The selected platform model's request
options must contain a verified `screenshot_vision` capability with image-count
and pixel limits, a price revision, and an explicit token-bound rule. That
server-owned block is stripped before vendor dispatch. Rule fields are defined
by `_capability` and `_ImageTokenBound` in that module; configured coefficients
must come from the endpoint's verified billing contract.

Image token allowances are calculated from image count and pixels. Base64 length
is excluded from the text allowance. Missing capability or prices prevents
external calls. Analysis sends one image per bounded call and requires the total
selected image bytes to fit the service input bound. The vendor receives local
refs and the selected redacted requirement text, never storage keys, signatures,
original PDFs, source HTML or unreleased images.

The worker conservatively retains all declared inputs as dependencies. It
checks authorization, selection state, hashes, redaction revision and model
identity before each admitted call and again before publication. Usage settles
before model output is parsed, including invalid or refused output. Per-call
usage records also retain image count, image-price revision and a digest of the
outbound request. Valid proposals are stored with resolved input foreign keys;
unknown refs and invalid regions yield safe reason codes. Wire regions are
translated from the sent PNG to its content coordinates before persistence.
Cancellation, ownership loss or accounting failure prevents publication.

### Prototype generation

[`prototype_generation.py`](../../server/app/services/prototype_generation.py)
sends the redacted requirement quote and the feature's name, description and
status, nothing else, through
[`prototyping.py`](../../server/app/providers/prototyping.py) on the same accounted
call boundary as drafting. The prompt asks for one self-contained page with
inline CSS/SVG and no visible prototype wording. The worker keeps the job lease
while the returned HTML is rendered by the offline sandbox `BrowserProvider`,
then stores HTML and PNG encrypted under `org/<org>/prototypes/<run>/`. The
`ScreenshotPrototypeRun` binds the generation job, input hash, both hashes and
the sandbox receipt (descriptor digest plus artifact hashes, runtime versions,
issues and metrics). Inputs are rebuilt before the admitted call and again
before publication, so a replaced feature selection stops the job. An
unavailable sandbox blocks admission. The source PNG is served only to the same
organization at `GET /prototype-runs/{id}/source`.

### Vendor captures

[`vendor_screenshots.py`](../../server/app/services/vendor_screenshots.py)
resolves a vendor source only from a succeeded, cleaned `vendor_capture` run of
the same task and extraction. It authorizes the page image through the sandbox
download checks, requires the product selection that the run captured, and
follows the proxy's allowed fetch receipts from the selected URL through its
redirects to the single HTTP 200 entry response. Ingest rereads the page PNG and
the retained archive from storage, rechecks their hashes, replays the local plan
against the page, and, for a web bundle, recomputes the entry body hash from the
archive entry named by the receipt.

The first page of a run creates its `ScreenshotVendorArchive`; later pages of the
same run reuse it. The record keeps the URL hashes, final origin, the first
`<title>` of the archived entry page, the proxy's capture time, content and
archive hashes, and foreign keys to the run, archive artifact and entry receipt.
Exact URLs stay in the encrypted sandbox records. Migration
[`0025_vendor_screenshots.py`](../../server/migrations/versions/0025_vendor_screenshots.py)
rejects an archive whose run, attempt, bytes, storage key or entry response
differ from the capture, and an asset whose page image belongs to another
attempt or capture format. Vendor images support only `hardware_documentation`,
and card confirmation must acknowledge `vendor_model_scope` in addition to the
general image warnings. An archive also records whether its capture was incomplete
and how many requests the proxy denied; both must match the sandbox receipts, and an
incomplete capture adds the `vendor_capture_incomplete` warning
([`0026`](../../server/migrations/versions/0026_vendor_incomplete_captures.py)). Later card and export checks reread the archive bytes;
they do not depend on the node's current fetch policy file.

### Vendor search

[`vendor_search.py`](../../server/app/services/vendor_search.py) fixes the task,
extraction, product selection and revision, the queries, the search service identity
and the vendor domains already recorded on this organization's current product
revisions with the same vendor name. The worker rebuilds those inputs before searching
and before publishing. [`search.py`](../../server/app/providers/search.py) calls the
Perplexity Search API when `BID_PERPLEXITY_API_KEY` is set, otherwise the SearXNG JSON
endpoint, without redirects or environment proxies; the key goes only to Perplexity. Candidates must pass the
fetch broker's URL canonicalization, so HTTP, credential-bearing and ambiguous URLs are
dropped; duplicates merge their engines. Known vendor domains rank first, then PDFs.
The run stores up to 20 candidates with their rank and flags in
`screenshot_search_runs`/`screenshot_search_candidates`; a search failure is
retryable and stores nothing. Adoption reuses product update and task selection with
the expected product revision, so a stale adoption is refused.

## Pitfalls

- Source hashes establish byte identity, not authenticity. The server cannot
  prove that a user-uploaded image was sufficiently redacted or that a depicted
  system is deployed.
- Privacy release does not grant Evidence confirmation or export permission.
  API tokens cannot hold `screenshot:ingest`, `evidence:confirm` or `export`.
- Image redaction remains mandatory when task text-redaction settings change.
  This explicit analysis path always masks requirement text before dispatch;
  ordinary text-only card generation does not acquire image input implicitly.
- A prototype without a valid delivery decision can still enter a confirmed
  draft and a review copy; formal export enforces the decision through
  [human-section-exports.md](human-section-exports.md).
- A sandbox render failure after the model call fails the job; a retry pays for a
  new generation.
- Search results are unverified. A known domain only ranks a candidate; it does
  not prove the page is the vendor's or describes the model. A vendor archive
  refuses a search-candidate link; adopted URLs reach captures through the product
  library.
- Adopting replaces the task's product selection, which invalidates Evidence bound
  to the previous revision. Search and adopt before capturing.
- Free SearXNG engines scrape public search sites and throttle quickly; some vendors'
  official pages do not appear. A person can still enter the URL directly. No client API writes a prototype run, vendor archive or
  renderer receipt, and archived HTML is never served as an active page.
- A PDF archive records no document title or page count; reading either would
  require decoding the untrusted PDF in the API process. The archived bytes and
  the page number remain the reviewable source.
- A vendor capture proves which bytes the proxy received from an allowed URL. It
  does not prove that the page describes the selected model; the reviewer checks
  the model and page scope on each card.
- Database unavailability after an object write can leave an inaccessible private
  orphan until its publication status can be checked. Deleting an object after
  an ambiguous commit without that check could destroy published evidence.
- The build script requires a Rust toolchain and dependency access. It creates
  the lockfile before its first build, then requires locked builds. A missing
  renderer fails explicitly; there is no Python drawing fallback.

## Code

- [Image contracts](../../server/app/schemas/screenshot_contracts.py) and
  [API routes](../../server/app/api/screenshots.py).
- [Ingest and material checks](../../server/app/services/screenshots.py),
  [job orchestration](../../server/app/services/screenshot_jobs.py) and
  [prototype decisions](../../server/app/services/prototype_decisions.py).
- [Renderer build](../../scripts/build_screenshot_renderer.sh) and
  [font/dependency licenses](../../stamp/THIRD_PARTY_LICENSES.md).
- [API boundaries](../../server/tests/test_screenshot_api.py),
  [card/draft chain](../../server/tests/test_screenshot_cards.py),
  [database gates](../../server/tests/test_screenshot_db.py),
  [vision HTTP boundary](../../server/tests/test_screenshot_vision.py),
  [prototype generation](../../server/tests/test_prototype_generation.py),
  [vendor captures](../../server/tests/test_vendor_screenshots.py),
  [vendor search](../../server/tests/test_vendor_search.py) and
  [CLI contract](../../server/tests/test_screenshot_cli.py).
