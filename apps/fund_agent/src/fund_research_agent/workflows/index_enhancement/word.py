"""Standalone DOCX builder, runnable with the bundled document Python runtime."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


def add_runs(paragraph, text: str) -> None:
    for number, part in enumerate(re.split(r"\*\*(.*?)\*\*", text)):
        run = paragraph.add_run(part)
        run.bold = number % 2 == 1


def add_table(doc, rows) -> None:
    table = doc.add_table(rows=1, cols=2)
    table.autofit = False
    table.columns[0].width, table.columns[1].width = Cm(4.5), Cm(21.2)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = "项目", "内容与依据"
    header = OxmlElement("w:tblHeader")
    table.rows[0]._tr.get_or_add_trPr().append(header)
    for a, b in rows:
        cells = table.add_row().cells
        cells[0].text, cells[1].text = a, b
    for i, row in enumerate(table.rows):
        for cell in row.cells:
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            props = cell._tc.get_or_add_tcPr()
            shade = OxmlElement("w:shd")
            shade.set(qn("w:fill"), "EFE7D7" if i == 0 else ("FAF6ED" if i % 2 == 0 else "FFFFFF"))
            props.append(shade)
            margins = OxmlElement("w:tcMar")
            for side in ("top", "left", "bottom", "right"):
                item = OxmlElement(f"w:{side}")
                item.set(qn("w:w"), "100")
                item.set(qn("w:type"), "dxa")
                margins.append(item)
            props.append(margins)
            for p in cell.paragraphs:
                p.paragraph_format.space_after = Pt(2)
                p.paragraph_format.line_spacing = 1.15
                for run in p.runs:
                    run.font.size = Pt(10)
                    run.bold = i == 0
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        b = OxmlElement(f"w:{side}")
        for key, value in {"val": "single", "sz": "4", "color": "D9D9D9"}.items():
            b.set(qn(f"w:{key}"), value)
        borders.append(b)
    table._tbl.tblPr.append(borders)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def build(payload: dict) -> Path:
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(29.7), Cm(21)
    section.top_margin = section.bottom_margin = Cm(1.4)
    section.left_margin = section.right_margin = Cm(2)
    for border in list(doc.styles.element.iter(qn("w:pBdr"))):
        border.getparent().remove(border)
    for name in ("Normal", "Title", "Subtitle", "Heading 1", "Heading 2", "Heading 3"):
        style = doc.styles[name]
        style.font.name = "Times New Roman"
        style._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "STSong")
        style.font.color.rgb = RGBColor(0, 0, 0)
    normal = doc.styles["Normal"]
    normal.font.size = Pt(11)
    normal.paragraph_format.line_spacing = 1.28
    normal.paragraph_format.space_after = Pt(7)
    for name in ("Heading 1", "Heading 2", "Heading 3"):
        doc.styles[name].paragraph_format.keep_with_next = True
        doc.styles[name].paragraph_format.space_before = Pt(12)
        doc.styles[name].paragraph_format.space_after = Pt(6)
    doc.styles["Heading 1"].font.size = Pt(16)
    doc.styles["Heading 2"].font.size = Pt(13)
    doc.add_paragraph(payload["title"], style="Title")
    doc.add_paragraph("Part1 产品定位与基准样章", style="Subtitle")
    doc.add_paragraph(f"000311.OF　资料截止 {payload['as_of']}；各原始资料发布日期见文末。")
    doc.add_heading("产品信息", level=1)
    add_table(doc, payload["info"])
    for block in re.split(r"\n\s*\n", payload["body"].strip()):
        if block.startswith("## "):
            lines = block.splitlines()
            doc.add_heading(lines[0][3:].replace("**", ""), level=1)
            if len(lines) > 1:
                add_runs(doc.add_paragraph(), " ".join(lines[1:]))
        elif block.startswith("### "):
            doc.add_heading(block[4:].replace("**", ""), level=2)
        else:
            add_runs(doc.add_paragraph(), block)
    doc.add_heading("合同基准与投资约束", level=1)
    # Formula is also in the body; repeat the exact source fact in the reference section.
    benchmark = next(
        (line for line in payload["markdown"].splitlines() if line.startswith("95%")), ""
    )
    if benchmark:
        doc.add_paragraph(benchmark)
    add_table(doc, payload["constraints"])
    doc.add_heading("A类份额费率", level=1)
    add_table(doc, payload["fees"])
    doc.add_heading("产品沿革", level=1)
    doc.add_picture(payload["timeline_path"], width=Cm(24))
    doc.add_heading("资料来源", level=1)
    for source in payload["source_lines"]:
        p = doc.add_paragraph(source)
        p.paragraph_format.space_after = Pt(3)
        p.paragraph_format.line_spacing = 1.1
        for run in p.runs:
            run.font.size = Pt(8.5)
    doc.add_paragraph("本样章仅含第一章，尚未进行业绩、收益归因与配置评价。")
    path = Path(payload["output"]).resolve()
    doc.save(path)
    return path


if __name__ == "__main__":
    print(build(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))))
