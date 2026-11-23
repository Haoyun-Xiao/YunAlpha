from __future__ import annotations

import argparse
import json
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

PANEL_TITLES = {
    "part1": "基本概况",
    "part2": "规模变动",
    "part3": "业绩分析",
    "part4": "盈亏分析",
    "part5": "组合管理",
    "part6": "组合特征",
    "part7": "行业配置",
    "part8": "个股配置",
    "part9": "个股案例",
    "part10": "投资能力",
    "summary": "投资复盘",
}

BODY_FONT = "Arial Unicode MS"
BODY_LATIN_FONT = "Arial Unicode MS"
HEADING_FONT = "Arial Unicode MS"
TITLE_FONT = "Arial Unicode MS"
BODY_SIZE = 12.0
TABLE_SIZE = 10.5
CHART_CONTAINER_SELECTOR = ".chart-row,.p4-year-charts-row,.p9-case-charts,.p4-detail-table-row"
CHART_DOM_SELECTOR = ".chart-inner,.p4-yc,.p4-yc-sm-item,.p9-case-chart"

WORD_EXPORT_STYLE = """
(() => {
  const style = document.createElement("style");
  style.id = "word-export-style";
  style.textContent = `
    html, body { background: #ffffff !important; }
    .ai-assistant-tab, .ai-assistant-drawer, .ai-assistant-critical-style,
    .back-top,
    .tab-nav { display: none !important; }
    .tab-panel {
      display: block !important;
      max-width: 1180px !important;
      width: 1180px !important;
      margin: 0 auto 28px !important;
    }
    .card { box-shadow: none !important; }
    .card.collapsed .card-body { display: block !important; }
    .chart-row, .p4-year-charts-row, .p9-case-charts, .p4-detail-table-row {
      background: #ffffff !important;
      border-radius: 6px !important;
      padding: 4px !important;
    }
    .chart-inner, .p4-yc, .p4-yc-sm-item, .p9-case-chart { background: #ffffff !important; }
  `;
  document.head.appendChild(style);
})();
"""

WORD_EXPORT_SCRIPT = """
() => {
  document.body.classList.add("word-export");
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.add("active");
    panel.style.display = "block";
  });
  document.querySelectorAll(".card.collapsed").forEach((card) => {
    card.classList.remove("collapsed");
  });
  if (window.CHART_JS && typeof window.initChartsForTab === "function") {
    Object.keys(window.CHART_JS).forEach((key) => {
      try { window.initChartsForTab(key); } catch (err) { console.error("Word export chart init failed", key, err); }
    });
  }
  setTimeout(() => {
    if (Array.isArray(window.__P9_CASE_WORD_REFRESHERS__)) {
      window.__P9_CASE_WORD_REFRESHERS__.forEach((fn) => {
        try { fn(); } catch (err) { console.error("Part9 word chart refresh failed", err); }
      });
    }
    if (window.echarts) {
      document.querySelectorAll(".chart-inner,.p4-yc,.p4-yc-sm-item,.p9-case-chart").forEach((el) => {
        const inst = window.echarts.getInstanceByDom(el);
        if (inst) inst.resize();
      });
    }
    window.__WORD_EXPORT_READY__ = true;
  }, 2500);
}
"""


@dataclass
class PanelContent:
    key: str
    title: str
    html: str
    charts: dict[str, Path]


