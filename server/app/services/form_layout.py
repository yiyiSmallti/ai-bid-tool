"""Rebuild the forms of a PDF tender's format chapter from page layout.

Chunk text loses what makes a form fillable: the drawn underlines that are its
blanks, empty table cells and merged cells. This module reads the form pages
again with PyMuPDF and keeps them: text lines on one baseline become one row,
a horizontal rule under or between text becomes a blank, ruled tables become
grids with their spans, and wrapped lines that reach the right margin are
joined back into paragraphs. Nothing is reworded.
"""

import re
from dataclasses import dataclass, field
from typing import Any, cast

import pymupdf

# Rows that always open a paragraph: list items, notes, and form labels such as 附件15：,
# which a page break would otherwise join to the previous form's last sentence.
LIST_MARK = re.compile(
    r"^(\d+[、.．]|（\d+）|\(\d+\)|[一二三四五六七八九十]+、|注[:：]|(附件|附表|格式)\s*\d)"
)
# A short signature row, like 投标人名称（盖章）, never continues the note above it.
SIGN_ROW = re.compile(r"^(投标人|供应商|法定代表人|授权|单位|企业|日\s*期)\S{0,20}$")
FOOTER_ZONE = 0.9
ROW_TOLERANCE = 3.0
RULE_BELOW = 4.0
RIGHT_SLACK = 14.0


@dataclass
class Blank:
    """An underlined space to fill; `hint` is text printed over the rule, like （招标人）."""

    width: float
    hint: str = ""


@dataclass
class Text:
    text: str
    size: float = 12.0
    bold: bool = False


type Piece = Text | Blank


@dataclass
class Paragraph:
    pieces: list[Piece]
    page: int
    indent: float = 0.0
    first_line: float = 0.0
    align: str = "left"


@dataclass
class Cell:
    text: str
    row: int
    column: int
    rows: int = 1
    columns: int = 1


@dataclass
class Grid:
    cells: list[Cell]
    widths: list[float]
    heights: list[float]
    page: int


type Block = Paragraph | Grid


@dataclass
class _Line:
    x0: float
    y0: float
    x1: float
    y1: float
    spans: list[Text] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(span.text for span in self.spans)


def _footer(line: _Line, height: float) -> bool:
    return line.y0 > height * FOOTER_ZONE and bool(
        re.fullmatch(r"[-—\s]*\d{1,4}[-—\s]*", line.text)
    )


def _inside(box, rect) -> bool:
    x0, y0, x1, y1 = box
    return x0 >= rect[0] - 2 and x1 <= rect[2] + 2 and y0 >= rect[1] - 2 and y1 <= rect[3] + 2


def _rules(page: pymupdf.Page) -> list[tuple[float, float, float]]:
    """Horizontal rules as (y, x0, x1): drawn lines and hairline rectangles."""
    found = []
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l" and abs(item[1].y - item[2].y) < 0.6:
                x0, x1 = sorted((item[1].x, item[2].x))
                found.append((item[1].y, x0, x1))
            elif item[0] == "re" and item[1].height < 1.5 and item[1].width > 6:
                found.append((item[1].y1, item[1].x0, item[1].x1))
    return found


def _grid(table, page_number: int) -> Grid:
    xs = sorted({round(edge, 1) for cell in table.cells if cell for edge in (cell[0], cell[2])})
    ys = sorted({round(edge, 1) for cell in table.cells if cell for edge in (cell[1], cell[3])})

    def merge(edges: list[float]) -> list[float]:
        out: list[float] = []
        for edge in edges:
            if not out or edge - out[-1] > 2:
                out.append(edge)
        return out

    xs, ys = merge(xs), merge(ys)

    def index(edges: list[float], value: float) -> int:
        return min(range(len(edges)), key=lambda position: abs(edges[position] - value))

    page = table.page
    # Characters are placed by their centre: overflowing glyphs of a neighbour stay out.
    chars = [
        (
            char["c"],
            (char["bbox"][0] + char["bbox"][2]) / 2,
            (char["bbox"][1] + char["bbox"][3]) / 2,
            char["bbox"][1],
        )
        for block in cast(dict, page.get_text("rawdict", clip=pymupdf.Rect(table.bbox)))["blocks"]
        for line in block.get("lines", [])
        for span in line["spans"]
        for char in span["chars"]
    ]
    cells = []
    for box in table.cells:
        if not box:
            continue
        column, row = index(xs, box[0]), index(ys, box[1])
        right, bottom = index(xs, box[2]), index(ys, box[3])
        inside = [
            (top, x, c) for c, x, y, top in chars if box[0] <= x <= box[2] and box[1] <= y <= box[3]
        ]
        text = _cell_text(sorted(inside))
        cells.append(
            Cell(
                text=text,
                row=row,
                column=column,
                rows=max(1, bottom - row),
                columns=max(1, right - column),
            )
        )
    widths = [b - a for a, b in zip(xs, xs[1:], strict=False)]
    heights = [b - a for a, b in zip(ys, ys[1:], strict=False)]
    return Grid(cells=cells, widths=widths, heights=heights, page=page_number)


