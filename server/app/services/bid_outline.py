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
from collections import Counter
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
FRONT_TABLE = re.compile(r"^(?:投标人?|供应商|磋商|谈判)?须知前附表$")
TOC_LINE = re.compile(r"(\.{3,}|…{2,}|·{3,}|\s\d{1,4}$|\D\d{1,4}$|[-—]\s*\d{1,4}\s*[-—]$)")
FORM_LABEL = re.compile(
    r"^(附件|附表|格式)\s*([0-9]+(?:[-－—][0-9]+)?|[一二三四五六七八九十]+)\s*[：:．.、]?\s*(.*)$"
)
# A group of forms names what it holds: 商务文件有关格式, 资格证明文件, 技术部分.
GROUP_WORD = re.compile(r"格式|文件|部分|材料|表格")
# A field label printed after a form title, as in 响应表（格式）项目名称： or
# 证书汇总表项目名称：, belongs to the form body.
FIELD_TAIL = re.compile(
    r"(?<=[表函书单式）)])(?:项目|采购|招标|包件|日期|投标人|供应商)[^，。；：:（）()]{0,6}[：:].*$"
)
SUB_FORM = re.compile(r"^[（(](\d{1,2})[）)]([^，。；：:]{2,30})$")
TITLE_END = re.compile(r"[表函书单式明）)件告诺页]$")
MERGED_ITEM = re.compile(r"\s+(?=\d{1,2}(?:[-－]\d{1,2}){1,2}、?\s*[^\s\d])")
NUMBERED = re.compile(r"^(附件|格式|\d|[一二三四五六七八九十]+、|[（(]\d)")
UNNUMBERED_FORM = re.compile(r"^[^，。；：:\t]{4,40}[（(]格式[）)]([（(][^）)]*[）)])*$")
INDEX_TITLE = re.compile(r"^[^，。；：:\t]{2,40}索引表?([（(][^）)]*[）)])*$")
PART = re.compile(r"^第([一二三四五六七八九十])部分(\S{2,20})$")
ATTACHED_FORM = re.compile(r"^附件[：:](\S{2,20})$")
# A short title opening a page before the first numbered form, like 廉洁合同.
COVER_LINES = 15
PAGE_TITLE = re.compile(r"^[^，。；：:\d]{2,20}(表|函|书|单|合同|协议|承诺|声明|证明)$")
LIST_HEADING = re.compile(r"^([一二三四五六七八九十]+)、\s*(.+)$")
LIST_ITEM = re.compile(
    r"^(\d{1,2})(?:[.．\-－](\d{1,2}))?(?:[.．\-－](\d{1,2}))?(?:、|[.．](?!\d)|\s)\s*(.+)$"
)
PAGE_FOOTER = re.compile(r"^[-—\s]*(第\s*)?\d{1,4}(\s*页)?(\s*/\s*\d+|\s*共\s*\d+\s*页)?[-—\s]*$")
SPACED_CHAR = re.compile(r"[\u4e00-\u9fff]")
DATE_CHARS = set("年月日时地址间")
SENTENCE = re.compile(r"[，。；！？]")
COVER_TITLE = re.compile(
    r"^((商务|技术|资格|资信|报价|价格|经济|商务技术)(及|和|与)?(?!证明)\S{0,6}|投标|响应)文件([（(][^）)]*[）)])?(正本|副本|封面)?([（(][^）)]*[）)])*$"
)

