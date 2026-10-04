"""Standalone template-adapter and DOCX-renderer tests.

Failure modes covered before gate-test implementation:

* malformed, oversized, path-traversing, encrypted, macro-enabled, or duplicate ZIP members;
* external relationships, non-page fields, hidden text/style, revisions, OLE/altChunk,
  text boxes, comments, embedded template media, unknown story XML, and static body text
  outside the exact registered-heading whitelist;
* missing, duplicate, out-of-order, nested, or mixed-text anchors; unknown placeholders;
* missing or wrong-kind styles, incomplete/duplicate seven-column bindings, and invalid widths;
* profile/template/static-content/hash mismatches, malformed or duplicate manifest ordinals,
  incomplete item coverage, final exports with gaps, and visible negative-deviation loss;
* attachment count/byte/pixel/hash mismatches, missing authorized page inputs, and output limits;
* nondeterministic ZIP metadata/core properties, changed source PNG bytes, missing bookmarks,
  non-editable tables, non-repeating headers, incorrect column widths, and absent review markers.
"""

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from copy import deepcopy
from pathlib import Path

import fitz
import pytest
from app.services.export_renderer import (
    ADAPTER_VERSION,
    ANCHORS,
    RENDERER_PROFILE,
    AuthorizedExportInputs,
    ExportRenderError,
    RenderLimits,
    render_export_docx,
    validate_template,
)
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches
from lxml import etree

REGISTERED_HEADING = "第五章 投标响应"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def sections():
    response_columns = [("ordinal", 6), ("requirement", 36), ("response", 44), ("compliance", 14)]
    return [
        {
            "section": section,
            "heading_style_id": "Heading1",
            "table_style_id": "TableGrid",
            "columns": [{"key": key, "width_percent": width} for key, width in response_columns]
            if section in {"substantive", "commercial", "technical"}
            else [],
        }
        for section in ANCHORS
    ]


def template_bytes(
    *,
    metadata=True,
    header_field="complex",
    static_text=REGISTERED_HEADING,
    section_break_at=3,
    second_section_geometry=False,
):
    document = Document()
    if "Table Grid" not in [style.name for style in document.styles]:
        document.styles.add_style("Table Grid", WD_STYLE_TYPE.TABLE)
    if static_text:
        document.add_paragraph(static_text, style="Heading 1")
    if metadata:
        document.add_paragraph("{{bid.task_name}}")
        document.add_paragraph("{{bid.tender_number}}")
    for index, marker in enumerate(ANCHORS.values()):
        if index == section_break_at:
            new_section = document.add_section(WD_SECTION.NEW_PAGE)
            if second_section_geometry:
                new_section.page_width = Inches(13)
                new_section.page_height = Inches(8)
                new_section.left_margin = Inches(0.5)
                new_section.right_margin = Inches(0.75)
                new_section.top_margin = Inches(0.4)
                new_section.bottom_margin = Inches(0.6)
        paragraph = document.add_paragraph()
        paragraph.add_run(marker[:8])
        paragraph.add_run(marker[8:])
    if header_field == "complex":
        paragraph = document.sections[0].header.paragraphs[0]
        begin = OxmlElement("w:fldChar")
        begin.set(qn("w:fldCharType"), "begin")
        instruction = OxmlElement("w:instrText")
        instruction.set(qn("xml:space"), "preserve")
        instruction.text = " PAGE "
        separate = OxmlElement("w:fldChar")
        separate.set(qn("w:fldCharType"), "separate")
        cached = OxmlElement("w:t")
        cached.text = "1"
        end = OxmlElement("w:fldChar")
        end.set(qn("w:fldCharType"), "end")
        paragraph.add_run()._r.extend((begin, instruction, separate, cached, end))
    elif header_field == "simple":
        paragraph = document.sections[0].header.paragraphs[0]
        field = OxmlElement("w:fldSimple")
        field.set(qn("w:instr"), " NUMPAGES ")
        run = OxmlElement("w:r")
        cached = OxmlElement("w:t")
        cached.text = "2"
        run.append(cached)
        field.append(run)
        paragraph._p.append(field)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def rewrite_part(content, part, mutate):
    source = zipfile.ZipFile(io.BytesIO(content))
    members = {item.filename: source.read(item) for item in source.infolist()}
    source.close()
    members[part] = mutate(members[part])
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    return output.getvalue()


