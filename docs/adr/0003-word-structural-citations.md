---
kind: adr
---

# 0003 Cite Word tender documents by document location

Date: 2026-10-01. Status: accepted.

## Context

Hard rule 6 originally required a page number for every requirement. Word pagination depends on the layout application, fonts, and paper. The sample tender documents (招标文件) were saved by WPS and reported 181 pages, but contained only 27 manual page breaks. Previously, Word needed conversion to PDF before extraction.

## Decision

- Change rule 6: cite PDF page numbers; cite Word section paths plus paragraphs or table cells. Quoted text must occur verbatim at the referenced location.
- Split Word content into paragraph and cell chunks in body order. Validate a citation only within its referenced chunk.
- Display paragraph numbers relative to the nearest containing section; use document-wide numbers internally.
- Exclude headers, footers, text boxes, footnotes, endnotes, comments, and images. Parsing warnings list skipped categories.
- Upgrade the Result contract from 1.0 to 1.1: `source.page` may be null; add `source.location`.

## Tradeoffs

- Chunk-level validation is stricter than page-level validation: a quote spanning two cells is rejected, but the location points directly to a cell.
- Stop inferring Word page numbers. Reviewers locate sections and table coordinates rather than pages.
- Requirements in text boxes are not extracted. The sample has none; if they appear, parsing warnings are the only way to notice them.
- Older-contract clients see `page: null` for Word sources, so the contract version increases for an incompatible change.

See [docx-citations.md](../notes/docx-citations.md) for the mechanism.
