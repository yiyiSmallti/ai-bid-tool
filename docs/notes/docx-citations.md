# Word citations by document location

## Problem

Word files have no stable pages: pagination depends on the editor, fonts, and
paper size, and WPS writes almost no page markers. Requirements extracted from
a Word tender still need a citation a reviewer can check, and the quote must
appear verbatim at that position.

## Usage

Upload a `.docx` tender and run `tender parse` and `req extract` as for a PDF;
see [cli.md](../guides/cli.md#run-the-tender-workflow). The document reports
`citation_mode: "block"`. Each requirement's `source` has `page: null` and a
`location` such as `第五章 采购需求 > 二、技术参数 > 第 12 段` or
`第五章 采购需求 > 表 5 第 3 行第 2 列`. The `Source` contract allows the
null page; [ADR 0003](../adr/0003-word-structural-citations.md) records the
decision.

## How it works

`parse_docx` in [docx_blocks.py](../../server/app/services/docx_blocks.py)
walks the body in reading order and turns it into blocks:

- A non-empty paragraph is `p{n}`, numbered across the whole file so the ID is
  unique. Its label counts paragraphs within the nearest heading.
- A table cell is `t{k}r{i}c{j}` on the table grid. A merged cell appears once,
  at its top-left origin (`gridSpan`, `vMerge`); `gridBefore` shifts the
  column. Nested tables extend the path: `t1r3c3/t1r1c2`.
- Content controls (`w:sdt`) are walked like ordinary content.
- Headings come from heading styles or outline levels, including inherited
  ones. A file with neither falls back to numbering (`第X章`, `一、`, `（一）`)
  and says so in the parse warnings.

Headers, footers, text boxes, images, footnotes, endnotes, and comments are not
parsed; the warnings list what was skipped. Footnote separators are recognised
by `w:type` rather than ID, because Word and WPS number them differently.

Blocks are grouped into chunks at level 1 and 2 headings, and a longer section
is split at block boundaries once it passes 8,000 characters. Migration `0013`
stores Word chunks with `page` null, a `seq`, and the `blocks` array; a CHECK
requires exactly one of page or blocks. Requirements store `location` the same
way. Re-parsing a document that the old parser stored as flat unverified text
replaces those chunks.

The adapters render a Word chunk as `<section path="...">` with one
`<block id="p37">` per block, and the model returns that ID as `ref`. The
adapter fills the location from the parsed block, never from model output.
`split_cited` in [extraction.py](../../server/app/services/extraction.py) requires
the location to equal the stored block and the quote to identify one contiguous
span inside that single block. NFKC normalization, curly/straight quote folding,
and whitespace removal are used to locate the span, but the saved `source.quote`
is copied exactly from the Word block. The unmodified provider quote is stored as
top-level `model_quote`. Zero matches produce `quote_not_at_position`. When the
normalized quote occurs more than once, the one occurrence bounded on both sides
by the text edge, whitespace, or list or sentence punctuation (`at_boundary` in
[extraction.py](../../server/app/services/extraction.py)) is cited, so `5mm插孔`
is not confused with the inside of `3.5mm插孔`; `.`, `-` and `/` do not count as
boundaries because they occur inside numbers and model names. Zero or several
such occurrences produce `ambiguous_quote`, even when one occurrence happens to
use the model's exact typography.

Database confirmation and draft checks call `response_locate_quote` from
[0019_citation_boundaries.py](../../server/migrations/versions/0019_citation_boundaries.py).
It maps NFKC-normalized characters back to whole original normalization units,
checks the same original-character boundaries, and rejects partial compatibility
expansions and unresolved ambiguity. The combining-character set is frozen from
Python's Unicode database when the migration runs. `response_citation_valid`
additionally requires the stored quote to occur verbatim at the bound page or
block. An inner `5mm插孔` match in `3.5mm插孔` therefore does not prevent review
of the single standalone `5mm插孔` occurrence. The same rule distinguishes
`内存：≥16 GB` from its occurrence inside `扩展内存：≥16 GB`.

The ★ rule (`★`, 实质性要求, 否决投标, 废标) splits each block on Chinese or ASCII
semicolons and newlines. It uses the located original quote interval to mark
every extracted item contained in the explicitly marked segment, leaves adjacent
unmarked segments unchanged even when they share a substring, and adds
a missing marked segment directly from the source. Heading-only segments ending
in a colon are skipped. Requirements are listed in reading order: document,
chunk `seq`, then block position.

Migration `0017` adds nullable `requirements.model_quote` without rewriting
historical requirements. A human administrator can preview and repair older
normalization-only citations with the explicit task and extraction job flow in
[the CLI guide](../guides/cli.md#repair-legacy-requirement-citations). Rows that
cannot be located uniquely remain unchanged.

## Pitfalls

- A quote that spans two blocks is rejected even if both blocks are adjacent;
  the model must cite one cell or paragraph.
- Repeated normalized text in one block is ambiguous unless exactly one
  occurrence is bounded as above. Repair uses the same rule and does not choose
  an occurrence by typography, proximity, or card state.
- Block IDs depend on parser behaviour. Changing what counts as a block needs a
  new `PARSER_VERSION` and a re-parse, or old citations point at different text.
- Key requirements inside text boxes or headers are not extracted. The parse
  warning is the only signal.
- Measured on the reference tender (WPS, 181 pages, 2,117 blocks): 446 of 449
  quotes verified with thinking disabled. The three failures were a quote
  joined with an ellipsis and two citations of the wrong paragraph. Such items
  are dropped and listed in `result.rejected`.

## Code

- [server/app/services/docx_blocks.py](../../server/app/services/docx_blocks.py): `parse_docx`, `Walker`, `skipped_content`.
- [server/app/services/extraction.py](../../server/app/services/extraction.py): `normalize`, `locate_quote`, `location_of`, `split_cited`, `merge_starred`.
- [server/app/providers/llm.py](../../server/app/providers/llm.py): block rendering and `ref` mapping.
- [server/migrations/versions/0013_docx_locations.py](../../server/migrations/versions/0013_docx_locations.py): schema.
- [server/migrations/versions/0017_exact_citations.py](../../server/migrations/versions/0017_exact_citations.py): exact-source quote provenance for new and legacy requirements.
- [server/tests/test_docx_blocks.py](../../server/tests/test_docx_blocks.py), [server/tests/test_docx_extraction.py](../../server/tests/test_docx_extraction.py): parser and end-to-end cases.
- [test_adversarial_citations.py](../../server/tests/test_adversarial_citations.py): extraction, repair, human confirmation, draft assembly and SQL/Python locator parity.