def mutate_xml(content, part, mutation):
    def change(value):
        root = etree.fromstring(value)
        mutation(root)
        return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    return rewrite_part(content, part, change)


def generated_pdf_page():
    pdf = fitz.open()
    page = pdf.new_page(width=320, height=220)
    page.draw_rect(fitz.Rect(18, 18, 302, 202), color=(0.1, 0.3, 0.7), width=3)
    page.insert_text((32, 62), "Certificate C-2048", fontsize=17)
    page.insert_text((32, 104), "Valid through 2031-12-31", fontsize=12)
    page.insert_text((32, 146), "Serial: S9-K7-P4", fontsize=12)
    pdf_bytes = pdf.tobytes(garbage=4, deflate=True)
    pdf.close()
    reopened = fitz.open(stream=pdf_bytes, filetype="pdf")
    pixmap = reopened[0].get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72), alpha=False)
    png = pixmap.tobytes("png")
    width, height = pixmap.width, pixmap.height
    reopened.close()
    return pdf_bytes, png, width, height


def generated_pdf_pages(count):
    pdf = fitz.open()
    for number in range(1, count + 1):
        page = pdf.new_page(width=216, height=288)
        red = ((number * 17) % 100) / 100
        blue = ((number * 31) % 100) / 100
        page.draw_rect(
            fitz.Rect(12 + number % 7, 12, 204, 276 - number % 11),
            color=(red, 0.25, blue),
            width=1 + number % 3,
        )
        page.insert_text((24, 48), f"Certificate page {number:03d}", fontsize=14)
        page.insert_text(
            (24, 82), f"Serial C{number * 7919:08d} / Batch B{number % 19:02d}", fontsize=9
        )
        page.insert_text(
            (24, 112), f"Validity 20{30 + number % 7}-12-{1 + number % 27:02d}", fontsize=9
        )
        page.draw_line(
            fitz.Point(24, 140 + number % 13),
            fitz.Point(190 - number % 17, 210),
            color=(blue, red, 0.4),
        )
    pdf_bytes = pdf.tobytes(garbage=4, deflate=True)
    pdf.close()
    reopened = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []
    for page in reopened:
        pixmap = page.get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72), alpha=False)
        pages.append((pixmap.tobytes("png"), pixmap.width, pixmap.height))
    reopened.close()
    return pdf_bytes, pages


def source(quote):
    return {
        "document_id": "00000000-0000-0000-0000-000000000101",
        "chunk_id": "00000000-0000-0000-0000-000000000102",
        "page": 7,
        "location": None,
        "quote": quote,
    }


def evidence(attachment_ordinal=None):
    value = {
        "id": "00000000-0000-0000-0000-000000000201",
        "org_id": "00000000-0000-0000-0000-000000000001",
        "task_id": "00000000-0000-0000-0000-000000000002",
        "card_id": "00000000-0000-0000-0000-000000000003",
        "input": {
            "kind": "certificate_pdf_page",
            "evidence_source_id": "00000000-0000-0000-0000-000000000202",
            "quote": "Certificate C-2048",
        },
        "selection_id": "00000000-0000-0000-0000-000000000203",
        "resource_revision_id": "00000000-0000-0000-0000-000000000204",
        "material_kind": "user_supplied_pdf_page",
        "quote_check": "human_page_review",
        "confirmed_by": "00000000-0000-0000-0000-000000000205",
        "confirmed_at": "2026-09-30T18:01:02Z",
        "active_selection": True,
    }
    if attachment_ordinal is not None:
        value["attachment_ordinal"] = attachment_ordinal
    return value


def row(ordinal, table, *, deviation="none", with_evidence=False):
    return {
        "response_item_id": f"item-{ordinal}",
        "requirement_id": f"requirement-{ordinal}",
        "card_id": f"card-{ordinal}",
        "card_revision_id": f"revision-{ordinal}",
        "kind": "row",
        "ordinal": ordinal,
        "source": source(f"第 {ordinal} 项设备参数须逐项响应"),
        "location_label": f"PDF 第 7 页，第 {ordinal} 项",
        "category": "technical" if table == "technical" else table,
        "starred": table == "substantive",
        "table": table,
        "response_kind": "evidence" if with_evidence else "commitment",
        "response_text": f"已按第 {ordinal} 项要求提供响应内容。",
        "deviation": deviation,
        "deviation_note": "指标低于要求，按负偏离如实列出。"
        if deviation == "negative"
        else "响应与招标要求一致。",
        "evidence": [evidence(1)] if with_evidence else [],
    }


