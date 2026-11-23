from __future__ import annotations

import html
import os
import re
import subprocess
import sys
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from fund_research_agent.legacy_port.renderers.export_word import _chrome_path
from fund_research_agent.paths import WriteBoundary, sha256_file
from fund_research_agent.workflows.index_enhancement.materials import normalized, write_json

CSS = """
:root{--bg:#f8f1e5;--paper:#fffdf8;--text:#24211d;--accent:#e3120b;--border:#d4c4a8}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--text);font-family:'PingFang SC','Microsoft YaHei',sans-serif;font-size:16px;line-height:1.85}
.sheet{max-width:1180px;margin:0 auto;padding:28px 32px 70px}.masthead{border-top:7px solid var(--accent);padding:24px 0 20px;border-bottom:2px solid #111}
.masthead h1{font-family:Georgia,'Songti SC',serif;font-size:34px;line-height:1.35;margin:12px 0}.subtitle{color:#655e54;font-size:14px}.tools{display:flex;gap:18px;flex-wrap:wrap;margin:12px 0 24px;font-size:14px}.tools a{color:#111;text-decoration:none;border-bottom:1px solid var(--accent)}
.chapter{padding:24px 32px;background:var(--paper);border:1px solid var(--border);border-radius:6px}.chapter h2{font-size:23px;line-height:1.5;margin:34px 0 16px;padding-bottom:8px;border-bottom:2px solid #111}.chapter h2:first-child{margin-top:0}.chapter h3{font-size:18px;margin:24px 0 10px;color:#111}p{margin:12px 0 18px;text-align:justify}strong{font-weight:650;color:#111}.prose{max-width:990px;margin:auto}.section-number{color:var(--accent)}
.table-scroll{overflow:auto;margin:18px 0 26px}table{border-collapse:collapse;width:100%;font-size:14px;line-height:1.75}th,td{padding:11px 14px;border:1px solid var(--border);vertical-align:top;text-align:left}th{background:#efe7d7;color:#111;font-weight:650}td:first-child{width:24%;min-width:115px;font-weight:550}tr:nth-child(even) td{background:#faf6ed}td a{overflow-wrap:anywhere}.source-ref{font-size:12px;color:#b02e20;white-space:nowrap}
.formula{padding:20px 24px;border-left:3px solid var(--accent);background:#f6eee1;margin:24px 0}.formula strong{font-family:Georgia,'Songti SC',serif;font-size:22px}.formula p{font-size:14px;margin:10px 0 0}
.timeline{padding:20px 26px;background:var(--paper)}.timeline h2{font-size:20px;border:0;margin:0 0 16px}.timeline ol{list-style:none;padding:0;margin:0;border-left:2px solid #c8b79b}.timeline li{position:relative;padding:0 0 22px 24px;display:flex;gap:24px;align-items:baseline}.timeline li:last-child{padding-bottom:0}.timeline li:before{content:'';position:absolute;left:-6px;top:11px;width:10px;height:10px;border-radius:50%;background:var(--accent)}.timeline time{font-family:Georgia,serif;font-size:18px;min-width:110px}.timeline span{font-size:15px}
details{border-top:1px solid var(--border);margin-top:28px;padding-top:18px}summary{cursor:pointer;font-weight:600}.sources p{font-size:13px;text-align:left;overflow-wrap:anywhere}.sources a{color:#654a32}.footer{margin-top:24px;font-size:12px;color:#655e54}a:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
@media(max-width:720px){.sheet{padding:16px 12px 40px}.masthead h1{font-size:27px}.chapter{padding:18px 16px}.chapter h2{font-size:21px}.timeline li{display:block}.timeline time{display:block}table{min-width:510px}.formula{padding:14px}.formula strong{font-size:19px}}
@media print{body{background:white}.sheet{padding:0;max-width:none}.tools{display:none}.chapter{border:0;padding:0}details{display:block}.timeline{break-inside:avoid}}
"""


def inline(text: str) -> str:
    safe = html.escape(text)
    safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
    safe = re.sub(r"\[(S\d+)\]", r'<a class="source-ref" href="#source-\1">[\1]</a>', safe)
    return safe


