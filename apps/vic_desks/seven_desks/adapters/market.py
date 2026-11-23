"""Read-only adapters for chips, sentiment and valuation frozen at 2026-11-09.

Only bounded snapshots and precomputed aggregates are queried. Stock history reads
push a symbol predicate into the yearly Parquet files; no request aggregates the
full stock history. All paths are resolved by the shared controlled Store.
"""
from __future__ import annotations

import calendar
import bisect
import json
import math
import re
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from seven_desks.daily import component_date, cutoff_for, observation, rows as daily_rows

CUTOFF = "2026-11-09"
from seven_desks.settings import FROZEN_VERSION
from seven_desks.store import version_root

DIMENSIONS_FILE = version_root(FROZEN_VERSION) / "data/market/ui-dimensions.json"
TABS = {
    "chips": [("overview", "总览"), ("industries", "行业筹码"), ("screen", "筹码筛选"), ("stock", "个股筹码")],
    "sentiment": [("overview", "温度总览"), ("dimensions", "维度分解"), ("indicators", "指标明细"), ("events", "信号事件"), ("replay", "历史复盘")],
    "valuation": [("overview", "估值总览"), ("index", "指数详情"), ("returns", "估值与收益"), ("structure", "结构与对照")],
}
TITLES = {"chips": "全 A 筹码结构", "sentiment": "情绪温度计", "valuation": "市场估值"}
SIGNALS = {"break_up": "突破筹码峰", "low_dense": "低位单峰密集", "washout": "获利盘出清", "release": "套牢解放", "all_profit": "全面获利", "high_div": "高位发散", "winner_drop": "获利盘骤降", "break_down": "跌破筹码峰"}
SIGNAL_RULES = {
    "break_up": "价格上穿前日95%成本线；前日90%集中度<15，前日获利<85%",
    "low_dense": "90%集中度<12，成本偏离在±5%内，一年价格位置≤30%",
    "washout": "获利<3%，且20个交易日前获利>30%", "release": "获利≥70%，且10个交易日前获利<20%",
    "all_profit": "获利>97%，且20个交易日前获利<60%", "high_div": "90%集中度>45，获利>80%，一年价格位置≥80%",
    "winner_drop": "5个交易日前获利>70%，且下降≥50个百分点",
    "break_down": "价格下穿前日5%成本线；前日90%集中度<15，前日获利>15%",
}
INDICES = {"000001.SH": "上证指数", "399001.SZ": "深证成指", "000016.SH": "上证50", "000300.SH": "沪深300", "000905.SH": "中证500", "399006.SZ": "创业板指", "000510.SH": "中证A500", "000852.SH": "中证1000", "000985.CSI": "中证全指", "932000.CSI": "中证2000", "000688.SH": "科创50", "000922.CSI": "中证红利"}
DIMS = {"valuation": "估值", "leverage": "杠杆", "volume": "量能", "breadth": "宽度", "capital": "资金", "volatility": "波动"}
INDICATORS = dict(zip(
    ["A1", "A2", "A3", "B1", "B2", "B3", "C1", "C2", "C3", "D1", "D2", "D3", "D4", "D5", "D6", "D7", "E1", "E2", "E3", "E4", "F1", "F2", "F3"],
    ["沪深300 PE(TTM)", "沪深300 PB", "全A PE(TTM)中位数", "融资余额", "融资买入占成交额5日", "融资余额20日变化", "两市换手率5日", "个股换手中位数5日", "量能放大比20/250日", "站上20日线占比", "站上60日线占比", "站上250日线占比", "60日净新高", "上涨成交额占比10日", "涨停−跌停", "昨日涨停今日均涨", "新发权益基金只数/月", "宽基ETF净申购20日", "产业资本净增持20日", "偏股基金股票仓位", "A股指数偏离年线", "下跌波动率20日", "VIX恐慌指数"],
))
EVENTS = {"top_breadth_divergence_proxy": "顶部宽度背离(代理)", "bottom_breadth_divergence_proxy": "底部宽度背离(代理)", "capitulation": "投降日", "breadth_thrust": "宽度推力", "leverage_flush": "杠杆出清", "etf_support": "托底资金", "ice_resonance": "冰点共振", "boil_resonance": "沸点共振"}
METRICS = {"pe_ttm": "PE(TTM)", "pb": "PB", "dividend_yield": "股息率"}
LABELS = {"is_st": "ST标记(名称快照)", "group": "分组", "selected_percentile": "所选窗口分位", "start_close": "起始点位/后复权价", "end_close": "结束点位", "end_date_5d": "T+5日期", "end_close_5d": "T+5后复权价", "end_date_20d": "T+20日期", "end_close_20d": "T+20后复权价", "return_5d": "T+5价格收益", "return_20d": "T+20价格收益", "return_60d": "T+60价格收益", "return_120d": "T+120价格收益",
    "symbol": "代码", "name": "名称", "trade_date": "数据日", "event_date": "触发日", "industry_name": "行业", "industry_code": "行业代码", "sample_count": "有效样本数", "close": "收盘价", "winner_rate": "获利比例(%)", "winner_percentile": "获利120日分位", "wr_change5": "获利5日变化(pp)", "wr_change20": "获利20日变化(pp)", "weight_avg": "平均成本", "cost_deviation": "成本偏离(%)", "concentration90": "90%集中度(%)", "concentration70": "70%集中度(%)", "con90_change5": "90%集中度5日变化(pp)", "con70_change5": "70%集中度5日变化(pp)", "position_range": "供应商区间位置(%)", "position_1y": "一年价格位置(%)", "trailing_return_20d": "过去20日涨跌幅", "turnover_rate": "换手率(%)", "float_mv": "流通市值(元)", "signals": "触发信号", "temperature": "温度", "slow_temperature": "慢变量", "dimension_count": "有效维数", "ice_dimensions": "冰点维数", "boil_dimensions": "沸点维数", "leading": "前瞻", "coincident": "同步", "lagging": "滞后", "raw_value": "原值", "percentile": "原值分位", "heat_score": "方向校正后热度", "indicator_id": "指标代码", "indicator": "指标", "source_date": "来源截止日", "open": "开盘价", "high": "最高价", "low": "最低价", "volume": "成交量", "amount": "成交额", "source": "来源", "availability": "可得日口径", "dimension": "维度", "timing_type": "类型", "weight": "项权重", "window_sessions": "窗口(交易日)", "minimum_samples": "最少样本", "reverse": "低值更热", "slow": "计入慢变量", "pit_status": "时点质量", "heat_change20": "热度20日变化(pp)", "event_type": "信号", "horizon": "持有交易日", "mean": "均值", "median": "中位数", "q25": "25分位", "q75": "75分位", "win_rate": "胜率", "band": "温度档位", "year": "年度", "price_return": "价格收益", "eps_growth": "盈利变化(代理)", "pe_change": "估值变化", "dividend_yield_proxy": "期末现金股息率", "identity_residual": "乘法恒等式残差", "index_symbol": "指数代码", "index_name": "指数", "pe_ttm": "PE(TTM)", "pb": "PB", "dividend_yield": "现金股息率(%)", "below_book_ratio": "破净率", "positive_pe_median": "正PE中位数", "small_large_pe_ratio": "中证1000/沪深300 PE", "growth_value_pe_ratio": "创业板/上证50 PE", "bucket": "分层", "stock_count": "股票数", "amount20": "近20日平均成交额", "static_pe": "静态PE", "static_pe_pct_3y": "静态PE三年分位", "pe_source": "PE来源", "pb_source": "PB来源", "dividend_source": "股息率来源", "earnings_mv_coverage": "盈利市值覆盖率", "equity_mv_coverage": "净资产市值覆盖率", "price_weight_coverage": "价格权重覆盖率", "dividend_mv_coverage": "股息市值覆盖率", "snapshot_date": "成分快照日", "coverage_ratio": "市值数据覆盖率", "sample_group": "样本组", "n5": "完整T+5样本", "n20": "完整T+20样本", "mean_excess_5d": "T+5平均超额", "mean_excess_20d": "T+20平均超额", "median_excess_20d": "T+20超额中位数", "win_rate_20d": "T+20超额胜率", "trigger_count": "触发次数", "quality_status": "质量状态", "horizon_months": "期限(月)", "forward_12m_annualized": "未来12月收益", "forward_36m_annualized": "未来36月年化收益", "pe_band_5y": "PE五年分位档", "rule": "计算规则", "label": "项目", "value": "数值", "score": "维度得分", "contribution": "温度贡献", "dimension_x": "维度X", "dimension_y": "维度Y", "correlation": "相关系数", "as_of_date": "计算截止日", "requested_node": "历史节点", "mean_excess": "平均超额", "triggers": "触发总数", "board": "板块", "size_bucket": "市值段", "metric": "指标", "unit": "单位", "missing_reason": "缺失原因", "actual_start": "实际起日", "actual_end": "实际止日", "requested_start": "请求起日", "requested_end": "请求止日", "total": "总条数",
}
RATIO_FIELDS = {"return_5d", "return_20d", "return_60d", "return_120d", "trailing_return_20d", "mean", "median", "q25", "q75", "win_rate", "price_return", "eps_growth", "pe_change", "dividend_yield_proxy", "identity_residual", "below_book_ratio", "coverage_ratio", "price_weight_coverage", "earnings_mv_coverage", "equity_mv_coverage", "dividend_mv_coverage", "mean_excess_5d", "mean_excess_20d", "median_excess_20d", "win_rate_20d", "forward_12m_annualized", "forward_36m_annualized"}
LABELS['position_range'] = '本地近一年成本区间位置(%)'


@lru_cache(maxsize=8)
def _load_dimensions(path):
    file = Path(path)
    if not file.exists():
        return {}, {}
    obj = json.loads(file.read_text())
    return {x["symbol"]: x for x in obj["stocks"]}, obj["industries"]


def _dimensions(store=None):
    # The parent snapshot builder may freeze this small, read-only prepared
    # identity artifact beside the Parquet inputs. Prefer that immutable copy.
    if store is not None and hasattr(store, "path"):
        frozen = Path(store.path("market/ui-dimensions.json"))
        if frozen.exists():
            return _load_dimensions(str(frozen))
        return {}, {}
    return _load_dimensions(str(DIMENSIONS_FILE))


def _select(key, label, options, value):
    return {"key": key, "label": label, "type": "select", "options": [{"value": str(v), "label": str(t)} for v, t in options], "value": str(value)}


def _input(key, label, value="", type="text"):
    return {"key": key, "label": label, "type": type, "value": value}


def _controls(board):
    common = [_input("date", "观察日(非交易日取此前最近日)", CUTOFF)]
    if board == "chips":
        return common + [_select("mode", "聚合口径", [("w", "流通市值加权"), ("med", "中位数"), ("eq", "等权")], "w"), _select("range", "历史区间", [("1m", "1月"), ("3m", "3月"), ("6m", "6月"), ("1y", "1年"), ("all", "全部")], "1y")]
    if board == "sentiment":
        return common + [_select("range", "历史区间", [("1y", "1年"), ("3y", "3年"), ("all", "全部")], "3y")] + [_input("w_" + k, v + "权重", 1, "number") for k, v in DIMS.items()]
    return common + [_select("win", "历史分位窗口", [(3, "3年"), (5, "5年"), (10, "10年"), (20, "20年")], 10)]


def metadata(board):
    if board not in TABS:
        raise ValueError(f"Unknown market board: {board}")
    controls = _controls(board)
    return {"id": board, "title": TITLES[board], "tabs": [{"id": k, "label": v} for k, v in TABS[board]], "defaults": {"tab": TABS[board][0][0], **{c["key"]: c["value"] for c in controls}}, "controls": controls, "as_of": CUTOFF, "description": "正式湖只读计算产物；原站未公开的边界采用已记录候选口径，非原站数值对账。"}


def _day(value, cutoff=CUTOFF):
    try:
        parsed = date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        parsed = date.fromisoformat(cutoff)
    return min(parsed, date.fromisoformat(cutoff)).isoformat()


def _date_str(value):
    return str(value)[:10] if value is not None else ""


def _clean(value):
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _section(id, title, kind, rows, keys, *, x=None, y=None, value=None, series=None, missing=None, action=False, quality=None):
    result = {"id": id, "title": title, "kind": kind, "columns": [{"key": k, "label": LABELS.get(k, k), **({"format": "percent"} if k in RATIO_FIELDS else {})} for k in keys], "rows": rows}
    if quality:
        result['quality'] = quality
    if x:
        result["x"] = x
    if y:
        result["y"] = y
    if value:
        result["value"] = value
    if series:
        result["series"] = [{"key": k, "label": LABELS.get(k, k)} for k in series]
    if not rows:
        result["missing_reason"] = missing or "该观察日与筛选条件下没有可用记录；未使用模拟数据填充。"
    elif missing:
        result["missing_reason"] = missing
    if action:
        result["action"] = {"type": "entity", "key": "symbol"}
    return result


def _read(store, board, file, columns=None, where=None, args=None, optional=False):
    rel = f"market/{board}/{file}.parquet"
    if optional:
        try:
            if hasattr(store, "path") and not Path(store.path(rel)).exists():
                return []
        except FileNotFoundError:
            return []
    return store.read(rel, columns=columns, where=where, params=args)



