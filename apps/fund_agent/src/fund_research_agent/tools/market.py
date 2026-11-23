"""Comparable point queries against the published lake and the external proxy."""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import Literal

import duckdb
from pydantic import BaseModel, Field, model_validator

from fund_research_agent.adapters.market_proxy import ProxyMCP
from fund_research_agent.domain.contracts import ToolResultEnvelope, ToolStatus
from fund_research_agent.domain.errors import DependencyUnavailable
from fund_research_agent.tools.registry import ToolRegistry, ToolSpec

LAKE_TOOL = "market.lake_lookup"
PROXY_TOOL = "market.proxy_lookup"
DS_MODELS = frozenset({"deepseek-v4.1-flash", "opencode-go/deepseek-v4.1-flash"})


class MarketLookup(BaseModel):
    dataset: Literal["trading_calendar", "daily_bars"]
    trade_date: date = Field(description="明确的交易日期，YYYY-MM-DD")
    symbol: str | None = Field(default=None, pattern=r"^\d{6}\.(SH|SZ|BJ)$",
                               description="股票日线必填，例如 600519.SH；交易日历省略")

    @model_validator(mode="after")
    def check_symbol(self):
        if (self.dataset == "daily_bars") != bool(self.symbol):
            raise ValueError("仅股票日线必填带市场后缀的证券代码")
        return self


def _empty(value: MarketLookup, origin: str, source: list[str]) -> ToolResultEnvelope:
    return ToolResultEnvelope(
        status=ToolStatus.PARTIAL, origin=origin, source=source, data_as_of=value.trade_date,
        data={"dataset": value.dataset, "rows": [], "note": "来源无该日记录；空结果不代表停牌或休市。"},
    )


def _result(value, origin, sources, row):
    return ToolResultEnvelope(
        status=ToolStatus.SUCCESS, origin=origin, source=sources, data_as_of=value.trade_date,
        data={"dataset": value.dataset, "rows": [row]},
    )


def lake_lookup(root: Path, value: MarketLookup) -> ToolResultEnvelope:
    day = value.trade_date
    calendar = value.dataset == "trading_calendar"
    folder = root / "curated" / value.dataset / f"trade_date={day.year if calendar else day}"
    sources = ["数据湖/curated/" + value.dataset]
    files = sorted(str(path) for path in folder.glob("*.parquet"))
    if not files:
        return _empty(value, "local_lake", sources)
    try:
        with duckdb.connect() as db:
            if calendar:
                rows = db.execute(
                    "SELECT DISTINCT is_trading FROM read_parquet(?, hive_partitioning=false) "
                    "WHERE replace(cast(trade_date AS VARCHAR), '-', '') = ?", [files, day.strftime("%Y%m%d")]
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT DISTINCT open, high, low, close FROM read_parquet(?, hive_partitioning=false) "
                    "WHERE trade_date = ? AND symbol = ?", [files, day, value.symbol]
                ).fetchall()
    except duckdb.Error:
        raise DependencyUnavailable("数据湖分区暂不可读") from None
    if not rows or any(field is None for result in rows for field in result):
        return _empty(value, "local_lake", sources)
    if len(rows) != 1:
        raise DependencyUnavailable("数据湖同一查询键有冲突记录")
    row = {"trade_date": day.isoformat()}
    if calendar:
        row["is_open"] = bool(rows[0][0])
        row["calendar"] = "A股"
    else:
        row.update(symbol=value.symbol, adjustment="none", unit="CNY",
                   **dict(zip(("open", "high", "low", "close"), rows[0], strict=True)))
    return _result(value, "local_lake", sources, row)


def proxy_lookup(proxy: ProxyMCP, value: MarketLookup) -> ToolResultEnvelope:
    day = value.trade_date.strftime("%Y%m%d")
    calendar = value.dataset == "trading_calendar"
    name = "trade_cal" if calendar else "daily"
    args = {"exchange": "SSE", "start_date": day, "end_date": day} if calendar else {
        "ts_code": value.symbol, "trade_date": day,
    }
    sources = [f"Tushare 代理 MCP/{name}"]
    rows = proxy.lookup(name, args)
    if not rows:
        return _empty(value, "external_proxy_mcp", sources)
    row = {"trade_date": value.trade_date.isoformat()}
    try:
        if len(rows) != 1:
            raise ValueError
        raw = rows[0]
        date_key = "cal_date" if calendar else "trade_date"
        if str(raw[date_key]).replace("-", "") != day:
            raise ValueError
        if calendar:
            if raw["exchange"] != "SSE" or str(raw["is_open"]) not in {"0", "1"}:
                raise ValueError
            row.update(calendar="SSE", is_open=str(raw["is_open"]) == "1")
        else:
            if raw["ts_code"] != value.symbol:
                raise ValueError
            row.update(symbol=value.symbol, adjustment="none", unit="CNY",
                       **{field: float(raw[field]) for field in ("open", "high", "low", "close")})
    except (KeyError, TypeError, ValueError):
        raise DependencyUnavailable("代理返回记录的键或字段与本次请求不符") from None
    return _result(value, "external_proxy_mcp", sources, row)


def register_market_tools(registry: ToolRegistry, root: Path, proxy: ProxyMCP | None) -> None:
    async def local(query: MarketLookup):
        return await asyncio.to_thread(lake_lookup, root, query)

    registry.register(ToolSpec(
        LAKE_TOOL, "首选：读正式数据湖的指定日期 A 股交易日历或单只股票未复权日线。",
        MarketLookup, local,
    ))
    if proxy is not None:
        async def external(query: MarketLookup):
            return await asyncio.to_thread(proxy_lookup, proxy, query)

        registry.register(ToolSpec(
            PROXY_TOOL, "只在湖缺记录、用户明确要求查代理或对账时，查一日 A 股交易日历或一只股票未复权日线。",
            MarketLookup, external, permission="network", timeout_seconds=35,
        ))