def markdown_html(text: str) -> str:
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        if block.startswith("## "):
            lines = block.splitlines()
            out.append(f"<h2>{inline(lines[0][3:])}</h2>")
            if len(lines) > 1:
                out.append(f"<p>{inline(' '.join(lines[1:]))}</p>")
        elif block.startswith("### "):
            out.append(f"<h3>{inline(block[4:])}</h3>")
        else:
            out.append(f"<p>{inline(block)}</p>")
    return "\n".join(out)


def table(rows: list[tuple[str, str]]) -> str:
    return (
        '<div class="table-scroll"><table><thead><tr><th>项目</th><th>内容与依据</th></tr></thead><tbody>'
        + "".join(
            f"<tr><td>{html.escape(label)}</td><td>{inline(value)}</td></tr>"
            for label, value in rows
        )
        + "</tbody></table></div>"
    )


def markdown_table(rows) -> str:
    return "| 项目 | 内容与依据 |\n|---|---|\n" + "\n".join(
        f"| {a} | {b.replace('|', '/').replace(chr(10), ' ')} |" for a, b in rows
    )


def build_content(data: dict, body: str) -> dict:
    facts = data["verified_facts"]["facts"]

    def value(key):
        item = facts[key]
        return f"{item['value']} [{item['source_id']}]"

    info = [
        (label, value(key))
        for label, key in (
            ("研究份额", "fund_name"),
            ("基金代码", "fund_code"),
            ("基金管理人", "management"),
            ("基金托管人", "custodian"),
            ("合同生效日", "found_date"),
            ("现任管理", "manager"),
            ("本基金任职起点", "manager_since"),
        )
    ]
    constraints = [
        (label, value(key))
        for label, key in (
            ("股票比例", "stock_range"),
            ("成分股及备选股", "constituent_min"),
            ("现金与短期国债", "cash_min"),
            ("日均跟踪偏离目标", "tracking_deviation"),
            ("年跟踪误差目标", "tracking_error"),
        )
    ]
    fees = [
        (label, value(key))
        for label, key in (
            ("A类管理费", "management_fee_a"),
            ("A类托管费", "custody_fee_a"),
            ("A类销售服务费", "sales_fee_a"),
        )
    ]
    if facts.get("share_classes"):
        info.append(("同产品其他份额", value("share_classes")))
    timeline = []
    found = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", facts["found_date"]["value"])
    if found:
        timeline.append(
            (
                f"{int(found[1]):04d}-{int(found[2]):02d}-{int(found[3]):02d}",
                f"基金合同生效；{facts['manager']['value']}开始管理本基金",
                facts["found_date"]["source_id"],
            )
        )
    change = facts.get("share_class_changes")
    if change:
        for match in re.finditer(
            r"(\d{4})年(\d{1,2})月(\d{1,2})日(?:起)?增设([CY])类", normalized(change["quote"])
        ):
            timeline.append(
                (
                    f"{int(match[1]):04d}-{int(match[2]):02d}-{int(match[3]):02d}",
                    f"增设{match[4]}类份额；本样章仍以A类为研究对象",
                    change["source_id"],
                )
            )
    timeline.sort()
    title = facts["fund_name"]["value"]
    source_lines = [
        f"[{s['source_id']}] {s['title']}，发布于{s['published_at']}。{s['url']}"
        for s in data["sources"]
    ]
    md = (
        f"# {title}\n\nPart1 产品定位与基准样章\n\n资料截止日 {data['as_of']}；各来源日期见文末。\n\n"
        "## 产品信息\n\n"
        + markdown_table(info)
        + "\n\n"
        + body
        + "\n\n## 合同基准与约束\n\n"
        + value("benchmark")
        + "\n\n"
        + markdown_table(constraints)
        + "\n\n## A类份额费率\n\n"
        + markdown_table(fees)
        + "\n\n## 产品沿革\n\n"
        + "\n".join(f"- {d} {label} [{source}]" for d, label, source in timeline)
        + "\n\n## 资料来源\n\n"
        + "\n\n".join(source_lines)
        + "\n\n本样章仅含第一章，尚未进行业绩、收益归因与配置评价。\n"
    )
    return {
        "title": title,
        "info": info,
        "constraints": constraints,
        "fees": fees,
        "timeline": timeline,
        "source_lines": source_lines,
        "markdown": md,
    }


