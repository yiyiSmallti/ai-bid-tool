"""Outline a parsed tender: its chapters, and the numbered forms of its format chapter.

The outline is derived from stored chunk text on every read. PDF chunks contribute
their page lines; Word chunks contribute paragraphs and table rows rebuilt from cell
positions, with tab-separated cells like PDF table rows. A tender whose chapters
cannot be found yields no forms rather than a guessed structure.

Scoring items come from the rule table parser unless a model review of the same
chunks succeeded; a review's items are only those verified against the source lines.
"""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError
from app.models.entities import Chunk, Job
from app.schemas.whole_bid import (
    BidOutline,
    ChapterRole,
    FormKind,
    OutlineChapter,
    OutlineForm,
    OutlineScoringItem,
    ScoringReviewOutput,
    ScoringWarning,
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
# An explicit marker in the item's row overrides the keyword guess.
SUBJECTIVE_MARK = re.compile(r"主观分|主观评分|主观项")
OBJECTIVE_MARK = re.compile(r"客观分|客观评分|客观项")
SCORING_TOTAL = re.compile(r"(?:总分|满分|合计|总计)[^\d\n]{0,8}?(\d{2,3}(?:\.\d+)?)\s*分")
SCORING_HEADER = re.compile(r"评分|评审|评价|分值|权重|区间|分数|满分")
NUMBER = re.compile(r"\d+(?:\.\d+)?")
# The lines sent for review: the scoring table from just above its header row.
REVIEW_LEAD = 5
REVIEW_MAX_LINES = 600
REVIEW_LINE_CHARS = 300

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


def chapter_spans(lines: list[Line]) -> list[tuple[int, int, str, ChapterRole]]:
    heads = chapter_heads(lines)
    return [
        (
            index,
            heads[position + 1][0] if position + 1 < len(heads) else len(lines),
            title,
            chapter_role(title),
        )
        for position, (index, title) in enumerate(heads)
    ]


def header_row(text: str) -> bool:
    return "\t" in text and len(SCORING_HEADER.findall(text)) >= 2


def review_region(lines: list[Line]) -> tuple[int, int, bool] | None:
    """Lines a model reads for scoring items: from the scoring table's header row.

    The header is looked for in the scoring chapter first, then anywhere, since some
    tenders put the table under an instructions chapter or have no chapters at all.
    """
    scoring = [(start, end) for start, end, _, role in chapter_spans(lines) if role == "scoring"]
    for start, end in [*scoring, (0, len(lines))]:
        header = next((i for i in range(start, end) if header_row(lines[i].text)), None)
        if header is None and (start, end) in scoring and end - start > 1:
            header = start + REVIEW_LEAD
        if header is not None:
            first = max(start, header - REVIEW_LEAD)
            last = min(end, first + REVIEW_MAX_LINES)
            return first, last, last < end
    return None


def review_lines(lines: list[Line], start: int, end: int) -> list[str]:
    return [
        f"L{index}|{lines[index].text.replace(chr(9), ' | ')[:REVIEW_LINE_CHARS]}"
        for index in range(start, end)
    ]


def squeeze(text: str) -> str:
    return re.sub(r"[\s|]+", "", unicodedata.normalize("NFKC", text))


def numbers(text: str) -> set[float]:
    return {float(value) for value in NUMBER.findall(unicodedata.normalize("NFKC", text))}


def title_at(texts: dict[int, str], line: int, stop: int, title: str) -> bool:
    """Whether `title` starts on `line`, whole or wrapped over the next two lines, or
    is assembled from the leading words of the item's lines, as a PDF table cell
    whose name wraps between rule lines prints it. A row number before the name is
    skipped."""
    own = squeeze(texts[line])
    window = own + "".join(squeeze(texts.get(line + k, "")) for k in (1, 2))
    found = window.find(title)
    if 0 <= found < len(own):
        return True
    rest = title
    for number in range(line, stop):
        taken = False
        for word in map(squeeze, texts.get(number, "").replace(" | ", " ").split()):
            shared = len(word) if rest.startswith(word) else len(rest) * word.startswith(rest)
            if not shared:
                if taken or not re.fullmatch(r"[\d.、]+", word):
                    break
                continue
            rest, taken = rest[shared:], True
            if not rest:
                return True
        if number == line and not taken:
            return False
    return False


def verify_review(
    texts: dict[int, str], output: ScoringReviewOutput
) -> tuple[list[dict], float | None, int]:
    """Keep the items whose title and top score are on the source lines they name.

    `texts` maps line numbers to the text the model was shown. A title must start on
    its line (see `title_at`); the top score must appear as a number between the line
    before the item and the next item. Rows of one item scored separately on the same
    line are merged. Returns kept items, the verified total and the number of
    rejected items.
    """
    starts = sorted({item.line for item in output.items if item.line in texts})
    kept: list[dict] = []
    rejected = 0
    for item in sorted(output.items, key=lambda item: item.line):
        title = re.sub(r"\s+", "", item.title)
        if item.line not in texts or not squeeze(title) or item.max <= 0:
            rejected += 1
            continue
        later = [line for line in starts if line > item.line]
        stop = later[0] if later else max(texts) + 1
        span = " ".join(texts.get(line, "") for line in range(item.line - 1, stop))
        if not title_at(texts, item.line, stop, squeeze(title)) or float(item.max) not in numbers(
            span
        ):
            rejected += 1
            continue
        same = next(
            (row for row in kept if row["line"] == item.line and row["title"] == title), None
        )
        if same is not None:
            same["max"] += item.max
        elif all(row["title"] != title for row in kept):
            kept.append({"line": item.line, "title": title, "max": item.max})
    total = output.total
    if total is not None and float(total) not in numbers("\n".join(texts.values())):
        total = None
    return kept, total, rejected


def range_top(score_range: str) -> float | None:
    values = NUMBER.findall(score_range)
    return float(values[-1]) if values else None


def subjective(title: str, rule: str, row: str) -> bool:
    if SUBJECTIVE_MARK.search(row) and not OBJECTIVE_MARK.search(row):
        return True
    if OBJECTIVE_MARK.search(row) and not SUBJECTIVE_MARK.search(row):
        return False
    text = title + rule
    return bool(SUBJECTIVE_ITEM.search(text) and not OBJECTIVE_ITEM.search(text))


def reviewed_items(lines: list[Line], review: dict) -> list[OutlineScoringItem]:
    """Items of a verified review, each with the source text up to the next item as rule."""
    rows = review["items"]
    items = []
    for position, row in enumerate(rows):
        later = [other["line"] for other in rows[position + 1 :] if other["line"] > row["line"]]
        stop = min(later[0] if later else review["end"], len(lines))
        rule = " ".join(" ".join(line.text for line in lines[row["line"] : stop]).split())
        items.append(
            OutlineScoringItem(
                title=row["title"],
                score_range=f"0~{row['max']:g}",
                rule=rule,
                subjective=subjective(row["title"], rule, lines[row["line"]].text),
            )
        )
    return items


def scoring(
    lines: list[Line], spans: list[tuple[int, int, str, ChapterRole]], review: dict | None
) -> dict:
    """Scoring items with their origin, stated total, item sum and any warnings."""
    warnings: list[ScoringWarning] = []
    scored = [(start, end) for start, end, _, role in spans if role == "scoring"]
    stated = "\n".join(line.text for start, end in scored for line in lines[start + 1 : end])
    if review is not None and review["items"]:
        items = reviewed_items(lines, review)
        total = review["total"]
        stated += "\n" + "\n".join(line.text for line in lines[review["start"] : review["end"]])
        origin = "model"
        if review["multiple_packages"]:
            warnings.append("multiple_packages")
    else:
        items = []
        for start, end in scored:
            items.extend(
                item
                for item in scoring_items(lines[start + 1 : end])
                if all(item.title != other.title for other in items)
            )
        found = SCORING_TOTAL.search(stated)
        total = float(found.group(1)) if found else None
        origin = "rules"
    tops = [range_top(item.score_range) for item in items]
    total_sum = sum(top for top in tops if top is not None) if items else None
    # Subtotals also read 合计…分; a stated figure equal to the item sum is the total.
    if total_sum is not None and total_sum in {
        float(value) for value in SCORING_TOTAL.findall(stated)
    }:
        total = total_sum
    if not items:
        warnings.append("no_items")
    elif total is None:
        warnings.append("no_total")
    elif total_sum is not None and abs(total_sum - total) > 0.01:
        warnings.append("sum_mismatch")
    # A cut region matters only while the items do not add up to the stated total.
    if review is not None and review["items"] and review["truncated"] and warnings:
        warnings.append("region_truncated")
    return {
        "scoring_items": items,
        "scoring_origin": origin,
        "scoring_total": total,
        "scoring_sum": total_sum,
        "scoring_warnings": warnings,
    }


def build_outline(document_id: UUID, chunks: list[dict], review: dict | None = None) -> BidOutline:
    lines = lines_of(chunks)
    spans = chapter_spans(lines)
    chapters: list[OutlineChapter] = []
    forms: list[OutlineForm] = []
    format_titles: list[str] = []
    for index, end, title, role in spans:
        chapters.append(OutlineChapter(title=title, role=role, **place(lines[index:end])))
        if role == "format":
            format_titles.append(title)
            for form in split_forms(lines[index + 1 : end]):
                forms.append(form.model_copy(update={"index": len(forms) + 1}))
    return BidOutline(
        document_id=document_id,
        chapters=chapters,
        format_chapter="；".join(format_titles) or None,
        forms=forms,
        **scoring(lines, spans, review),
    )


def chunks_digest(chunks: list[dict]) -> str:
    """The same digest job manifests record as `chunks_sha256`."""
    return hashlib.sha256(
        json.dumps(
            {"chunks": chunks}, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


async def latest_review(
    session: AsyncSession, document_id: UUID, chunks: list[dict]
) -> dict | None:
    """The newest succeeded scoring review of exactly these chunks."""
    job = await session.scalar(
        select(Job)
        .where(
            Job.document_id == document_id,
            Job.kind == "scoring_review",
            Job.status == "succeeded",
        )
        .order_by(Job.finished_at.desc())
        .limit(1)
    )
    if job is None or job.result.get("chunks_sha256") != chunks_digest(chunks):
        return None
    return job.result["review"]


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
    chunks = await load_chunks(session, document.id)
    return build_outline(document.id, chunks, await latest_review(session, document.id, chunks))