def _extra(store, file, columns=None, where=None, args=None):
    """Read only supplements that the version builder froze with this Store."""
    rel = f"market/ui-extra/{file}.parquet"
    if hasattr(store, "path") and not Path(store.path(rel)).exists():
        return []
    return store.read(rel, columns=columns, where=where, params=args)


def _market_revision(store, name, where=None, args=()):
    rel = f"revision/market/phase3-{name}.parquet"
    if not Path(store.path(rel)).is_file():
        return []
    return store.read(rel, where=where, params=args)


def _revised_sentiment_display(store, end, indicator=None):
    if indicator is None:
        where, args = "trade_date=?", [end]
    else:
        where, args = "indicator_id=? AND trade_date<=?", [indicator, end]
    display = _extra(store, "sentiment-display", where=where, args=args)
    if indicator not in (None, "VIX_TERM"):
        return display
    # Do not borrow the latest valid pair when one input is missing on the
    # immediately preceding US observation date.
    revised = _market_revision(store, "vix-term", "trade_date=?" if indicator is None else "trade_date<=?", [end])
    if not revised:
        return display
    term = {_date_str(r["trade_date"]): r for r in revised}
    for item in display:
        row = term.get(_date_str(item["trade_date"]))
        if item.get("indicator_id") != "VIX_TERM" or row is None:
            continue
        item.update(raw_value=row["raw_value"], source_date=row["source_date"],
                    source="Cboe VIX3M / FRED VIXCLS", unit="倍",
                    availability="两指数同一美股日期，严格早于A股观察日；仅展示",
                    missing_reason=None if row["raw_value"] is not None else "前一个美股观测日缺同日两期配对")
    return display


def _announced_amounts(store, sessions, day):
    if day < "2026-10-09" or len(sessions) < 20 or sessions[-20] < "2026-10-09":
        return []
    start = sessions[-20]
    rows = _market_revision(store, "holder-amounts", "announce_date>=? AND announce_date<=?", [start, day])
    if not rows:
        return []
    count = sum(row["event_count"] for row in rows)
    with_price = sum(row["with_price_count"] for row in rows)
    total = {}
    for key in ("observed_increase", "observed_decrease"):
        valid = [row[key] for row in rows if row[key] is not None]
        total[key] = sum(valid) if valid else None
    return [{"window_start": start, "window_end": day, "source_as_of": max(row["announce_date"] for row in rows),
             "event_count": count,
             "with_price_count": with_price, "coverage_ratio": with_price / count,
             **total}]


def _visible_prices(store, symbol, end):
    return _extra(store, "index-prices", where="symbol=? AND trade_date<=?", args=[symbol, end])


def _correlations(daily, end):
    tail = _through(daily, end)[-250:]
    out = []
    for a in DIMS:
        for b in DIMS:
            pairs = [(r.get("dimension_" + a), r.get("dimension_" + b)) for r in tail]
            pairs = [(x, y) for x, y in pairs if _num(x) is not None and _num(y) is not None]
            n = len(pairs)
            correlation = None
            if n >= 2:
                mx, my = sum(x for x, _ in pairs) / n, sum(y for _, y in pairs) / n
                cov = sum((x - mx) * (y - my) for x, y in pairs)
                var_x = sum((x - mx) ** 2 for x, _ in pairs)
                var_y = sum((y - my) ** 2 for _, y in pairs)
                if var_x and var_y:
                    correlation = cov / math.sqrt(var_x * var_y)
            out.append({"dimension_x": DIMS[a], "dimension_y": DIMS[b], "correlation": correlation, "sample_count": n, "as_of_date": end})
    return out


def _quantile(values, p):
    values = sorted(values)
    if not values:
        return None
    i = (len(values) - 1) * p
    a, b = math.floor(i), math.ceil(i)
    return values[a] + (values[b] - values[a]) * (i - a)


def _event_paths(events, prices, dates, end, event_type):
    # Market-session calendar is authoritative. Missing price observations never
    # shorten a horizon or get carried across a suspended/missing session.
    dates = [d for d in dates if d <= end]
    indices = {d: i for i, d in enumerate(dates)}
    close = {_date_str(r["trade_date"]): r["close"] for r in prices if _num(r.get("close")) is not None}
    starts = {_date_str(r["event_date"]) for r in events if r["event_type"] == event_type and _date_str(r["event_date"]) <= end}
    out = []
    for h in range(121):
        returns = []
        for d in starts:
            i = indices.get(d)
            if i is None or i + h >= len(dates):
                continue
            a, b = close.get(d), close.get(dates[i + h])
            if a and b is not None:
                returns.append(b / a - 1)
        if returns:
            out.append({"event_type": event_type, "horizon": h, **_stats(returns), "q25": _quantile(returns, .25), "q75": _quantile(returns, .75), "as_of_date": end})
    return out


def _band_returns(daily, prices, end):
    visible = _through(daily, end)
    close = {_date_str(r["trade_date"]): r["close"] for r in prices if _num(r.get("close")) is not None}
    groups = {(band, h): [] for band in range(5) for h in (5, 20, 60, 120)}
    for i, row in enumerate(visible):
        t = _num(row.get("temperature"))
        if t is None:
            continue
        band = 0 if t < 20 else 1 if t < 35 else 2 if t < 65 else 3 if t < 80 else 4
        a = close.get(_date_str(row["trade_date"]))
        if not a:
            continue
        for h in (5, 20, 60, 120):
            if i + h < len(visible):
                b = close.get(_date_str(visible[i + h]["trade_date"]))
                if b is not None:
                    groups[band, h].append(b / a - 1)
    return [{"band": band, "horizon": h, **_stats(values), "as_of_date": end} for (band, h), values in groups.items()]

def _through(rows, end, key="trade_date"):
    return sorted([dict(r) for r in rows if _date_str(r.get(key)) <= end], key=lambda r: _date_str(r.get(key)))


def _history(rows, end, range):
    rows = _through(rows, end)
    n = {"1m": 21, "3m": 63, "6m": 126, "1y": 252, "3y": 756, "10y": 2520}.get(range)
    return rows[-n:] if n else rows


def _num(value, default=None):
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def _int(value, default, lo=1, hi=100000):
    n = _num(value, default)
    return max(lo, min(hi, int(n)))


def _paged(rows, params, default_sort="float_mv", default_desc=True):
    sort = str(params.get("sort", default_sort))
    aliases = {"wr": "winner_rate", "dev": "cost_deviation", "c90": "concentration90", "c70": "concentration70", "mv": "float_mv", "pos": "position_range", "pos1y": "position_1y"}
    sort = aliases.get(sort, sort)
    if rows and sort not in rows[0]:
        sort = default_sort
    desc = str(params.get("order", "desc" if default_desc else "asc")) == "desc"
    valid = [r for r in rows if r.get(sort) is not None]
    missing = [r for r in rows if r.get(sort) is None]
    valid.sort(key=lambda r: (r.get(sort), str(r.get("symbol", ""))), reverse=desc)
    ordered = valid + missing
    page_size = _int(params.get("page_size"), 50, hi=200)
    pages = max(1, math.ceil(len(rows) / page_size))
    page = _int(params.get("page"), 1, hi=pages)
    return ordered[(page - 1) * page_size:page * page_size], {"page": page, "page_size": page_size, "total": len(rows), "pages": pages, "sort": sort, "order": "desc" if desc else "asc"}


def _page_controls(params, sorts):
    return [_select("sort", "排序字段", [(k, LABELS.get(k, k)) for k in sorts], params.get("sort", sorts[0])), _select("order", "排序", [("desc", "降序"), ("asc", "升序")], params.get("order", "desc")), _input("page", "页码", params.get("page", 1), "number"), _select("page_size", "每页条数", [(20, "20"), (50, "50"), (100, "100"), (200, "200")], params.get("page_size", 50))]


def _reply(board, tab, params, sections, controls=(), warnings=(), observed=None, cutoff=CUTOFF):
    observed = observed or _day(params.get("trade_date", cutoff), cutoff)
    base = _controls(board)
    for c in base:
        if c["key"] == "date":
            c["value"] = cutoff
        if c["key"] in params:
            c["value"] = params[c["key"]]
    base += list(controls)
    return _clean({"title": f"{TITLES[board]} · {dict(TABS[board]).get(tab, tab)}", "description": f"只读冻结数据截至 {cutoff}；本页观察日 {observed}。空值保留，不用录制值补齐。", "sections": sections, "controls": base, "warnings": list(warnings), "as_of": observed, "data_date": observed})


def _chip_col(metric, mode):
    suffix = {"med": "_median", "eq": "_equal", "equal": "_equal", "median": "_median"}.get(mode, "")
    return metric + suffix


def _chip_snapshot(store, day):
    if day < "2018-02-01":
        return []
    return _without_forward(_read(store, "chips", f"daily-{day[:4]}", where="trade_date = ?", args=[day]))


def _chip_history(store, symbol, end):
    if end < "2018-02-01":
        return []
    bucket = symbol.split('.')[0][-2:]
    indexed = Path(store.path(f"market/chip-index/bucket={bucket}"))
    # The inherited prebuilt symbol index does not contain newly released costs.
    if indexed.is_dir() and not Path(store.path("revision/market/chip-daily-extended.json")).is_file():
        return _without_forward(store.query("SELECT * EXCLUDE (bucket) FROM read_parquet(?, union_by_name=true) WHERE symbol=? AND trade_date<=? ORDER BY trade_date", [str(indexed/'*.parquet'), symbol, end]))
    paths = [str(store.path(f"market/chips/daily-{year}.parquet")) for year in range(2018, int(end[:4]) + 1)]
    return _without_forward(store.query("SELECT * FROM read_parquet(?, hive_partitioning=false, union_by_name=true) WHERE symbol=? AND trade_date<=? ORDER BY trade_date", [paths, symbol, end]))


def _without_forward(rows):
    forbidden = {'return_5d','return_20d','return_60d','return_120d','excess_5d','excess_20d','forward20','forward60'}
    return [{k:v for k,v in row.items() if k not in forbidden} for row in rows]


def _chip_enrich(rows, day, store, market_dates):
    names, _ = _dimensions(store)
    past = {}
    if day in market_dates:
        i = market_dates.index(day)
        for offset in [5, 20]:
            if i >= offset:
                d = market_dates[i - offset]
                cols = ["symbol", "close", "hfq_close", "concentration90", "concentration70"]
                past[offset] = {r["symbol"]: r for r in _read(store, "chips", f"daily-{d[:4]}", columns=cols, where="trade_date=?", args=[d])}
    revision = 'revision/market/chip-cost-position.parquet'
    positions = {r['symbol']: r for r in store.read(revision, where='trade_date=?', params=[day])} if Path(store.path(revision)).exists() else {}
    extra = {r["symbol"]: r for r in _extra(store, f"chips/bars-{day[:4]}", columns=["symbol", "turnover_rate"], where="trade_date=?", args=[day])}
    result = []
    for original in rows:
        r = _without_forward([original])[0]
        identity = names.get(r["symbol"], {})
        r.update({k: identity.get(k) for k in ["name", "industry_name", "industry_code"]})
        r["name"] = r.get("name") or r["symbol"]
        position = positions.get(r['symbol'], {})
        r['position_range'] = position.get('position_range')
        r['cost_position_sample_count'] = position.get('sample_count')
        r['cost_position_coverage'] = position.get('coverage')
        r['cost_position_low'] = position.get('cost_low')
        r['cost_position_high'] = position.get('cost_high')
        r['cost_position_formula_id'] = position.get('formula_id', 'local_cost_range_252market_sessions')
        r['cost_position_formula_version'] = position.get('formula_version', 'v2')
        r['cost_position_sample_scope'] = position.get('sample_scope', 'last_252_market_sessions;valid_positive_ordered_cost_pairs')
        r['cost_position_missing_reason'] = position.get('missing_reason') if position else 'revision_artifact_or_security_observation_missing'
        r["turnover_rate"] = extra.get(r["symbol"], {}).get("turnover_rate")
        if r["turnover_rate"] is None and day == CUTOFF:
            r["turnover_rate"] = identity.get("turnover_rate")
        r["is_st"] = "ST" in r["name"].upper()
        r["board"] = "STAR" if r["symbol"].startswith("688") else "ChiNext" if r["symbol"].startswith("30") else "BSE" if r["symbol"].endswith(".BJ") else "Main"
        mv = _num(r.get("float_mv"))
        r["size_bucket"] = "missing" if mv is None else "small" if mv < 5e9 else "medium" if mv < 2e10 else "large"
        r["wr_change5"] = r["winner_rate"] - r["wr5"] if r.get("winner_rate") is not None and r.get("wr5") is not None else None
        r["wr_change20"] = r["winner_rate"] - r["wr20"] if r.get("winner_rate") is not None and r.get("wr20") is not None else None
        for key, out in [("concentration90", "con90_change5"), ("concentration70", "con70_change5")]:
            p = past.get(5, {}).get(r["symbol"], {}).get(key)
            r[out] = r[key] - p if p is not None and r.get(key) is not None else None
        old = past.get(20, {}).get(r["symbol"], {}).get("hfq_close")
        r["trailing_return_20d"] = r["hfq_close"] / old - 1 if old and r.get("hfq_close") else None
        r["signals"] = "、".join(label for signal, label in SIGNALS.items() if r.get(signal))
        result.append(r)
    return result