def complete_manifest(template, inspection, png, width, height, *, mode="review_copy"):
    items = [
        row(1, "substantive"),
        row(2, "commercial"),
        row(3, "technical", deviation="negative", with_evidence=True),
        {
            "response_item_id": "item-4",
            "requirement_id": "requirement-4",
            "card_id": "card-4",
            "card_revision_id": "revision-4",
            "kind": "comply_only",
            "ordinal": 4,
            "source": source("投标文件格式须严格遵守"),
            "location_label": "PDF 第 7 页，格式条款",
            "category": "qualification",
            "starred": False,
            "evidence": [],
            "disposition_by": "00000000-0000-0000-0000-000000000301",
            "disposition_at": "2026-09-30T18:03:04Z",
        },
        {
            "response_item_id": "item-5",
            "requirement_id": "requirement-5",
            "card_id": None,
            "card_revision_id": None,
            "kind": "gap",
            "ordinal": 5,
            "source": source("交付前须补齐验收记录"),
            "location_label": "PDF 第 7 页，交付条款",
            "category": "commercial",
            "starred": False,
            "evidence": [],
            "gap_reasons": ["needs_material"],
        },
    ]
    return {
        "version": "export-manifest-v1",
        "org_id": "00000000-0000-0000-0000-000000000001",
        "mode": mode,
        "renderer_profile": RENDERER_PROFILE,
        "rule_version": "response-draft-v3",
        "task": {
            "id": "00000000-0000-0000-0000-000000000002",
            "name": "高性能计算平台采购",
            "tender_number": "ZB-2026-091",
        },
        "draft": {"id": "draft-1", "input_hash": "1" * 64},
        "template": {
            "task_template_id": "task-template-1",
            "template_revision_id": "template-revision-1",
            "sha256": hashlib.sha256(template).hexdigest(),
            "binding_id": "binding-1",
            "binding_hash": "2" * 64,
            "sections": sections(),
            "static_content_hash": inspection.static_content_hash,
            "adapter_version": ADAPTER_VERSION,
            "registered_headings": [REGISTERED_HEADING],
        },
        "items": items,
        "attachments": [
            {
                "ordinal": 1,
                "label": "E001",
                "selection_id": "00000000-0000-0000-0000-000000000203",
                "revision_id": "00000000-0000-0000-0000-000000000204",
                "original_sha256": "3" * 64,
                "title": "Synthetic certificate declaration（QMS-2026）",
                "page": 1,
                "png_sha256": hashlib.sha256(png).hexdigest(),
                "size_bytes": len(png),
                "width": width,
                "height": height,
                "material_kind": "user_supplied_pdf_page",
                "evidence_ids": ["00000000-0000-0000-0000-000000000201"],
                "requirement_ids": ["requirement-3"],
            }
        ],
        "issues": [],
        "acknowledged_issue_ids": [],
        "input_hash": "4" * 64,
    }


def save_inputs(tmp_path, template, png):
    template_path = tmp_path / "fixed-template.docx"
    page_path = tmp_path / "page-1.png"
    template_path.write_bytes(template)
    page_path.write_bytes(png)
    return AuthorizedExportInputs(template_path, {1: page_path})


def test_adapter_report_is_json_ready_and_metadata_aware():
    content = template_bytes()
    report = validate_template(content, sections(), [REGISTERED_HEADING])
    assert report.template_sha256 == hashlib.sha256(content).hexdigest()
    assert report.adapter_version == ADAPTER_VERSION
    assert report.metadata_fields == ("task_name", "tender_number")
    assert [entry["section"] for entry in report.anchors] == list(ANCHORS)
    assert [entry["paragraph"] for entry in report.anchors] == sorted(
        entry["paragraph"] for entry in report.anchors
    )
    simple = validate_template(
        template_bytes(header_field="simple"), sections(), [REGISTERED_HEADING]
    )
    assert simple.metadata_fields == report.metadata_fields


