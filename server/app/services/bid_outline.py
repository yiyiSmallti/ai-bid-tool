"""Outline a parsed tender: its chapters, and the numbered forms of its format chapter.

The outline is derived from stored chunk text on every read, so it needs no model,
no job and no table. PDF chunks contribute their page lines; Word chunks contribute
paragraphs and table rows rebuilt from cell positions, with tab-separated cells like
PDF table rows. A tender whose chapters cannot be found yields no forms rather than a
guessed structure.
"""

import re
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError
from app.models.entities import Chunk
from app.schemas.whole_bid import (
    BidOutline,
    ChapterRole,
    FormKind,
    OutlineChapter,
    OutlineForm,
    OutlineScoringItem,
)
from app.services import documents

CHAPTER = re.compile(r"^第\s*[一二三四五六七八九十百零〇\d]+\s*[章篇部]分?\s*(.*)$")
# A table-of-contents line carries dot leaders or ends in its page number, bare or as - 12 -.
TOC_LINE = re.compile(r"(\.{3,}|…{2,}|·{3,}|\s\d{1,4}$|\D\d{1,4}$|[-—]\s*\d{1,4}\s*[-—]$)")
FORM_LABEL = re.compile(
    r"^(附件|附表|格式)\s*([0-9]+(?:[-－—][0-9]+)?|[一二三四五六七八九十]+)\s*[：:．.、]?\s*(.*)$"
)
LIST_HEADING = re.compile(r"^([一二三四五六七八九十]+)、\s*(.+)$")
LIST_ITEM = re.compile(r"^(\d{1,2})、\s*(.+)$")
PAGE_FOOTER = re.compile(r"^[-—\s]*(第\s*)?\d{1,4}(\s*页)?(\s*/\s*\d+|\s*共\s*\d+\s*页)?[-—\s]*$")
SPACED_CHAR = re.compile(r"[\u4e00-\u9fff]")
DATE_CHARS = set("年月日时地址间")
SENTENCE = re.compile(r"[，。；！？]")
COVER_TITLE = re.compile(
    r"^((商务|技术|资格|资信|报价|价格|经济|商务技术)(及|和|与)?\S{0,6}|投标|响应)文件(正本|副本)?$"
)

# A scoring rule row: item, score range such as 0~25 or 0-10分, then the rule.
SCORING_ROW = re.compile(
    r"^([^\t\d][^\t]{0,40})\t\s*(\d+(?:\.\d+)?\s*[~～\-—至]\s*\d+(?:\.\d+)?)\s*分?\s*(?:\t|$)"
)
SUBJECTIVE_ITEM = re.compile(
    r"方案|预案|措施|组织|人员管理|设施|配置|建议|特色|机制|计划|保障|制度|需求理解"
)
OBJECTIVE_ITEM = re.compile(r"报价|价格|业绩|相符|编制")

KIND_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("deviation", re.compile(r"偏离")),
    ("scoring_index", re.compile(r"评分.*(对应|索引|对照)|(对应|索引|对照).*评分")),
    ("price", re.compile(r"开标一览|报价|价格表|分项价格")),
    ("solution", re.compile(r"方案|实施计划|服务计划|组织设计|技术文件.*自拟")),
    ("roster", re.compile(r"负责人|项目经理|人员|成员|团队|业绩|类似项目")),
    ("copy", re.compile(r"复印件|扫描件|营业执照|证明材料|证明文件|资质证书|证书")),
    ("letter", re.compile(r"投标书|投标函|响应函|授权|身份证明|资格证明书|法定代表人")),
    ("declaration", re.compile(r"声明|承诺|保证")),
)
ROLE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("format", re.compile(r"格式")),
    ("scoring", re.compile(r"评标|评分|评审|评定|评比")),
    ("needs", re.compile(r"需求|技术要求|采购内容|服务内容|技术规格|项目要求|采购清单|服务要求")),
    ("instructions", re.compile(r"须知|公告|邀请|说明")),
)


@dataclass(frozen=True)
class Line:
    text: str
    page: int | None
    block: str | None