def _chrome_path() -> Path | None:
    candidates = [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        Path.home() / "Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    for name in ("chrome", "chrome.exe", "msedge", "msedge.exe"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def _resolve_fund(name_or_alias: str) -> str:
    return name_or_alias.strip()


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _set_run_font(run, size: float = BODY_SIZE, font: str = BODY_FONT, latin_font: str | None = None) -> None:
    run.font.name = latin_font or font
    run._element.rPr.rFonts.set(qn("w:eastAsia"), font)
    run._element.rPr.rFonts.set(qn("w:ascii"), latin_font or font)
    run._element.rPr.rFonts.set(qn("w:hAnsi"), latin_font or font)
    run._element.rPr.rFonts.set(qn("w:cs"), latin_font or font)
    run.font.size = Pt(size)


def _format_body_paragraph(paragraph, first_line: bool = True) -> None:
    fmt = paragraph.paragraph_format
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(3)
    fmt.line_spacing = 1.28
    if first_line:
        fmt.first_line_indent = Pt(BODY_SIZE * 2)
    paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY


def _format_heading_paragraph(paragraph, level: int) -> None:
    fmt = paragraph.paragraph_format
    fmt.first_line_indent = None
    fmt.space_before = Pt(10 if level == 1 else 7)
    fmt.space_after = Pt(5 if level == 1 else 3)
    fmt.line_spacing = 1.08
    for run in paragraph.runs:
        run.bold = True
        _set_run_font(run, size=15 if level == 1 else 12.5 if level == 2 else 11.5, font=HEADING_FONT, latin_font=HEADING_FONT)


def _extract_metadata(page) -> dict[str, str]:
    return page.evaluate(
        """() => ({
          title: (document.querySelector(".title, h1")?.innerText || document.title || "").trim(),
          subtitle: (document.querySelector(".subtitle")?.innerText || "").trim(),
          fundName: window.FUND_CONTEXT?.fund_name || "",
          fundCode: window.FUND_CONTEXT?.fund_code || "",
          manager: window.FUND_CONTEXT?.manager || ""
        })"""
    )


def _capture_panels(report_html: Path, assets_dir: Path) -> tuple[dict[str, str], list[PanelContent]]:
    from playwright.sync_api import sync_playwright

    chrome = _chrome_path()

    assets_dir.mkdir(parents=True, exist_ok=True)
    for stale in assets_dir.glob("*.png"):
        stale.unlink()

    with sync_playwright() as p:
        launch_options = {
            "headless": True,
            "args": ["--disable-gpu", "--no-first-run", "--disable-extensions"],
        }
        if chrome:
            launch_options["executable_path"] = str(chrome)
        browser = p.chromium.launch(
            **launch_options,
        )
        page = browser.new_page(viewport={"width": 1360, "height": 1100}, device_scale_factor=1.5)
        page_errors = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.goto(report_html.resolve().as_uri(), wait_until="networkidle", timeout=90000)
        page.evaluate(WORD_EXPORT_STYLE)
        page.evaluate(WORD_EXPORT_SCRIPT)
        page.wait_for_function("() => window.__WORD_EXPORT_READY__ === true", timeout=45000)
        page.wait_for_timeout(1000)
        unavailable_charts = page.evaluate("""() => {
          const unavailable = [];
          const valid = value => value !== null && value !== undefined && value !== '-';
          for (const el of document.querySelectorAll('[_echarts_instance_]')) {
            const chart = echarts.getInstanceByDom(el);
            const series = chart?.getOption()?.series || [];
            const hasData = series.some(s => (s.data || []).some(item => {
              const value = item && typeof item === 'object' && !Array.isArray(item) ? item.value : item;
              return Array.isArray(value) ? value.some(valid) : valid(value);
            }));
            if (chart && !hasData && el.id !== 'chart-p3b') {
              unavailable.push(el.id);
              chart.dispose();
              el.innerHTML = '<p style="padding:32px;text-align:center;color:#666">该指标暂无可用数据，未绘制图表。</p>';
            }
          }
          return unavailable;
        }""")

        metadata = _extract_metadata(page)
        panel_keys = page.evaluate(
            """() => Array.from(document.querySelectorAll(".tab-panel"))
              .map((panel) => panel.id.replace(/^tab-/, ""))
              .filter(Boolean)"""
        )
        panels: list[PanelContent] = []

        for key in panel_keys:
            title = PANEL_TITLES.get(key, key)
            panel_selector = f"#tab-{key}"

            chart_refs = page.evaluate(
                """(selector) => {
                  const panel = document.querySelector(selector);
                  if (!panel) return [];
                  const charts = Array.from(panel.querySelectorAll(".chart-row,.p4-year-charts-row,.p9-case-charts,.p4-detail-table-row"));
                  charts.forEach((el, idx) => {
                    el.setAttribute("data-word-chart-id", `${selector.replace("#tab-", "")}-${idx + 1}`);
                  });
                  return charts.map((el) => el.getAttribute("data-word-chart-id"));
                }""",
                panel_selector,
            )

            chart_paths: dict[str, Path] = {}
            for idx, chart_ref in enumerate(chart_refs):
                path = assets_dir / f"{key}_chart_{idx + 1:02d}.png"
                page.locator(f'{panel_selector} [data-word-chart-id="{chart_ref}"]').screenshot(
                    path=str(path),
                    timeout=30000,
                )
                if path.exists() and path.stat().st_size > 0:
                    chart_paths[chart_ref] = path

            # Keep charts as screenshots, but leave placeholders in the text flow so Word follows the webpage order.
            html = page.evaluate(
                """(selector) => {
                  const panel = document.querySelector(selector);
                  if (!panel) return "";
                  const clone = panel.querySelector(".card-body")?.cloneNode(true);
                  if (!clone) return "";
                  clone.querySelectorAll(".chart-row,.p4-year-charts-row,.p9-case-charts,.p4-detail-table-row").forEach((node) => {
                    const ref = node.getAttribute("data-word-chart-id");
                    const placeholder = document.createElement("div");
                    placeholder.setAttribute("data-word-chart-ref", ref || "");
                    node.replaceWith(placeholder);
                  });
                  clone.querySelectorAll("script,style").forEach((n) => n.remove());
                  return clone.innerHTML;
                }""",
                panel_selector,
            )

            panels.append(PanelContent(key=key, title=title, html=html, charts=chart_paths))

        chart_state = page.evaluate("""() => Array.from(document.querySelectorAll('[_echarts_instance_]')).map(el => {const chart=echarts.getInstanceByDom(el); const option=chart?.getOption(); return {id:el.id, initialized:!!chart, series:(option?.series||[]).map(s=>({type:s.type, points:(s.data||[]).filter(x=>x!==null).length})), dates:(option?.xAxis||[]).flatMap(a=>a.data||[]).filter(d=>/^20\\d\\d-\\d\\d-\\d\\d$/.test(d))};})""")
        state = {"page_errors": page_errors, "charts": chart_state, "unavailable_charts": unavailable_charts}
        (assets_dir / "browser_validation.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        browser.close()
        if page_errors:
            raise ValueError(f"render_browser_error:{page_errors}")
        return metadata, panels


def _set_cell_shading(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def _set_cell_text(cell, text: str, bold: bool = False) -> None:
    cell.text = ""
    paragraph = cell.paragraphs[0]
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    paragraph.paragraph_format.line_spacing = 1.15
    run = paragraph.add_run(text)
    run.bold = bold
    _set_run_font(run, size=TABLE_SIZE, font=BODY_FONT, latin_font=BODY_LATIN_FONT)


def _setup_document(doc: Document) -> None:
    section = doc.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width = Inches(11.69)
    section.page_height = Inches(8.27)
    section.top_margin = Inches(0.5)
    section.bottom_margin = Inches(0.5)
    section.left_margin = Inches(0.62)
    section.right_margin = Inches(0.62)
    section.header_distance = Inches(0.35)
    section.footer_distance = Inches(0.35)

    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = BODY_LATIN_FONT
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
    normal._element.rPr.rFonts.set(qn("w:ascii"), BODY_LATIN_FONT)
    normal._element.rPr.rFonts.set(qn("w:hAnsi"), BODY_LATIN_FONT)
    normal.font.size = Pt(BODY_SIZE)
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(3)
    normal.paragraph_format.line_spacing = 1.28

    for name, size, color in [
        ("Title", 22, "111111"),
        ("Heading 1", 15, "2F5B9A"),
        ("Heading 2", 12.5, "2F5B9A"),
        ("Heading 3", 11.5, "1F4D78"),
        ("Heading 4", 11, "1F4D78"),
    ]:
        style = styles[name]
        style.font.name = HEADING_FONT
        style._element.rPr.rFonts.set(qn("w:eastAsia"), HEADING_FONT)
        style._element.rPr.rFonts.set(qn("w:ascii"), HEADING_FONT)
        style._element.rPr.rFonts.set(qn("w:hAnsi"), HEADING_FONT)
        style._element.rPr.rFonts.set(qn("w:cs"), HEADING_FONT)
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor.from_string(color)
        if name != "Title":
            style.font.bold = True
        style.paragraph_format.keep_with_next = True
        style.paragraph_format.first_line_indent = None
        style.paragraph_format.space_before = Pt(10 if name == "Heading 1" else 7)
        style.paragraph_format.space_after = Pt(5 if name == "Heading 1" else 3)
        style.paragraph_format.line_spacing = 1.08

    for name in ("List Bullet", "List Number"):
        style = styles[name]
        style.font.name = BODY_LATIN_FONT
        style._element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
        style._element.rPr.rFonts.set(qn("w:ascii"), BODY_LATIN_FONT)
        style._element.rPr.rFonts.set(qn("w:hAnsi"), BODY_LATIN_FONT)
        style.font.size = Pt(BODY_SIZE)
        style.paragraph_format.space_after = Pt(2)
        style.paragraph_format.line_spacing = 1.22


def _add_heading(doc: Document, text: str, level: int):
    heading = doc.add_heading(text, level=level)
    _format_heading_paragraph(heading, level)
    return heading


def _add_runs(paragraph, node: Tag, inherited_bold: bool = False) -> None:
    for child in node.children:
        if isinstance(child, NavigableString):
            text = str(child)
            if text:
                run = paragraph.add_run(text)
                run.bold = inherited_bold
                _set_run_font(run)
        elif isinstance(child, Tag):
            if child.name == "br":
                paragraph.add_run().add_break()
            else:
                _add_runs(paragraph, child, inherited_bold or child.name in {"strong", "b"})


def _add_paragraph_from_tag(doc: Document, tag: Tag, style: str | None = None) -> None:
    text = _clean_text(tag.get_text(" "))
    if not text:
        return
    paragraph = doc.add_paragraph(style=style)
    _format_body_paragraph(paragraph, first_line=style is None)
    _add_runs(paragraph, tag)


def _is_word_skipped_web_table_block(tag: Tag) -> bool:
    text = _clean_text(tag.get_text(" "))
    return (
        "一级行业持仓比例热力表" in text
        or "前十大持仓变化" in text
    ) and tag.find("table") is not None


def _has_class(tag: Tag, class_name: str) -> bool:
    classes = tag.get("class") or []
    return class_name in classes


def _add_html_fragment(doc: Document, html: str, charts: dict[str, Path] | None = None) -> None:
    soup = BeautifulSoup(html or "", "html.parser")
    for child in soup.children:
        if isinstance(child, NavigableString):
            if _clean_text(str(child)):
                doc.add_paragraph(_clean_text(str(child)))
            continue
        if not isinstance(child, Tag):
            continue
        if _is_word_skipped_web_table_block(child):
            continue
        if _has_class(child, "private-holdings-block"):
            doc.add_page_break()
            nested_html = "".join(str(x) for x in child.children)
            _add_html_fragment(doc, nested_html, charts=charts)
            continue

        chart_ref = child.attrs.get("data-word-chart-ref")
        if chart_ref:
            chart_path = (charts or {}).get(str(chart_ref))
            if chart_path:
                _add_chart_images(doc, [chart_path])
            continue

        if child.name in {"h1", "h2", "h3"}:
            _add_heading(doc, _clean_text(child.get_text(" ")), level=2)
        elif child.name == "h4":
            _add_heading(doc, _clean_text(child.get_text(" ")), level=3)
        elif child.name in {"h5", "h6"}:
            _add_heading(doc, _clean_text(child.get_text(" ")), level=4)
        elif child.name == "p":
            _add_paragraph_from_tag(doc, child)
        elif child.name in {"ul", "ol"}:
            list_style = "List Bullet" if child.name == "ul" else "List Number"
            for li in child.find_all("li", recursive=False):
                _add_paragraph_from_tag(doc, li, style=list_style)
        elif child.name == "table":
            rows = child.find_all("tr")
            if not rows:
                continue
            max_cols = max(len(row.find_all(["td", "th"])) for row in rows)
            table = doc.add_table(rows=len(rows), cols=max_cols)
            table.style = "Table Grid"
            for r_idx, row in enumerate(rows):
                cells = row.find_all(["td", "th"])
                for c_idx, source_cell in enumerate(cells):
                    cell = table.cell(r_idx, c_idx)
                    _set_cell_text(cell, _clean_text(source_cell.get_text(" ")), bold=source_cell.name == "th")
                    if r_idx == 0:
                        _set_cell_shading(cell, "F2F4F7")
            if doc.paragraphs:
                doc.paragraphs[-1].paragraph_format.space_after = Pt(4)
        else:
            nested_html = "".join(str(x) for x in child.children)
            if nested_html:
                _add_html_fragment(doc, nested_html, charts=charts)


def _usable_width_inches(doc: Document) -> float:
    section = doc.sections[0]
    return (section.page_width - section.left_margin - section.right_margin) / 914400


def _add_chart_images(doc: Document, chart_paths: Iterable[Path]) -> None:
    width = Inches(_usable_width_inches(doc))
    for path in chart_paths:
        paragraph = doc.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.paragraph_format.space_before = Pt(1)
        paragraph.paragraph_format.space_after = Pt(2)
        run = paragraph.add_run()
        run.add_picture(str(path), width=width)


def _cover_title(metadata: dict[str, str], fund_name: str) -> str:
    manager = metadata.get("manager") or fund_name.split("_", 1)[0]
    fund_short = metadata.get("fundName") or fund_name
    if "_" in fund_short:
        fund_short = fund_short.split("_", 1)[1]
    return f"{manager}-{fund_short}-深度分析"


def _add_cover(doc: Document, metadata: dict[str, str], fund_name: str) -> None:
    title = _cover_title(metadata, fund_name)
    paragraph = doc.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.space_before = Pt(4)
    paragraph.paragraph_format.space_after = Pt(12)
    paragraph.paragraph_format.line_spacing = 1.0
    run = paragraph.add_run(title)
    run.bold = True
    _set_run_font(run, size=22, font=TITLE_FONT, latin_font="Arial")
    run.font.color.rgb = RGBColor.from_string("111111")
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(2)


def export_word(fund: str, output: str | Path | None = None, report_html: str | Path | None = None) -> Path:
    fund_name = _resolve_fund(fund)
    if report_html is None:
        raise ValueError("report_html 必须显式传入")
    report_html_path = Path(report_html).resolve()
    if not report_html_path.exists():
        raise SystemExit(f"报告不存在：{report_html_path}。请先运行 python build_html.py --fund \"{fund_name}\"")

    if output is None:
        raise ValueError("output 必须显式传入")
    out_docx = Path(output).resolve()
    out_docx.parent.mkdir(parents=True, exist_ok=True)
    assets_dir = out_docx.parent / "word_assets"

    metadata, panels = _capture_panels(report_html_path, assets_dir)

    doc = Document()
    _setup_document(doc)
    _add_cover(doc, metadata, fund_name)

    for idx, panel in enumerate(panels):
        if idx:
            doc.add_page_break()
        _add_heading(doc, panel.title, level=1)
        _add_html_fragment(doc, panel.html, charts=panel.charts)

    doc.save(out_docx)
    if not out_docx.exists() or out_docx.stat().st_size == 0:
        raise SystemExit("Word 导出失败：未生成有效文件。")
    return out_docx


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a fund report HTML to an editable Word document with chart screenshots.")
    parser.add_argument("--fund", required=True, help="基金名称或可唯一匹配的简称，例如：刘旭")
    parser.add_argument("--report-html", help="可选：指定要导出的 report.html 路径。")
    parser.add_argument("--output", help="输出 DOCX 路径。默认输出到对应 output/<基金>/ 下。")
    args = parser.parse_args()
    docx = export_word(args.fund, args.output, report_html=args.report_html)
    print(f"Word exported: {docx}")
    print(f"Size: {docx.stat().st_size / 1024 / 1024:.2f} MB")


if __name__ == "__main__":
    main()
