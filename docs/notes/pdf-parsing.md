# PDF parsing and raster limits

## Problem

A small PDF can declare a page large enough to exhaust a worker during rendering.
A scanned body can also share a page with native page numbers or headers, so the
presence of extractable text does not establish that the whole page was parsed.

## Usage

Submit parsing through the [background job workflow](background-jobs.md#usage).
Inspect the parse result's warnings before extracting requirements. A page marked
`ocr=true` includes OCR output in its stored text; it may also contain native text.

## How it works

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

[parsing.py](../../server/app/services/parsing.py) computes the union of displayed
image bounding boxes, excluding the union of native text blocks. Geometry is
clipped to the visible page in unrotated coordinates; overlapping images count
once. Image metadata is read without extracting image binary content. OCR runs
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
cannot establish OCR accuracy or completeness. `citation_verified` means the
stored text has a known page, not that every glyph was correctly recognized.

Previously stored chunks are retained because requirements may already cite them.
A new parse that differs from stored text returns an explicit warning rather than
silently claiming the new OCR text was saved. Upload in a new task to obtain fresh
chunks without rewriting historical citation sources.

The raster budget limits the output bitmap allocation. It is not a bound on all
MuPDF resources, such as embedded image decoding or complex vector processing.

## Code

- [parsing.py](../../server/app/services/parsing.py): validation, page geometry and text merging.
- [pdf_raster.py](../../server/app/core/pdf_raster.py): shared allocation preflight.
- [test_pdf_raster.py](../../server/tests/test_pdf_raster.py): synthetic PDF parse and job regressions using fake OCR.