def render_outputs(root: Path, data: dict, body: str, boundary: WriteBoundary) -> dict:
    output = boundary.ensure_dir(root / "output")
    content = build_content(data, body)
    boundary.write_text(output / "report.md", content["markdown"])
    f = data["verified_facts"]["facts"]["benchmark"]
    timeline = "".join(
        f"<li><time>{d}</time><span>{html.escape(t)} {inline(f'[{s}]')}</span></li>"
        for d, t, s in content["timeline"]
    )
    sources = "".join(
        f'<p id="source-{s["source_id"]}">[{s["source_id"]}] {html.escape(s["title"])}（{s["published_at"]}）'
        f'<br><a href="{html.escape(s["url"], quote=True)}" target="_blank" rel="noopener">基金公司原始PDF</a></p>'
        for s in data["sources"]
    )
    page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(content["title"])} 指增研究 Part1</title><link rel="icon" href="data:,"><style>{CSS}</style></head><body><main class="sheet">
<header class="masthead"><div class="subtitle">基金深度研究 · 指数增强</div><h1>{html.escape(content["title"])}</h1>
<div class="subtitle">000311.OF　／　Part1 产品定位与基准样章　／　资料截止 {data["as_of"]}</div></header>
<nav class="tools"><a href="report.docx" download>下载 Word</a><a href="report.md" download>下载 Markdown</a><a href="../handbook.md">阅读投资手册</a></nav>
<article class="chapter"><div class="prose"><h2>产品信息</h2>{table(content["info"])}
{markdown_html(body)}<h2>合同基准与投资约束</h2><div class="formula"><strong>{html.escape(f["value"])}</strong>
<p>固定年收益项不是现金权重，也不是已实现超额；条款中的比例区间不代表实际仓位。{inline(f"[{f['source_id']}]")}</p></div>
{table(content["constraints"])}<h2>A类份额费率</h2>{table(content["fees"])}
<section class="timeline" id="timeline"><h2>产品沿革</h2><ol>{timeline}</ol></section>
<details class="sources"><summary>查看原始资料与发布日期</summary>{sources}</details>
<p class="footer">本样章仅含第一章，尚未进行业绩、收益归因与配置评价。不同资料的发布日期不等于报告生成日期。</p>
</div></article></main></body></html>"""
    html_path = boundary.write_text(output / "report.html", page)
    problems = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, executable_path=str(_chrome_path()) if _chrome_path() else None
        )
        tab = browser.new_page(viewport={"width": 1360, "height": 1000}, device_scale_factor=1.5)
        tab.on("pageerror", lambda exc: problems.append(str(exc)))
        tab.goto(html_path.as_uri(), wait_until="load")
        tab.locator("#timeline").screenshot(path=str(output / "timeline.png"))
        tab.evaluate("window.scrollTo(0, 0)")
        tab.screenshot(path=str(output / "html-first-screen.png"))
        tab.screenshot(path=str(output / "html-full-page.png"), full_page=True)
        tab.set_viewport_size({"width": 390, "height": 844})
        mobile = tab.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        browser.close()
    if problems or not mobile:
        raise ValueError(f"html_validation_failed:{problems}:mobile={mobile}")
    word_payload = write_json(
        boundary,
        output / "word-input.json",
        {
            **content,
            "body": body,
            "as_of": data["as_of"],
            "timeline_path": str(output / "timeline.png"),
            "output": str(output / "report.docx"),
        },
    )
    bundled = Path(
        "/path/to/local-user/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
    )
    python = os.getenv(
        "FUND_AGENT_DOCUMENT_PYTHON", str(bundled) if bundled.exists() else sys.executable
    )
    subprocess.run(
        [python, str(Path(__file__).with_name("word.py")), str(word_payload)],
        check=True,
        timeout=120,
    )
    soup = BeautifulSoup(page, "html.parser")
    validation = {
        "valid": True,
        "javascript_errors": problems,
        "mobile_no_page_overflow": mobile,
        "tables": len(soup.select("table")),
        "timeline_events": len(content["timeline"]),
        "external_assets": len(soup.select("script[src],link[rel=stylesheet]")),
        "word_visual_qa": "pending_separate_render",
    }
    write_json(boundary, output / "render_validation.json", validation)
    return {
        kind: {"path": str(output / name), "sha256": sha256_file(output / name)}
        for kind, name in {
            "markdown": "report.md",
            "html": "report.html",
            "docx": "report.docx",
        }.items()
    }
