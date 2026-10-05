# PDF processing isolation and raster limits

## Problem

A small PDF can declare a page large enough to exhaust a worker during rendering.
Embedded image decoding and vector processing can also consume resources before
the output pixmap exists. Untrusted PDF work must not share the API or job
worker's address space.
A scanned body can also share a page with native page numbers or headers, so the
presence of extractable text does not establish that the whole page was parsed.
Conversely, uncovered margins of a background image beneath native text must not
force OCR of an already readable page.

## Usage

Submit parsing through the [background job workflow](background-jobs.md#usage).
Inspect the parse result's warnings before extracting requirements. A page marked
`ocr=true` includes OCR output in its stored text; it may also contain native text.

## How it works

[pdf_process.py](../../server/app/core/pdf_process.py) starts a fresh Python
subprocess for each upload validation, document parse, preview render, evidence
page render or converted-PDF page count. A parse opens and validates the PDF and
reads every page in **one child**, not one child per page. PyMuPDF is imported in
that child only after resource limits are installed. The child receives document
bytes and operation parameters through a private directory, without database,
storage or provider credentials in its environment.

The parent enforces a wall-clock deadline and kills and reaps the child on timeout
or cancellation. On supported Unix systems the child limits CPU time and output
file size and disables core dumps. Linux additionally enforces address-space and
data-segment limits. Limit setup failures fail the operation; they do not fall
back to processing in the parent. Defaults and deployment overrides live in
[PDFSettings and Settings](../../server/app/core/config.py) and
[deploy/.env.example](../../deploy/.env.example): `BID_PDF_TIMEOUT_SECONDS`,
`BID_PDF_CPU_SECONDS`, `BID_PDF_MEMORY_BYTES` and `BID_PDF_OUTPUT_BYTES`.
The CPU and wall limits cover a whole document parse in one child, so they are
sized for the largest accepted scanned document (`max_pages`), where every page
is rasterized for OCR; lowering them below that turns large genuine tenders into
`pdf_resource_limits` failures.

Only JSON data crosses back: native text, geometry, warnings and base64-encoded
PNG bytes. Results are spooled into a bounded private temporary file and read
one page at a time; OCR calls and usage accounting run in the parent after the
child exits successfully, so OCR latency does not consume the PDF deadline.
Partial results from a failed child are never sent to OCR or saved as chunks.
Temporary plaintext is removed on success, refusal, timeout and cancellation.
Timeout, resource exhaustion, unexpected child failure and invalid child output
map to the non-retryable `ServiceError` code `pdf_resource_limits` (exit code 4).
Ordinary validation and raster-budget refusals retain their specific error codes.

[pdf_raster.py](../../server/app/core/pdf_raster.py) defines the shared PDF raster
budget: at most 20,000,000 pixels and 8192 pixels along either edge. Each renderer
checks finite positive geometry and outward-rounded target dimensions before
calling `get_pixmap`. OCR requests 180 DPI, reducing it to the highest integer
resolution that fits, with a minimum of 72 DPI. Upload validation and parsing
both refuse pages that cannot fit at the minimum, with non-retryable
`pdf_raster_limits` and an instruction to resize or split the page. Parsing
validates all pages before rendering any of them.

Evidence and online previews retain their fixed DPI and existing limit error
codes. The isolated PDF runner maps the same budget failure to `image_limit`.
Certificate PNG/JPEG composition has a separate image-header allocation limit,
described in [Composed originals](versioned-certificate-files.md#composed-originals).

[pdf_reading.py](../../server/app/core/pdf_reading.py) computes the union of displayed
image bounding boxes, excluding the union of native text blocks. Geometry is
clipped to the visible page in unrotated coordinates; overlapping images count
once. Before counting an image, it measures the union of native text block areas
inside that image. If this covers at least **25% of the visible image area**, the
image is treated as background and excluded from both OCR triggering and the
small-image warning. This threshold allows ordinary page margins and line spacing
around dense native text; a page number or short header alone is far below it.
The decision is per image: a background cannot hide a separate scanned body image
elsewhere on the page. Image metadata is read without extracting image binary content. OCR runs
when no native text exists or when the uncovered image area occupies at least
20% of the page. Pure-text pages retain their native text without an OCR call.

The stored text keeps native spans and appends additional OCR text. Native spans
already present in OCR are removed using the normalization and boundary rules
from [extraction.py](../../server/app/services/extraction.py). No provenance
labels are inserted into citable text. Requirement quotes must still match a
unique contiguous span of the stored page text.

## Pitfalls

Reduced OCR resolution, uncovered image regions of 5% to 20% of the page, and OCR
that yields no additional text each produce a page-specific warning. Smaller
images, such as logos, seals and signatures, are neither recognized nor reported. Provider
failures fail the job under the [background job rules](background-jobs.md).
Area coverage is a heuristic: it can send large illustrations to OCR, and it
can miss image-only content beneath substantial overlaid native text. Sparse
letterheads below the background threshold can still trigger OCR. It cannot
establish OCR accuracy or completeness. `citation_verified` means the
stored text has a known page, not that every glyph was correctly recognized.

Previously stored chunks are retained because requirements may already cite them.
A new parse that differs from stored text returns an explicit warning rather than
silently claiming the new OCR text was saved. Upload in a new task to obtain fresh
chunks without rewriting historical citation sources.

Production runs on **Linux**. On **macOS**, neither `RLIMIT_AS` nor `RLIMIT_DATA`
is installed: its virtual-memory allocation does not provide the Linux memory
boundary. Wall time, CPU time and output limits still apply, but macOS development
runs do **not** enforce the configured PDF memory cap. Platforms without the
stdlib `resource` module have only the parent deadline and application output
bound. See Python's [resource limits](https://docs.python.org/3/library/resource.html#resource-limits)
for OS-dependent support.

These are per-operation resource guards, not a filesystem/network sandbox or an
aggregate tenant concurrency budget. Container memory limits and admission
control still matter when several children run together. The existing sandbox
PDF runner already has a separate container and keeps its own lifecycle.
Certificate upload composition has its own processing path; the page render
guards do not claim to isolate that composition or general image decoding.

## Code

- [parsing.py](../../server/app/services/parsing.py): document entry points, parent orchestration and OCR text merging.
- [pdf_process.py](../../server/app/core/pdf_process.py): parent deadline, cleanup and plain-data boundary.
- [pdf_child.py](../../server/app/core/pdf_child.py): child limits and operation dispatch.
- [pdf_reading.py](../../server/app/core/pdf_reading.py): child-only PDF validation and page reading.
- [pdf_raster.py](../../server/app/core/pdf_raster.py): shared allocation preflight.
- [test_pdf_raster.py](../../server/tests/test_pdf_raster.py): synthetic PDF parse and job regressions using fake OCR.
- [test_pdf_process.py](../../server/tests/test_pdf_process.py): child timeout/crash, cleanup and background regressions.