# A scoring rule row: item, score range such as 0~25 or 0-10分, then the rule.
SCORING_ROW = re.compile(
    r"^([^\t\d][^\t]{0,40})\t\s*(\d+(?:\.\d+)?\s*[~～\-—至]\s*\d+(?:\.\d+)?)\s*分?\s*(?:\t|$)"
)
SUBJECTIVE_ITEM = re.compile(
    r"方案|预案|措施|组织|人员管理|设施|配置|建议|特色|机制|计划|保障|制度|需求理解|分析"
)
OBJECTIVE_ITEM = re.compile(r"报价|价格|业绩|相符|编制")
# Scores given in bands (得 8-10 分) or by comparative wording are an evaluator's
# judgement; counted scores (每提供一个得 1 分) and price formulas are not.
JUDGED = re.compile(
    r"得\s*\d+(?:\.\d+)?\s*[-~－—至]\s*\d+(?:\.\d+)?\s*分|较好|较强|较弱|较差|较完整|较高|酌情|综合评[价分审]"
)
PRICED = re.compile(r"报价|价格")
COUNTED = re.compile(
    r"每(?:提供|有|具有)?\s*(?:一|1)?\s*[个份项条人家次本][^，。；]{0,30}?得\s*\d"
    r"|满足得\s*\d+(?:\.\d+)?\s*分[，,；;]\s*不"
)
ITEM_DECOR = re.compile(r"^(?:[（(]\d{1,2}[）)]|\d{1,2}[、.．](?!\d))|(?:\d+(?:\.\d+)?分)?[：:]$")
RULE_END = re.compile(r"(?:第\s*[\d一二三四五六七八九十]+\s*(?:包|标段|标包)|合计\s*[\d：:])")
ASSESSED = re.compile(r"评委(?:会)?(?:认定|判断)")
WORDED = re.compile(r"[\u4e00-\u9fffA-Za-z]")
# An explicit marker in the item's title or its own text, such as a 备注 cell holding
# only 主观, overrides the keyword guess.
SUBJECTIVE_MARK = re.compile(r"主观(?:分|评分|项)|(?:^|\s)主观(?:\s|$)")
OBJECTIVE_MARK = re.compile(r"客观(?:分|评分|项)|(?:^|\s)客观(?:\s|$)")
SCORING_TOTAL = re.compile(r"(?:总分|满分|合计|总计)[^\d\n]{0,8}?(\d{2,3}(?:\.\d+)?)\s*分")
SCORING_HEADER = re.compile(r"评分|评审|评价|分值|权重|区间|分数|满分")
NUMBER = re.compile(r"\d+(?:\.\d+)?")
# The lines sent for review: the scoring table from just above its header row.
REVIEW_LEAD = 5
REVIEW_MAX_LINES = 600
REVIEW_LINE_CHARS = 300

PRICE_FORM = re.compile(r"开标一览|报价|价格表|分项价格|费用.*(明细|测算)|测算明细")
KIND_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("deviation", re.compile(r"偏离")),
    (
        "scoring_index",
        re.compile(r"(评分|评标|评审).*(对应|索引|对照)|(对应|索引|对照).*评分|评审因素.*响应"),
    ),
    ("price", PRICE_FORM),
    # A title ending in 声明 or 承诺 is a declaration whatever it names.
    ("declaration", re.compile(r"(声明|承诺|证明函)[函书]?(格式)?([（(][^）)]*[）)])*$")),
    # Before roster and copy: 法定代表人（单位负责人）证明书 and 投标保证书 are letters.
    ("letter", re.compile(r"投标书|投标函|响应函|授权|身份证明|资格证明书|法定代表人|投标保证书")),
    ("solution", re.compile(r"方案|实施计划|服务计划|组织设计|技术文件.*自拟|报告$|技术措施$")),
    (
        "roster",
        re.compile(
            r"负责人|项目经理|人员|成员|团队|业绩|类似项目|同类项目|设备.*(表|清单)"
            r"|(证书|资质).*(汇总|一览)表"
        ),
    ),
    ("copy", re.compile(r"复印件|扫描件|营业执照|证明.{0,12}材料|证明文件|资质证书|证书|电子件$")),
    ("declaration", re.compile(r"声明|承诺|保证书|廉洁")),
)
ROLE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("format", re.compile(r"格式")),
    ("scoring", re.compile(r"评标|评分|评审|评定|评比")),
    (
        "needs",
        re.compile(r"需求|概况|技术要求|采购内容|服务内容|技术规格|项目要求|采购清单|服务要求"),
    ),
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
    heads = sorted(found.values())
    # A document numbers its chapters in one unit, the unit its first heading uses; a
    # 第二部分 inside a 第三章 is a part of that chapter. Of the rest, the longest run
    # of numbers that never fall is the chapters (a tender may repeat a number); a
    # contents line or reference outside it is not.
    units = [unit for _, title in heads if (unit := chapter_unit(title))]
    heads = [head for head in heads if units and chapter_unit(head[1]) == units[0]]
    numbers = [chapter_number(title) or 0 for _, title in heads]
    length = [1] * len(heads)
    previous: list[int | None] = [None] * len(heads)
    for current in range(len(heads)):
        for earlier in range(current):
            if numbers[earlier] <= numbers[current] and length[earlier] + 1 >= length[current]:
                length[current], previous[current] = length[earlier] + 1, earlier
    kept: list[tuple[int, str]] = []
    position = max(range(len(heads)), key=lambda at: length[at], default=None)
    while position is not None:
        kept.insert(0, heads[position])
        position = previous[position]
    return front_heads(lines, kept) + kept