def _chip_filters(rows, params, signal_ids=None):
    query = str(params.get("q", params.get("search", ""))).strip().casefold()
    signal = str(params.get("preset", params.get("signal", "all")))
    industry = params.get("l1", params.get("industry", "all"))
    lo, hi = _num(params.get("wr_min")), _num(params.get("wr_max"))
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    con = _num(params.get("c90_max", params.get("con90_max")))
    out = []
    for r in rows:
        if signal_ids is not None and r["symbol"] not in signal_ids:
            continue
        if industry not in (None, "all", "") and r.get("industry_code") != industry:
            continue
        if params.get("board", "all") not in ("all", "", r["board"]):
            continue
        if params.get("size", "all") not in ("all", "", r["size_bucket"]):
            continue
        if str(params.get("no_st", "0")) in ("1", "true") and r["is_st"]:
            continue
        if query and query not in (r["symbol"] + r["name"]).casefold():
            continue
        wr = r.get("winner_rate")
        if lo is not None and (wr is None or wr < lo):
            continue
        if hi is not None and (wr is None or wr > hi):
            continue
        if con is not None and (r.get("concentration90") is None or r["concentration90"] > con):
            continue
        out.append(r)
    return out


CHIP_TABLE = ["symbol", "name", "industry_name", "board", "is_st", "winner_rate", "wr_change5", "wr_change20", "weight_avg", "cost_deviation", "concentration90", "concentration70", "con90_change5", "con70_change5", "position_range", "position_1y", "trailing_return_20d", "turnover_rate", "float_mv", "signals"]


def _chips(tab, params, store):
    cutoff = component_date(store, "chips", "cost_and_signals", CUTOFF)
    end = _day(params.get("trade_date", cutoff), cutoff)
    markets = _through(_read(store, "chips", "market-daily"), end)
    if not markets:
        return _reply("chips", tab, params, [_section("missing", "筹码数据", "table", [], [], missing="筹码原始覆盖从2018-02-01开始；该日以前无可用记录。")])
    day = _date_str(markets[-1]["trade_date"])
    observed = day
    dates = [_date_str(r["trade_date"]) for r in markets]
    mode = str(params.get("mode", "w"))
    metric = {"wr": "winner_rate", "dev": "cost_deviation", "c90": "concentration90", "c70": "concentration70"}.get(str(params.get("metric", "wr")), "winner_rate")
    metric_control = _select("metric", "图表指标", [("wr", "获利比例"), ("dev", "成本偏离"), ("c90", "90%集中度"), ("c70", "70%集中度")], params.get("metric", "wr"))
    sections, controls = [], []
    warnings = ["行业归属使用截止日快照回贴历史，不是历史PIT；名称为当前证券身份快照。信号和回测采用已记录候选边界，未证明与原站算法相同。", "剔ST按冻结名称中的ST标识实现；不宣称完整历史风险警示状态。OHLC和换手取当日真实来源，不延用其他日期。"]
    if tab == "overview":
        latest = dict(markets[-1])
        for k in ["winner_rate", "cost_deviation", "concentration90", "concentration70"]:
            latest[k] = latest.get(_chip_col(k, mode))
        latest["winner_percentile"] = latest.get(_chip_col("winner_rate", mode) + "_percentile_120d")
        kpis = [{"label": LABELS.get(k, k), "value": latest.get(k)} for k in ["winner_rate", "winner_percentile", "cost_deviation", "concentration90", "concentration70", "sample_count", "coverage_ratio"]]
        kpis += [{"label": "深套股票占比(0–1)", "value": latest.get("deep_trapped_ratio")}, {"label": "全面获利股票占比(0–1)", "value": latest.get("all_profit_ratio")}]
        sections.append(_section("summary", "筹码总览", "kpi", kpis, ["label", "value"]))
        hist = _history(markets, day, params.get("range", "1y"))
        chart = [{"trade_date": r["trade_date"], metric: r.get(_chip_col(metric, mode))} for r in hist]
        sections.append(_section("history", "聚合指标历史 · " + LABELS[metric], "line", chart, ["trade_date", metric], x="trade_date", series=[metric]))
        dist = _read(store, "chips", "winner-distribution", where="trade_date=?", args=[day])
        dist.sort(key=lambda r: r["bucket"])
        dk = "float_mv" if params.get("dist", "count") == "mv" else "stock_count"
        sections.append(_section("distribution", "获利比例分布", "bar", dist, ["bucket", dk], x="bucket", series=[dk]))
        seg = _read(store, "chips", "segments", where="trade_date=?", args=[day])
        medians = {}
        if mode in ("med", "median"):
            for stock in _chip_snapshot(store, day):
                symbol = stock["symbol"]
                board = "STAR" if symbol.startswith("688") else "ChiNext" if symbol.startswith("30") else "BSE" if symbol.endswith(".BJ") else "Main"
                mv = _num(stock.get("float_mv"))
                size = "missing" if mv is None else "small" if mv < 5e9 else "medium" if mv < 2e10 else "large"
                value = _num(stock.get("winner_rate"))
                if value is not None:
                    medians.setdefault((board, size), []).append(value)
        for r in seg:
            r["bucket"] = r["board"] + " / " + r["size_bucket"]
            r["winner_rate"] = r.get("winner_rate_equal" if mode in ("eq", "equal") else "winner_rate") if mode not in ("med", "median") else _quantile(medians.get((r["board"], r["size_bucket"]), []), .5)
        sections.append(_section("segments", "板块与市值段", "bar", seg, ["bucket", "sample_count", "winner_rate"], x="bucket", series=["winner_rate"]))
        industry = _read(store, "chips", "industry-daily", where="trade_date=?", args=[day])
        _, labels = _dimensions(store)
        for r in industry:
            r["industry_name"] = labels.get(r["industry_code"], r["industry_code"])
            r["winner_rate"] = r.get(_chip_col("winner_rate", mode))
        ordered = sorted((r for r in industry if _num(r.get("winner_rate")) is not None), key=lambda r: r["winner_rate"])
        rank = ordered[:6] + ordered[-6:]
        sections.append(_section("industry-ranks", "行业获利后6与前6", "bar", rank, ["industry_name", "winner_rate"], x="industry_name", series=["winner_rate"]))
        counts = _read(store, "chips", "signal-daily-counts", where="trade_date=?", args=[day])
        for r in counts:
            r["event_type"] = SIGNALS.get(r["event_type"], r["event_type"])
        sections.append(_section("signals", "当日信号快报", "bar", counts, ["event_type", "trigger_count"], x="event_type", series=["trigger_count"]))
        benchmark = _history(_visible_prices(store, "000985.CSI", day), day, params.get("range", "1y"))
        sections.append(_section("benchmark", "中证全指独立行情 · 000985.CSI", "line", benchmark, ["trade_date", "close", "volume", "amount"], x="trade_date", series=["close"], missing="冻结版本尚未包含已准备的ui-extra/index-prices补充行情。" if not benchmark else None))
        controls = [metric_control, _select("dist", "获利分布口径", [("count", "只数"), ("mv", "流通市值")], params.get("dist", "count"))]
    elif tab in ("industries", "industry"):
        _, labels = _dimensions(store)
        chosen = str(params.get("l1", "801780.SI"))
        controls = [metric_control, _select("l1", "申万一级行业", sorted(labels.items()), chosen)]
        industry = _read(store, "chips", "industry-daily", where="trade_date<=?", args=[day])
        current = [dict(r) for r in industry if _date_str(r["trade_date"]) == day]
        for r in current:
            r["industry_name"] = labels.get(r["industry_code"], r["industry_code"])
            r["winner_rate"] = r.get(_chip_col("winner_rate", mode))
            r["cost_deviation"] = r.get(_chip_col("cost_deviation", mode))
            r["concentration90"] = r.get(_chip_col("concentration90", mode))
        weekly = _read(store, "chips", "industry-weekly", where="trade_date<=?", args=[day])
        start = _date_str(_history(markets, day, params.get("range", "1y"))[0]["trade_date"])
        heat = []
        for r in weekly:
            if _date_str(r["trade_date"]) < start:
                continue
            heat.append({"trade_date": r["trade_date"], "industry_name": labels.get(r["industry_code"], r["industry_code"]), "value": r.get(_chip_col(metric, mode))})
        maxima = {}
        for r in heat:
            if r["value"] is not None:
                maxima[_date_str(r["trade_date"])] = max(maxima.get(_date_str(r["trade_date"]), -math.inf), r["value"])
        for r in heat:
            mx = maxima.get(_date_str(r["trade_date"]))
            r["column_normalized"] = r["value"] / mx if mx and r["value"] is not None else None
        sections.append(_section("heatmap", "行业×每周末交易日 · 数值为原值，色阶按列最大值归一", "heatmap", heat, ["trade_date", "industry_name", "value", "column_normalized"], x="trade_date", y="industry_name", value="column_normalized", series=["value"]))
        sections.append(_section("quadrant", "行业象限 · 获利比例×成本偏离", "scatter", current, ["industry_name", "winner_rate", "cost_deviation", "concentration90", "sample_count"], x="winner_rate", series=["cost_deviation"]))
        hist = _history([r for r in industry if r["industry_code"] == chosen], day, params.get("range", "1y"))
        trend = [{"trade_date": r["trade_date"], metric: r.get(_chip_col(metric, mode))} for r in hist]
        sections.append(_section("industry-history", labels.get(chosen, chosen) + " · 指标走势", "line", trend, ["trade_date", metric], x="trade_date", series=[metric]))
        rows = _chip_enrich(_chip_snapshot(store, day), day, store, dates)
        rows = [r for r in rows if r.get("industry_code") == chosen]
        dist = [{"bucket": label, "stock_count": sum(1 for r in rows if lo <= r["winner_rate"] < hi)} for label, lo, hi in [("0–20", 0, 20), ("20–40", 20, 40), ("40–60", 40, 60), ("60–80", 60, 80), ("80–97", 80, 97), ("97–100", 97, 101)]]
        sections.append(_section("industry-distribution", "所选行业六档获利分布", "bar", dist, ["bucket", "stock_count"], x="bucket", series=["stock_count"]))
        page, pagination = _paged(rows, params)
        table = _section("constituents", "行业成分股", "table", page, CHIP_TABLE, action=True)
        table["pagination"] = pagination
        sections.append(table)
        controls += _page_controls(params, ["float_mv", "winner_rate", "cost_deviation", "concentration90", "position_range", "position_1y", "name"])
    elif tab == "screen":
        _, labels = _dimensions(store)
        signal = str(params.get("preset", params.get("signal", "all")))
        window = str(params.get("window", "d0"))
        n = {"d5": 5, "d20": 20}.get(window, 1)
        begin = dates[max(0, len(dates) - n)]
        signal_ids = None
        if signal != "all" or n > 1:
            if signal == "all":
                events = _read(store, "chips", "events", columns=["entity_id", "event_type", "event_date"], where="event_date>=? AND event_date<=?", args=[begin, day])
            else:
                events = _read(store, "chips", "events", columns=["entity_id", "event_type", "event_date"], where="event_type=? AND event_date>=? AND event_date<=?", args=[signal, begin, day])
            signal_ids = {r["entity_id"] for r in events}
        rows = _chip_enrich(_chip_snapshot(store, day), day, store, dates)
        rows = _chip_filters(rows, params, signal_ids)
        if signal != "all":
            for r in rows:
                r["signals"] = SIGNALS.get(signal, signal) + " · " + ("当日" if n == 1 else f"近{n}交易日")
        page, pagination = _paged(rows, params)
        table = _section("stocks", f"筛选结果 · {len(rows)}只", "table", page, CHIP_TABLE, action=True)
        table["pagination"] = pagination
        sections.append(table)
        catalog = [{"event_type": SIGNALS[k], "rule": SIGNAL_RULES[k]} for k in SIGNALS]
        sections.append(_section("catalog", "八信号目录 · 同股同信号10交易日冷却；上市不足60交易日不触发", "table", catalog, ["event_type", "rule"]))
        backtest = _read(store, "chips", "backtest")
        backtest = [r for r in backtest if signal == "all" or r["event_type"] == signal]
        for r in backtest:
            r["event_type"] = SIGNALS.get(r["event_type"], r["event_type"])
            r["sample_group"] = "全部" if r["sample_group"] == "all" else "近一年"
        sections.append(_section("backtest", "固定最新截止日回测 · 不随观察日或筛选窗口变化 · 仅完整期限", "table", backtest, ["event_type", "sample_group", "triggers", "n5", "mean_excess_5d", "n20", "mean_excess_20d", "median_excess_20d", "win_rate_20d"]))
        event_symbol = str(params.get("backtest_code", "")).strip().upper()
        details = []
        if event_symbol:
            event_rows = _read(store, "chips", "events", where="entity_id=? AND event_date<=?", args=[event_symbol, day])
            stock_prices = {_date_str(r["trade_date"]): r.get("hfq_close") for r in _chip_history(store, event_symbol, day)}
            benchmark_prices = {_date_str(r["trade_date"]): r.get("close") for r in _visible_prices(store, "000985.CSI", day)}
            sessions = {d: i for i, d in enumerate(dates)}
            for event_row in event_rows:
                if signal != "all" and event_row["event_type"] != signal:
                    continue
                d = _date_str(event_row["event_date"])
                i = sessions.get(d)
                row = {"symbol": event_symbol, "event_date": d, "event_type": SIGNALS.get(event_row["event_type"], event_row["event_type"]), "start_close": stock_prices.get(d)}
                row["benchmark_start"] = benchmark_prices.get(d)
                for h in (5, 20):
                    end_date = dates[i + h] if i is not None and i + h < len(dates) else None
                    end_close = stock_prices.get(end_date)
                    row[f"end_date_{h}d"] = end_date
                    row[f"end_close_{h}d"] = end_close
                    row[f"return_{h}d"] = end_close / row["start_close"] - 1 if end_close is not None and row["start_close"] else None
                    row[f"benchmark_end_{h}d"] = benchmark_prices.get(end_date)
                    b = row[f"benchmark_end_{h}d"]
                    row[f"excess_{h}d"] = row[f"return_{h}d"] - (b / row["benchmark_start"] - 1) if row[f"return_{h}d"] is not None and b is not None and row["benchmark_start"] else None
                details.append(row)
            details.sort(key=lambda r: r["event_date"], reverse=True)
        page_detail, detail_pagination = _paged(details, params, "event_date")
        detail_section = _section("backtest-events", "单股逐事件回测端点 · 后复权价格", "table", page_detail, ["symbol", "event_date", "event_type", "start_close", "benchmark_start", "end_date_5d", "end_close_5d", "benchmark_end_5d", "return_5d", "excess_5d", "end_date_20d", "end_close_20d", "benchmark_end_20d", "return_20d", "excess_20d"], missing="输入完整股票代码查看其信号回测端点；未成熟或缺交易日价格保留空值。")
        detail_section["pagination"] = detail_pagination
        sections.append(detail_section)
        counts = _read(store, "chips", "signal-daily-counts", where="trade_date<=?", args=[day])
        counts = [r for r in counts if signal == "all" or r["event_type"] == signal]
        start = _date_str(_history(markets, day, params.get("range", "1y"))[0]["trade_date"])
        pivot = {}
        event_keys = list(SIGNALS) if signal == "all" else [signal]
        for row in counts:
            d = _date_str(row["trade_date"])
            if d >= start:
                pivot.setdefault(d, {"trade_date": d, **{k: 0 for k in event_keys}})[row["event_type"]] = row["trigger_count"]
        LABELS.update(SIGNALS)
        series_rows = [pivot[k] for k in sorted(pivot)]
        sections.append(_section("signal-history", "每日信号触发次数", "line", series_rows, ["trade_date"] + event_keys, x="trade_date", series=event_keys))
        controls = [_input("backtest_code", "逐事件回测股票代码", event_symbol), _select("preset", "预设信号", [("all", "全部信号")] + list(SIGNALS.items()), signal), _select("window", "触发窗口(全部信号当日显示全部股票)", [("d0", "当日"), ("d5", "近5日"), ("d20", "近20日")], window), _select("l1", "行业", [("all", "全部行业")] + sorted(labels.items()), params.get("l1", "all")), _select("board", "板块", [("all", "全部"), ("Main", "主板"), ("ChiNext", "创业板"), ("STAR", "科创板"), ("BSE", "北交所")], params.get("board", "all")), _select("size", "流通市值段", [("all", "全部"), ("small", "小于50亿"), ("medium", "50–200亿"), ("large", "至少200亿")], params.get("size", "all")), _input("wr_min", "获利下限(%)", params.get("wr_min", ""), "number"), _input("wr_max", "获利上限(%)", params.get("wr_max", ""), "number"), _input("c90_max", "90%集中度上限(%)", params.get("c90_max", ""), "number"), _select("no_st", "剔除ST", [("0", "否"), ("1", "是")], params.get("no_st", "0")), _input("q", "股票名称或代码", params.get("q", ""))] + _page_controls(params, ["float_mv", "winner_rate", "cost_deviation", "concentration90", "position_range", "position_1y", "name"])
    elif tab in ("stock", "detail"):
        symbol = str(params.get("code", params.get("symbol", ""))).strip().upper()
        controls = [_input("code", "完整股票代码", symbol), _input("obs", "个股观察日", params.get("obs", day))]
        if not symbol:
            sections.append(_section("stock", "请选择股票", "table", [], CHIP_TABLE, missing="输入完整证券代码，例如600519.SH，再进入个股筹码。"))
        else:
            hist = _through(_chip_history(store, symbol, day), _day(params.get("obs", day), cutoff))
            if not hist:
                sections.append(_section("stock", symbol + " · 无筹码记录", "table", [], CHIP_TABLE, missing="该证券在所选日期前没有筹码数据；请核对完整代码或调整观察日。"))
            else:
                obs = _date_str(hist[-1]["trade_date"])
                observed = obs
                enriched = _chip_enrich([hist[-1]], obs, store, dates)
                sections.append(_section("stock-summary", enriched[0]["name"] + " · " + obs, "table", enriched, ["trade_date", "close"] + CHIP_TABLE, action=True))
                official = _market_revision(store, "chip-native", "symbol=? AND trade_date=?", [symbol, obs])
                if official:
                    for row in official:
                        lower, upper, close = _num(row["his_low"]), _num(row["his_high"]), _num(hist[-1]["close"])
                        row["native_position"] = (close-lower)/(upper-lower) if lower and upper and upper > lower and close else None
                    part = _section("chip-native", "同日 Tushare 原生筹码记录", "table", official,
                                    ["trade_date", "winner_rate", "weight_avg", "cost_5pct", "cost_50pct",
                                     "cost_95pct", "his_low", "his_high", "native_position", "source"])
                    part["description"] = "原生高低边界不等于本地252交易日成本区间；低界为零或高低无效时不计算位置。只使用同股同日。"
                    for col in part["columns"]:
                        if col["key"] == "native_position":
                            col.update(label="原生边界位置（推算）", format="percent")
                        elif col["key"] in ("his_low", "his_high"):
                            col["label"] = "原生低界" if col["key"] == "his_low" else "原生高界"
                    sections.append(part)
                shown = _history(hist, obs, params.get("range", "1y"))
                bands = ["close", "weight_avg", "cost_5pct", "cost_15pct", "cost_50pct", "cost_85pct", "cost_95pct"]
                sections.append(_section("cost-bands", "价格、平均成本与五档成本分位带 · 点击日期切换", "line", shown, ["trade_date"] + bands, x="trade_date", series=bands))
                sections[-1]["action"] = {"type": "params", "panel": "stock", "params": {"obs": "$trade_date", "code": symbol}}
                sections.append(_section("stock-indicators", "个股获利、偏离与集中度", "line", shown, ["trade_date", "winner_rate", "cost_deviation", "concentration90", "concentration70"], x="trade_date", series=["winner_rate", "cost_deviation", "concentration90", "concentration70"]))
                events = _read(store, "chips", "events", columns=["entity_id", "event_type", "event_date"], where="entity_id=? AND event_date<=?", args=[symbol, obs])
                for r in events:
                    r["event_type"] = SIGNALS.get(r["event_type"], r["event_type"])
                events = sorted(events, key=lambda r: _date_str(r["event_date"]), reverse=True)[:40]
                sections.append(_section("stock-events", "近期信号", "table", events, ["event_date", "event_type"]))
                if enriched[0].get("industry_code"):
                    peers = _chip_enrich(_chip_snapshot(store, obs), obs, store, dates)
                    peers = [r for r in peers if r.get("industry_code") == enriched[0]["industry_code"]]
                    page, pagination = _paged(peers, params)
                    table = _section("peers", "同业筹码对比", "table", page, CHIP_TABLE, action=True)
                    table["pagination"] = pagination
                    sections.append(table)
                    controls += _page_controls(params, ["float_mv", "winner_rate", "concentration90", "name"])
                bars = []
                for year in sorted({_date_str(r["trade_date"])[:4] for r in shown}):
                    bars += _extra(store, f"chips/bars-{year}", where="symbol=? AND trade_date>=? AND trade_date<=?", args=[symbol, _date_str(shown[0]["trade_date"]), obs])
                costs = {_date_str(r["trade_date"]): r["weight_avg"] for r in shown}
                bars = _through(bars, obs)
                for r in bars:
                    r["weight_avg"] = costs.get(_date_str(r["trade_date"]))
                band_rows = {_date_str(r['trade_date']): r for r in shown}
                for r in bars:
                    source = band_rows.get(_date_str(r['trade_date']), {})
                    for key in ('cost_5pct','cost_15pct','cost_50pct','cost_85pct','cost_95pct'):
                        r[key] = source.get(key)
                candle = _section("candlestick", "真实OHLC K线与成本分位 · 不复权", "candlestick", bars, ["trade_date", "open", "close", "low", "high", "volume", "turnover_rate", "weight_avg", "cost_5pct", "cost_15pct", "cost_50pct", "cost_85pct", "cost_95pct"], x="trade_date", series=["open", "close", "low", "high"], missing="冻结版本尚未包含已准备的ui-extra/chips补充行情。" if not bars else None)
                candle["overlays"] = [{"key": key, "label": label} for key, label in [('weight_avg','平均成本'),('cost_5pct','成本5%'),('cost_15pct','成本15%'),('cost_50pct','成本50%'),('cost_85pct','成本85%'),('cost_95pct','成本95%')]]
                candle['action'] = {'type':'params','panel':'stock','params':{'obs':'$trade_date','code':symbol}}
                candle['sample_scope'] = '冻结不复权日线与同日成本分位按日期左连接；缺失不填充'
                sections.append(candle)
    else:
        raise ValueError(f"Unknown chips panel: {tab}")
    if tab=='stock' and str(params.get('code',params.get('entity_id',''))).upper():
        symbol=str(params.get('code',params.get('entity_id',''))).upper()
        requested=observation(params,cutoff_for(store,'chips',CUTOFF))
        if params.get('obs'):
            requested=min(requested,str(params['obs']))
        quotes=daily_rows(store,'stock-quotes',where='symbol=? AND trade_date>? AND trade_date<=?',
                          params=[symbol,day,requested])
        if quotes:
            part=_section('daily-raw-quotes','成本数据截止后 · 正式不复权日线','line',quotes,
                          ['trade_date','open','high','low','close'],x='trade_date',series=['close'])
            part.update(presentation='candlestick',ohlc={'open':'open','high':'high','low':'low','close':'close'})
            sections.append(part)
    return _reply("chips", tab, params, sections, controls, warnings, observed, cutoff_for(store,"chips",cutoff))