def _cell_text(chars: list[tuple[float, float, str]]) -> str:
    """Join a cell's characters line by line; wrapped lines run on without breaks."""
    lines: list[list[tuple[float, str]]] = []
    tops: list[float] = []
    for top, x, char in chars:
        if tops and abs(tops[-1] - top) <= ROW_TOLERANCE:
            lines[-1].append((x, char))
        else:
            tops.append(top)
            lines.append([(x, char)])
    return "".join("".join(char for _, char in sorted(line)) for line in lines).strip()


def _lines(page: pymupdf.Page, tables: list) -> list[_Line]:
    out = []
    for block in cast(dict, page.get_text("dict"))["blocks"]:
        for raw in block.get("lines", []):
            spans = [
                Text(span["text"], round(span["size"], 1), bool(span["flags"] & 16))
                for span in raw["spans"]
                if span["text"]
            ]
            line = _Line(*raw["bbox"], spans=spans)
            if not line.text.strip() or _footer(line, page.rect.height):
                continue
            if any(_inside(raw["bbox"], table.bbox) for table in tables):
                continue
            out.append(line)
    return out


def _rows(lines: list[_Line]) -> list[list[_Line]]:
    rows: list[list[_Line]] = []
    for line in sorted(lines, key=lambda line: (line.y0, line.x0)):
        if rows and abs(rows[-1][0].y0 - line.y0) <= ROW_TOLERANCE:
            rows[-1].append(line)
        else:
            rows.append([line])
    return [sorted(row, key=lambda line: line.x0) for row in rows]


