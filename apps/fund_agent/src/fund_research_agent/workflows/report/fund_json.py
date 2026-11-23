from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from fund_research_agent.domain.contracts import FundIdentity
from fund_research_agent.domain.report_data import PIPELINE_VERSION
from fund_research_agent.legacy_port.fund_json.calculations import calculate_holdings
from fund_research_agent.legacy_port.fund_json.lake import LakeRepository
from fund_research_agent.legacy_port.fund_json.model import JSON_TYPES, FundSpec
from fund_research_agent.legacy_port.fund_json.render import render_all
from fund_research_agent.paths import WriteBoundary


def generate_single_fund(
    identity: FundIdentity,
    database_path: Path,
    output_dir: Path,
    as_of: date,
    boundary: WriteBoundary,
    reports_overlay: Path | None = None,
    scales_overlay: Path | None = None,
) -> dict:
    output = boundary.ensure_dir(output_dir)
    spec = FundSpec(
        fund_symbol=identity.fund_symbol,
        fund_name=identity.fund_name,
        manager_name=identity.manager_name,
        tenure_start=identity.manager_tenure_start or date(2015, 1, 1),
        tenure_end=identity.manager_tenure_end,
    )
    repository = LakeRepository(database_path, reports_overlay=reports_overlay, as_of=as_of, scales_overlay=scales_overlay)
    try:
        holdings = calculate_holdings(repository, spec, as_of)
        if holdings.empty:
            raise ValueError("研究任期内没有持仓数据")
        documents = render_all(spec, repository, holdings, as_of)
        if set(documents) != set(JSON_TYPES):
            raise ValueError("单基金 JSON 渲染器没有返回完整 12 类文档")
        nav = repository.nav(spec.fund_symbol)
        metadata = {
            "pipeline_version": PIPELINE_VERSION, "requested_as_of": as_of.isoformat(),
            "data_as_of": nav.index.max().date().isoformat() if not nav.empty else None,
            "holding_report_period": str(holdings["report_period"].max().date()),
            "quality_flags": list(dict.fromkeys(repository.quality_flags)),
            "pnl_method": "unchanged_legacy_disclosure_estimate",
            "sources": {"nav": "lake_primary_overlay_gap_fill", "holdings": "fund_holdings", "scale": "fund_scale_and_local_overlay"},
        }
        scales = repository.scales(spec.fund_symbol).dropna(subset=["scale_yi"])
        metadata["metric_coverage"] = {
            "nav": {"data_as_of": metadata["data_as_of"], "source": "fund_nav", "unit": "adjusted_nav", "scope": "share_class"},
            "holdings": {"report_period": metadata["holding_report_period"], "source": "fund_holdings", "unit": "ratio", "scope": "quarterly_top10_or_semiannual_full"},
            "scale": {"report_period": str(scales["report_period"].max().date()) if not scales.empty else None, "source": "fund_scale_and_local_overlay", "unit": "亿元", "scope": "identified_share_classes_sum"},
        }
        files = {}
        for kind, text in documents.items():
            try:
                json.loads(text)
                normalized = text
            except json.JSONDecodeError:
                # Legacy “投资能力” is prompt-ready prose. Preserve it losslessly in a
                # JSON envelope so all twelve single-fund contracts are machine-readable.
                normalized = json.dumps(
                    {"format": "legacy_prompt_text", "prompt_text": text},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            value = json.loads(normalized)
            value["_metadata"] = metadata
            normalized = json.dumps(value, ensure_ascii=False, allow_nan=False)
            path = boundary.write_text(output / f"{spec.agent_name}_{kind}.txt", normalized)
            files[kind] = str(path)
        return {
            "agent_name": spec.agent_name,
            "fund_symbol": spec.fund_symbol,
            "files": files,
            "metadata": metadata,
            "holding_rows": len(holdings),
            "first_period": str(holdings["report_period"].min().date()),
            "last_period": str(holdings["report_period"].max().date()),
        }
    finally:
        repository.close()