def _weights(params):
    weights = {k: max(0.0, min(5.0, _num(params.get("w_" + k), 1.0))) for k in DIMS}
    if params.get("w"):
        parts = str(params["w"]).split(",")
        if len(parts) == 6:
            weights = {k: max(0.0, min(5.0, _num(v, 1.0))) for k, v in zip(DIMS, parts)}
    return weights


def _temperature(row, weights):
    values = [(row.get("dimension_" + k), w) for k, w in weights.items() if _num(row.get("dimension_" + k)) is not None and w > 0]
    if len(values) < 4:
        return None
    # Match the observed frontend Math.round for positive heat scores.
    return math.floor(sum(v * w for v, w in values) / sum(w for _, w in values) * 10 + 0.5) / 10


def _mature_event_rows(events, dates, end):
    index = {d: i for i, d in enumerate(dates)}
    end_i = index.get(end, len(dates) - 1)
    out = []
    for original in events:
        r = dict(original)
        for h in (5, 20, 60, 120):
            if index.get(_date_str(r["event_date"]), end_i + 1) + h > end_i:
                r[f"return_{h}d"] = None
        out.append(r)
    return out


def _stats(values):
    values = sorted(v for v in values if _num(v) is not None)
    n = len(values)
    mid = (values[(n - 1) // 2] + values[n // 2]) / 2 if n else None
    return {"sample_count": n, "mean": sum(values) / n if n else None, "median": mid, "win_rate": sum(v > 0 for v in values) / n if n else None}


def _sentiment(tab, params, store):
    cutoff = component_date(store,"sentiment","temperature",CUTOFF)
    end = _day(params.get("trade_date", cutoff), cutoff)
    daily = _through(_read(store, "sentiment", "daily"), end)
    if not daily:
        return _reply("sentiment", tab, params, [_section("missing", "情绪数据", "table", [], [], missing="该观察日早于受控情绪序列起点2016-01-04。")])
    day = _date_str(daily[-1]["trade_date"])
    latest = dict(daily[-1])
    weights = _weights(params)
    custom = any(w != 1 for w in weights.values())
    shown = _history(daily, day, params.get("range", "3y"))
    for r in shown:
        if custom:
            r["temperature"] = _temperature(r, weights)
    if custom:
        latest["temperature"] = _temperature(latest, weights)
    sections, controls = [], []
    warnings = ["分位、资金可得日与历史财报为本湖候选计算口径；final-snapshot回贴不等于严格PIT，也不代表原站对账。新发权益基金产品合并仍有未证明的份额关系，相关指标为部分覆盖。", "自定义维度权重仅作用于浏览温度和贡献，至少4个正权重有效维度才出数；事件和历史复盘始终使用预计算等权口径。"]
    if tab in ("overview", "dimensions"):
        current_indicators = _read(store, 'sentiment', 'indicators', where='trade_date=?', args=[day])
        launches = _read(store, 'sentiment', 'fund-launch-counts-monthly', where='trade_date<=?', args=[day])
        launches = sorted(launches, key=lambda r: _date_str(r['trade_date']))
        launch = launches[-1] if launches else {}
        for item in current_indicators:
            if item.get('indicator_id') == 'E1':
                item.update({'formula_id':'verified_product_else_unmerged_share','formula_version':'frozen_v2',
                    'sample_scope':'已完成上月；已核关系合并，未解析份额各计一项；当前分类快照',
                    'verified_relation_share_count':launch.get('verified_relation_share_count'),
                    'unresolved_share_count':launch.get('unresolved_share_count'),
                    'share_count':launch.get('share_count'),
                    'missing_reason':'份额关系未完全确认；产品数含未合并份额' if launch.get('unresolved_share_count') else None})
            elif item.get('indicator_id') == 'E3' and item.get('heat_score') is None:
                item.update({'formula_id':'complete_20_session_exact_amount','formula_version':'frozen_v2',
                    'sample_scope':'仅完整金额的20交易日事件；独立估算未接入温度', 'missing_reason':'20日事件金额不完整或历史分位不足'})
            elif item.get('indicator_id') == 'F2':
                item.update({'formula_id':'downside_std_positive_zero','formula_version':'frozen_v2',
                    'sample_scope':'沪深300日收益上涨置零、保留负收益，20日样本标准差乘sqrt(252)年化；原站规则未恢复'})
        prior = daily[-21] if len(daily) > 20 else {}
        dimrows = []
        for key, label in DIMS.items():
            members = [r for r in current_indicators if r.get('dimension') == key and r.get('indicator_id') != 'E4']
            missing = [r['indicator_id'] for r in members if _num(r.get('heat_score')) is None]
            dimrows.append({'dimension': label, 'dimension_id': key, 'score': latest.get('dimension_' + key),
                'previous_score': prior.get('dimension_' + key), 'previous_date': _date_str(prior.get('trade_date')) or None,
                'weight': weights[key], 'valid_items': len(members)-len(missing), 'expected_items': len(members),
                'coverage': (len(members)-len(missing))/len(members) if members else None,
                'missing_indicators': missing, 'missing_reason': ('缺少指标热度：' + ','.join(missing)) if missing else (None if members else '指标明细缺失'),
                'formula_id': 'available_weighted_dimension_heat', 'formula_version': 'frozen_v2',
                'sample_scope': '冻结指标明细；E4仅慢变量，不计六维温度'})
        if any(r['missing_indicators'] or not r['expected_items'] for r in dimrows):
            warnings.append('温度为缺项后按有效维度和指标重归一的部分覆盖结果；资金等维缺项可能显著影响总温度，详见维度覆盖表。')
        denom = sum(r["weight"] for r in dimrows if r["score"] is not None and r["weight"] > 0)
        valid = sum(r["score"] is not None and r["weight"] > 0 for r in dimrows)
        for r in dimrows:
            r["contribution"] = r["score"] * r["weight"] / denom if valid >= 4 and r["score"] is not None and r["weight"] > 0 else None
        if tab == "overview":
            keys = ["temperature", "slow_temperature", "dimension_count", "ice_dimensions", "boil_dimensions", "leading", "coincident", "lagging"]
            sections.append(_section("gauge", "温度仪（指标覆盖见维度明细） · 0–20冰点 / 20–35偏冷 / 35–65中性 / 65–80偏热 / 80–100沸点", "gauge", [{"label": LABELS[k], "value": latest.get(k)} for k in keys], ["label", "value"]))
            sections[-1].update({'formula_id':'six_dimension_available_weighted_temperature','formula_version':'frozen_v2','sample_scope':'至少4个有效维度；各维仅有效分项重归一','coverage':dimrows,'missing_reason':'; '.join(r['dimension']+': '+r['missing_reason'] for r in dimrows if r['missing_reason']) or None})
            temperature = _num(latest.get("temperature"))
            sections[-1]["band"] = None if temperature is None else "冰点" if temperature < 20 else "偏冷" if temperature < 35 else "中性" if temperature < 65 else "偏热" if temperature < 80 else "沸点"
            sections[-1]["thresholds"] = [20, 35, 65, 80]
            sections.append(_section("radar", "六维得分", "radar", dimrows, ["dimension", "score", "previous_score", "previous_date", "valid_items", "expected_items", "missing_reason"], x="dimension", series=["score", "previous_score"]))
            sections.append(_section("contributions", "六维温度贡献", "bar", dimrows, ["dimension", "weight", "contribution"], x="dimension", series=["contribution"]))
            sections.append(_section("temperature-history", "温度、慢变量与前瞻/同步/滞后历史", "line", shown, ["trade_date", "temperature", "slow_temperature", "leading", "coincident", "lagging"], x="trade_date", series=["temperature", "slow_temperature", "leading", "coincident", "lagging"]))
            sections[-4]["actions"] = [{"label": "重置等权", "type": "params", "panel": "overview", "params": {**{"w_" + k: 1 for k in DIMS}, "w": ""}}]
            for r in current_indicators:
                r["indicator"] = INDICATORS.get(r["indicator_id"], r["indicator_id"])
            sections.append(_section("freshness", "各指标实际来源日期与缺项", "table", current_indicators, ["indicator", "raw_value", "source_date", "sample_count", "minimum_samples", "percentile", "pit_status", "verified_relation_share_count", "unresolved_share_count", "sample_scope", "missing_reason"]))
        else:
            sections.append(_section("dimension-cards", "六维度得分与指标覆盖", "kpi", [{**r, "label": r["dimension"], "value": r["score"]} for r in dimrows], ["label", "value", "valid_items", "expected_items", "missing_reason"]))
            sections.append(_section("dimension-contributions", "维度权重与温度贡献", "bar", dimrows, ["dimension", "weight", "contribution"], x="dimension", series=["contribution"]))
            items = _read(store, "sentiment", "indicators", where="trade_date=?", args=[day])
            for r in items:
                r["indicator"] = INDICATORS.get(r["indicator_id"], r["indicator_id"])
                r["dimension"] = DIMS.get(r["dimension"], r["dimension"])
                r["symbol"] = r["indicator_id"]
            sections.append(_section("dimension-items", "维度所属指标 · 点击指标下钻", "table", items, ["indicator_id", "indicator", "dimension", "raw_value", "heat_score", "weight", "source_date"], action=True))
            dimension_keys = ["dimension_" + k for k in DIMS]
            for k, label in DIMS.items():
                LABELS["dimension_" + k] = label
            sections.append(_section("dimensions-history", "六维历史", "line", shown, ["trade_date"] + dimension_keys, x="trade_date", series=dimension_keys))
            sections.append(_section("timing-history", "前瞻 / 同步 / 滞后", "line", shown, ["trade_date", "leading", "coincident", "lagging"], x="trade_date", series=["leading", "coincident", "lagging"]))
            corr = _correlations(daily, day)
            sections.append(_section("correlations", "截至观察日近250个交易日两两相关 · 有效配对样本数单列", "heatmap", corr, ["dimension_x", "dimension_y", "correlation", "sample_count", "as_of_date"], x="dimension_x", y="dimension_y", value="correlation", series=["correlation"]))
        sections.append(_section('dimension-coverage', '六维指标覆盖 · E4仅慢变量', 'table', dimrows, ['dimension','score','previous_score','previous_date','valid_items','expected_items','coverage','missing_reason','sample_scope','formula_id','formula_version']))
    elif tab == "indicators":
        chosen = str(params.get("item", "A1"))
        if chosen not in INDICATORS:
            chosen = "A1"
        controls = [_select("item", "所选指标", list(INDICATORS.items()), chosen)]
        items = _read(store, "sentiment", "indicators", where="trade_date=?", args=[day])
        dates = [_date_str(r["trade_date"]) for r in daily]
        past = _read(store, "sentiment", "indicators", where="trade_date=?", args=[dates[-21]]) if len(dates) > 20 else []
        old = {r["indicator_id"]: r.get("heat_score") for r in past}
        for r in items:
            r["indicator"] = INDICATORS.get(r["indicator_id"], r["indicator_id"])
            r["heat_change20"] = r["heat_score"] - old[r["indicator_id"]] if r.get("heat_score") is not None and old.get(r["indicator_id"]) is not None else None
            r["dimension"] = DIMS.get(r["dimension"], r["dimension"])
            r["timing_type"] = {"leading": "前瞻", "coincident": "同步", "lagging": "滞后"}.get(r["timing_type"], r["timing_type"])
            r["symbol"] = r["indicator_id"]
        sections.append(_section("indicators", "23项指标明细", "table", items, ["indicator_id", "indicator", "dimension", "raw_value", "percentile", "heat_score", "heat_change20", "timing_type", "reverse", "weight", "slow", "window_sessions", "minimum_samples", "sample_count", "source_date", "pit_status"], action=True))
        hist = _read(store, "sentiment", "indicators", where="indicator_id=? AND trade_date<=?", args=[chosen, day])
        hist = _history(hist, day, params.get("range", "3y"))
        sections.append(_section("indicator-raw", INDICATORS[chosen] + " · 原值走势", "line", hist, ["trade_date", "raw_value", "source_date"], x="trade_date", series=["raw_value"]))
        sections.append(_section("indicator-percentile", INDICATORS[chosen] + " · 分位与热度走势", "line", hist, ["trade_date", "percentile", "heat_score"], x="trade_date", series=["percentile", "heat_score"]))
        display = _revised_sentiment_display(store, day)
        auxiliary_day=observation(params,cutoff_for(store,'sentiment',day))
        money_rows=daily_rows(store,'sentiment-money',where='trade_date<=?',params=[auxiliary_day])
        money_rows.sort(key=lambda row:row['trade_date'])
        money_rows=[{**r,'indicator_id':'MAIN20','indicator':'主力净流入20日','unit':'CNY',
                     'source':'curated/market_money_flow.net_amount','availability':'完整20交易日；不计温度'} for r in money_rows]
        if money_rows and money_rows[-1]['trade_date']>day:
            display=[r for r in display if r.get('indicator_id')!='MAIN20']+[money_rows[-1]]
        sections.append(_section("display-only", "只展示区 · 不计入温度 · 缺源和历史不足逐项说明", "table", display, ["indicator", "raw_value", "unit", "source_date", "source", "availability", "missing_reason"], missing="冻结版本尚未包含已准备的ui-extra/sentiment-display补充序列。" if not display else None))
        display_item = str(params.get("display_item", "PMI"))
        display_hist = _revised_sentiment_display(store, day, display_item)
        if display_item=='MAIN20':
            points={_date_str(r['trade_date']):r for r in display_hist}
            points.update({r['trade_date']:r for r in money_rows})
            display_hist=[points[k] for k in sorted(points)]
        display_hist = _history(display_hist, auxiliary_day if display_item=='MAIN20' else day, params.get("range", "3y"))
        sections.append(_section("display-history", "辅助指标历史 · 不计入温度", "line", display_hist, ["trade_date", "raw_value", "source_date"], x="trade_date", series=["raw_value"]))
        amount = _announced_amounts(store, dates, day)
        if amount:
            part = _section("holder-amount-coverage", "E3 · 股东增减持有披露价格样本（CNY，不计温度）", "table", amount,
                            ["window_start", "window_end", "source_as_of", "event_count", "with_price_count", "coverage_ratio",
                             "observed_increase", "observed_decrease"])
            part["description"] = "按公告日，不按成交日。变动股数×披露均价仅为可算样本；缺金额不补零，不冒充全市场净额。"
            for col in part["columns"]:
                col["label"] = {"window_start": "窗口起日", "window_end": "窗口止日", "source_as_of": "最近源公告日",
                                "event_count": "去重公告事件", "with_price_count": "有可算金额事件",
                                "coverage_ratio": "公告金额可算率", "observed_increase": "样本增持估值（元）",
                                "observed_decrease": "样本减持估值（元）"}[col["key"]]
            sections.append(part)
        controls.append(_select("display_item", "辅助指标", [("PMI", "制造业PMI"), ("MAIN20", "主力净流入20日"), ("VIX", "VIX现货"), ("VIX_TERM", "VIX期限结构"), ("HS300_PE20", "沪深300 PE20年分位")], display_item))
    elif tab == "events":
        benchmark = str(params.get("index", params.get("benchmark", "000002.SH")))
        benchmark = benchmark if benchmark in ("000002.SH", "000300.SH") else "000002.SH"
        chosen = str(params.get("event", "all"))
        all_events = _read(store, "sentiment", "events", where="entity_id=? AND event_date<=?", args=[benchmark, day])
        all_events = _mature_event_rows(all_events, [_date_str(r["trade_date"]) for r in daily], day)
        filtered = [dict(r) for r in all_events if chosen == "all" or r["event_type"] == chosen]
        for r in filtered:
            r["event_type"] = EVENTS.get(r["event_type"], r["event_type"])
        filtered.sort(key=lambda r: _date_str(r["event_date"]), reverse=True)
        page, pagination = _paged(filtered, params, "event_date")
        table = _section("event-list", "事件列表 · " + ("上证A股" if benchmark == "000002.SH" else "沪深300"), "table", page, ["event_date", "event_type", "return_5d", "return_20d", "return_60d", "return_120d", "quality_status"])
        table["pagination"] = pagination
        sections.append(table)
        timeline = [{"event_date": r["event_date"], "event_type": EVENTS.get(r["event_type"], r["event_type"]), "event_code": r["event_type"], "trigger_count": 1} for r in all_events if chosen == "all" or r["event_type"] == chosen]
        part = _section("timeline", "信号时间轴 · 按类型分泳道", "timeline", timeline, ["event_date", "event_type", "trigger_count"], x="event_date", y="event_type", series=["trigger_count"])
        part["action"] = {"type": "params", "panel": "events", "params": {"event": "$event_code", "path_event": "$event_code"}}
        sections.append(part)
        stats = []
        types = sorted({r["event_type"] for r in all_events})
        for event in types:
            if chosen != "all" and chosen != event:
                continue
            for h in (5, 20, 60, 120):
                stats.append({"event_type": EVENTS.get(event, event), "horizon": h, **_stats([r.get(f"return_{h}d") for r in all_events if r["event_type"] == event])})
        sections.append(_section("event-stats", "分类后续收益 · 只计观察日前已满期限", "table", stats, ["event_type", "horizon", "sample_count", "mean", "median", "win_rate"]))
        path_type = chosen if chosen != "all" else str(params.get("path_event", "ice_resonance"))
        base_events = _read(store, "sentiment", "events", columns=["event_date", "event_type"], where="entity_id='000002.SH' AND event_date<=?", args=[day])
        paths = _event_paths(base_events, _visible_prices(store, "000002.SH", day), [_date_str(r["trade_date"]) for r in daily], day, path_type)
        sections.append(_section("event-path", "截至观察日的事件后0–120日路径 · 固定上证A股 · " + EVENTS.get(path_type, path_type), "line", paths, ["horizon", "q25", "median", "mean", "q75", "sample_count", "as_of_date"], x="horizon", series=["q25", "median", "mean", "q75"], missing="该类别在观察日前没有具备有效起止价格的完整期限样本。" if not paths else None))
        controls = [_select("index", "事件收益指数", [("000002.SH", "上证A股"), ("000300.SH", "沪深300")], benchmark), _select("event", "事件类别", [("all", "全部")] + list(EVENTS.items()), chosen), _select("path_event", "路径事件(未选类别时)", list(EVENTS.items()), path_type)] + _page_controls(params, ["event_date", "event_type"])
    elif tab in ("replay", "history"):
        nodes = _through(_read(store, "sentiment", "replay-nodes"), day)
        nodes = [r for r in nodes if _date_str(r["trade_date"]) != day]
        current_node = dict(daily[-1])
        current_node["requested_node"] = "当前观察日"
        nodes.append(current_node)
        part = _section("nodes", "历史节点与当前观察日 · 点击查看指标", "table", nodes, ["requested_node", "trade_date", "temperature", "slow_temperature", "dimension_count"] + ["dimension_" + k for k in DIMS])
        part["action"] = {"type": "params", "panel": "replay", "params": {"node": "$trade_date"}}
        sections.append(part)
        node_day = _day(params.get("node", day), cutoff)
        eligible_nodes = [r for r in daily if _date_str(r["trade_date"]) <= min(node_day, day)]
        node_day = _date_str(eligible_nodes[-1]["trade_date"]) if eligible_nodes else day
        node_items = _read(store, "sentiment", "indicators", where="trade_date=?", args=[node_day])
        for row in node_items:
            row["indicator"] = INDICATORS.get(row["indicator_id"], row["indicator_id"])
        sections.append(_section("node-indicators", node_day + " · 节点23项指标原值与分位", "table", node_items, ["indicator", "raw_value", "percentile", "heat_score", "source_date"]))
        benchmark = str(params.get("index", "000002.SH"))
        bands = _band_returns(daily, _visible_prices(store, benchmark, day), day)
        band_names = {0: "冰点<20", 1: "偏冷20–35", 2: "中性35–65", 3: "偏热65–80", 4: "沸点≥80"}
        for r in bands:
            r["band"] = band_names.get(r["band"], r["band"])
        sections.append(_section("band-returns", "历史温度档位后续收益 · 相邻日期为重叠样本", "table", bands, ["band", "horizon", "sample_count", "mean", "median", "win_rate"], missing=None))
        # Replay deliberately uses the original equal-weight daily objects.
        replay = _history(daily, day, params.get("range", "3y"))
        sections.append(_section("slow-history", "慢变量等权历史 · 至少6项有效", "line", replay, ["trade_date", "slow_temperature", "slow_count"], x="trade_date", series=["slow_temperature"]))
        controls = [_select("index", "复盘收益指数", [("000002.SH", "上证A股"), ("000300.SH", "沪深300")], benchmark), _input("node", "复盘指标观察日", node_day)]
    else:
        raise ValueError(f"Unknown sentiment panel: {tab}")
    return _reply("sentiment", tab, params, sections, controls, warnings, day, cutoff)


def _index_controls(params):
    return [_select("code", "指数", list(INDICES.items()), params.get("code", "000300.SH")), _select("metric", "估值指标", list(METRICS.items()), params.get("metric", "pe_ttm"))]


def _month_add(day, months):
    d = date.fromisoformat(_date_str(day))
    index = d.year * 12 + d.month - 1 + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(d.day, calendar.monthrange(year, month + 1)[1])).isoformat()


def _index_group(symbol):
    if symbol in {"000001.SH", "399001.SZ", "000985.CSI"}:
        return "市场"
    if symbol in {"399006.SZ", "000688.SH", "000922.CSI"}:
        return "风格"
    return "宽基"


def _channel_history(rows, metric, years, end=CUTOFF):
    rows = _through(rows, end)
    values, queue, start = [], [], 0
    for r in rows:
        d = _date_str(r["trade_date"])
        cutoff = _month_add(d, -12 * years)
        while start < len(queue) and queue[start][0] < cutoff:
            _, value = queue[start]
            values.pop(bisect.bisect_left(values, value))
            start += 1
        value = _num(r.get(metric))
        if value is not None:
            bisect.insort(values, value)
            queue.append((d, value))
        ready = len(values) >= int(years * 252 * .8)
        for q in (20, 40, 60, 80):
            i = (len(values) - 1) * q / 100
            a, b = math.floor(i), math.ceil(i)
            r[f"{metric}_channel_{q}"] = values[a] + (values[b] - values[a]) * (i - a) if ready else None
    return rows


def _valuation(tab, params, store):
    official_cutoff = getattr(store, "valuation_cutoff", CUTOFF)
    rebuilt = "dividends_and_historical_statistics" in getattr(store,"daily_boards",{}).get("valuation",{}) and Path(store.path("revision/market/valuation-daily-rebuilt.json")).is_file()
    cutoff = max(official_cutoff,component_date(store,"valuation","dividends_and_historical_statistics",CUTOFF))
    revised = official_cutoff != CUTOFF
    end = _day(params.get("trade_date", cutoff), cutoff)
    stock_day=observation(params, cutoff_for(store,'valuation',cutoff))
    daily_structure=daily_rows(store,'valuation-structure',where='trade_date<=?',params=[stock_day])
    daily_structure.sort(key=lambda r:r['trade_date'])

    def price_rows(code):
        rows = _visible_prices(store, code, day)
        if revised and not rebuilt and day > CUTOFF:
            rows = _through(rows, CUTOFF)
            rows += _read(store, "valuation", "phase3-prices", where="symbol=? AND trade_date<=?", args=[code, day])
        return _through(rows, day)
    win = str(params.get("win", "10"))
    win = win if win in ("3", "5", "10", "20") else "10"
    symbol = str(params.get("code", params.get("symbol", "000300.SH"))).upper()
    symbol = symbol if symbol in INDICES else "000300.SH"
    metric = {"pe": "pe_ttm", "dy": "dividend_yield"}.get(str(params.get("metric", "pe_ttm")), str(params.get("metric", "pe_ttm")))
    metric = metric if metric in METRICS else "pe_ttm"
    calendar_rows = _through(_read(store, "valuation", "temperature", columns=["trade_date", "temperature", "sample_count", "pe_ttm_pct_5y", "pb_pct_5y", "ashare_pe_pct_5y", "inverse_dividend_pct_5y"]), end)
    if not calendar_rows:
        return _reply("valuation", tab, params, [_section("missing", "估值数据", "table", [], [], missing="该观察日早于冻结估值包覆盖起点2016-01-04。")])
    day = _date_str(calendar_rows[-1]["trade_date"])
    sections, controls = [], []
    warnings = ["官方PE/PB优先，其余为成分合成候选；成分有效日、财报修订可得性未达到严格PIT。合成有效市值覆盖不足95%保留空值，不跨口径补齐。", "分位采用已记录周采样与80%门槛候选口径，不代表原站精确对账；历史不足的10/20年分位会为空。价格收益不含现金股息，未来收益仅计完整期限。"]
    if revised:
        if not rebuilt:
            warnings.append("正式指数PE/PB与全A估值结构分别更新。合成指数和股息率沿用原截面。")
        warnings.append("新增周分位按已完成的交易周最后交易日，中位秩、窗口有效周样本至少80%。正式指数非正PE/PB不入分位。个股母集按湖内证券主档的上市、退市日期过滤（含CDR，不排除ST），缺上市日无法判定并另列。破净率分母是非空PB，分子是PB＜1，含负PB；原湖PE缺失不判作亏损。")
    stats_cutoff = day if rebuilt else min(day, CUTOFF)
    current = _read(store, "valuation", "daily", where="trade_date=?", args=[day])
    for r in current:
        r["index_name"] = INDICES.get(r["index_symbol"], r["index_symbol"])
        r["symbol"] = r["index_symbol"]
    if tab == "overview":
        latest = calendar_rows[-1]
        hs300 = next((r for r in current if r['index_symbol'] == '000300.SH'), {})
        pe_pb_day = day
        if rebuilt and hs300.get('pe_ttm') is None and hs300.get('pb') is None and official_cutoff < day:
            rows = _read(store,'valuation','daily',where='index_symbol=? AND trade_date=?',
                         args=['000300.SH',official_cutoff])
            if rows:
                hs300, pe_pb_day = rows[0],official_cutoff
        all_index = next((r for r in current if r['index_symbol'] == '000985.CSI'), {})
        market_rows = _read(store, 'valuation', 'stock-structure', where='trade_date=?', args=[day])
        structure = market_rows[0] if market_rows else {}
        if daily_structure and daily_structure[-1]['trade_date'] > day:
            structure=daily_structure[-1]
        pct_key = f'pe_ttm_pct_{win}y'
        candidates = sorted((r for r in current if _num(r.get(pct_key)) is not None), key=lambda r: (r[pct_key], r['index_symbol']))
        cheap, expensive = (candidates[0], candidates[-1]) if candidates else ({}, {})
        def item(label, value, source, unit='', fmt=None, scope=None):
            row = {'label':label, 'value':value, 'source_date':day, 'source':source, 'unit':unit,
                   'sample_scope':scope, 'missing_reason':None if _num(value) is not None else '该观察日来源值或有效分位不足'}
            if fmt:
                row['format'] = fmt
            return row
        summary = [
            item('估值温度', latest.get('temperature'), 'valuation/temperature', scope='固定5年；四项至少三项'),
            item('沪深300 PE', hs300.get('pe_ttm'), 'valuation/daily:000300.SH', '倍'),
            item('沪深300 PB', hs300.get('pb'), 'valuation/daily:000300.SH', '倍'),
            item('全市场正PE中位数', structure.get('positive_pe_median'), 'valuation/stock-structure', '倍'),
            item('中证全指股息率', all_index.get('dividend_yield'), 'valuation/daily:000985.CSI', '%'),
            item('破净率', structure.get('below_book_ratio'), 'valuation/stock-structure', fmt='percent'),
            item('最便宜宽基' + (' · '+cheap['index_name'] if cheap else ''), cheap.get(pct_key), 'valuation/daily', '%', scope=f'{win}年PE分位；12指数中{len(candidates)}只有效'),
            item('最贵宽基' + (' · '+expensive['index_name'] if expensive else ''), expensive.get(pct_key), 'valuation/daily', '%', scope=f'{win}年PE分位；12指数中{len(candidates)}只有效'),
        ]
        for kpi in summary:
            if kpi['label'] in ('沪深300 PE','沪深300 PB'):
                kpi['source_date'] = pe_pb_day
            if kpi['label'] in ('全市场正PE中位数','破净率'):
                kpi['source_date']=structure.get('trade_date',day)
        sections.append(_section('temperature', '估值总览 · 正式指数 '+pe_pb_day+' / 全A结构 '+str(structure.get('trade_date',day)), 'kpi', summary, ['label','value']))
        sections[-1]['description'] = f'估值观察日 {day}，沪深300正式 PE/PB {pe_pb_day}，全A结构 {structure.get("trade_date",day)}；最便宜/最贵按所选{win}年PE有效分位排序。'
        if revised and not rebuilt and day > CUTOFF:
            sections[-1]["description"] += " 股息率、合成指数和估值地图可回看11-09同口径截面。"
            sections[-1]["actions"] = [{"type": "params", "panel": "overview", "label": "查看 11-09 完整截面",
                                       "params": {"date": CUTOFF}}]
        elif revised and day == CUTOFF:
            sections[-1]["actions"] = [{"type": "params", "panel": "overview", "label": "回到最新观察日",
                                       "params": {"date": cutoff}}]
        matrix = []
        for r in current:
            for m, label in METRICS.items():
                row = {"symbol": r["index_symbol"], "index_name": r["index_name"], "metric": label, "value": r.get(m)}
                for w in (3, 5, 10, 20):
                    row[f"pct_{w}y"] = r.get(f"{m}_pct_{w}y")
                    row[f"samples_{w}y"] = r.get(f"{m}_n_{w}y")
                    LABELS[f"pct_{w}y"] = f"{w}年分位"
                    LABELS[f"samples_{w}y"] = f"{w}年周样本"
                row["selected_percentile"] = r.get(f"{m}_pct_{win}y")
                row["band"] = min(4, int(row["selected_percentile"] // 20)) if row["selected_percentile"] is not None else None
                row["group"] = _index_group(r["index_symbol"])
                row["missing_reason"] = "周样本未达到该窗口80%门槛或原指标缺失" if row["selected_percentile"] is None else ""
                matrix.append(row)
        sections.append(_section("matrix", f"12指数×三指标×3/5/10/20年分位 · 当前窗口{win}年", "table", matrix, ["symbol", "index_name", "group", "metric", "value", "selected_percentile", "band", "pct_3y", "pct_5y", "pct_10y", "pct_20y", "samples_3y", "samples_5y", "samples_10y", "samples_20y", "missing_reason"], action=True))
        maps = _read(store, "valuation", "map-daily", where="trade_date=?", args=[day], optional=True)
        if maps:
            for r in maps:
                r["index_name"] = INDICES.get(r["index_symbol"], r["index_symbol"])
                r["symbol"] = r["index_symbol"]
                r["group"] = _index_group(r["symbol"])
            part = _section("map", "估值地图 · 固定5年 · 泡大小为近20日平均成交额", "bubble", maps, ["symbol", "index_name", "group", "pe_ttm_pct_5y", "dividend_yield_pct_5y", "amount20"], x="pe_ttm_pct_5y", y="dividend_yield_pct_5y", series=["dividend_yield_pct_5y"], action=True)
            part["size_key"] = "amount20"
            sections.append(part)
        else:
            sections.append(_section("map", "估值地图", "bubble", [], [], missing="该数据日缺少同口径股息分位或成交额，地图留空。"))
        hist = _history(calendar_rows, day, params.get("range", "3y"))
        for r in hist:
            t = _num(r.get("temperature"))
            r["band"] = None if t is None else "冰点" if t < 20 else "偏冷" if t < 35 else "中性" if t < 65 else "偏热" if t < 80 else "沸点"
        components = ["temperature", "pe_ttm_pct_5y", "pb_pct_5y", "ashare_pe_pct_5y", "inverse_dividend_pct_5y"]
        sections.append(_section("temperature-history", "估值温度与四分项 · 固定五年", "line", hist, ["trade_date", "band", "sample_count"] + components, x="trade_date", series=components))
        component_names = {'pe_ttm_pct_5y':'沪深300 PE五年分位','pb_pct_5y':'沪深300 PB五年分位','ashare_pe_pct_5y':'全A PE中位五年分位','inverse_dividend_pct_5y':'100−沪深300股息五年分位'}
        component_rows = [{'label':name,'value':latest.get(key),'source_date':day,'missing_reason':None if _num(latest.get(key)) is not None else '该项样本不足或原值缺失'} for key,name in component_names.items()]
        sections.append(_section('temperature-components', f'温度四项与有效数（{latest.get("sample_count")} / 4）', 'table', component_rows, ['label','value','source_date','missing_reason']))
        controls = [_select("range", "温度历史区间", [("3y", "3年"), ("all", "全部")], params.get("range", "3y"))]
    elif tab == "index":
        full_history = _read(store, "valuation", "daily", where="index_symbol=? AND trade_date<=?", args=[symbol, day])
        full_history = _channel_history(full_history, metric, int(win), day)
        prices = {_date_str(r["trade_date"]): r["close"] for r in price_rows(symbol) if _num(r.get("close")) is not None}
        hist = _history(full_history, day, params.get("range", "10y"))
        for r in hist:
            r["close"] = prices.get(_date_str(r["trade_date"]))
        channels = [f"{metric}_channel_{q}" for q in (20, 40, 60, 80)]
        for q in (20, 40, 60, 80):
            LABELS[f"{metric}_channel_{q}"] = f"{win}年{q}%通道"
        sections.append(_section("channel", INDICES[symbol] + " · " + METRICS[metric] + f"估值通道({win}年)与指数点位", "line", hist, ["trade_date", metric] + channels + ["close"], x="trade_date", series=[metric] + channels + ["close"]))
        sections[-1]["secondary_axis"] = ["close"]
        if revised:
            sections[-1]["description"] = "通道用本地日频样本；下方历史分位用周样本，两者不混用。"
        pct_key = f"{metric}_pct_{win}y"
        LABELS[pct_key] = f"{win}年历史分位"
        sections.append(_section("index-percentile", INDICES[symbol] + f" · {win}年分位与指数点位", "line", hist, ["trade_date", metric, pct_key, "close"], x="trade_date", series=[pct_key, "close"]))
        sections[-1]["secondary_axis"] = ["close"]
        selected = [r for r in current if r["index_symbol"] == symbol]
        sections.append(_section("provenance", "当前值、来源与覆盖率", "table", selected, ["index_name", "trade_date", "pe_ttm", "pb", "dividend_yield", "pe_source", "pb_source", "dividend_source", "snapshot_date", "earnings_mv_coverage", "equity_mv_coverage", "dividend_mv_coverage", "price_weight_coverage"]))
        if revised and (not selected or _num(selected[0].get(metric)) is None):
            last_day = max((_date_str(row["trade_date"]) for row in full_history
                            if _num(row.get(metric)) is not None), default=None)
            part = sections[-1]
            part["description"] = METRICS[metric] + f"在 {day} 无有效值。"
            if last_day:
                part["description"] += f"该指标最近可用日为 {last_day}。"
                part["actions"] = [{"type": "params", "panel": "index", "label": f"回看 {last_day}",
                                    "params": {"date": last_day, "code": symbol, "metric": metric}}]
            else:
                part["description"] += "本快照没有该指标的历史观测。"
        annual = _read(store, "valuation", "annual-return-decomposition", where="index_symbol=? AND trade_date<=?", args=[symbol, day])
        annual.sort(key=lambda r: r["year"])
        annual_day = _date_str(annual[-1]["trade_date"]) if annual else None
        for r in annual:
            yearly = [(d, v) for d, v in sorted(prices.items()) if d[:4] == str(r["year"]) and d <= _date_str(r["trade_date"])]
            previous = [(d, v) for d, v in sorted(prices.items()) if d[:4] < str(r["year"])]
            r["start_close"] = previous[-1][1] if previous else None
            r["end_close"] = yearly[-1][1] if yearly else None
            r["actual_start"] = previous[-1][0] if previous else None
            r["actual_end"] = yearly[-1][0] if yearly else None
        sections.append(_section("annual", "年度价格收益拆解 · (1+盈利变化)×(1+估值变化)−1", "table", annual, ["year", "actual_start", "start_close", "actual_end", "end_close", "price_return", "eps_growth", "pe_change", "identity_residual", "dividend_yield_proxy"]))
        if revised and annual_day:
            sections[-1]["description"] = f"该指数保存的年度拆解截至 {annual_day}；与观察日独立。"
        prices = {_date_str(r["trade_date"]): r["close"] for r in price_rows(symbol) if r.get("close")}
        samples = [{**r, "close": prices.get(_date_str(r["trade_date"]))} for r in _through(full_history, day)]
        requested_start = _day(params.get("start", "2020-01-01"), cutoff)
        requested_end = min(_day(params.get("end", day), cutoff), day)
        start_rows = [r for r in samples if _date_str(r["trade_date"]) <= requested_start and (r.get("close") or 0) > 0 and (r.get("pe_ttm") or 0) > 0]
        end_rows = [r for r in samples if _date_str(r["trade_date"]) <= requested_end and (r.get("close") or 0) > 0 and (r.get("pe_ttm") or 0) > 0]
        interval = []
        if start_rows and end_rows:
            a, b = start_rows[-1], end_rows[-1]
            if _date_str(b["trade_date"]) > _date_str(a["trade_date"]):
                interval = [{"requested_start": requested_start, "requested_end": requested_end, "actual_start": a["trade_date"], "actual_end": b["trade_date"], "price_return": b["close"] / a["close"] - 1, "eps_growth": (b["close"] / b["pe_ttm"]) / (a["close"] / a["pe_ttm"]) - 1, "pe_change": b["pe_ttm"] / a["pe_ttm"] - 1}]
        sections.append(_section("interval", "自选区间拆解 · 日频真实价格，实际端点向前取最近估值与行情均有效交易日", "table", interval, ["requested_start", "requested_end", "actual_start", "actual_end", "price_return", "eps_growth", "pe_change"], missing=None if interval else "自选区间内没有两个不同且PE/价格均有效的交易日端点。"))
        peer_group = str(params.get("peer_group", _index_group(symbol)))
        peers = [dict(r) for r in current if peer_group == "all" or _index_group(r["symbol"]) == peer_group]
        for r in peers:
            history = [x for x in price_rows(r["symbol"]) if _num(x.get("close")) is not None]
            if revised:
                prices_by_day = {_date_str(item["trade_date"]): item["close"] for item in history}
                start = _date_str(calendar_rows[-21]["trade_date"]) if len(calendar_rows) >= 21 else None
                prior = prices_by_day.get(start)
                r["price_return"] = prices_by_day[day] / prior - 1 if prior and day in prices_by_day else None
            else:
                r["price_return"] = history[-1]["close"] / history[-21]["close"] - 1 if len(history) >= 21 and history[-21]["close"] else None
            r["group"] = _index_group(r["symbol"])
        sections.append(_section("peers", "同组指数估值与近20交易日价格收益", "table", peers, ["symbol", "index_name", "group", "pe_ttm", "pb", "dividend_yield", f"{metric}_pct_{win}y", "price_return"], action=True))
        if revised:
            old = _read(store, "valuation", "phase3-comparisons",
                        where="symbol=? AND trade_date<=?", args=[symbol, day])
            latest_sources = {}
            for row in _through(old, day):
                latest_sources[(row["provider"], row["metric"])] = row
            sections.append(_section(
                "alternative-observations", "来源对照 · 中证原生指标／乐咕原频率，不计入分位",
                "table", list(latest_sources.values()),
                ["provider", "trade_date", "metric", "observation_frequency", "value", "unit"],
                missing="该指数与观察日尚无可用的中证官网或乐咕观测。",
            ))
        controls = _index_controls(params) + [_select("peer_group", "对照分组", [("all", "全部"), ("宽基", "宽基"), ("风格", "风格"), ("市场", "市场")], peer_group), _select("range", "曲线区间", [("3y", "3年"), ("10y", "10年"), ("all", "全部")], params.get("range", "10y")), _input("start", "拆解起日", requested_start), _input("end", "拆解止日", requested_end)]
    elif tab == "returns":
        samples = _read(store, "valuation", "monthly-return-samples", where="index_symbol=? AND trade_date<=?", args=[symbol, stats_cutoff])
        sample_rows = []
        for original in samples:
            r = dict(original)
            r["percentile"] = r.get(f"{metric}_pct_{win}y")
            r["band"] = min(4, int(r["percentile"] // 20)) if r["percentile"] is not None else None
            for months in (12, 36):
                if _month_add(r["trade_date"], months) > stats_cutoff:
                    r[f"forward_{months}m_annualized"] = None
            if r["band"] is not None:
                sample_rows.append(r)
        stats = []
        for band in range(5):
            for months in (12, 36):
                stats.append({"band": f"{band*20}–{(band+1)*20}%", "horizon_months": months, **_stats([r.get(f"forward_{months}m_annualized") for r in sample_rows if r["band"] == band])})
        # This reviewed release only matches 5-year PE bands at its exact snapshot date.
        if revised and not rebuilt and day == CUTOFF and win == "5" and metric == "pe_ttm":
            published = _read(store, "valuation", "phase3-returns",
                              where="entity_id LIKE ? AND obs_date=?", args=[symbol + ":pe_5y_band:%", day])
            metrics = {f"valuation_forward_{m}m_{field}": (m, key) for m in (12, 36)
                       for field, key in (("mean_annualized_price_return", "mean"),
                                          ("median_annualized_price_return", "median"),
                                          ("positive_return_win_rate", "win_rate"))}
            for row in published:
                months, field = metrics[row["metric_id"]]
                band = int(row["entity_id"].rsplit(":", 1)[-1])
                target = stats[band * 2 + (months == 36)]
                if target["sample_count"] != row["sample_count"]:
                    raise ValueError("湖侧收益分组与当前样本数不一致")
                target[field] = row["value"]
                target["source"] = row["data_version"]
            if published:
                warnings.append("11月9日五年PE档位的均值、中位数与胜率读取湖侧已发布统计；其他日期、窗口和指标仍按本地样本统计。")
        horizon = _int(params.get("horizon"), 12)
        horizon = 36 if horizon == 36 else 12
        ret = f"forward_{horizon}m_annualized"
        sections.append(_section("return-statistics", f"{INDICES[symbol]} · {METRICS[metric]} {win}年分位五档前向收益", "table", stats, ["band", "horizon_months", "sample_count", "mean", "median", "win_rate"]))
        dots = [r for r in sample_rows if r.get(ret) is not None]
        if revised:
            last_month = max((_date_str(row["trade_date"]) for row in samples if row["month_complete"]), default="无")
            sections[-1]["description"] = (
                f"价格收益样本截至 {stats_cutoff}，最后完整月样本 {last_month}；"
                "当前分位参照使用所选观察日。12/36个月价格收益不含股息，月样本重叠。"
            )
        sections.append(_section("scatter", f"月度样本 · 分位×未来{horizon}月年化价格收益", "scatter", dots, ["trade_date", "percentile", "band", ret], x="percentile", series=[ret]))
        hist = []
        for lo, hi in [(-math.inf, -.5), (-.5, -.2), (-.2, 0), (0, .2), (.2, .5), (.5, math.inf)]:
            hist.append({"bucket": f"{lo:g} 至 {hi:g}", "sample_count": sum(lo <= r[ret] < hi for r in dots)})
        sections.append(_section("return-distribution", "完整期限收益分布", "bar", hist, ["bucket", "sample_count"], x="bucket", series=["sample_count"]))
        selected = [r for r in current if r["index_symbol"] == symbol]
        sections.append(_section("current-band", "当前指数分位参照", "table", selected, ["index_name", metric, f"{metric}_pct_{win}y", f"{metric}_n_{win}y"]))
        controls = _index_controls(params) + [_select("horizon", "散点和分布持有期", [(12, "12个月"), (36, "36个月年化")], horizon)]
    elif tab == "structure":
        ratios = _history(_read(store, "valuation", "relative-valuations"), day, params.get("range", "3y"))
        sections.append(_section("relative", "大小盘与成长价值估值比", "line", ratios, ["trade_date", "small_large_pe_ratio", "growth_value_pe_ratio"], x="trade_date", series=["small_large_pe_ratio", "growth_value_pe_ratio"]))
        structure = _through(_read(store, "valuation", "stock-structure"), stock_day)
        by_day={_date_str(r['trade_date']):r for r in structure}
        by_day.update({_date_str(r['trade_date']):r for r in daily_structure})
        structure=[by_day[key] for key in sorted(by_day)]
        structure_day=_date_str(structure[-1]['trade_date']) if structure else day
        latest = structure[-1:] if structure else []
        sections.append(_section("market-structure", "全市场正PE中位与破净率", "table", latest, ["trade_date", "sample_count", "positive_pe_median", "below_book_ratio"] + (["eligible_count", "missing_row_count", "positive_pe_count", "valid_pb_count", "negative_pb_count", "unknown_listing_count"] if day > CUTOFF else [])))
        if revised and day > CUTOFF:
            names = {"eligible_count": "可判定在市股票/CDR", "missing_row_count": "无当日估值记录",
                     "positive_pe_count": "正PE样本数", "valid_pb_count": "有效PB分母",
                     "negative_pb_count": "负PB数（计入破净）", "unknown_listing_count": "主档缺上市日"}
            for col in sections[-1]["columns"]:
                if col["key"] in names:
                    col["label"] = names[col["key"]]
        sections.append(_section("below-book", "破净率历史", "line", _history(structure, structure_day, params.get("range", "3y")), ["trade_date", "below_book_ratio"], x="trade_date", series=["below_book_ratio"]))
        histogram = daily_rows(store,'valuation-pe-distribution',where='trade_date=?',params=[structure_day]) or _read(
            store, "valuation", "pe-histogram", where="trade_date=?", args=[structure_day], optional=True)
        sections.append(_section("pe-distribution", "全A PE分布 · 亏损/零与缺值单列", "bar", histogram, ["bucket", "stock_count"], x="bucket", series=["stock_count"], missing="冻结包尚未发布逐档PE直方图；不把分位点冒充股票分布。" if not histogram else None))
        industry_day = min(day, getattr(store,"daily_boards",{}).get("valuation",{}).get(
            "dividends_and_historical_statistics",{}).get("industry_date") or day)
        industries = _read(store, "valuation", "industry-static-valuations", where="trade_date=?", args=[industry_day], optional=True)
        page, pagination = _paged(industries, params, "static_pe_pct_3y", False)
        table = _section("industries", "申万一级静态PE及三年分位 · 非TTM", "table", page, ["industry_name", "trade_date", "static_pe", "pb", "static_pe_pct_3y", "sample_count"])
        table["pagination"] = pagination
        if revised and not rebuilt and day > CUTOFF and not industries:
            table["missing_reason"] = "申万行业静态PE仍截至11月9日，切换至该日或更早可查看。"
        sections.append(table)
        sections.append(_section("cross-market", "跨市场估值对照", "table", [], ["index_name", "pe_ttm", "pb", "dividend_yield"], missing="该受控估值包未提供美股板块PE；全球资产价格不能替代PE。原站港股亦暂无估值，未伪造跨市场对照。"))
        controls = [_select("range", "历史区间", [("3y", "3年"), ("10y", "10年"), ("all", "全部")], params.get("range", "3y"))] + _page_controls(params, ["static_pe_pct_3y", "static_pe", "pb", "industry_name"])
    else:
        raise ValueError(f"Unknown valuation panel: {tab}")
    return _reply("valuation", tab, params, sections, controls, warnings, day, cutoff)


def panel(board, panel_id, params, store):
    if board not in TABS:
        raise ValueError(f"Unknown market board: {board}")
    params = dict(params or {})
    if "date" in params:
        params["trade_date"] = params["date"]
    elif "trade_date" in params:
        params["date"] = params["trade_date"]
    aliases = {"industry": "industries", "detail": "stock", "entity": "stock"} if board == "chips" else {"history": "replay", "decompose": "dimensions", "entity": "indicators"} if board == "sentiment" else {"detail": "index", "entity": "index"}
    if board == "sentiment" and panel_id == "entity":
        params["item"] = params.get("entity_id", params.get("code", "A1"))
    tab = aliases.get(panel_id, panel_id)
    return {"chips": _chips, "sentiment": _sentiment, "valuation": _valuation}[board](tab, params, store)


def capital_additions(panel_id, params, store, payload):
    """Apply market revision to the existing owners response, without changing its adapter.

    The shared server calls this only for capital_flow, before serialization/cache.
    An original snapshot without these inputs keeps its old responses.
    """
    if not Path(store.path("revision/market/phase3-market-manifest.json")).is_file():
        return payload
    tab = payload.get("tab", panel_id)
    day = _day(params.get("date", params.get("trade_date", CUTOFF)))
    sections = payload["sections"]

    def replace(section):
        for i, existing in enumerate(sections):
            if existing["id"] == section["id"]:
                sections[i] = section
                return
        sections.append(section)

    def units(section, names, money=()):
        for col in section["columns"]:
            key = col["key"]
            if key in names:
                col["label"] = names[key]
            if key in money:
                col.update(format="money", unit="CNY")
        return section

    if tab in ("overview", "cross"):
        north = _market_revision(store, "capital-north", "trade_date<=?", [day])
        rows = {}
        for row in sorted(north, key=lambda r: r["trade_date"]):
            rows.setdefault(row["trade_date"], {"trade_date": row["trade_date"]})[row["channel"]] = row["turnover"]
        for row in rows.values():
            row["total"] = row["north_sh"] + row["north_sz"] if {"north_sh", "north_sz"} <= row.keys() else None
        labels = {"north_sh": "沪股通", "north_sz": "深股通", "total": "两通道合计"}
        if rows and tab == "overview":
            same_day = rows.get(day, {})
            summary = next((part for part in sections if part["id"] == "overview_summary"), None)
            if summary:
                for kpi in summary["rows"]:
                    if kpi["label"] == "北向全市场成交":
                        # Its original anchor follows the main-money source date, which can be stale.
                        kpi.update(value=same_day.get("total"), trade_date=day,
                                   source="港交所日统计 · 两通道，人民币",
                                   missing_reason=None if same_day.get("total") is not None else "该观察日没有双通道全市场成交。")
            values = list(rows.values())[-1:]
            part = units(_section("north_current", "北向全市场成交 · 最近可用日（CNY）", "table", values,
                                  ["trade_date", "north_sh", "north_sz", "total"]), labels, labels)
            part["description"] = "港交所沪深股通实际成交，不是北向净买入；未与十大成交混加。"
            replace(part)
        if rows and tab == "cross":
            channel = params.get("north_channel", params.get("channel", "all"))
            fields = [channel] if channel in ("north_sh", "north_sz") else list(labels)
            points = list(rows.values())[-250:]
            part = units(_section("cross_turnover", "北向全市场成交 · 港交所停披后分通道（CNY）",
                                  "line", points, ["trade_date", *fields], x="trade_date", series=fields),
                         labels, fields)
            part["series"] = [{"key": key, "label": labels[key], "unit": "CNY"} for key in fields]
            part["description"] = "仅在两通道同日均有金额时显示合计；原比率仍按其有效日期，不拿十大成交或净买入补齐。"
            # Keep pre-cessation history available alongside the newer provider.
            existing = next((part for part in sections if part["id"] == "cross_turnover"), None)
            if existing and existing["rows"]:
                existing["id"] = "cross_turnover_legacy"
                existing["title"] = "北向停披前成交 · 原来源历史（CNY）"
                existing["quality"] = "此图仅保留原来源；停披后的港交所同日成交见独立图。"
            replace(part)
    if tab == "overview":
        try:
            win = int(params.get("win", 20))
        except (ValueError, TypeError):
            win = 20
        win = win if win in (1, 5, 20, 60, 100) else 20
        balances = _market_revision(store, "capital-balance", "obs_date=? AND window_days=?", [day, win])
        if balances:
            codes = {"stock_etf": "股票ETF", "financing": "融资买入减偿还", "southbound": "南向净买入",
                     "ipo": "IPO募资", "holder_decrease": "股东减持"}
            for row in balances:
                row["line"] = codes.get(row["line"], row["line"])
            money = ("observed_sum", "complete_window_value", "observed_complete_window_estimate")
            names = {"obs_date": "窗口观察日", "line": "分项", "window_days": "窗口交易日",
                     "source_as_of": "分项最近来源日", "valid_days": "已观测天数", "strict_valid_days": "严格有效天数",
                     "observed_sum": "已观测小计", "complete_window_value": "严格完整窗口",
                     "observed_complete_window_estimate": "完整窗口估算"}
            part = units(_section("formal_balance", "湖已发布资金窗口 · 观察值与严格值分列（CNY）",
                                  "table", sorted(balances, key=lambda row: row["line"]), list(names)), names, money)
            part["description"] = "仅显示所选日和窗口，分项日期、严格有效天数独立。不从部分值、估算值推导净额。"
            sections.append(part)
    if tab in ("margin", "stocks"):
        code = str(params.get("code", params.get("entity_id", ""))).strip().upper()
        exchange = params.get("exchange", "all")
        suffixes = {"all": (".SH", ".SZ"), "SSE": (".SH",), "SZSE": (".SZ",), "BSE": (".BJ",)}
        if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code) and code.endswith(suffixes.get(exchange, suffixes["all"])):
            rows = _market_revision(store, "capital-margin", "symbol=? AND trade_date<=?", [code, day])
            if rows:
                names = {"financing_buy": "融资买入", "financing_repay": "融资偿还",
                         "financing_net_buy": "融资净买入"}
                part = _section("margin_observed_security", f"{code} · 交易日原生融资买入与偿还（CNY）",
                                "table", sorted(rows, key=lambda row: row["trade_date"], reverse=True)[:20],
                                ["trade_date", "symbol", *names, "source"])
                part["description"] = "仅显示同股同日买入、偿还同时可用的观察，净买入＝买入−偿还；不以余额变化代替。"
                sections.append(units(part, names, names))
    return _clean(payload)
