"""Safe, offline renderers for structured industry-chain reports.

The renderer deliberately keeps the model's prose and the machine-readable
research objects separate.  Numbers shown in tables are read from the
structured report fields; they are never extracted from ``body_md``.
"""

from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from markdown_it import MarkdownIt

_MD = MarkdownIt("commonmark", {"html": False, "linkify": False, "breaks": False}).enable("table")
_SAFE_URL = re.compile(r"^(?:https?://|mailto:|/|#)", re.IGNORECASE)
_STAGE_ORDER = {"upstream": 0, "上游": 0, "midstream": 1, "中游": 1, "downstream": 2, "下游": 2}
_METRIC_LABELS = {
    "close": "最新收盘价",
    "pe_ttm": "市盈率（TTM）",
    "pb": "市净率",
    "ps_ttm": "市销率（TTM）",
    "total_mv": "总市值",
    "float_mv": "流通市值",
    "revenue": "营业收入",
    "total_revenue": "营业收入（总额）",
    "revenue_yoy": "营业收入同比",
    "net_profit": "净利润",
    "net_profit_yoy": "净利润同比",
    "gross_margin": "毛利率",
    "roe": "ROE",
    "net_cash_operate": "经营活动现金流净额",
    "total_assets": "总资产",
    "total_equity": "所有者权益",
    "total_liabilities": "总负债",
    "net_income": "净利润（报表）",
    "operating_cost": "营业成本",
    "operating_profit": "营业利润",
    "total_profit": "利润总额",
    "net_cash_invest": "投资活动现金流净额",
    "net_cash_finance": "筹资活动现金流净额",
    "capex": "资本开支",
    "end_cash": "期末现金",
    "eps": "每股收益",
    "eps_deducted": "扣非每股收益",
    "pe_percentile_3y": "PE 分位（3年）",
    "pb_percentile_3y": "PB 分位（3年）",
    "ytd_return": "年初至今收益",
    "ytd_return_hfq": "年初至今收益（后复权）",
    "bps": "每股净资产",
    "ocf_per_share": "每股经营现金流",
}
_METRIC_ORDER = list(_METRIC_LABELS)
_MONEY_UNITS = {"CNY", "RMB", "元"}
_READER_LABELS = {
    "roic_not_computed_no_guessed_formula": "ROIC 所需口径不完整，未计算",
    "financial_report_period_mismatch": "财务与业务分部的报告期不一致",
    "business_segments_non_pit": "业务分部缺少公告日期，不能还原历史可得信息",
    "pe_ttm_percentile_3y_unavailable": "缺少有效市盈率，无法计算三年分位",
    "ytd_return_hfq_baseline_missing": "缺少年初复权基准，无法计算年内收益",
    "deterministic_calculation": "确定性计算",
    "source_fact": "来源陈述",
    "model_summary": "资料归纳",
    "inference": "研究推断",
    "external_untrusted": "外部资料待核实",
    "published_at_unknown": "发布日期未知",
    "candidate_mapping": "公司归属核对",
    "candidate_companies": "候选公司",
    "company_packs": "公司数据快照",
    "financial_evidence": "财务证据",
    "value_pool_items": "价值池分析",
    "quality_flags": "资料质量标记",
    "announce_date": "公告日期",
    "report_period": "报告期",
    "as_of": "数据截止日",
    "total_revenue": "营业总收入",
    "business_segments": "业务分部",
    "source_defined": "原始口径",
    "unconfirmed": "尚未确认",
    "indirect": "间接相关",
    "direct": "直接相关",
    "upstream": "上游",
    "midstream": "中游",
    "downstream": "下游",
    "supply": "供给关系",
    "non-PIT": "非历史时点口径",
    "non_pit": "非历史时点口径",
    "PE_TTM": "滚动市盈率",
    "PS_TTM": "滚动市销率",
    "capex": "资本开支",
    "companies": "公司列表",
    "pack": "数据快照",
    "null": "未评分",
    "business": "主营业务",
    "hfq": "后复权",
    "node": "节点",
    "revenue": "营业收入",
}


def _reader_words(value: Any) -> str:
    """Translate implementation vocabulary, not legitimate industry acronyms."""
    source = _text(value)
    for key in sorted(_READER_LABELS, key=len, reverse=True):
        source = re.sub(
            rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", _READER_LABELS[key], source
        )
    source = re.sub(r"\blake_\*", "数据湖", source)
    return source


def metric_label(key: str) -> str:
    """Return a reader-facing Chinese label while retaining unknown keys."""
    return _METRIC_LABELS.get(str(key), str(key))


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def metric_display(key: str, metric: Any) -> str:
    """Format a metric with unit and both period/as-of dates.

    Monetary values are shown in CNY yuan below 100 million and in explicitly
    labelled CNY hundred-million yuan above it.  Missing source values remain
    ``N/A`` and are never converted to zero.
    """
    if not isinstance(metric, Mapping):
        return _text(metric, "N/A") if metric not in (None, "") else "N/A"
    value = metric.get("value")
    number = _number(value)
    unit = _text(metric.get("unit"), "").strip()
    if value in (None, "") or number is None and not isinstance(value, str):
        display = "N/A"
    elif number is not None and unit in _MONEY_UNITS:
        if abs(number) >= 100_000_000:
            display = f"{number / 100_000_000:,.2f} 亿元（CNY）"
        else:
            display = f"{number:,.2f} 元（CNY）"
    elif number is not None and unit == "CNY/share":
        display = f"{number:,.2f} 元/股"
    elif number is not None and unit == "multiple":
        display = f"{number:,.2f} 倍"
    elif number is not None and unit == "%":
        display = f"{number:,.2f}%"
    elif number is not None:
        display = f"{number:,.2f}" + (f" {unit}" if unit and unit != "source_defined" else "")
    else:
        display = _text(value, "N/A") + (f" {unit}" if unit and unit != "source_defined" else "")
    period = metric.get("period")
    as_of = metric.get("as_of")
    meta: list[str] = []
    if period not in (None, ""):
        meta.append(f"报告期：{_text(period)}")
    if as_of not in (None, ""):
        meta.append(f"数据截至：{_text(as_of)}")
    return display + ("\n" + "；".join(meta) if meta else "")


