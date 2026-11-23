from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from datetime import date
from io import StringIO
from pathlib import Path
from typing import Any

import duckdb

from fund_research_agent.domain.contracts import FundIdentity
from fund_research_agent.paths import WriteBoundary, sha256_file

PART_FILENAMES = {
    "part2": "part2_规模变动.json",
    "part3": "part3_业绩分析.json",
    "part4": "part4_盈亏分析.json",
    "part5": "part5_组合管理.json",
    "part6": "part6_组合特征.json",
    "part7": "part7_行业配置.json",
    "part8": "part8_个股配置.json",
    "part9": "part9_个股案例.json",
    "part10": "part10_中观与选股.json",
}


def _load(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 根节点必须是对象: {path}")
    return value


def _root_payload(data: dict[str, Any], symbol: str) -> dict[str, Any]:
    for key, value in data.items():
        if key.startswith(symbol + "_") and isinstance(value, dict):
            return value
    raise ValueError(f"未找到基金根节点: {symbol}")


def _meaningful(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_meaningful(item) for item in value.values())
    if isinstance(value, list):
        return any(_meaningful(item) for item in value)
    return value not in (None, "")


def _safe_number(value: Any) -> float:
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value or "").replace("%", "").replace(",", "").strip()
        try:
            number = float(text)
        except ValueError:
            return 0.0
    return number if math.isfinite(number) else 0.0


