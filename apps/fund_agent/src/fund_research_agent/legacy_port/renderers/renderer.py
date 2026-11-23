from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup
from docx import Document

from fund_research_agent.legacy_port.renderers.build_html import (
    generate_html,
    load_nav_index_cutoff_date,
)
from fund_research_agent.legacy_port.renderers.export_word import PANEL_TITLES, export_word
from fund_research_agent.paths import WriteBoundary

ASSET_DIR = Path(__file__).resolve().parent / "assets"
EXPECTED_PANELS = {*(f"part{number}" for number in range(1, 11)), "summary"}


def render_report(
    fund_name: str,
    report_markdown: Path,
    part_data_dir: Path,
    render_data_dir: Path,
    output_dir: Path,
    as_of: date,
    boundary: WriteBoundary,
) -> tuple[dict[str, Path], dict]:
    report_markdown = report_markdown.resolve(strict=True)
    part_data_dir = part_data_dir.resolve(strict=True)
    output_dir = boundary.ensure_dir(output_dir)
    html_path = boundary.assert_writable(output_dir / "report.html")
    docx_path = boundary.assert_writable(output_dir / "report.docx")
    generate_html(
        fund_name,
        report_file_override=str(report_markdown),
        out_html_override=str(html_path),
        data_dir_override=str(part_data_dir),
        web_asset_dir_override=str(ASSET_DIR),
        data_cutoff_date_override=as_of,
        nav_data_dir_override=str(render_data_dir),
    )
    _inline_assets(html_path)
    boundary.ensure_dir(output_dir / "word_assets")
    boundary.assert_writable(output_dir / "word_assets/browser_validation.json")
    export_word(
        fund_name,
        output=docx_path,
        report_html=html_path,
    )
    browser_path = output_dir / "word_assets/browser_validation.json"
    if browser_path.exists():
        unavailable = json.loads(browser_path.read_text(encoding="utf-8")).get("unavailable_charts", [])
        if unavailable:
            css = "".join(
                f'[id={json.dumps(chart_id, ensure_ascii=False)}]{{position:relative}}'
                f'[id={json.dumps(chart_id, ensure_ascii=False)}]::after{{content:"该指标暂无可用数据，未绘制图表。";position:absolute;inset:0;background:#fffdf8;display:flex;align-items:center;justify-content:center;color:#666}}'
                for chart_id in unavailable
            )
            boundary.write_text(html_path, html_path.read_text(encoding="utf-8").replace("</head>", f"<style>{css}</style></head>"))
    validation = validate_rendered_outputs(report_markdown, html_path, docx_path, expected_nav_date=load_nav_index_cutoff_date(str(render_data_dir)))
    boundary.write_text(
        output_dir / "render_validation.json",
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n",
    )
    if not validation["valid"]:
        raise ValueError(f"render_validation_failed:{validation['errors']}")
    return {
        "markdown": report_markdown,
        "html": html_path,
        "docx": docx_path,
    }, validation


def _inline_assets(html_path: Path) -> None:
    html = html_path.read_text(encoding="utf-8")
    echarts = (ASSET_DIR / "echarts.min.js").read_text(encoding="utf-8")
    sidebar_css = (ASSET_DIR / "sidebar.css").read_text(encoding="utf-8")
    sidebar_js = (ASSET_DIR / "sidebar.js").read_text(encoding="utf-8")
    html, echarts_count = re.subn(
        r'<script src="[^"]*/echarts\.min\.js"></script>',
        lambda _match: f"<script>{echarts}</script>",
        html,
        count=1,
    )
    html, css_count = re.subn(
        r'<link rel="stylesheet" href="[^"]*/sidebar\.css[^\"]*">',
        lambda _match: f"<style>{sidebar_css}</style>",
        html,
        count=1,
    )
    html, sidebar_count = re.subn(
        r'<script src="[^"]*/sidebar\.js[^\"]*"></script>',
        lambda _match: f"<script>{sidebar_js}</script>",
        html,
        count=1,
    )
    if (echarts_count, css_count, sidebar_count) != (1, 1, 1):
        raise ValueError(
            "renderer_asset_inlining_failed:"
            f"echarts={echarts_count},css={css_count},sidebar={sidebar_count}"
        )
    html_path.write_text(html, encoding="utf-8")


def validate_rendered_outputs(
    report_markdown: Path, html_path: Path, docx_path: Path, *, expected_nav_date: date | None = None
) -> dict:
    errors: list[str] = []
    markdown = report_markdown.read_text(encoding="utf-8")
    for number in range(1, 11):
        if f"Part{number}" not in markdown:
            errors.append(f"markdown_missing_part{number}")
    if "不构成任何投资建议" not in markdown:
        errors.append("markdown_missing_disclaimer")

    html = html_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    panels = {
        str(node.get("id", "")).removeprefix("tab-")
        for node in soup.select(".tab-panel")
    }
    missing_panels = sorted(EXPECTED_PANELS - panels)
    if missing_panels:
        errors.append("html_missing_panels:" + ",".join(missing_panels))
    chart_containers = len(
        soup.select(".chart-inner,.p4-yc,.p4-yc-sm-item,.p9-case-chart")
    )
    if chart_containers == 0:
        errors.append("html_has_no_charts")
    if "echarts.init" not in html or "echarts.min.js\"></script>" in html:
        errors.append("html_missing_inline_echarts")

    browser_manifest = docx_path.parent / "word_assets/browser_validation.json"
    browser = json.loads(browser_manifest.read_text(encoding="utf-8")) if browser_manifest.exists() else {}
    if browser_manifest.exists():
        if browser.get("page_errors"):
            errors.append("html_javascript_errors")
        for chart in browser.get("charts", []):
            if not chart.get("initialized") or not any(series.get("points", 0) for series in chart.get("series", [])):
                errors.append(f"html_empty_chart:{chart.get('id')}")
    if expected_nav_date is not None:
        nav_chart = next((chart for chart in browser.get("charts", []) if chart.get("id") == "chart-p3b"), None)
        if nav_chart is None or not nav_chart.get("dates") or max(nav_chart["dates"]) != expected_nav_date.isoformat():
            errors.append("html_nav_date_mismatch")
    document = Document(docx_path)
    docx_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    if "不构成任何投资建议" not in docx_text:
        errors.append("docx_missing_disclaimer")
    if len(document.inline_shapes) == 0:
        errors.append("docx_has_no_chart_images")
    heading_titles = [p.text for p in document.paragraphs if p.style.name == "Heading 1"]
    expected_titles = [PANEL_TITLES[f"part{number}"] for number in range(1, 11)]
    positions = []
    for title in expected_titles:
        if title not in heading_titles:
            errors.append(f"docx_missing_section:{title}")
        else:
            positions.append(heading_titles.index(title))
    if positions != sorted(positions):
        errors.append("docx_section_order_mismatch")
    return {
        "browser_validation": browser,
        "warnings": [f"optional_chart_unavailable:{chart}" for chart in browser.get("unavailable_charts", [])],
        "valid": not errors,
        "errors": errors,
        "markdown_bytes": report_markdown.stat().st_size,
        "html_bytes": html_path.stat().st_size,
        "docx_bytes": docx_path.stat().st_size,
        "html_panels": sorted(panels),
        "html_chart_containers": chart_containers,
        "docx_chart_images": len(document.inline_shapes),
        "docx_paragraphs": len(document.paragraphs),
    }