def _text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _esc(value: Any, fallback: str = "") -> str:
    return html.escape(_text(value, fallback), quote=True)


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _evidence_catalog(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return only explicitly supplied evidence records, in stable order."""
    catalog: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _list(report.get("evidence")):
        if not isinstance(item, Mapping):
            continue
        evidence_id = _text(item.get("evidence_id"), "").strip()
        if not evidence_id or evidence_id in seen:
            continue
        seen.add(evidence_id)
        catalog.append({"index": len(catalog) + 1, **dict(item), "evidence_id": evidence_id})
    return catalog


def _annotate_evidence_markdown(value: Any, catalog: list[Mapping[str, Any]]) -> str:
    """Use numbered citations and keep internal identifiers out of prose.

    Only exact known IDs get a citation. Unknown IDs are hidden in the reader
    view, not guessed; the original report JSON remains unchanged.
    """
    source = _text(value, "")
    if not source:
        return source
    urls: list[str] = []

    def protect_url(match: re.Match[str]) -> str:
        urls.append(match.group(0))
        return f"READERSAFEURL{len(urls) - 1}TOKEN"

    source = re.sub(r"https?://[^\s<>\]\)]+", protect_url, source)
    by_id = {_text(item.get("evidence_id")): int(item["index"]) for item in catalog}
    alternatives = "|".join(re.escape(key) for key in sorted(by_id, key=len, reverse=True))

    def bracket(match: re.Match[str]) -> str:
        raw = match.group(1)
        index = by_id.get(raw)
        if index is not None:
            return f"[证据{index}](#evidence-row-{index})"
        parts = [p.strip() for p in re.split(r"[,，;；]", raw)]
        fact_types = {"source_fact", "deterministic_calculation", "model_summary", "inference"}
        ids = [re.sub(r"\s*等$", "", p) for p in parts if p not in fact_types]
        if ids and all(p in by_id for p in ids):
            return "、".join(f"[证据{by_id[p]}](#evidence-row-{by_id[p]})" for p in ids)
        return match.group(0)

    annotated = re.sub(r"\[([^\]]+)\]", bracket, source)

    def standalone(match: re.Match[str]) -> str:
        index = by_id.get(match.group(1))
        return f"[证据{index}](#evidence-row-{index})" if index is not None else match.group(0)

    annotated = (
        re.sub(
            rf"(?<![A-Za-z0-9_\-\[])({alternatives})(?![A-Za-z0-9_\-\]])",
            standalone,
            annotated,
        )
        if alternatives
        else annotated
    )
    # Unresolved internal IDs are not readable citations. Keep their originals
    # in the JSON contract; never invent a link to a different source.
    annotated = re.sub(
        r"\b(?:lake|web|ev|report|job|art)_[A-Za-z0-9_-]*[0-9][A-Za-z0-9_-]*\b|\b[0-9a-fA-F]{24,}\b",
        "",
        annotated,
    )
    annotated = re.sub(r"[（(\[]\s*[,，;；、\s]*[）)\]]", "", annotated)
    annotated = _reader_words(annotated)
    for index, url in enumerate(urls):
        annotated = annotated.replace(f"READERSAFEURL{index}TOKEN", url)
    return annotated


def _md_cell(value: Any) -> str:
    return _text(value).replace("|", "\\|").replace("\n", " ").strip()


def _rows_table(headers: Iterable[Any], rows: Iterable[Iterable[Any]]) -> str:
    hs = [_md_cell(v) for v in headers]
    if not hs:
        return ""
    out = ["| " + " | ".join(hs) + " |", "| " + " | ".join("---" for _ in hs) + " |"]
    for row in rows:
        values = list(row)
        values.extend([""] * max(0, len(hs) - len(values)))
        out.append("| " + " | ".join(_md_cell(v) for v in values[: len(hs)]) + " |")
    return "\n".join(out)


def _metric_value(metric: Any) -> Any:
    return metric_display("", metric)


def _report_title(report: Mapping[str, Any]) -> str:
    return _text(report.get("title") or report.get("anchor") or "产业链研究报告", "产业链研究报告")


_CORE_METRICS = [
    "close",
    "pe_ttm",
    "pb",
    "ps_ttm",
    "pe_percentile_3y",
    "pb_percentile_3y",
    "ytd_return",
    "ytd_return_hfq",
    "total_mv",
    "float_mv",
    "revenue",
    "revenue_yoy",
    "net_profit",
    "net_profit_yoy",
    "gross_margin",
    "roe",
    "net_cash_operate",
]


def _strip_duplicate_title(body: Any, title: str) -> str:
    source = _text(body, "").lstrip()
    if not source or not title:
        return source
    return re.sub(rf"^#\s+{re.escape(title)}\s*\n+", "", source, count=1)


def _claim_text(value: Any) -> str:
    if not isinstance(value, Mapping):
        return _text(value, "")
    return _text(
        value.get("text")
        or value.get("claim_text")
        or value.get("claim")
        or value.get("title")
        or value.get("name"),
        "",
    )


def _claim_counter(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    return _text(
        value.get("counter_evidence")
        or value.get("counter_factor")
        or value.get("detail")
        or value.get("note"),
        "",
    )


def _claim_evidence(value: Any) -> list[str]:
    if not isinstance(value, Mapping):
        return []
    return [_text(item).strip() for item in _list(value.get("evidence_ids")) if _text(item).strip()]


def _citation_markdown(ids: Iterable[str], catalog: list[Mapping[str, Any]]) -> str:
    by_id = {_text(item.get("evidence_id")): int(item["index"]) for item in catalog}
    refs = []
    for evidence_id in ids:
        index = by_id.get(_text(evidence_id))
        if index is not None:
            refs.append(f"[证据{index}](#evidence-row-{index})")
    return "、".join(refs)


def _citation_html(ids: Iterable[str], catalog: list[Mapping[str, Any]]) -> str:
    by_id = {_text(item.get("evidence_id")): int(item["index"]) for item in catalog}
    refs = []
    for evidence_id in ids:
        raw = _text(evidence_id)
        index = by_id.get(raw)
        if index is not None:
            refs.append(f'<a class="evidence-ref" href="#evidence-row-{index}">证据{index}</a>')
    return "、".join(refs)


def _claim_markdown(value: Any, catalog: list[Mapping[str, Any]]) -> str:
    text_value = _annotate_evidence_markdown(_claim_text(value), catalog)
    parts = [text_value]
    counter = _claim_counter(value)
    if counter:
        parts.append(f"**反向因素**：{_annotate_evidence_markdown(counter, catalog)}")
    evidence = _claim_evidence(value)
    if evidence:
        parts.append(f"**证据**：{_citation_markdown(evidence, catalog)}")
    return "；".join(part for part in parts if part)


def _score_label(value: Any) -> str:
    if not isinstance(value, Mapping):
        return _text(value)
    label = _text(value.get("label", value.get("key", "")))
    weight = _number(value.get("weight"))
    return f"{label}（{weight:.0%}）" if weight is not None and 0 <= weight <= 1 else label


def _normalise_relevance(value: Any) -> str:
    raw = _text(value, "").strip().lower()
    return {"直接": "direct", "间接": "indirect", "未确认": "unconfirmed"}.get(raw, raw)


def _company_relevance(report: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for segment in _list(report.get("segment_tables")):
        if not isinstance(segment, Mapping):
            continue
        for company in _list(segment.get("companies")):
            if not isinstance(company, Mapping) or not company.get("symbol"):
                continue
            value = _normalise_relevance(company.get("relevance"))
            if value in {"direct", "indirect"} or company.get("symbol") not in result:
                result[company["symbol"]] = value or "unconfirmed"
    return result


def _company_metric_keys(
    companies: Iterable[Mapping[str, Any]], *, core: bool = False
) -> list[str]:
    found: set[str] = set()
    for company in companies:
        metrics = company.get("metrics") if isinstance(company.get("metrics"), Mapping) else {}
        found.update(str(key) for key in metrics)
    if core:
        return [key for key in _CORE_METRICS if key in found]
    return [key for key in _METRIC_ORDER if key in found] + sorted(found - set(_METRIC_ORDER))


def render_markdown(report: dict) -> str:
    """Render a report contract as a portable Markdown document.

    ``body_md`` remains the authored narrative.  Structured sections are
    appended from their own fields so that downloads remain auditable even
    when a model response omits a prose section.
    """

    report = report if isinstance(report, Mapping) else {}
    evidence_catalog = _evidence_catalog(report)
    title = _report_title(report)
    lines = [f"# {title}", ""]
    meta: list[str] = []
    for key, label in (("anchor", "研究对象"), ("as_of", "研究基准日")):
        if report.get(key) not in (None, ""):
            meta.append(f"- **{label}**：{_text(report[key])}")
    if meta:
        lines.extend(meta + [""])
    if report.get("headline"):
        lines.extend(
            ["> " + _md_cell(_annotate_evidence_markdown(report["headline"], evidence_catalog)), ""]
        )

    priorities = [
        item for item in _list(report.get("research_priorities")) if isinstance(item, Mapping)
    ]
    if priorities:
        lines.extend(["## 研究优先级与失效条件", ""])
        for item in priorities:
            lines.extend([f"### {item.get('priority', '')}优先级：{item.get('target', '')}", ""])
            for key, label in [
                ("rationale", "研究依据"),
                ("verification", "需要验证"),
                ("invalidation", "失效条件"),
            ]:
                lines.extend(
                    [
                        f"**{label}**：{_annotate_evidence_markdown(item.get(key, ''), evidence_catalog)}",
                        "",
                    ]
                )
            lines.extend([_citation_markdown(_claim_evidence(item), evidence_catalog), ""])

    body = _annotate_evidence_markdown(
        _strip_duplicate_title(report.get("body_md"), title), evidence_catalog
    ).strip()
    if body:
        lines.extend([body, ""])

    graph = report.get("chain_graph") if isinstance(report.get("chain_graph"), Mapping) else {}
    nodes = _list(graph.get("nodes"))
    edges = _list(graph.get("edges"))
    if nodes or edges:
        lines.extend(["## 产业链结构", ""])
        if nodes:
            lines.extend(
                [
                    _rows_table(
                        ["节点", "位置", "说明", "事实类型"],
                        [
                            [
                                n.get("name", n.get("id", "")),
                                n.get("position", ""),
                                n.get("description", ""),
                                n.get("fact_type", ""),
                            ]
                            for n in nodes
                            if isinstance(n, Mapping)
                        ],
                    ),
                    "",
                ]
            )
        if edges:
            names = {
                node.get("id"): node.get("name", node.get("id"))
                for node in nodes
                if isinstance(node, Mapping)
            }
            lines.extend(
                [
                    "### 关系",
                    "",
                    _rows_table(
                        ["起点", "终点", "关系", "标签", "事实类型"],
                        [
                            [
                                names.get(e.get("source"), "未命名环节"),
                                names.get(e.get("target"), "未命名环节"),
                                e.get("kind", ""),
                                e.get("label", ""),
                                e.get("fact_type", ""),
                            ]
                            for e in edges
                            if isinstance(e, Mapping)
                        ],
                    ),
                    "",
                ]
            )

    segments = [s for s in _list(report.get("segment_tables")) if isinstance(s, Mapping)]
    if segments:
        lines.extend(["## 环节研究底稿", ""])
        for segment in segments:
            segment_name = _text(segment.get("segment") or segment.get("segment_id"), "未命名环节")
            segment_body = _annotate_evidence_markdown(segment.get("body_md", ""), evidence_catalog)
            companies = [c for c in _list(segment.get("companies")) if isinstance(c, Mapping)]
            detail_lines = [segment_body] if segment_body else []
            if companies:
                detail_lines.extend(
                    [
                        _rows_table(
                            ["代码", "公司", "相关性", "业务暴露"],
                            [
                                [
                                    c.get("symbol", ""),
                                    c.get("name", ""),
                                    c.get("relevance", ""),
                                    _annotate_evidence_markdown(
                                        c.get("exposure_note", c.get("exposure", "")),
                                        evidence_catalog,
                                    ),
                                ]
                                for c in companies
                            ],
                        ),
                        "",
                    ]
                )
            lines.extend(
                [
                    f"<details><summary>{_md_cell(segment_name)}</summary>",
                    "",
                    "\n".join(detail_lines).strip(),
                    "",
                    "</details>",
                    "",
                ]
            )

    companies = [c for c in _list(report.get("companies")) if isinstance(c, Mapping)]
    relevance = _company_relevance(report)
    confirmed = [
        c
        for c in companies
        if relevance.get(c.get("symbol"), "unconfirmed") in {"direct", "indirect"}
    ]
    unconfirmed = [c for c in companies if c not in confirmed]
    if confirmed:
        lines.extend(["## 公司比较", ""])
        metric_keys = _company_metric_keys(confirmed, core=True)
        headers = ["代码", "公司", "相关性"] + [metric_label(key) for key in metric_keys]
        rows = []
        for company in confirmed:
            metrics = company.get("metrics") if isinstance(company.get("metrics"), Mapping) else {}
            rows.append(
                [
                    company.get("symbol", ""),
                    company.get("name", ""),
                    relevance.get(company.get("symbol"), "unconfirmed"),
                ]
                + [metric_display(k, metrics.get(k)) for k in metric_keys]
            )
        lines.extend([_rows_table(headers, rows), ""])
    if unconfirmed:
        full_keys = _company_metric_keys(unconfirmed, core=False)
        full_rows = []
        for company in unconfirmed:
            metrics = company.get("metrics") if isinstance(company.get("metrics"), Mapping) else {}
            full_rows.append(
                [company.get("symbol", ""), company.get("name", ""), "unconfirmed"]
                + [metric_display(k, metrics.get(k)) for k in full_keys]
            )
        lines.extend(
            [
                "<details><summary>未确认候选公司（完整量化字段）</summary>",
                "",
                _rows_table(
                    ["代码", "公司", "相关性"] + [metric_label(key) for key in full_keys], full_rows
                ),
                "",
                "</details>",
                "",
            ]
        )

    score = report.get("scorecard") if isinstance(report.get("scorecard"), Mapping) else {}
    cols = _list(score.get("cols"))
    score_rows = [r for r in _list(score.get("rows")) if isinstance(r, Mapping)]
    if cols or score_rows:
        lines.extend(["## 环节评分卡", ""])
        headers = ["环节"] + [_score_label(c) for c in cols] + ["合计"]
        rows = []
        for row in score_rows:
            cells = _list(row.get("cells"))
            values = []
            for cell in cells:
                score_value = (
                    cell.get("score")
                    if isinstance(cell, Mapping) and cell.get("score") is not None
                    else "N/A"
                )
                note = (
                    _annotate_evidence_markdown(cell.get("note", ""), evidence_catalog)
                    if isinstance(cell, Mapping)
                    else ""
                )
                counter = (
                    _annotate_evidence_markdown(cell.get("counter_factor", ""), evidence_catalog)
                    if isinstance(cell, Mapping)
                    else ""
                )
                evidence = (
                    _citation_markdown(_claim_evidence(cell), evidence_catalog)
                    if isinstance(cell, Mapping)
                    else ""
                )
                detail = "；".join(
                    part
                    for part in (
                        note,
                        f"反向因素：{counter}" if counter else "",
                        f"证据：{evidence}" if evidence else "",
                    )
                    if part
                )
                values.append(
                    f"{score_value}<br><details><summary>依据</summary>{_md_cell(detail)}</details>"
                    if detail
                    else score_value
                )
            rows.append(
                [row.get("segment", "")]
                + values
                + [row.get("total") if row.get("total") is not None else "N/A"]
            )
        lines.extend([_rows_table(headers, rows), ""])

    value_pool = report.get("value_pool") if isinstance(report.get("value_pool"), Mapping) else {}
    items = [i for i in _list(value_pool.get("items")) if isinstance(i, Mapping)]
    if items or value_pool.get("method"):
        lines.extend(["## 价值池", ""])
        if value_pool.get("method"):
            lines.extend([f"方法：{_md_cell(value_pool['method'])}", ""])
        for index, item in enumerate(items, 1):
            lines.extend([f"### 判断 {index}", _claim_markdown(item, evidence_catalog), ""])

    for key, heading in (
        ("access_advice", "观察与配置线索"),
        ("watch_items", "后续跟踪"),
        ("risks", "风险反证"),
        ("data_gaps", "数据缺口"),
    ):
        values = _list(report.get(key))
        if values:
            lines.extend([f"## {heading}", ""])
            for value in values:
                if isinstance(value, Mapping):
                    lines.append(f"- {_claim_markdown(value, evidence_catalog)}")
                else:
                    lines.append(
                        f"- {_md_cell(_annotate_evidence_markdown(value, evidence_catalog))}"
                    )
            lines.append("")

    evidence = evidence_catalog
    if evidence:
        lines.extend(
            [
                "## 来源与证据",
                "",
                _rows_table(
                    ["引用", "来源", "日期", "事实类型"],
                    [
                        [
                            f'<a id="evidence-row-{e["index"]}"></a>[证据{e["index"]}](#evidence-row-{e["index"]})',
                            f"{e.get('title', '')} {e.get('url', '')}".strip(),
                            e.get("published_at", ""),
                            _reader_words(e.get("fact_type", "")),
                        ]
                        for e in evidence
                    ],
                ),
                "",
            ]
        )

    lines.extend(["---", "", f"生成于：{_text(report.get('created_at'), '未记录')}", ""])
    return _reader_words("\n".join(lines).rstrip()) + "\n"


def _safe_href(url: Any) -> str:
    value = _text(url).strip()
    return value if _SAFE_URL.match(value) else "#"


def _body_html(body_md: Any, evidence_catalog: list[Mapping[str, Any]] | None = None) -> str:
    # markdown-it disables raw HTML and rejects javascript: links in its
    # default validator.  Replace hrefs once more because this is a report
    # boundary and must stay safe if the markdown library changes defaults.
    tokens = _MD.parse(_annotate_evidence_markdown(body_md, evidence_catalog or []))
    for token in tokens:
        if token.type in {"heading_open", "heading_close"}:
            token.tag = "h" + str(min(6, int(token.tag[1:]) + 1))
    rendered = _MD.renderer.render(tokens, _MD.options, {})
    rendered = rendered.replace("<table>", '<div class="table-wrap"><table>').replace(
        "</table>", "</table></div>"
    )

    def replace_href(match: re.Match[str]) -> str:
        quote, url = match.group(1), html.unescape(match.group(2))
        return f"href={quote}{html.escape(_safe_href(url), quote=True)}{quote}"

    return re.sub(r'href=(["\'])(.*?)\1', replace_href, rendered, flags=re.IGNORECASE)


def _graph_svg(graph: Mapping[str, Any]) -> tuple[str, str]:
    nodes = [n for n in _list(graph.get("nodes")) if isinstance(n, Mapping)]
    edges = [e for e in _list(graph.get("edges")) if isinstance(e, Mapping)]
    if not nodes:
        return '<div class="empty-state">暂无可视化产业链结构</div>', "{}"
    groups: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for node in nodes:
        pos = node.get("position", "midstream")
        rank = _STAGE_ORDER.get(str(pos).lower(), 1)
        if isinstance(pos, (int, float)):
            rank = max(0, min(2, int(pos)))
        groups[rank].append(node)
    positions: dict[str, tuple[float, float]] = {}
    col_x = {0: 150, 1: 500, 2: 850}
    height = max(240, max((len(group) for group in groups.values()), default=1) * 96 + 80)
    for rank, group in groups.items():
        gap = height / (len(group) + 1)
        for index, node in enumerate(group, start=1):
            positions[_text(node.get("id"), str(index))] = (col_x.get(rank, 500), gap * index)
    svg: list[str] = [
        f'<svg class="chain-graph" viewBox="0 0 1000 {height:.0f}" role="img" aria-label="产业链关系图">',
        '<defs><marker id="chain-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#6b7c91"/></marker></defs>',
    ]
    for edge in edges:
        source, target = _text(edge.get("source")), _text(edge.get("target"))
        if source not in positions or target not in positions:
            continue
        x1, y1 = positions[source]
        x2, y2 = positions[target]
        if x1 == x2:
            direction = 1 if y2 > y1 else -1
            sx, sy, tx, ty = x1, y1 + 28 * direction, x2, y2 - 28 * direction
        else:
            direction = 1 if x2 > x1 else -1
            sx, sy, tx, ty = x1 + 86 * direction, y1, x2 - 86 * direction, y2
        svg.append(
            f'<g><title>{_esc(edge.get("label", "关联"))}</title><line class="chain-edge" x1="{sx:.1f}" y1="{sy:.1f}" x2="{tx:.1f}" y2="{ty:.1f}" marker-end="url(#chain-arrow)"/></g>'
        )
    for node in nodes:
        node_id = _text(node.get("id"))
        x, y = positions.get(node_id, (500, 50))
        label = _text(node.get("name", node_id))
        visible = label if len(label) <= 22 else label[:21] + "…"
        parts = [visible[i : i + 11] for i in range(0, len(visible), 11)]
        text = "".join(
            f'<tspan x="{x:.1f}" y="{y + (5 if len(parts) == 1 else -4 + index * 19):.1f}">{_esc(part)}</tspan>'
            for index, part in enumerate(parts)
        )
        svg.append(
            f'<g class="chain-node" tabindex="0" role="button" data-node-id="{_esc(node_id)}"><title>{_esc(label)}</title><rect x="{x - 86:.1f}" y="{y - 28:.1f}" width="172" height="56" rx="10"/><text>{text}</text></g>'
        )
    svg.append("</svg>")
    data = {"nodes": [dict(node) for node in nodes], "edges": [dict(edge) for edge in edges]}
    return "".join(svg), json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def _structured_html(
    report: Mapping[str, Any], evidence_catalog: list[Mapping[str, Any]] | None = None
) -> tuple[str, str]:
    blocks: list[str] = []
    graph = report.get("chain_graph") if isinstance(report.get("chain_graph"), Mapping) else {}
    graph_svg, graph_json = _graph_svg(graph)
    blocks.append(
        f'<section class="report-section graph-section" id="graph"><div class="section-heading"><h2>产业链结构</h2><span>点击节点查看证据</span></div><div class="graph-layout"><div class="graph-box">{graph_svg}</div><aside class="graph-detail" id="graph-detail"><p class="muted">选择一个环节，查看公司和证据。</p></aside></div></section>'
    )

    segments = [s for s in _list(report.get("segment_tables")) if isinstance(s, Mapping)]
    if segments:
        cards: list[str] = []
        for segment in segments:
            body = _body_html(segment.get("body_md", ""), evidence_catalog)
            companies = _company_table(segment.get("companies"), list(evidence_catalog or []))
            cards.append(
                f'<details class="segment-card"><summary>{_esc(segment.get("segment") or segment.get("segment_id"), "未命名环节")}</summary><div class="segment-content">{body}{companies}</div></details>'
            )
        blocks.append(
            '<section class="report-section" id="segments"><div class="section-heading"><h2>环节研究底稿</h2><span>分环节研究输出；综合判断见正文</span></div><div class="segment-grid">'
            + "".join(cards)
            + "</div></section>"
        )

    blocks.append(_html_company_section(report))

    score = report.get("scorecard") if isinstance(report.get("scorecard"), Mapping) else {}
    cols = _list(score.get("cols"))
    score_rows = [r for r in _list(score.get("rows")) if isinstance(r, Mapping)]
    if cols or score_rows:
        blocks.append(
            f'<section class="report-section" id="scorecard"><div class="section-heading"><h2>环节评分卡</h2><span>缺少证据时保留 N/A；查看依据与反向因素</span></div>{_html_scorecard(score, list(evidence_catalog or []))}</section>'
        )

    value_pool = report.get("value_pool") if isinstance(report.get("value_pool"), Mapping) else {}
    items = [i for i in _list(value_pool.get("items")) if isinstance(i, Mapping)]
    if items or value_pool.get("method"):
        body = (
            f'<p class="method-note">方法：{_esc(value_pool.get("method"), "未记录")}</p>'
            + _html_claim_list(items, evidence_catalog)
        )
        blocks.append(
            f'<section class="report-section" id="value-pool"><div class="section-heading"><h2>价值池</h2><span>不填补缺失份额</span></div>{body}</section>'
        )

    for key, heading, ident in (
        ("access_advice", "观察与配置线索", "advice"),
        ("watch_items", "后续跟踪", "watch-items"),
        ("risks", "风险反证", "risks"),
        ("data_gaps", "数据缺口", "gaps"),
    ):
        values = _list(report.get(key))
        if not values:
            continue
        blocks.append(
            f'<section class="report-section compact-list" id="{ident}"><div class="section-heading"><h2>{heading}</h2><span>{len(values)} 条</span></div>{_html_claim_list(values, evidence_catalog)}</section>'
        )

    evidence = list(evidence_catalog or _evidence_catalog(report))
    if evidence:
        table = _html_evidence_table(evidence)
        blocks.append(
            f'<section class="report-section" id="evidence"><div class="section-heading"><h2>来源与证据</h2><span>{len(evidence)} 条记录</span></div>{table}</section>'
        )
    return "".join(blocks), graph_json


def _html_table(
    headers: Iterable[Any],
    rows: Iterable[Iterable[Any]],
    numeric: bool = False,
    row_ids: Iterable[str] | None = None,
) -> str:
    hs = list(headers)
    head = "".join(f"<th>{_esc(h)}</th>" for h in hs)
    body: list[str] = []
    ids = list(row_ids or [])
    for row_index, row in enumerate(rows):
        values = list(row)
        values.extend([""] * max(0, len(hs) - len(values)))
        cells = []
        for idx, value in enumerate(values[: len(hs)]):
            cls = ' class="numeric"' if numeric and idx else ""
            cells.append(
                f'<td{cls} style="white-space:pre-line">{_esc(_reader_words(value) if value is not None else "N/A")}</td>'
            )
        row_id = f' id="{_esc(ids[row_index])}"' if row_index < len(ids) else ""
        body.append("<tr" + row_id + ">" + "".join(cells) + "</tr>")
    return (
        '<div class="table-wrap"><table><thead><tr>'
        + head
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def _html_claim(value: Any, catalog: list[Mapping[str, Any]]) -> str:
    body = [_body_html(_claim_text(value), catalog)]
    counter = _claim_counter(value)
    if counter:
        body.append(
            f'<div class="claim-counter"><strong>反向因素：</strong>{_body_html(counter, catalog)}</div>'
        )
    evidence = _claim_evidence(value)
    if evidence:
        body.append(f'<div class="claim-evidence">证据：{_citation_html(evidence, catalog)}</div>')
    return '<li class="claim-item">' + "".join(body) + "</li>"


def _html_claim_list(
    values: Iterable[Any], catalog: list[Mapping[str, Any]], *, details_id: str | None = None
) -> str:
    items = list(values)
    if not items:
        return ""
    content = (
        '<ul class="claim-list">'
        + "".join(
            _html_claim(item, catalog)
            if isinstance(item, Mapping)
            else f'<li class="claim-item">{_body_html(item, catalog)}</li>'
            for item in items
        )
        + "</ul>"
    )
    return (
        f'<details class="claim-details" id="{_esc(details_id)}"><summary>展开全部（{len(items)} 条）</summary>{content}</details>'
        if details_id
        else content
    )


def _html_scorecard(score: Mapping[str, Any], catalog: list[Mapping[str, Any]]) -> str:
    cols = _list(score.get("cols"))
    rows = [row for row in _list(score.get("rows")) if isinstance(row, Mapping)]
    if not cols and not rows:
        return ""
    headers = ["环节"] + [_score_label(col) for col in cols] + ["合计"]
    head = "".join(f"<th>{_esc(label)}</th>" for label in headers)
    body: list[str] = []
    explanations: list[str] = []
    for row in rows:
        cells = _list(row.get("cells"))
        rendered = [f"<td>{_esc(row.get('segment'))}</td>"]
        dimensions = []
        for index, _ in enumerate(cols):
            cell = cells[index] if index < len(cells) and isinstance(cells[index], Mapping) else {}
            score_value = cell.get("score") if cell.get("score") is not None else "N/A"
            notes = []
            if cell.get("note"):
                notes.append(_body_html(cell["note"], catalog))
            if cell.get("counter_factor"):
                notes.append(
                    f'<div class="claim-counter"><strong>反向因素：</strong>{_body_html(cell["counter_factor"], catalog)}</div>'
                )
            if cell.get("evidence_ids"):
                notes.append("证据：" + _citation_html(cell["evidence_ids"], catalog))
            dimensions.append(
                f'<div class="score-dimension"><h4>{_esc(_score_label(cols[index]))} · {_esc(score_value)}</h4>{"".join(notes) if notes else "暂无说明"}</div>'
            )
            rendered.append(f'<td class="numeric"><strong>{_esc(score_value)}</strong></td>')
        total = row.get("total") if row.get("total") is not None else "N/A"
        rendered.append(f'<td class="numeric"><strong>{_esc(total)}</strong></td>')
        body.append("<tr>" + "".join(rendered) + "</tr>")
        explanations.append(
            f'<details class="score-explanation"><summary>{_esc(row.get("segment"))}：查看依据与反向因素</summary><div class="score-dimensions">{"".join(dimensions)}</div></details>'
        )
    return (
        '<div class="table-wrap scorecard-wrap"><table><thead><tr>'
        + head
        + "</tr></thead><tbody>"
        + "".join(body)
        + '</tbody></table></div><div class="score-explanations">'
        + "".join(explanations)
        + "</div>"
    )


def _html_company_section(report: Mapping[str, Any]) -> str:
    companies = [
        company for company in _list(report.get("companies")) if isinstance(company, Mapping)
    ]
    if not companies:
        return ""
    relevance = _company_relevance(report)
    confirmed = [
        company
        for company in companies
        if relevance.get(company.get("symbol"), "unconfirmed") in {"direct", "indirect"}
    ]
    unconfirmed = [company for company in companies if company not in confirmed]

    def rows_for(items: list[Mapping[str, Any]], keys: list[str]) -> list[list[Any]]:
        result = []
        for company in items:
            metrics = company.get("metrics") if isinstance(company.get("metrics"), Mapping) else {}
            result.append(
                [f"{company.get('name', '')}\n{company.get('symbol', '')}"]
                + [metric_display(key, metrics.get(key)).split("\n", 1)[0] for key in keys]
            )
        return result

    html_parts: list[str] = []
    if confirmed:
        groups = [
            (
                "估值与市场表现",
                ["close", "pe_ttm", "pb", "total_mv", "pe_percentile_3y", "ytd_return_hfq"],
            ),
            (
                "经营与财务质量",
                [
                    "revenue",
                    "revenue_yoy",
                    "net_profit",
                    "net_profit_yoy",
                    "gross_margin",
                    "roe",
                    "net_cash_operate",
                ],
            ),
        ]
        found = _company_metric_keys(confirmed)
        for heading, keys in groups:
            keys = [key for key in keys if key in found]
            if keys:
                html_parts.append(
                    f'<h3>{heading}</h3><div class="company-matrix">'
                    + _html_table(
                        ["公司"] + [metric_label(key) for key in keys], rows_for(confirmed, keys)
                    )
                    + "</div>"
                )
        html_parts.append(
            '<p class="muted">金额已标明单位；数据日期、报告期、相关性及其余指标见下方各公司明细。N/A 表示缺少有效数据，不代表零。</p>'
        )
    else:
        html_parts.append(
            '<p class="muted">暂无已确认的直接或间接相关公司；候选公司保留在下方。</p>'
        )
    for items, heading in (
        (confirmed, "已确认公司完整指标"),
        (unconfirmed, "未确认候选公司与完整量化字段"),
    ):
        if not items:
            continue
        details = []
        for company in items:
            metrics = company.get("metrics") or {}
            metric_rows = [
                [
                    metric_label(key),
                    metric_display(key, metrics.get(key)).split("\n", 1)[0],
                    (metrics.get(key) or {}).get("period", "未提供"),
                    (metrics.get(key) or {}).get("as_of", "未提供"),
                ]
                for key in _company_metric_keys([company])
            ]
            label = f"{company.get('name', '')}（{company.get('symbol', '')}）"
            relation = _reader_words(relevance.get(company.get("symbol"), "unconfirmed"))
            details.append(
                f'<details class="company-detail"><summary>{_esc(label)} · {_esc(relation)}</summary>{_html_table(["指标", "数值", "报告期", "数据日期"], metric_rows)}</details>'
            )
        html_parts.append(
            f'<details class="quant-details"><summary>{heading}（{len(items)} 家）</summary>{"".join(details)}</details>'
        )
    return (
        '<section class="report-section" id="companies"><div class="section-heading"><h2>公司比较</h2><span>核心表仅展示已确认映射</span></div>'
        + "".join(html_parts)
        + "</section>"
    )


def _html_priorities(report: Mapping[str, Any], catalog: list[Mapping[str, Any]]) -> str:
    priorities = [
        item for item in _list(report.get("research_priorities")) if isinstance(item, Mapping)
    ]
    if not priorities:
        return ""
    rows = []
    for item in priorities:
        fields = "".join(
            f"<div><h4>{label}</h4>{_body_html(item.get(key, ''), catalog)}</div>"
            for key, label in [
                ("rationale", "研究依据"),
                ("verification", "需要验证"),
                ("invalidation", "失效条件"),
            ]
        )
        rows.append(
            f'<article class="priority-item"><h3><span class="priority-label">{_esc(item.get("priority"))}优先级</span>{_esc(item.get("target"))}</h3><div class="priority-fields">{fields}</div><p class="claim-evidence">{_citation_html(_claim_evidence(item), catalog)}</p></article>'
        )
    return (
        '<section class="report-section" id="priorities"><div class="section-heading"><h2>研究优先级与失效条件</h2><span>研究排序，不是交易指令</span></div>'
        + "".join(rows)
        + "</section>"
    )


def _html_evidence_table(evidence: Iterable[Mapping[str, Any]]) -> str:
    """Show readable sources; raw identifiers remain in the JSON download."""
    rows: list[str] = []
    for item in evidence:
        index = int(item["index"])
        url = _safe_href(item.get("url"))
        url_html = (
            f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noreferrer">查看原文</a>'
            if url != "#"
            else "本地数据快照"
        )
        citation = f'<a class="evidence-ref" href="#evidence-row-{index}">证据{index}</a>'
        cells = [
            f"<td>{citation}</td>",
            f"<td>{_esc(item.get('title'))}</td>",
            f"<td>{url_html}</td>",
            f"<td>{_esc(item.get('published_at'), '未提供')}</td>",
            f"<td>{_esc(_reader_words(item.get('fact_type', '未标记')))}</td>",
        ]
        rows.append(f'<tr id="evidence-row-{index}">' + "".join(cells) + "</tr>")
    head = "".join(
        f"<th>{_esc(label)}</th>" for label in ["引用", "来源", "链接", "日期", "事实类型"]
    )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        + head
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _company_table(companies: Any, catalog: list[Mapping[str, Any]]) -> str:
    entries = [c for c in _list(companies) if isinstance(c, Mapping)]
    if not entries:
        return ""
    rows = []
    for company in entries:
        cells = [
            _esc(company.get("symbol", "")),
            _esc(company.get("name", "")),
            _esc(_reader_words(company.get("relevance", ""))),
            _body_html(company.get("exposure_note", company.get("exposure", "")), catalog),
        ]
        rows.append("<tr>" + "".join("<td>" + value + "</td>" for value in cells) + "</tr>")
    headings = "".join("<th>" + label + "</th>" for label in ["代码", "公司", "相关性", "业务暴露"])
    return (
        '<div class="table-wrap exposure-table"><table><thead><tr>'
        + headings
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def render_html(report: dict) -> str:
    """Render a self-contained report page with an interactive SVG graph."""

    report = report if isinstance(report, Mapping) else {}
    title = _report_title(report)
    evidence_catalog = _evidence_catalog(report)
    structured, graph_json = _structured_html(report, evidence_catalog)
    priorities_html = _html_priorities(report, evidence_catalog)
    nav = [
        ("narrative", "核心结论"),
        ("priorities", "优先级"),
        ("graph", "产业链"),
        ("companies", "公司"),
        ("scorecard", "评分"),
        ("value-pool", "价值池"),
        ("risks", "风险"),
        ("segments", "底稿"),
        ("evidence", "证据"),
    ]
    nav_html = "".join(f'<a href="#{ident}">{_esc(label)}</a>' for ident, label in nav)
    body_html = _body_html(
        _strip_duplicate_title(report.get("body_md", ""), title), evidence_catalog
    )
    as_of = _esc(report.get("as_of"), "未记录")
    html_doc = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title><link rel="icon" href="data:,"><style>{_CSS}</style></head><body>
<header class="report-header"><div><h1>{_esc(title)}</h1><div class="headline">{_body_html(report.get("headline") or "结构化研究结果与证据链", evidence_catalog)}</div><dl class="meta"><div><dt>研究对象</dt><dd>{_esc(report.get("anchor"), "未记录")}</dd></div><div><dt>研究基准日</dt><dd>{as_of}</dd></div></dl></div></header>
<nav class="report-nav" aria-label="报告章节">{nav_html}</nav>
<main>{priorities_html}<section class="report-section narrative" id="narrative"><div class="section-heading"><h2>研究正文</h2></div>{body_html or '<p class="muted">暂无正文。</p>'}</section>{structured}</main>
<footer>研究基准日：{as_of} · 原始标识与完整数据保留在 JSON 下载中</footer>
<script type="application/json" id="chain-graph-data">{graph_json}</script>
<script>{_GRAPH_SCRIPT}</script></body></html>"""
    return html_doc


_CSS = r"""
:root{--ink:#17263b;--muted:#6c7d92;--line:#dbe3ec;--paper:#fbfcfe;--panel:#fff;--blue:#315fe8;--teal:#0b9d9a;--amber:#b7771c;--danger:#c04a51;--shadow:0 10px 28px rgba(24,48,82,.08)}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}.report-header{display:flex;justify-content:space-between;gap:48px;padding:56px max(28px,calc((100vw - 1180px)/2)) 38px;background:#f2f6fb;border-bottom:1px solid var(--line)}.eyebrow{margin:0 0 14px;color:var(--teal);font-size:12px;letter-spacing:.15em;font-weight:700}.report-header h1{margin:0;font-size:34px;letter-spacing:-.02em;line-height:1.2}.headline{max-width:720px;margin:16px 0 0;color:#4c5c70;font-size:17px}.meta{display:flex;align-items:flex-start;gap:24px;margin:0;padding-top:26px}.meta div{min-width:100px}.meta dt{color:var(--muted);font-size:12px}.meta dd{margin:3px 0;font-weight:650}.report-nav{position:sticky;top:0;z-index:3;display:flex;gap:22px;padding:11px max(28px,calc((100vw - 1180px)/2));background:rgba(251,252,254,.94);border-bottom:1px solid var(--line);backdrop-filter:blur(12px);overflow:auto}.report-nav a{white-space:nowrap;font-size:13px;font-weight:650}main{max-width:1180px;margin:0 auto;padding:24px 28px 80px}.report-section{margin:28px 0;padding:28px;background:var(--panel);border:1px solid var(--line);box-shadow:var(--shadow)}.section-heading{display:flex;justify-content:space-between;align-items:baseline;gap:16px;margin-bottom:18px;border-bottom:1px solid var(--line);padding-bottom:13px}.section-heading h2{margin:0;font-size:22px;letter-spacing:-.015em}.section-heading span{color:var(--muted);font-size:12px}.narrative p,.segment-card p{max-width:85ch}.narrative h1,.narrative h2,.narrative h3{line-height:1.35;margin-top:1.4em}.narrative h1{font-size:25px}.narrative h2{font-size:21px}.narrative h3{font-size:17px}.narrative blockquote{border-left:3px solid var(--teal);margin:20px 0;padding:5px 18px;color:#4c5c70;background:#f5fafb}.narrative code{padding:2px 5px;background:#edf1f6;border-radius:4px;font:13px ui-monospace,SFMono-Regular,Menlo,monospace}.narrative pre{overflow:auto;padding:16px;background:#15253b;color:#eef4fb;border-radius:8px}.narrative pre code{padding:0;background:transparent}.graph-layout{display:grid;grid-template-columns:minmax(0,1fr) 260px;gap:18px}.graph-box{overflow:auto;border:1px solid var(--line);background:#f8fafc;border-radius:8px}.chain-graph{display:block;min-width:720px;width:100%;height:auto}.chain-edge{stroke:#a5b3c2;stroke-width:1.5}.edge-label{fill:#718197;font-size:11px;text-anchor:middle}.chain-node{cursor:pointer;outline:none}.chain-node rect{fill:#fff;stroke:#7b8ea7;stroke-width:1.5;transition:.15s}.chain-node:hover rect,.chain-node:focus rect{fill:#edf4ff;stroke:var(--blue);stroke-width:2}.chain-node text{text-anchor:middle;fill:var(--ink);font-size:14px;font-weight:650}.graph-detail{border-left:1px solid var(--line);padding:12px 16px;background:#fbfcfe}.graph-detail h3{margin:0 0 7px;font-size:17px}.graph-detail ul{margin:8px 0;padding-left:19px}.graph-detail .tag{display:inline-block;margin:3px 4px 3px 0;padding:2px 7px;border-radius:100px;background:#eaf4f4;color:#117b79;font-size:11px}.segment-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:16px}.segment-card{padding:20px;background:#fbfcfe;border:1px solid var(--line)}.segment-card h3{margin:0 0 12px;font-size:18px}.table-wrap{overflow:auto}.table-wrap table{width:100%;border-collapse:collapse;min-width:520px}.table-wrap th,.table-wrap td{padding:10px 12px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}.table-wrap th{background:#f3f6fa;color:#566980;font-size:12px;font-weight:700;white-space:nowrap}.table-wrap td{font-size:13px}.table-wrap details{color:var(--muted);font-size:11px}.table-wrap details code{display:block;max-width:260px;overflow-wrap:anywhere;color:var(--ink);font:11px ui-monospace,SFMono-Regular,Menlo,monospace}.evidence-ref{font-weight:700;color:var(--blue)}.table-wrap .numeric{text-align:right;font-variant-numeric:tabular-nums}.method-note{padding:10px 13px;background:#f7f9fc;color:#55677d}.compact-list ul{margin:0;padding:0;list-style:none}.compact-list li{display:flex;gap:12px;padding:11px 0;border-bottom:1px solid var(--line)}.compact-list li:last-child{border-bottom:0}.compact-list strong{min-width:130px}.muted{color:var(--muted)}footer{padding:20px 28px 45px;text-align:center;color:var(--muted);font-size:12px}@media(max-width:800px){.report-header{display:block;padding:34px 20px 25px}.report-header h1{font-size:28px}.meta{padding-top:24px;gap:12px;flex-wrap:wrap}.report-nav{padding-left:20px}.report-section{padding:20px;margin:18px 0}.graph-layout{grid-template-columns:1fr}.graph-detail{border-left:0;border-top:1px solid var(--line)}main{padding:10px 14px 60px}}
"""


_CSS += r"""
.report-header{display:block;padding-top:38px;padding-bottom:28px}.report-header>div{max-width:1068px;margin:auto}.report-header h1{font-size:30px;line-height:1.4}.headline{max-width:70ch;font-size:16px}.meta{padding-top:18px;gap:36px}.meta div{min-width:100px}.meta dd{white-space:nowrap;font-size:14px}
.report-section{scroll-margin-top:64px;min-width:0}.section-heading{flex-wrap:wrap}.section-heading span{font-size:13px}.report-section p{overflow-wrap:break-word}.report-section h3{font-size:18px;line-height:1.5}.report-section p,.report-section li{line-height:1.85}.report-section li>p{margin:0 0 8px}.report-section :is(h2,h3,h4){scroll-margin-top:66px}
.priority-item{padding:20px 0;border-bottom:1px solid var(--line)}.priority-item:last-child{border-bottom:0;padding-bottom:0}.priority-item h3{margin:0 0 18px}.priority-label{display:inline-block;padding:2px 8px;margin-right:10px;background:#edf3ff;color:#315fe8;font-size:13px;border-radius:4px;vertical-align:middle;white-space:nowrap}.priority-fields{display:grid;grid-template-columns:1fr 1fr;gap:14px 28px}.priority-fields>div:first-child{grid-column:1/-1}.priority-fields h4{margin:0 0 6px;font-size:14px;color:#526681}.priority-fields p{margin:0}.priority-fields>div{min-width:0}
.graph-layout{grid-template-columns:1fr}.graph-detail{border-left:0;border-top:1px solid var(--line);padding:18px 20px;display:grid;grid-template-columns:1fr 1fr;gap:0 32px}.graph-detail h3,.graph-detail>p:first-of-type{grid-column:1/-1}.graph-detail>p{margin:4px 0}.graph-detail ul{grid-column:1/-1}.graph-detail .tag{justify-self:start}.graph-detail li{margin-bottom:6px}
.segment-grid{display:flex;flex-direction:column;gap:12px}.segment-card{padding:0;min-width:0}.segment-card summary{cursor:pointer;padding:16px 20px;font-size:17px;font-weight:650}.segment-card[open]>summary{border-bottom:1px solid var(--line)}.segment-content{padding:6px 24px 24px;max-width:100%;overflow:hidden}.segment-content>p,.segment-content>ul,.segment-content>ol{max-width:78ch}.segment-content h2{font-size:22px}.segment-content h3{font-size:18px}.segment-content h4{font-size:16px}
.table-wrap{max-width:100%;overscroll-behavior-x:contain}.table-wrap td{line-height:1.65;min-width:80px;overflow-wrap:break-word}.table-wrap th{white-space:normal;min-width:85px;line-height:1.5}.narrative .table-wrap td,.segment-content .table-wrap td{min-width:130px;max-width:420px}.company-matrix td{white-space:nowrap!important;font-variant-numeric:tabular-nums}.company-matrix td:first-child{white-space:pre-line!important;min-width:175px}.company-matrix th{min-width:118px}.company-detail td{min-width:140px}.company-detail td:first-child{min-width:200px}.scorecard-wrap table{min-width:850px}.scorecard-wrap td:first-child{min-width:170px}.scorecard-wrap .numeric{vertical-align:middle;font-size:15px}.score-explanations{margin-top:18px}.score-explanation,.company-detail{border-bottom:1px solid var(--line)}.score-explanation>summary,.company-detail>summary{cursor:pointer;padding:13px 4px;font-weight:600}.score-dimensions{display:grid;grid-template-columns:1fr 1fr;gap:20px 32px;padding:8px 4px 22px}.score-dimension{min-width:0}.score-dimension h4{margin:0 0 8px;color:#315fe8;font-size:15px}.score-dimension p{margin:0 0 8px}
.claim-list{margin:0;padding:0;list-style:none}.claim-item,.compact-list .claim-item{display:block;padding:16px 0;border-bottom:1px solid var(--line)}.claim-item:last-child{border-bottom:0}.claim-item p{margin:0 0 8px;max-width:80ch}.claim-counter{margin-top:8px;color:#8b5f1e;font-size:14px}.claim-counter p{display:inline}.claim-evidence{margin-top:8px;color:var(--muted);font-size:13px}.claim-details,.quant-details{margin-top:16px}.claim-details>summary,.quant-details>summary{cursor:pointer;padding:12px 14px;border:1px solid var(--line);background:#f5f8fc;color:#53677f;font-weight:650}.claim-details[open]>summary,.quant-details[open]>summary{margin-bottom:10px}.company-detail{padding:0 12px}.evidence-ref{white-space:nowrap}#evidence td:first-child{min-width:74px}#evidence td:nth-child(2){width:55%;min-width:240px}#evidence td:nth-child(n+3){white-space:nowrap}
a[href^="#evidence-row-"]{white-space:nowrap}summary:focus-visible,a:focus-visible{outline:2px solid var(--blue);outline-offset:3px}@media(max-width:800px){.priority-fields,.score-dimensions{grid-template-columns:1fr}.priority-fields>div:first-child{grid-column:auto}.graph-detail{display:block}.segment-content{padding:4px 16px 20px}.section-heading h2{font-size:20px}.report-header h1{font-size:25px}.meta{gap:24px}.report-section{padding:18px}.score-dimensions{gap:16px}.report-section p,.report-section li{font-size:15px}}@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
"""


_GRAPH_SCRIPT = r"""
(() => {
  const data = JSON.parse(document.getElementById('chain-graph-data')?.textContent || '{}');
  const detail = document.getElementById('graph-detail');
  const nodes = new Map((data.nodes || []).map(n => [String(n.id), n]));
  const edges = data.edges || [];
  function show(id) {
    const node = nodes.get(String(id));
    if (!node || !detail) return;
    const incoming = edges.filter(e => String(e.target) === String(id)).map(e => String(e.source));
    const outgoing = edges.filter(e => String(e.source) === String(id)).map(e => String(e.target));
    detail.replaceChildren();
    const title = document.createElement('h3'); title.textContent = node.name || node.id; detail.append(title);
    const desc = document.createElement('p'); desc.textContent = node.description || '暂无环节说明。'; detail.append(desc);
    const types = {source_fact:'来源陈述', deterministic_calculation:'确定性计算', model_summary:'资料归纳', inference:'研究推断'};
    const type = document.createElement('span'); type.className = 'tag'; type.textContent = types[node.fact_type] || '未标记'; detail.append(type);
    const evidence = document.createElement('p'); evidence.className = 'muted'; evidence.textContent = (node.evidence_ids || []).length + ' 条证据'; detail.append(evidence);
    const relation = document.createElement('p'); relation.className = 'muted'; relation.textContent = `上游 ${incoming.length} 个 · 下游 ${outgoing.length} 个`; detail.append(relation);
    const relations = document.createElement('ul');
    edges.filter(e => String(e.source) === String(id) || String(e.target) === String(id)).forEach(edge => {
      const item = document.createElement('li');
      item.textContent = `${nodes.get(String(edge.source))?.name || edge.source} → ${nodes.get(String(edge.target))?.name || edge.target}：${edge.label || '关联'}`;
      relations.append(item);
    });
    detail.append(relations);
  }
  document.querySelectorAll('.chain-node').forEach(el => {
    el.addEventListener('click', () => show(el.dataset.nodeId));
    el.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); show(el.dataset.nodeId); } });
  });
})();
"""