def _stock_name_index(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        result[str(row.get("股票名称", ""))].append(row)
    return result


class PartDataBuilder:
    """把 12 类原始 JSON 恢复成旧报告节点使用的 Part 契约。"""

    def __init__(self, database_path: Path, boundary: WriteBoundary) -> None:
        self.database_path = database_path.resolve()
        self.boundary = boundary

    def build(
        self,
        identity: FundIdentity,
        json_files: dict[str, str],
        output_dir: Path,
        as_of: date,
        material_files: list[Path] | None = None,
    ) -> dict[str, Any]:
        output_dir = self.boundary.ensure_dir(output_dir)
        raw = {kind: _load(path) for kind, path in json_files.items()}
        symbol = identity.fund_symbol
        meta = {
            "基金代码": symbol,
            "基金简称": identity.fund_name,
            "基金经理": identity.manager_name,
        }

        scale = _root_payload(raw["基金规模"], symbol)
        config = _root_payload(raw["基金配置"], symbol)
        features = _root_payload(raw["配置特征"], symbol)
        preferences = _root_payload(raw["配置偏好"], symbol)
        tracking = _root_payload(raw["配置跟踪"], symbol)
        annual = _root_payload(raw["年度盈亏"], symbol)
        quarterly = _root_payload(raw["季度盈亏"], symbol)
        cumulative = _root_payload(raw["累计盈亏"], symbol)
        turnover = _root_payload(raw["基金换手"], symbol)

        annual_values = {
            year: value
            for year, value in annual.get("年度盈亏(万元)", {}).items()
            if _meaningful(value)
        }
        valid_years = {str(year)[:4] for year in annual_values}
        quarterly_values = {
            period: value
            for period, value in quarterly.get("季度盈亏(万元)", {}).items()
            if str(period)[:4] in valid_years and _meaningful(value)
        }
        report_payload = next(iter(raw["季度运作报告"].values()), {})
        if not isinstance(report_payload, dict):
            report_payload = {}

        feature_rows = features.get("配置特征", [])
        part5_rows = [
            {
                "报告期": row.get("报告期"),
                "股票仓位": row.get("股票仓位"),
                "前五大行业集中度": row.get("前五大行业集中度"),
                "前十大个股集中度": row.get("前十大个股集中度"),
                "占比大于1%个股数": row.get("占比大于1%个股数"),
            }
            for row in feature_rows
        ]
        part6_rows = [
            {
                "报告期": row.get("报告期"),
                "加权平均PE": row.get("加权平均PE"),
                "加权平均PB": row.get("加权平均PB"),
                "加权平均SIZE": row.get("加权平均SIZE"),
                "加权平均ROE": row.get("加权平均ROE"),
                "加权平均股息率": row.get("加权平均股息率"),
                "指标覆盖": row.get("指标覆盖", {}),
                "披露口径": row.get("披露口径"),
            }
            for row in feature_rows
        ]
        full_cumulative = cumulative.get("累计盈亏(万元)", {}).get("全阶段累计", {})
        annual_industry = {
            year: {key: value.get(key, {}) for key in ("大类", "一级")}
            for year, value in annual_values.items()
        }
        annual_stocks = {
            year: value.get("个股", {})
            for year, value in annual_values.items()
            if value.get("个股")
        }
        parts: dict[str, dict[str, Any]] = {
            "part2": {**meta, "规模变动(亿元)": scale.get("规模变动(亿元)", {})},
            "part3": {**raw["数据看板"], **meta},
            "part4": {
                **meta,
                "年度列表": list(annual_values),
                "行业个股映射": config.get("行业个股映射", {}),
                "年度盈亏(万元)": annual_values,
                "季度盈亏(万元)": quarterly_values,
                "季度运作报告": {
                    "source_file": Path(json_files["季度运作报告"]).name,
                    "periods": report_payload,
                },
            },
            "part5": {
                **meta,
                "年度换手(倍)": turnover.get("年度换手(倍)", {}),
                "仓位与集中度": part5_rows,
            },
            "part6": {**meta, "估值与盈利": part6_rows},
            "part7": {
                **meta,
                "行业配置": {
                    key: config.get(key, []) for key in ("大类板块", "一级行业", "二级行业")
                },
                "行业配置偏好": preferences.get("行业前五出现次数", []),
                "行业累计盈亏(万元)": {
                    key: full_cumulative.get(key, {}) for key in ("大类", "一级")
                },
                "年度行业盈亏(万元)": annual_industry,
            },
            "part8": {
                **meta,
                "重仓个股": config.get("重仓个股", []),
                "个股配置偏好": preferences.get("个股前十出现次数", []),
                "个股累计盈亏(万元)": full_cumulative.get("个股", {}),
                "年度个股盈亏(万元)": annual_stocks,
                "配置跟踪": tracking.get("配置跟踪", {}),
            },
        }
        for part in ("part5", "part6", "part7", "part8"):
            parts[part]["最新季度快照"] = features.get("最新季度快照", {})
        for part in parts:
            parts[part]["_metadata"] = raw["基金配置"].get("_metadata", {})
        parts["part9"] = self._build_part9(identity, parts["part8"], parts["part4"], as_of)
        parts["part10"] = {
            **meta,
            "part": "part10",
            "素材说明": "Part10 仅对核心原始数据和公开交流材料做来源包装，不做语义摘要。",
            "sources": {
                "基金配置": raw["基金配置"],
                "季度盈亏": raw["季度盈亏"],
                "年度盈亏": raw["年度盈亏"],
                "季度运作报告": raw["季度运作报告"],
                "公开路演交流": [
                    {
                        "source_file": path.name,
                        "content_type": "text",
                        "content": path.read_text(encoding="utf-8-sig"),
                    }
                    for path in material_files or []
                    if path.is_file()
                ],
            },
        }

        mapping: dict[str, str] = {}
        checks = []
        for part, payload in parts.items():
            self._validate(part, payload, identity, as_of)
            text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
            path = self.boundary.write_text(output_dir / PART_FILENAMES[part], text + "\n")
            mapping[part] = str(path)
            checks.append(
                {
                    "part": part,
                    "path": str(path),
                    "sha256": sha256_file(path),
                    "bytes": path.stat().st_size,
                    "status": "valid",
                }
            )
        render_data_dir = self._write_render_data(identity, output_dir, as_of)
        return {
            "files": mapping,
            "checks": checks,
            "render_data_dir": str(render_data_dir),
            "source_hashes": {
                kind: sha256_file(Path(path)) for kind, path in json_files.items()
            },
            "valid": True,
        }

    def _write_render_data(
        self, identity: FundIdentity, output_dir: Path, as_of: date
    ) -> Path:
        render_data_dir = self.boundary.ensure_dir(output_dir / "render-data")
        connection = duckdb.connect(str(self.database_path), read_only=True)
        try:
            from fund_research_agent.legacy_port.fund_json.lake import LakeRepository
            repo = LakeRepository(self.database_path, as_of=as_of)
            try:
                nav_rows = [(stamp.date(), value) for stamp, value in repo.nav(identity.fund_symbol).items()]
            finally:
                repo.close()
            index_rows = connection.execute(
                "SELECT trade_date,close FROM index_bars "
                "WHERE symbol='000300.SH' AND trade_date<=? AND close IS NOT NULL "
                "QUALIFY row_number() OVER (PARTITION BY trade_date ORDER BY fetched_at DESC,source)=1 ORDER BY trade_date",
                [as_of],
            ).fetchall()
        finally:
            connection.close()

        nav_buffer = StringIO()
        nav_writer = csv.writer(nav_buffer)
        nav_writer.writerow(["日期", identity.fund_name])
        nav_writer.writerows(
            (row[0].isoformat(), row[1]) for row in nav_rows if row[0] and row[1] is not None
        )
        index_buffer = StringIO()
        index_writer = csv.writer(index_buffer)
        index_writer.writerow(["symbol", "name", "fetch_time", "date", "close"])
        index_writer.writerows(
            ("000300.SH", "沪深300", "", row[0].isoformat(), row[1])
            for row in index_rows
            if row[0] and row[1] is not None
        )
        self.boundary.write_text(
            render_data_dir / "基金复权净值数据.csv", nav_buffer.getvalue()
        )
        self.boundary.write_text(render_data_dir / "000300_SH.csv", index_buffer.getvalue())
        return render_data_dir

    def _build_part9(
        self,
        identity: FundIdentity,
        part8: dict[str, Any],
        part4: dict[str, Any],
        as_of: date,
    ) -> dict[str, Any]:
        pnl = [
            (name, _safe_number(value))
            for name, value in part8.get("个股累计盈亏(万元)", {}).items()
        ]
        gains = sorted((item for item in pnl if item[1] > 0), key=lambda item: item[1], reverse=True)[:5]
        losses = sorted((item for item in pnl if item[1] < 0), key=lambda item: item[1])[:5]
        holdings_by_name = _stock_name_index(part8.get("重仓个股", []))
        cases = []
        connection = duckdb.connect(str(self.database_path), read_only=True)
        try:
            for group, selected in (("盈利前五", gains), ("亏损前五", losses)):
                for rank, (name, value) in enumerate(selected, start=1):
                    holdings = holdings_by_name.get(name, [])
                    codes = sorted({str(row.get("股票代码", "")) for row in holdings if row.get("股票代码")})
                    periods = sorted(str(row.get("报告期", "")) for row in holdings if row.get("报告期"))
                    start_year = max(identity.manager_tenure_start.year if identity.manager_tenure_start else 2014, int(periods[0][:4]) - 1 if periods else 2014)
                    end_year = min(as_of.year, int(periods[-1][:4]) + 1 if periods else as_of.year)
                    prices, finance = self._stock_series(
                        connection, codes, start_year, end_year, as_of
                    )
                    annual = [
                        {"年份": year, "盈亏(万元)": _safe_number(values[name])}
                        for year, values in sorted(part8.get("年度个股盈亏(万元)", {}).items())
                        if name in values
                    ]
                    quarterly = []
                    for quarter, values in sorted(part4.get("季度盈亏(万元)", {}).items()):
                        stock_pnl = values.get("个股", {}) if isinstance(values, dict) else {}
                        if name in stock_pnl:
                            quarterly.append({"季度": quarter, "季度标签": quarter, "盈亏(万元)": _safe_number(stock_pnl[name])})
                    trajectory = [
                        {
                            **row,
                            "报告期标签": self._period_label(str(row.get("报告期", ""))),
                        }
                        for row in sorted(holdings, key=lambda row: str(row.get("报告期", "")))
                    ]
                    cases.append(
                        {
                            "分组": group,
                            "排名": rank,
                            "股票名称": name,
                            "股票代码": codes,
                            "累计盈亏(万元)": round(value, 2),
                            "匹配": {
                                "匹配名称": name,
                                "有持仓数据": bool(trajectory),
                                "有财务指标": bool(finance),
                                "有月度价格": bool(prices),
                            },
                            "年度盈亏(万元)": annual,
                            "季度盈亏(万元)": quarterly,
                            "持仓摘要": self._holding_summary(trajectory),
                            "持仓轨迹": trajectory,
                            "财务指标": finance,
                            "月度价格": prices,
                        }
                    )
        finally:
            connection.close()
        return {
            "基金代码": identity.fund_symbol,
            "基金简称": identity.fund_name,
            "基金经理": identity.manager_name,
            "基金名称": f"{identity.manager_name}_{identity.fund_name}",
            "数据口径": "持仓轨迹仅代表定期报告中的重仓披露，不代表完整买卖流水。",
            "筛选规则": "按个股累计盈亏选择盈利前五和亏损前五。",
            "数据覆盖": {
                "案例股数量": len(cases),
                "盈利案例数量": len(gains),
                "亏损案例数量": len(losses),
                "有持仓数据数量": sum(item["匹配"]["有持仓数据"] for item in cases),
                "有财务指标数量": sum(item["匹配"]["有财务指标"] for item in cases),
                "有月度价格数量": sum(item["匹配"]["有月度价格"] for item in cases),
            },
            "案例个股": cases,
        }

    @staticmethod
    def _stock_series(
        connection: duckdb.DuckDBPyConnection,
        codes: list[str],
        start_year: int,
        end_year: int,
        as_of: date,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not codes:
            return [], []
        placeholders = ",".join("?" for _ in codes)
        price_rows = connection.execute(
            f"SELECT max(trade_date),arg_max(close,trade_date) FROM daily_bars "
            f"WHERE symbol IN ({placeholders}) AND year(trade_date) BETWEEN ? AND ? "
            "AND trade_date<=? "
            "GROUP BY year(trade_date),month(trade_date) ORDER BY 1",
            [*codes, start_year, end_year, as_of],
        ).fetchall()
        valuation_rows = connection.execute(
            f"SELECT max(trade_date),arg_max(close,trade_date),arg_max(pe_ttm,trade_date),"
            f"arg_max(pb,trade_date),arg_max(total_mv,trade_date) "
            f"FROM valuation_metrics v LEFT JOIN daily_bars b USING(symbol,trade_date) "
            f"WHERE v.symbol IN ({placeholders}) AND year(v.trade_date) BETWEEN ? AND ? "
            "AND v.trade_date<=? "
            "GROUP BY year(trade_date),quarter(trade_date) ORDER BY 1",
            [*codes, start_year, end_year, as_of],
        ).fetchall()
        prices = [
            {"日期": row[0].isoformat(), "收盘价": round(float(row[1]), 4)}
            for row in price_rows
            if row[0] and row[1] is not None and row[1] > 0
        ]
        finance = [
            {
                "报告期": row[0].strftime("%Y%m%d"),
                "日期": row[0].isoformat(),
                "收盘价": round(float(row[1]), 4) if row[1] is not None else None,
                "PE": round(float(row[2]), 4) if row[2] is not None else None,
                "PB": round(float(row[3]), 4) if row[3] is not None else None,
                "SIZE": round(float(row[4]) / 1e8, 4) if row[4] is not None else None,
                "ROE": None,
                "股息率": None,
            }
            for row in valuation_rows
            if row[0]
        ]
        return prices, finance

    @staticmethod
    def _period_label(period: str) -> str:
        if len(period) == 8:
            quarter = {"0331": "Q1", "0630": "Q2", "0930": "Q3", "1231": "Q4"}.get(period[4:])
            if quarter:
                return period[:4] + quarter
        return period

    @classmethod
    def _holding_summary(cls, trajectory: list[dict[str, Any]]) -> dict[str, Any]:
        if not trajectory:
            return {
                "持仓口径": "仅统计定期报告披露的重仓个股记录。",
                "持仓期数": 0,
                "重仓记录期数": 0,
            }
        peak = max(trajectory, key=lambda row: _safe_number(row.get("占基金净值比")))
        return {
            "持仓口径": "仅统计定期报告披露的重仓个股记录。",
            "首次进入前十大": trajectory[0]["报告期标签"],
            "最后出现在前十大": trajectory[-1]["报告期标签"],
            "持仓期数": len(trajectory),
            "重仓记录期数": len(trajectory),
            "峰值报告期": peak["报告期标签"],
            "峰值占比": peak.get("占基金净值比"),
            "峰值市值(万元)": peak.get("持仓市值(万元)"),
        }

    @staticmethod
    def _validate(
        part: str,
        payload: dict[str, Any],
        identity: FundIdentity,
        as_of: date,
    ) -> None:
        expected = {
            "基金代码": identity.fund_symbol,
            "基金简称": identity.fund_name,
            "基金经理": identity.manager_name,
        }
        for key, value in expected.items():
            if str(payload.get(key, "")).strip() != value:
                raise ValueError(f"{part} 身份不一致: {key}={payload.get(key)!r}, expected={value!r}")
        if part != "part10" and not _meaningful(
            {key: value for key, value in payload.items() if key not in expected and key != "_metadata"}
        ):
            raise ValueError(f"{part} 没有可用数据")
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
        import re
        def check(value, key=""):
            if isinstance(value, dict):
                for name, item in value.items():
                    check(str(name), "key")
                    check(item, str(name))
            elif isinstance(value, list):
                for item in value:
                    check(item, key)
            elif isinstance(value, str) and (key == "key" or any(word in key for word in ("日期", "报告期", "published_date", "announce_date", "as_of"))):
                for match in re.finditer(r"(?<!\d)(20\d{2})[-/]?(\d{2})[-/]?(\d{2})(?!\d)", value):
                    try:
                        actual = date(*map(int, match.groups()))
                    except ValueError:
                        continue
                    if actual > as_of:
                        raise ValueError(f"{part} 包含超过 as_of 的日期: {actual}")
                quarter = re.fullmatch(r"(20\d{2})(?:Q([1-4])|年([一二三四])季报)", value)
                if quarter:
                    q = int(quarter[2]) if quarter[2] else "一二三四".index(quarter[3]) + 1
                    actual = date(int(quarter[1]), q * 3, 31 if q in (1, 4) else 30)
                    if actual > as_of:
                        raise ValueError(f"{part} 包含超过 as_of 的报告期: {value}")
        check(payload)