@pytest.mark.parametrize(
    ("part", "mutation"),
    [
        (
            "word/document.xml",
            lambda root: root.find(f"{{{W_NS}}}body").insert(0, etree.Element(f"{{{W_NS}}}ins")),
        ),
        (
            "word/document.xml",
            lambda root: root.find(f"{{{W_NS}}}body").insert(0, etree.Element(f"{{{W_NS}}}object")),
        ),
        (
            "word/styles.xml",
            lambda root: root.find(f"{{{W_NS}}}style").append(etree.Element(f"{{{W_NS}}}vanish")),
        ),
        (
            "word/header1.xml",
            lambda root: root.append(etree.Element(f"{{{W_NS}}}mystery")),
        ),
    ],
)
def test_adapter_refuses_revisions_ole_hidden_styles_and_unknown_header_xml(part, mutation):
    content = mutate_xml(template_bytes(), part, mutation)
    with pytest.raises(ExportRenderError, match="Unsupported|Unknown") as error:
        validate_template(content, sections(), [REGISTERED_HEADING])
    assert error.value.code == "unsupported_template"


def test_adapter_refuses_external_relationship_and_non_page_field():
    def external(root):
        relation = etree.SubElement(root, f"{{{REL_NS}}}Relationship")
        relation.set("Id", "rIdExternal")
        relation.set(
            "Type",
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        )
        relation.set("Target", "https://invalid.example")
        relation.set("TargetMode", "External")

    with pytest.raises(ExportRenderError, match="External"):
        validate_template(
            mutate_xml(template_bytes(), "word/_rels/document.xml.rels", external),
            sections(),
            [REGISTERED_HEADING],
        )

    def field(root):
        instruction = root.find(f".//{{{W_NS}}}instrText")
        instruction.text = ' INCLUDETEXT "https://invalid.example" '

    with pytest.raises(ExportRenderError, match="field instruction"):
        validate_template(
            mutate_xml(template_bytes(), "word/header1.xml", field),
            sections(),
            [REGISTERED_HEADING],
        )

    def cached_business_text(root):
        root.find(f".//{{{W_NS}}}t").text = "客户项目 1"

    with pytest.raises(ExportRenderError, match="business text"):
        validate_template(
            mutate_xml(
                template_bytes(header_field="simple"),
                "word/header1.xml",
                cached_business_text,
            ),
            sections(),
            [REGISTERED_HEADING],
        )


def test_adapter_refuses_static_business_text_unknown_marker_and_bad_columns():
    with pytest.raises(ExportRenderError, match="registered chapter heading"):
        validate_template(
            template_bytes(static_text="上一项目报价与客户名称"), sections(), [REGISTERED_HEADING]
        )
    unknown = template_bytes(static_text="{{bid.customer_secret}}")
    with pytest.raises(ExportRenderError, match="unknown or mixed-text placeholder"):
        validate_template(unknown, sections(), [REGISTERED_HEADING])
    bad = sections()
    bad[0]["columns"] = bad[0]["columns"][:-1]
    with pytest.raises(ExportRenderError, match="four columns"):
        validate_template(template_bytes(), bad, [REGISTERED_HEADING])


def test_adapter_refuses_template_media():
    document = Document(io.BytesIO(template_bytes()))
    _, png, _, _ = generated_pdf_page()
    document.add_picture(io.BytesIO(png))
    output = io.BytesIO()
    document.save(output)
    with pytest.raises(ExportRenderError, match="media"):
        validate_template(output.getvalue(), sections(), [REGISTERED_HEADING])