def page_lines(chunk: dict) -> list[Line]:
    lines = [line.strip() for line in (chunk["text"] or "").splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    if lines and PAGE_FOOTER.match(lines[-1]):
        lines.pop()
    return [Line(text, chunk["page"], None) for text in lines if text]


def block_lines(chunk: dict) -> list[Line]:
    """Rebuild Word paragraphs and table rows; empty form cells stay empty columns."""
    out: list[Line] = []
    row: dict[int, str] = {}
    row_key: tuple[int, int] | None = None
    row_block = ""

    def flush():
        if row:
            width = max(row)
            cells = [row.get(column, "") for column in range(1, width + 1)]
            out.append(Line("\t".join(cells), None, row_block))
            row.clear()

    for block in chunk["blocks"] or []:
        text = " ".join(str(block.get("text", "")).split())
        if block.get("kind") == "cell":
            key = (block.get("table") or 0, block.get("row") or 0)
            if key != row_key:
                flush()
                row_key, row_block = key, block.get("label") or block.get("block_id")
            row[block.get("column") or 1] = text
            continue
        flush()
        row_key = None
        if text:
            out.append(Line(text, None, block.get("label") or block.get("block_id")))
    flush()
    return out


def lines_of(chunks: list[dict]) -> list[Line]:
    out: list[Line] = []
    for chunk in chunks:
        out.extend(block_lines(chunk) if chunk.get("blocks") else page_lines(chunk))
    return out


def classify(title: str, rules: tuple[tuple[str, re.Pattern[str]], ...], default: str) -> str:
    for name, pattern in rules:
        if pattern.search(title):
            return name
    return default


def chapter_role(title: str) -> ChapterRole:
    if "合同" in title and not re.search(r"投标|响应", title):
        return "contract"
    return cast(ChapterRole, classify(title, ROLE_RULES, "other"))


def chapter_heads(lines: list[Line]) -> list[tuple[int, str]]:
    """Chapter heading lines, skipping the table of contents and in-text references."""
    candidates: list[tuple[int, int, int | None, str]] = []
    for index, line in enumerate(lines):
        match = CHAPTER.match(line.text)
        if not match or len(line.text) > 40 or SENTENCE.search(line.text):
            continue
        if TOC_LINE.search(line.text):
            continue
        text, last = line.text, index
        # A bare 第四章 often carries its title on the next line.
        following = lines[index + 1].text if index + 1 < len(lines) else ""
        if not match.group(1).strip(" ：:") and title_line(following):
            text, last = text + following, index + 1
        candidates.append((index, last, chapter_number(line.text), re.sub(r"\s+", "", text)))

    # A contents page without page numbers, or a sentence listing the chapters, puts
    # consecutively numbered headings on adjacent lines.
    def listed(a, b) -> bool:
        return b[0] - a[1] == 1 and a[2] is not None and b[2] == a[2] + 1

    found: dict[str, tuple[int, str]] = {}
    for position, head in enumerate(candidates):
        before = candidates[position - 1] if position else None
        after = candidates[position + 1] if position + 1 < len(candidates) else None
        if (before and listed(before, head)) or (after and listed(head, after)):
            continue
        # A repeated heading is the table of contents first and the chapter later.
        found[head[3]] = (head[0], head[3])
    return sorted(found.values())


NUMERALS = "零一二三四五六七八九"


def chapter_number(text: str) -> int | None:
    match = re.match(r"^第\s*([一二三四五六七八九十零〇\d]+)", text)
    if not match:
        return None
    value = match.group(1)
    if value.isdigit():
        return int(value)
    if "十" in value:
        tens, _, ones = value.partition("十")
        return (NUMERALS.index(tens) if tens else 1) * 10 + (NUMERALS.index(ones) if ones else 0)
    return NUMERALS.index(value) if len(value) == 1 and value in NUMERALS else None


def title_line(text: str) -> bool:
    return 0 < len(text) <= 20 and not SENTENCE.search(text) and not CHAPTER.match(text)


def place(lines: list[Line]) -> dict:
    pages = [line.page for line in lines if line.page is not None]
    blocks = [line.block for line in lines if line.block is not None]
    return {
        "page_start": pages[0] if pages else None,
        "page_end": pages[-1] if pages else None,
        "block_start": blocks[0] if blocks else None,
        "block_end": blocks[-1] if blocks else None,
    }


def spaced_title(texts: list[str]) -> str | None:
    """A cover title printed one character per line, like 商/务/文/件.

    Dates (年/月/日), column numbers and field labels split by spacing (地/址：) are
    not titles, so only runs of two or more title characters count.
    """
    run: list[str] = []
    for text in texts + [""]:
        if len(text) == 1 and SPACED_CHAR.match(text) and text not in DATE_CHARS:
            run.append(text)
            continue
        if len(run) >= 2:
            return "".join(run)
        run = []
    return None


def form_title(label_rest: str, body: list[Line]) -> tuple[str, bool]:
    """The form title and whether the form is a cover page with a spaced-out title."""
    rest = label_rest.strip().rstrip("：:")
    texts = [line.text for line in body]
    spaced = spaced_title(texts)
    if spaced:
        return spaced, True
    if rest:
        return rest[:80], False
    return (texts[0][:80] if texts else "未命名格式"), False


def list_forms(lines: list[Line]) -> list[tuple[int, str, str, bool]]:
    """Forms of a format chapter numbered as a list: 一、 groups, or the 1、 2、 forms in them.

    Numbered items count only in sequence from 1 under each group, so the numbered
    clauses inside a form are not taken for forms. Groups holding such forms, like
    一、商务响应文件有关格式, stay as divider pages before them.
    """
    groups: list[tuple[int, str, str, bool]] = []
    items: list[tuple[int, str, str, bool]] = []
    expected = 1
    for index, line in enumerate(lines):
        match = LIST_HEADING.match(line.text)
        if match and len(line.text) <= 30 and not SENTENCE.search(match.group(2)):
            groups.append((index, f"{match.group(1)}、", match.group(2), True))
            expected = 1
            continue
        item = LIST_ITEM.match(line.text)
        if (
            groups
            and item
            and int(item.group(1)) == expected
            and len(line.text) <= 40
            and "\t" not in line.text
            and not SENTENCE.search(item.group(2))
        ):
            items.append((index, f"{item.group(1)}、", item.group(2), False))
            expected += 1
    if len(items) > len(groups):
        return sorted(groups + items)
    return [(index, label, rest, False) for index, label, rest, _ in groups]


def split_forms(lines: list[Line]) -> list[OutlineForm]:
    marks: list[tuple[int, str, str, bool]] = []
    seen: set[str] = set()
    for index, line in enumerate(lines):
        match = FORM_LABEL.match(line.text)
        if match and len(line.text) <= 120:
            label = f"{match.group(1)}{match.group(2)}"
            if label not in seen:
                seen.add(label)
                marks.append((index, label, match.group(3), False))
    if not marks:
        marks = list_forms(lines)
    forms: list[OutlineForm] = []
    for position, (index, label, rest, divider) in enumerate(marks):
        end = marks[position + 1][0] if position + 1 < len(marks) else len(lines)
        body = lines[index + 1 : end]
        title, spaced = form_title(rest, body)
        kind = cast(
            FormKind,
            "cover"
            if divider or spaced or COVER_TITLE.match(title)
            else classify(title, KIND_RULES, "form"),
        )
        forms.append(
            OutlineForm(
                index=position + 1,
                label=label,
                title=title,
                kind=kind,
                text="\n".join(line.text for line in body),
                **place(lines[index:end]),
            )
        )
    return forms


def scoring_items(lines: list[Line]) -> list[OutlineScoringItem]:
    items: list[OutlineScoringItem] = []
    rows: list[tuple[int, int, re.Match[str], str]] = []
    for index, line in enumerate(lines):
        match = SCORING_ROW.match(line.text.strip("\t"))
        if match and not SENTENCE.search(match.group(1)):
            # An item name wrapped in its cell leaves its first part on the line before.
            before = lines[index - 1].text if index else ""
            wrapped = (
                index > 0
                and (not rows or rows[-1][1] < index - 1)
                and "\t" not in before
                and len(before) <= 20
                and not re.search(r"[，。；！？：:]", before)
            )
            rows.append((index - 1 if wrapped else index, index, match, before if wrapped else ""))
    for position, (_, index, match, head) in enumerate(rows):
        title = re.sub(r"\s+", "", head + match.group(1))
        end = rows[position + 1][0] if position + 1 < len(rows) else len(lines)
        first = lines[index].text.strip("\t")[match.end() :]
        rule = " ".join("\n".join([first, *(line.text for line in lines[index + 1 : end])]).split())
        text = title + rule
        if all(item.title != title for item in items):
            items.append(
                OutlineScoringItem(
                    title=title,
                    score_range=match.group(2),
                    rule=rule,
                    subjective=bool(
                        SUBJECTIVE_ITEM.search(text) and not OBJECTIVE_ITEM.search(text)
                    ),
                )
            )
    return items


def build_outline(document_id: UUID, chunks: list[dict]) -> BidOutline:
    lines = lines_of(chunks)
    heads = chapter_heads(lines)
    chapters: list[OutlineChapter] = []
    forms: list[OutlineForm] = []
    scoring: list[OutlineScoringItem] = []
    format_titles: list[str] = []
    for position, (index, title) in enumerate(heads):
        end = heads[position + 1][0] if position + 1 < len(heads) else len(lines)
        role = chapter_role(title)
        chapters.append(OutlineChapter(title=title, role=role, **place(lines[index:end])))
        if role == "scoring":
            scoring.extend(scoring_items(lines[index + 1 : end]))
        if role == "format":
            format_titles.append(title)
            for form in split_forms(lines[index + 1 : end]):
                forms.append(form.model_copy(update={"index": len(forms) + 1}))
    return BidOutline(
        document_id=document_id,
        chapters=chapters,
        format_chapter="；".join(format_titles) or None,
        forms=forms,
        scoring_items=scoring,
    )


async def load_chunks(session: AsyncSession, document_id: UUID) -> list[dict]:
    rows = (
        await session.execute(
            select(Chunk.page, Chunk.text, Chunk.blocks)
            .where(Chunk.document_id == document_id)
            .order_by(Chunk.seq)
        )
    ).all()
    return [{"page": page, "text": text, "blocks": blocks} for page, text, blocks in rows]


async def outline(session: AsyncSession, document_id: UUID) -> BidOutline:
    document = await documents.require_document(session, document_id)
    if document.status != "parsed":
        raise ServiceError("not_parsed", "Parse the document before outlining it", 400, 2)
    return build_outline(document.id, await load_chunks(session, document.id))
