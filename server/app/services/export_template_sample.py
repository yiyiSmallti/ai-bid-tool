"""The built-in starter export template and the binding that fits it.

A4 with common Chinese bid margins, 宋体 小四 body, 黑体 三号 headings, a grid table
style, a centered page number and the six section anchors in contract order. It has
no other static text, so it passes the export adapter as uploaded.
"""

import io

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from docx.styles.style import ParagraphStyle

from app.services.export_renderer import (
    DEFAULT_LIMITS,
    SECTIONS,
    TABLE_SECTIONS,
    deterministic_package,
    set_core_properties,
)

HEADING_STYLE_ID = "Heading1"
TABLE_STYLE_ID = "TableGrid"
COLUMN_WIDTHS = (("ordinal", 6), ("requirement", 36), ("response", 42), ("compliance", 16))


def _fonts(style: ParagraphStyle, east_asian: str, latin: str, size: float) -> None:
    style.font.name = latin
    style.font.size = Pt(size)
    style.font.color.rgb = RGBColor(0, 0, 0)
    fonts = style.element.get_or_add_rPr().get_or_add_rFonts()
    fonts.set(qn("w:eastAsia"), east_asian)
    fonts.set(qn("w:ascii"), latin)
    fonts.set(qn("w:hAnsi"), latin)


def binding_sections() -> list[dict]:
    return [
        {
            "section": section,
            "heading_style_id": HEADING_STYLE_ID,
            "table_style_id": TABLE_STYLE_ID,
            "columns": [{"key": key, "width_percent": width} for key, width in COLUMN_WIDTHS]
            if section in TABLE_SECTIONS
            else [],
        }
        for section in SECTIONS
    ]


def build() -> bytes:
    document = Document()
    # Make the grid table style part of the package without leaving a table behind.
    table = document.add_table(rows=1, cols=1)
    table.style = "Table Grid"
    table._element.getparent().remove(table._element)
    normal, heading = document.styles["Normal"], document.styles["Heading 1"]
    assert isinstance(normal, ParagraphStyle) and isinstance(heading, ParagraphStyle)
    _fonts(normal, "宋体", "Times New Roman", 12)
    _fonts(heading, "黑体", "Times New Roman", 16)
    heading.font.bold = True
    heading.paragraph_format.space_before = Pt(12)
    heading.paragraph_format.space_after = Pt(6)
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(2.54)
    section.left_margin = section.right_margin = Cm(3.18)
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    for name in SECTIONS:
        document.add_paragraph("{{bid." + name + "}}")
    # python-docx starts the body with one empty paragraph; keep only the anchors.
    first = document.paragraphs[0]
    if not first.text:
        first._element.getparent().remove(first._element)
    set_core_properties(document)
    buffer = io.BytesIO()
    document.save(buffer)
    return deterministic_package(buffer.getvalue(), DEFAULT_LIMITS)