def test_review_copy_renders_all_sections_deterministically_and_preserves_png(tmp_path):
    template = template_bytes()
    inspection = validate_template(template, sections(), [REGISTERED_HEADING])
    pdf, png, width, height = generated_pdf_page()
    assert pdf.startswith(b"%PDF") and png.startswith(b"\x89PNG")
    manifest = complete_manifest(template, inspection, png, width, height)
    inputs = save_inputs(tmp_path, template, png)
    first = render_export_docx(manifest, inputs)
    second = render_export_docx(deepcopy(manifest), inputs)
    assert first.content == second.content
    assert first.sha256 == hashlib.sha256(first.content).hexdigest()
    assert first.renderer_profile == RENDERER_PROFILE
    with zipfile.ZipFile(io.BytesIO(first.content)) as archive:
        assert archive.namelist() == sorted(archive.namelist())
        assert all(entry.date_time == (1980, 1, 1, 0, 0, 0) for entry in archive.infolist())
        media = [name for name in archive.namelist() if name.startswith("word/media/")]
        assert len(media) == 1
        assert archive.read(media[0]) == png
        document_xml = archive.read("word/document.xml")
        assert b"{{bid." not in document_xml
        assert b' w:name="bid_E001"' in document_xml
        assert b' w:anchor="bid_E001"' in document_xml
    rendered = Document(io.BytesIO(first.content))
    all_text = "\n".join(paragraph.text for paragraph in rendered.paragraphs)
    assert "高性能计算平台采购" in all_text
    assert "ZB-2026-091" in all_text
    assert "审阅件·存在缺口·不得提交" in "\n".join(
        paragraph.text for section in rendered.sections for paragraph in section.footer.paragraphs
    )
    assert len(rendered.tables) == 6
    for table in rendered.tables[:3]:
        assert len(table.columns) == 4
        header = table.rows[0]._tr.find(qn("w:trPr")).find(qn("w:tblHeader"))
        assert header is not None
    table_text = "\n".join(
        cell.text for table in rendered.tables for row in table.rows for cell in row.cells
    )
    assert "负偏离" in table_text
    assert "本次抽取范围内无缺口" not in table_text
    # Provenance stays in the provenance manifest, never in the printed section.
    printed = all_text + "\n" + table_text
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", printed)
    assert not re.search(r"\b[0-9a-f]{64}\b", printed)
    assert not re.search(r"\[(technical|qualification|commercial|scoring|substantive)\]", printed)
    attachment_titles = [
        paragraph for paragraph in rendered.paragraphs if paragraph.text.startswith("附件 E001")
    ]
    assert len(attachment_titles) == 1
    assert attachment_titles[0].paragraph_format.page_break_before is True
    assert len(rendered.sections) == 2
    assert all(
        "审阅件·存在缺口·不得提交"
        in "\n".join(paragraph.text for paragraph in section.footer.paragraphs)
        for section in rendered.sections
    )


def test_final_section_rejects_gap_then_renders_without_review_marker(tmp_path):
    template = template_bytes()
    inspection = validate_template(template, sections(), [REGISTERED_HEADING])
    _, png, width, height = generated_pdf_page()
    manifest = complete_manifest(template, inspection, png, width, height, mode="final_section")
    inputs = save_inputs(tmp_path, template, png)
    with pytest.raises(ExportRenderError, match="cannot contain gaps") as error:
        render_export_docx(manifest, inputs)
    assert error.value.code == "export_gaps_present"
    manifest["items"].pop()
    result = render_export_docx(manifest, inputs)
    rendered = Document(io.BytesIO(result.content))
    assert "不得提交" not in "\n".join(
        paragraph.text for section in rendered.sections for paragraph in section.footer.paragraphs
    )
    assert "本次抽取范围内无缺口" in "\n".join(
        cell.text for table in rendered.tables for row in table.rows for cell in row.cells
    )


def test_renderer_refuses_missing_metadata_corrupt_page_and_limits(tmp_path):
    template = template_bytes()
    inspection = validate_template(template, sections(), [REGISTERED_HEADING])
    _, png, width, height = generated_pdf_page()
    manifest = complete_manifest(template, inspection, png, width, height)
    inputs = save_inputs(tmp_path, template, png)
    manifest["task"]["tender_number"] = None
    with pytest.raises(ExportRenderError, match="Task metadata") as error:
        render_export_docx(manifest, inputs)
    assert error.value.code == "missing_template_metadata"
    manifest["task"]["tender_number"] = "ZB-2026-091"
    manifest["attachments"][0]["png_sha256"] = "0" * 64
    with pytest.raises(ExportRenderError, match="bytes do not match") as error:
        render_export_docx(manifest, inputs)
    assert error.value.code == "attachment_integrity"
    manifest["attachments"][0]["png_sha256"] = hashlib.sha256(png).hexdigest()
    with pytest.raises(ExportRenderError, match="output byte limit") as error:
        render_export_docx(manifest, inputs, limits=RenderLimits(max_output_bytes=1))
    assert error.value.code == "export_resource_limit"
    with zipfile.ZipFile(io.BytesIO(template)) as archive:
        template_expanded = sum(entry.file_size for entry in archive.infolist())
    with pytest.raises(ExportRenderError, match="expanded size") as error:
        render_export_docx(
            manifest,
            inputs,
            limits=RenderLimits(max_expanded_bytes=template_expanded + 1),
        )
    assert error.value.code == "export_resource_limit"