def _pieces(row: list[_Line], rules: list[tuple[float, float, float]], right: float) -> list[Piece]:
    """Text and blanks of one row in x order.

    A rule under whole lines makes them the blank's printed hint; a rule under part
    of a line only underlines that text. A wide gap after a colon or before 年/月/日
    is an unruled blank, and so is the end of a row that stops at a colon.
    """
    bottom = max(line.y1 for line in row)
    under = sorted(
        (x0, x1) for y, x0, x1 in rules if bottom - 3 <= y <= bottom + RULE_BELOW and x1 > x0 + 4
    )
    items: list[tuple[float, Piece]] = []
    used: set[int] = set()
    for x0, x1 in under:
        if any(line.x0 < x0 - 2 < line.x1 - 4 or line.x0 + 4 < x1 + 2 < line.x1 for line in row):
            continue
        covered = [
            position
            for position, line in enumerate(row)
            if line.x0 >= x0 - 2 and line.x1 <= x1 + 2 and position not in used
        ]
        hint = "".join(row[position].text for position in covered).strip()
        used.update(covered)
        if hint and not re.search(r"[\w\u4e00-\u9fff]", hint):
            # Punctuation printed on the rule's end belongs after the blank.
            items.append((x0, Blank(width=x1 - x0)))
            items.append((x1, Text(hint, row[covered[0]].spans[0].size)))
            continue
        items.append((x0, Blank(width=x1 - x0, hint=hint)))
    for position, line in enumerate(row):
        if position not in used:
            items.extend(_split_underscores(line))
    items.sort(key=lambda item: item[0])
    pieces: list[Piece] = []
    previous_end = None
    for x, piece in items:
        gap = x - previous_end if previous_end is not None else 0
        if gap >= 14 and isinstance(piece, Text) and pieces and isinstance(pieces[-1], Text):
            if pieces[-1].text.rstrip().endswith(("：", ":")) or piece.text[:1] in "年月日":
                pieces.append(Blank(width=gap))
            else:
                pieces.append(Text("　" * min(4, int(gap // 12))))
        pieces.append(piece)
        previous_end = x + (piece.width if isinstance(piece, Blank) else _width(piece))
    if _field_label_at_end(pieces):
        pieces.append(Blank(width=max(72.0, right - (previous_end or right))))
    return pieces


def _field_label_at_end(pieces: list[Piece]) -> bool:
    """A row ending in a short label and colon, like 投标人全称：, asks for a value."""
    if not pieces or not isinstance(pieces[-1], Text):
        return False
    label = pieces[-1].text.strip()
    if not label.rstrip("：:"):
        return False
    if not label.endswith(("：", ":")) or len(label) > 16:
        return False
    return not re.search(r"如下|下列|以下|请寄|附件|格式|^\d|^注", label)


def _width(text: Text) -> float:
    return sum(text.size if ord(char) > 0x2E7F else text.size / 2 for char in text.text)


def _split_underscores(line: _Line) -> list[tuple[float, Piece]]:
    """Typed underscores (＿＿＿ or ___) are blanks too."""
    out: list[tuple[float, Piece]] = []
    x = line.x0
    for span in line.spans:
        for part in re.split(r"([_＿]{2,})", span.text):
            if not part:
                continue
            if re.fullmatch(r"[_＿]{2,}", part):
                width = sum(span.size if char == "＿" else span.size / 2 for char in part)
                out.append((x, Blank(width=width)))
                x += width
            else:
                piece = Text(part, span.size, span.bold)
                out.append((x, piece))
                x += _width(piece)
    return out


def _starts_new(row: list[_Line], previous: list[_Line] | None, left: float, right: float) -> bool:
    """Whether a row opens a paragraph: the previous row stopped short of the right
    margin, this row is a list item, sits indented like a signature block, or follows
    a wide gap."""
    if previous is None:
        return True
    if row[0].x0 > left + 40 or previous[0].x0 > left + 40:
        return True
    text = "".join(line.text for line in row).strip()
    if LIST_MARK.match(text) or SIGN_ROW.match(text):
        return True
    if previous[-1].x1 < right - RIGHT_SLACK:
        return True
    gap = row[0].y0 - previous[0].y1
    return gap > (previous[0].y1 - previous[0].y0) * 2.5


def page_blocks(
    pdf: pymupdf.Document,
    page_number: int,
    left: float,
    right: float,
    carry: tuple[list[_Line], Paragraph] | None = None,
) -> tuple[list[Block], tuple[list[_Line], Paragraph] | None]:
    """Blocks of one page; `carry` is the last row and paragraph of the previous page,
    so a sentence broken by the page continues in the same paragraph."""
    page = pdf[page_number - 1]
    tables = list(cast(Any, page.find_tables()).tables)
    rules = [
        rule
        for rule in _rules(page)
        if not any(_inside((rule[1], rule[0], rule[2], rule[0]), table.bbox) for table in tables)
    ]
    rows = _rows(_lines(page, tables))
    items: list[tuple[float, Block]] = [
        (table.bbox[1], _grid(table, page_number)) for table in tables
    ]
    previous, current = carry if carry else (None, None)
    centre = (left + right) / 2
    for row in rows:
        pieces = _pieces(row, rules, right)
        if not pieces:
            continue
        if current is not None and not _starts_new(row, previous, left, right):
            if current.indent == 0 and row[0].x0 - left > 1:
                current.indent = row[0].x0 - left
                current.first_line = current.first_line - current.indent
            current.pieces.extend(pieces)
        else:
            x0, x1 = row[0].x0, row[-1].x1
            align = "left"
            if abs((x0 + x1) / 2 - centre) < 12 and x0 - left > 40:
                align = "center"
            current = Paragraph(
                pieces=pieces,
                page=page_number,
                first_line=0.0 if align == "center" else max(0.0, x0 - left),
                align=align,
            )
            items.append((row[0].y0, current))
        previous = row
    items.sort(key=lambda item: item[0])
    blocks = [block for _, block in items]
    last = blocks[-1] if blocks else current
    state = (previous, last) if isinstance(last, Paragraph) and previous else None
    return blocks, state


def margins(pdf: pymupdf.Document, pages: list[int]) -> tuple[float, float]:
    """The text column: the leftmost common left edge and the most common right edge.

    Hanging indents make continuation lines outnumber first lines, so the left edge
    is the smallest one that several lines share, not the most frequent.
    """
    lefts: dict[int, int] = {}
    rights: dict[int, int] = {}
    for number in pages:
        page = pdf[number - 1]
        for block in cast(dict, page.get_text("dict"))["blocks"]:
            for line in block.get("lines", []):
                x0, _, x1, _ = line["bbox"]
                lefts[round(x0)] = lefts.get(round(x0), 0) + 1
                if x1 > page.rect.width * 0.7:
                    rights[round(x1)] = rights.get(round(x1), 0) + 1
    if not lefts:
        return 72.0, 523.0
    common = [x for x, count in lefts.items() if count >= 3] or list(lefts)
    right = max(rights, key=lambda x: rights[x]) if rights else 523
    return float(min(common)), float(right)


def form_blocks(content: bytes, pages: list[int]) -> list[Block]:
    """Blocks of the given pages in order; the tender's own page breaks are not kept."""
    with pymupdf.open(stream=content, filetype="pdf") as pdf:
        left, right = margins(pdf, pages)
        out: list[Block] = []
        carry = None
        for number in pages:
            blocks, carry = page_blocks(pdf, number, left, right, carry)
            out.extend(blocks)
        return out


def plain(block: Block) -> str:
    if isinstance(block, Paragraph):
        return "".join(
            piece.text if isinstance(piece, Text) else piece.hint or "＿" for piece in block.pieces
        )
    if isinstance(block, Grid):
        return "\t".join(cell.text for cell in block.cells)
    return ""
