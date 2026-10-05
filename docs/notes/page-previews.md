# Online page previews

## Problem

Reviewers must check tender citations, certificate originals and released Word
exports without downloading files or having Word installed, including on a
server. A preview must show the stored bytes, not a copy that may have drifted,
and must not widen who can see an export.

## Usage

The org console opens previews in place. A PDF tender original and a certificate
original are shown page by page; the review detail opens the cited page. A Word
tender original is shown from its parsed paragraphs and table cells with the
cited block highlighted, because its page layout is not stored. A released
export is converted to PDF on first open and then shown page by page; later
opens of the same file reuse that conversion.

| Route | Access |
| --- | --- |
| `GET /documents/{id}/pages/{page}/preview` | `task:read`; PDF originals only |
| `GET /resources/certificates/revisions/{id}/file/pages/{page}/preview` | certificate file read |
| `POST /exports/{id}/preview` (`?retry=true` after a failure) | the export download gate |
| `GET /exports/{id}/preview` | export access |
| `GET /exports/{id}/preview/pages/{page}` | export access; conversion succeeded |

Page routes return `image/png` with `no-store`; `zoom=1` renders at 110 DPI and
`zoom=2` at 200 DPI. Export routes answer 404 for another org's or an unknown
export and 403 for roles that cannot download exports.

## How it works

[page_previews.py](../../server/app/services/page_previews.py) reads the stored
original, checks its hash, and renders one page with PyMuPDF inside the shared
[PDF raster budget](pdf-parsing.md#how-it-works) and the PNG byte limit in
`MAX_PNG_BYTES`. Archived certificate pages use the same raster budget.

Opening an export preview passes the same gate as a download, then creates or
reuses one `export_preview` job keyed by export ID and file hash, and records
`export.preview_opened`. The job submission carries the export's immutable
storage key, size and hash, because the worker role cannot read exports. The
worker checks the bytes against that hash and sends them only to the private
converter in [converter.py](../../server/app/providers/converter.py), a
Gotenberg (LibreOffice) service set by `BID_CONVERTER_URL`. The returned PDF is
opened and bounded by `BID_PREVIEW_MAX_PAGES` before it is stored encrypted
under the org's prefix and recorded in the job result. An unreadable, oversized
or failed conversion stores nothing; the job stays failed until an explicit
retry. The generic job reader applies export access to these jobs and omits the
stored PDF location.

Converted pages follow LibreOffice's layout with the converter's CJK fonts, so
line breaks and pagination can differ slightly from Word; the downloaded DOCX
remains the deliverable.