def test_each_anchor_uses_its_own_section_geometry(tmp_path):
    template = template_bytes(section_break_at=1, second_section_geometry=True)
    inspection = validate_template(template, sections(), [REGISTERED_HEADING])
    assert [anchor["section_index"] for anchor in inspection.anchors] == [0, 1, 1, 1, 1, 1]
    _, png, width, height = generated_pdf_page()
    manifest = complete_manifest(template, inspection, png, width, height)
    result = render_export_docx(manifest, save_inputs(tmp_path, template, png))
    rendered = Document(io.BytesIO(result.content))
    response_widths = [
        sum(int(column.get(qn("w:w"))) for column in table._tbl.tblGrid.gridCol_lst)
        for table in rendered.tables[:3]
    ]
    assert response_widths[0] != response_widths[1]
    assert response_widths[1] == response_widths[2]
    assert rendered.inline_shapes[0].width <= Inches(13 - 0.5 - 0.75)
    assert rendered.inline_shapes[0].height <= Inches(8 - 0.4 - 0.6 - 1)


def test_cross_process_126_distinct_pdf_pages_are_deterministic_and_byte_exact(tmp_path):
    template = template_bytes()
    inspection = validate_template(template, sections(), [REGISTERED_HEADING])
    pdf, pages = generated_pdf_pages(126)
    assert pdf.startswith(b"%PDF")
    manifest = complete_manifest(template, inspection, *pages[0])
    manifest["mode"] = "final_section"
    manifest["items"] = []
    manifest["attachments"] = []
    page_paths = {}
    for number, (png, width, height) in enumerate(pages, 1):
        item = row(number, ("substantive", "commercial", "technical")[(number - 1) % 3])
        material = evidence(number)
        material["id"] = f"evidence-{number:03d}"
        material["selection_id"] = f"selection-{number:03d}"
        material["resource_revision_id"] = f"revision-{number:03d}"
        material["input"] = {
            **material["input"],
            "evidence_source_id": f"source-{number:03d}",
            "quote": f"Certificate page {number:03d}",
        }
        item["response_kind"] = "evidence"
        item["evidence"] = [material]
        manifest["items"].append(item)
        manifest["attachments"].append(
            {
                "ordinal": number,
                "label": f"E{number:03d}",
                "selection_id": material["selection_id"],
                "revision_id": material["resource_revision_id"],
                "original_sha256": hashlib.sha256(pdf).hexdigest(),
                "title": "Synthetic certificate declaration（QMS-2026）",
                "page": number,
                "png_sha256": hashlib.sha256(png).hexdigest(),
                "size_bytes": len(png),
                "width": width,
                "height": height,
                "material_kind": "user_supplied_pdf_page",
                "evidence_ids": [material["id"]],
                "requirement_ids": [item["requirement_id"]],
            }
        )
        path = tmp_path / f"page-{number}.png"
        path.write_bytes(png)
        page_paths[number] = path
    template_path = tmp_path / "template.docx"
    template_path.write_bytes(template)
    direct = render_export_docx(manifest, AuthorizedExportInputs(template_path, page_paths))

    child_root = tmp_path / "child"
    child_root.mkdir(mode=0o700)
    (child_root / "template.docx").write_bytes(template)
    for number, path in page_paths.items():
        (child_root / f"page-{number}.png").write_bytes(path.read_bytes())
    request = {
        "manifest": manifest,
        "pages": list(page_paths),
        "memory_bytes": 1024 * 1024 * 1024,
        "limits": {
            "max_requirements": 2_000,
            "max_attachments": 300,
            "max_output_bytes": 512 * 1024 * 1024,
            "max_expanded_bytes": 1024 * 1024 * 1024,
        },
    }
    (child_root / "request.json").write_text(json.dumps(request, sort_keys=True))
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])}
    completed = subprocess.run(
        [sys.executable, "-m", "app.jobs.export_child", str(child_root)],
        check=False,
        capture_output=True,
        env=environment,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr.decode()
    receipt = json.loads((child_root / "result.json").read_text())
    child = (child_root / "candidate.docx").read_bytes()
    assert receipt["sha256"] == direct.sha256
    assert child == direct.content
    source_hashes = {hashlib.sha256(png).hexdigest() for png, _, _ in pages}
    assert len(source_hashes) == 126
    with zipfile.ZipFile(io.BytesIO(child)) as archive:
        media_hashes = {
            hashlib.sha256(archive.read(name)).hexdigest()
            for name in archive.namelist()
            if name.startswith("word/media/")
        }
    assert media_hashes == source_hashes
    artifact = os.environ.get("BID_EXPORT_STRESS_ARTIFACT")
    if artifact:
        destination = Path(artifact)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(child)