def front_heads(lines: list[Line], kept: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Unnumbered sections before the first chapter: a 投标人须知前附表 printed on its
    own, or, in an excerpt with no chapters, a first line naming its section."""
    if not kept:
        first = re.sub(r"\s+", "", lines[0].text) if lines else ""
        return [(0, first)] if title_line(first) and chapter_role(first) != "other" else []
    texts = [re.sub(r"\s+", "", line.text) for line in lines[: kept[0][0]]]
    # The contents page lists it too, next to the numbered chapters.
    for index in range(len(texts) - 1, -1, -1):
        if FRONT_TABLE.match(texts[index]) and not any(
            CHAPTER.match(text) for text in texts[index + 1 : index + 4]
        ):
            return [(index, texts[index])]
    return []


def chapter_unit(title: str) -> str | None:
    match = re.match(r"^第[^章篇部]{1,4}([章篇部])", title)
    return match.group(1) if match else None


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


def form_title(
    label_rest: str, body: list[Line], running: frozenset[str] = frozenset()
) -> tuple[str, bool]:
    """The form title and whether the form is a cover page with a spaced-out title.

    `running` holds page headers such as 招标文件, which are never a title.
    """
    rest = FIELD_TAIL.sub("", label_rest.strip()).rstrip("：:")
    # A heading run into the form's own title, as in 声明函的格式（…）中小企业声明函.
    for cut in range(len(rest) - 4, 3, -1):
        if rest.startswith(rest[cut:]) and re.search(r"[)）]$", rest[:cut]):
            rest = rest[:cut]
            break
    texts = [line.text for line in body if line.text.strip() not in running]
    spaced = spaced_title(texts)
    if spaced:
        return spaced, True
    # A title wrapped onto a second line, like 财务状况及税收、社会保障资金 / 缴纳情况声明函.
    if (
        rest
        and texts
        and not TITLE_END.search(rest)
        and len(texts[0]) <= 20
        and TITLE_END.search(texts[0])
        and not re.search(r"[，。；：:\t\s]", texts[0])
    ):
        rest += texts[0]
    title = rest or re.split(r"[，。；,;]", texts[0])[0] if (rest or texts) else "未命名格式"
    return re.sub(r"\s+", "", title.split("\t")[0])[:80], False


def list_forms(lines: list[Line]) -> list[tuple[int, str, str, bool]]:
    """Forms of a format chapter numbered as a list: 一、 groups, or the 1、 2、 forms in them.

    Numbered items count only in sequence from 1 under each group, so the numbered
    clauses inside a form are not taken for forms. Groups holding such forms, like
    一、商务响应文件有关格式, stay as divider pages before them.
    """
    parts = part_forms(lines)
    if parts:
        return parts
    groups: list[tuple[int, str, str, bool]] = []
    items: list[tuple[int, str, str, bool]] = []
    expected = sub = subsub = 1
    numerals = "一二三四五六七八九十"
    for index, line in enumerate(lines):
        # A PDF line can run two numbered titles together, as 1 满足…规定 1-1 营业执照….
        for part in MERGED_ITEM.split(line.text.strip()):
            text = FIELD_TAIL.sub("", part)
            # A field label such as 四、各行业划型标准为： or 1、单位名称： is not a heading.
            if (
                len(text) > (60 if TITLE_END.search(text) else 40)
                or "\t" in text
                or text.endswith(("：", ":"))
            ):
                continue
            match = LIST_HEADING.match(text)
            if (
                match
                and len(text) <= 30
                and not SENTENCE.search(match.group(2))
                and GROUP_WORD.search(match.group(2))
            ):
                number = numerals.find(match.group(1)) + 1
                if number == 1 and groups and not items:
                    # Groups listed with nothing under them, as in an index table, were a preview.
                    groups = []
                if number == len(groups) + 1:
                    groups.append((index, f"{match.group(1)}、", match.group(2), True))
                    expected = 1
                # Any other 一、 二、 is a list inside a form.
                continue
            item = LIST_ITEM.match(text)
            # A note in brackets, like （本项目不适用。）, does not make the title a sentence.
            if not item or SENTENCE.search(re.sub(r"[（(][^）)]*[）)]", "", item.group(4))):
                continue
            number, minor, third = int(item.group(1)), item.group(2), item.group(3)
            separator = "-" if re.match(r"^\d+[-－]", text) else "."
            if minor is None and number == expected:
                items.append((index, f"{number}、", item.group(4), False))
                expected, sub = expected + 1, 1
            elif (
                third is None and minor is not None and number == expected - 1 and int(minor) == sub
            ):
                # 6.1, 6.2 under form 6.
                items.append((index, f"{number}{separator}{minor}", item.group(4), False))
                sub, subsub = sub + 1, 1
            elif (
                third is not None
                and minor is not None
                and number == expected - 1
                and int(minor) == sub - 1
                and int(third) == subsub
            ):
                # 8.2.1 under 8.2.
                label = f"{number}{separator}{minor}{separator}{third}"
                items.append((index, label, item.group(4), False))
                subsub += 1
    if len(items) > len(groups):
        return sorted(groups + items, key=lambda mark: mark[0])
    return [(index, label, rest, False) for index, label, rest, _ in groups]


def part_forms(lines: list[Line]) -> list[tuple[int, str, str, bool]]:
    """Forms of a format chapter in parts: 第一部分 dividers, each holding 一、 二、 forms.

    A second volume may number its parts from 第一部分 again. Under a part, the 一、
    forms count in sequence and an 附件： line is a form too; numbered lines before
    the first part, like the clauses of a 廉洁合同, are not forms.
    """
    marks: list[tuple[int, str, str, bool]] = []
    expected = None
    numerals = "一二三四五六七八九十"
    for index, line in enumerate(lines):
        text = re.sub(r"\s+", "", line.text)
        if len(text) > 30 or SENTENCE.search(text):
            continue
        part = PART.match(text)
        if part:
            marks.append((index, f"第{part.group(1)}部分", part.group(2), True))
            expected = 1
            continue
        if expected is None:
            continue
        heading = LIST_HEADING.match(text)
        attached = ATTACHED_FORM.match(text)
        if heading and numerals.find(heading.group(1)) + 1 == expected:
            marks.append((index, f"{heading.group(1)}、", heading.group(2), False))
            expected += 1
        elif attached:
            marks.append((index, "附件", attached.group(1), False))
    return marks if sum(mark[3] for mark in marks) >= 2 else []


def unnumbered_forms(
    lines: list[Line], marks: list[tuple[int, str, str, bool]]
) -> list[tuple[int, str, str, bool]]:
    """Forms printed without a number: the bid cover, index tables and （格式） pages.

    A cover counts before the first numbered form or right under a group divider. An
    index table or （格式） page counts anywhere except as the title line of the form
    just above it.
    """
    found = []
    texts = [line.text for line in lines]
    squeezed = [re.sub(r"\s+", "", text) for text in texts]
    first = marks[0][0] if marks else len(lines)
    # A cover page holds a few lines; a 投标文件 cell in a full table is not a cover.
    counts = Counter(line.page for line in lines)
    for index, line in enumerate(lines):
        text = FIELD_TAIL.sub("", squeezed[index])
        above = [mark for mark in marks if mark[0] < index]
        close = bool(above) and index - above[-1][0] <= 2
        following = squeezed[index + 1] if index + 1 < len(lines) else ""
        page_top = index == 0 or lines[index - 1].page != line.page
        if (
            (UNNUMBERED_FORM.match(text) or INDEX_TITLE.match(text))
            and "\t" not in line.text
            and not NUMBERED.match(text)
            and not (close and not above[-1][3])
            and all(row[2] != text for row in found)
            # A heading over two index tables, like 评标索引, is not a table itself;
            # numbered parts, like 1．商务部分评分索引, belong to the table above.
            and not (
                INDEX_TITLE.match(following)
                and "\t" not in texts[index + 1]
                and not NUMBERED.match(following)
            )
        ):
            found.append((index, "", text, False))
        elif (
            COVER_TITLE.match(text)
            and (counts[line.page] <= COVER_LINES or (close and above[-1][3]))
            and not (found and index - found[-1][0] <= 2)
        ):
            # The volume printed under the title, like （第一册：资格分册）, names it.
            if re.fullmatch(r"[（(][^（）()]{1,20}[）)]", following):
                text += following
            found.append((index, "", text, False))
        elif (
            page_top
            and PAGE_TITLE.match(text)
            and not NUMBERED.match(text)
            # Past the first numbered form, only a letter addressed 致： starts a new
            # one; a second table on the next page belongs to the form above, and a
            # title under a bare 附件11 is that form's.
            and (
                index < first
                or (
                    following.startswith("致")
                    and not close
                    and not any(text in squeezed[mark[0]] for mark in above[-1:])
                )
            )
        ):
            found.append((index, "", text, False))
        # A cover title printed one character per line, like 投/标/文/件.
        elif (
            index < first and len(text) == 1 and (index == 0 or len(texts[index - 1].strip()) != 1)
        ):
            spaced = spaced_title(texts[index:])
            if spaced and COVER_TITLE.match(spaced) and texts[index].strip() == spaced[0]:
                found.append((index + len(spaced) - 1, "", spaced, False))
    return found


def sub_forms(
    lines: list[Line], marks: list[tuple[int, str, str, bool]]
) -> list[tuple[int, str, str, bool]]:
    """Forms numbered （1） （2） under a labelled form, such as the declarations of 附件四.

    Only a short title ending like a form's (表, 函, 书, 声明) counts, numbered in
    sequence or starting again from （1）; numbered clauses inside a form are sentences.
    """
    found: list[tuple[int, str, str, bool]] = []
    for position, (start, _, _, _) in enumerate(marks):
        end = marks[position + 1][0] if position + 1 < len(marks) else len(lines)
        expected = 1
        for index in range(start + 1, end):
            match = SUB_FORM.match(re.sub(r"\s+", "", lines[index].text))
            if not match or SENTENCE.search(match.group(2)) or not TITLE_END.search(match.group(2)):
                continue
            number = int(match.group(1))
            if number in (expected, 1):
                found.append((index, f"（{number}）", match.group(2), False))
                expected = number + 1
    return found


def split_forms(lines: list[Line]) -> list[OutlineForm]:
    marks: list[tuple[int, str, str, bool]] = []
    names: list[str] = []
    seen: set[str] = set()
    for index, line in enumerate(lines):
        match = FORM_LABEL.match(line.text)
        if match and len(line.text) <= 120:
            label = f"{match.group(1)}{re.sub(r'[－—]', '-', match.group(2))}"
            # A label printed again right after itself, like 附件9 for each of two
            # declarations, is a second form; elsewhere a repeat is a reference.
            named = match.group(3) or (lines[index + 1].text if index + 1 < len(lines) else "")
            if label not in seen or (marks[-1][1] == label and named != names[-1]):
                seen.add(label)
                marks.append((index, label, match.group(3), False))
                names.append(named)
    subs = sub_forms(lines, marks)
    parents = {mark[0] for mark in marks if any(mark[0] < sub[0] for sub in subs)}
    parents = {max(mark[0] for mark in marks if mark[0] < sub[0]) for sub in subs} & parents
    if not marks:
        marks = list_forms(lines)
    marks = sorted(marks + subs + unnumbered_forms(lines, marks), key=lambda mark: mark[0])
    # A short line printed on three or more pages is a page header.
    pages: dict[str, set[int | None]] = {}
    for line in lines:
        if len(line.text.strip()) <= 20:
            pages.setdefault(line.text.strip(), set()).add(line.page)
    running = frozenset(text for text, seen_on in pages.items() if len(seen_on) >= 3)
    forms: list[OutlineForm] = []
    for position, (index, label, rest, divider) in enumerate(marks):
        end = marks[position + 1][0] if position + 1 < len(marks) else len(lines)
        body = lines[index + 1 : end]
        title, spaced = form_title(rest, body, running)
        kind = cast(
            FormKind,
            "cover"
            if divider or spaced or COVER_TITLE.match(title)
            else classify(title, KIND_RULES, "form"),
        )
        # A form holding its own numbered forms, like 附件五 投标方简介, is their divider.
        if kind == "form" and index in parents:
            kind = "cover"
        # An untitled form, such as a price table under 附件三, shows a price in its
        # first rows.
        if (
            kind == "form"
            and not rest.strip()
            and PRICE_FORM.search(" ".join(line.text for line in body[:2]))
        ):
            kind = "price"
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
                and not re.search(r"[，。；！？：:×=/]", before)
            )
            rows.append((index - 1 if wrapped else index, index, match, before if wrapped else ""))
    for position, (_, index, match, head) in enumerate(rows):
        title = re.sub(r"\s+", "", head + match.group(1))
        end = rows[position + 1][0] if position + 1 < len(rows) else len(lines)
        first = lines[index].text.strip("\t")[match.end() :]
        rule = " ".join("\n".join([first, *(line.text for line in lines[index + 1 : end])]).split())
        # A placeholder row such as / names no item.
        if WORDED.search(title) and all(item.title != title for item in items):
            items.append(
                OutlineScoringItem(
                    title=title,
                    score_range=match.group(2),
                    rule=rule,
                    subjective=subjective(title, rule),
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


def title_at(texts: dict[int, str], line: int, stop: int, title: str) -> str | None:
    """The source name of an item `title` says starts on `line`, or None.

    The name is on the line, whole or wrapped over the next two lines; or it is
    assembled from the leading words of the item's lines, as a PDF table cell whose
    name wraps between rule lines prints it, and then the assembled words are the
    name even where the model dropped one character of them; or the cell's first
    word starts the title and the item's own text spells the title out. A row
    number before the name is skipped."""
    own = squeeze(texts[line])
    window = own + "".join(squeeze(texts.get(line + k, "")) for k in (1, 2))
    found = window.find(title)
    if 0 <= found < len(own):
        return title
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
                return title
        if number == line and not taken:
            break
    first = texts[line].replace(" | ", " ").split()
    if not first or len(squeeze(first[0])) < 2 or not title.startswith(squeeze(first[0])):
        return None
    cell = ""
    for number in range(line, stop):
        words = texts.get(number, "").replace(" | ", " ").split()
        if not words or re.fullmatch(r"[\d.、分；;]+", squeeze(words[0])):
            continue
        word = squeeze(words[0])
        if len(word) > 4:
            break
        cell += word
        if len(cell) >= len(title) and len(cell) - len(title) <= 1 and subsequence(title, cell):
            return cell
        if len(cell) > len(title):
            break
    spelled = squeeze(" ".join(texts.get(number, "") for number in range(line - 3, stop)))
    return title if title in spelled else None


def subsequence(short: str, long: str) -> bool:
    letters = iter(long)
    return all(letter in letters for letter in short)


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
        name = title_at(texts, item.line, stop, squeeze(title))
        if name is None or float(item.max) not in numbers(span):
            rejected += 1
            continue
        if name != squeeze(title):
            title = name
        # The list number and the score printed with a name, as in (1)验收方案2分：, are
        # not part of it.
        title = ITEM_DECOR.sub("", title) or title
        # A name cell holding a formula, like 报价分＝价格分值×…, is named by its left side.
        title = re.split(r"[=＝]", title)[0] or title
        if kept and len(title) > 30 and SENTENCE.search(title):
            # A sub-row printed without a name of its own, only its scoring sentence,
            # adds its points to the item above it.
            kept[-1]["max"] += item.max
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


def subjective(title: str, rule: str) -> bool:
    for text in (title, rule):
        marked, unmarked = bool(SUBJECTIVE_MARK.search(text)), bool(OBJECTIVE_MARK.search(text))
        if marked != unmarked:
            return marked
    if PRICED.search(title) and not re.search(r"合理", title):
        return False
    if ASSESSED.search(rule):
        return True
    if COUNTED.search(rule):
        return False
    if JUDGED.search(rule):
        return True
    text = title + rule
    return bool(SUBJECTIVE_ITEM.search(text) and not OBJECTIVE_ITEM.search(text))


def reviewed_items(lines: list[Line], review: dict) -> list[OutlineScoringItem]:
    """Items of a verified review, each with the source text up to the next item as rule."""
    rows = review["items"]
    items = []
    for position, row in enumerate(rows):
        later = [other["line"] for other in rows[position + 1 :] if other["line"] > row["line"]]
        stop = min(later[0] if later else review["end"], len(lines))
        if not later:
            # The last item's text ends at the table's total or the next package.
            stop = next(
                (i for i in range(row["line"] + 1, stop) if RULE_END.match(lines[i].text.strip())),
                stop,
            )
        rule = " ".join(" ".join(line.text for line in lines[row["line"] : stop]).split())
        items.append(
            OutlineScoringItem(
                title=row["title"],
                score_range=f"0~{row['max']:g}",
                rule=rule,
                subjective=subjective(row["title"], rule),
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
