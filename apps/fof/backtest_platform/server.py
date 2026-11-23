# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import base64
from io import BytesIO
import json
import mimetypes
import sys
import traceback
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import pandas as pd

from .exposure_analysis import build_exposure_snapshot
from .fund_backtest import BacktestRequest, FundSeries, run_fund_backtest_analysis_window, run_fund_backtest_core
from .portfolio_store import PortfolioStore
from .strategy_store import StrategyStore
from .workspace import DATA_DIR, STATE_DIR, initialize_state


STATIC_DIR = Path(__file__).resolve().parent / "static"
FUND_MASTER = DATA_DIR / "fund_master.csv"
INDEX_NAV_DIR = DATA_DIR / "index_nav"
FUND_LABELS_FILE = DATA_DIR / "fund_labels.csv"
STRATEGY_DIR = STATE_DIR / "strategies"
PORTFOLIO_DIR = STATE_DIR / "portfolios"
DATA_FREQUENCIES = ("daily", "weekly", "monthly")
DATA_FREQUENCY_LABELS = {"daily": "日频", "weekly": "周频", "monthly": "月频"}

INDEX_NAMES = {
    "000016.SH": "上证50",
    "000300.SH": "沪深300",
    "000852.SH": "中证1000",
    "000905.SH": "中证500",
    "000906.SH": "中证800",
    "000922.SH": "中证红利",
    "000985.SH": "中证全指",
    "399006.SZ": "创业板指",
    "399370.SZ": "国证成长",
    "399371.SZ": "国证价值",
}


@dataclass(frozen=True)
class AssetMeta:
    id: str
    code: str
    name: str
    manager: str
    company: str
    fund_type: str
    inception_date: str
    start: str
    end: str
    count: int
    manager_start_date: str
    cluster_label: str
    role_label: str
    full_label: str


class FundDataStore:
    def __init__(self) -> None:
        self.funds: dict[str, FundSeries] = {}
        self.benchmarks: dict[str, FundSeries] = {}
        self.meta: dict[str, AssetMeta] = {}
        self.loaded = False

    def ensure_loaded(self) -> None:
        if self.loaded:
            return
        self.funds.clear()
        self.benchmarks.clear()
        self.meta.clear()
        self._load_funds()
        self._load_benchmark()
        self.loaded = True

    def _load_funds(self) -> None:
        if not FUND_MASTER.exists():
            raise FileNotFoundError(f"缺少基金主表: {FUND_MASTER}")
        fund_labels = load_fund_labels(FUND_LABELS_FILE)
        manager_start_dates = load_manager_start_dates(DATA_DIR / "query" / "fund_master.parquet")
        for row in read_csv(FUND_MASTER):
            ts_code = row["ts_code"]
            labels = fund_labels.get(ts_code, {})
            nav_file = row.get("nav_file") or f"fund_nav/{ts_code}.csv"
            nav_path = DATA_DIR / nav_file
            if not nav_path.exists():
                continue
            nav = load_fund_nav(nav_path)
            if nav.empty:
                continue
            fund = FundSeries(
                id=ts_code,
                name=row.get("fund_name") or ts_code,
                manager=row.get("manager") or "",
                nav=nav,
            )
            self.funds[ts_code] = fund
            self.meta[ts_code] = AssetMeta(
                id=ts_code,
                code=ts_code,
                name=fund.name,
                manager=fund.manager,
                company=row.get("fund_company") or "",
                fund_type=row.get("fund_type") or row.get("investment_type") or "",
                inception_date=row.get("inception_date") or "",
                start=nav.index.min().strftime("%Y-%m-%d"),
                end=nav.index.max().strftime("%Y-%m-%d"),
                count=int(nav.count()),
                manager_start_date=manager_start_dates.get(ts_code, ""),
                cluster_label=labels.get("cluster_label", ""),
                role_label=labels.get("role_label", ""),
                full_label=labels.get("full_label", ""),
            )

    def _load_benchmark(self) -> None:
        if not INDEX_NAV_DIR.exists():
            raise FileNotFoundError(f"缺少指数行情目录: {INDEX_NAV_DIR}")
        for benchmark_path in csv_data_files(INDEX_NAV_DIR):
            nav = load_index_nav(benchmark_path)
            if nav.empty:
                continue
            benchmark_id = benchmark_path.stem
            self.benchmarks[benchmark_id] = FundSeries(
                benchmark_id,
                INDEX_NAMES.get(benchmark_id, benchmark_id),
                "",
                nav,
            )
        if "000300.SH" not in self.benchmarks:
            raise FileNotFoundError(f"缺少沪深300指数文件: {INDEX_NAV_DIR / '000300.SH.csv'}")
        for fund_id, fund in self.funds.items():
            self.benchmarks[fund_id] = fund

    def assets_payload(self) -> dict[str, Any]:
        self.ensure_loaded()
        funds = [
            meta.__dict__
            for meta in sorted(self.meta.values(), key=lambda item: (item.name, item.code))
        ]
        index_benchmarks = [
            {
                "id": benchmark.id,
                "code": benchmark.id,
                "name": benchmark.name,
                "kind": "index",
                "manager": "",
                "fund_type": "指数",
                "start": benchmark.nav.index.min().strftime("%Y-%m-%d"),
                "end": benchmark.nav.index.max().strftime("%Y-%m-%d"),
                "count": int(benchmark.nav.count()),
            }
            for benchmark in sorted(
                [benchmark for benchmark in self.benchmarks.values() if benchmark.id not in self.meta],
                key=lambda item: _benchmark_sort_key(Path(f"{item.id}.csv")),
            )
        ]
        fund_benchmarks = [
            {
                "id": meta.id,
                "code": meta.code,
                "name": meta.name,
                "kind": "fund",
                "manager": meta.manager,
                "fund_type": meta.fund_type,
                "cluster_label": meta.cluster_label,
                "role_label": meta.role_label,
                "full_label": meta.full_label,
                "manager_start_date": meta.manager_start_date,
                "start": meta.start,
                "end": meta.end,
                "count": meta.count,
            }
            for meta in sorted(self.meta.values(), key=lambda item: (item.name, item.code))
        ]
        return {
            "funds": funds,
            "benchmarks": index_benchmarks + fund_benchmarks,
            "tradingCalendar": [
                date.strftime("%Y-%m-%d")
                for date in self.benchmarks["000300.SH"].nav.index
            ],
        }


store = FundDataStore()
strategy_store = StrategyStore(STRATEGY_DIR)
portfolio_store = PortfolioStore(PORTFOLIO_DIR)
_legacy_strategy_portfolios_checked = False


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def load_fund_labels(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}

    labels: dict[str, dict[str, str]] = {}
    for row in read_csv(path):
        code = clean_text(row.get("ts_code"))
        if not code:
            continue
        labels[code] = {
            "cluster_label": clean_text(row.get("cluster_label")),
            "role_label": clean_text(row.get("role_label")),
            "full_label": clean_text(row.get("full_label")),
        }
    return labels


def load_manager_start_dates(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        df = pd.read_parquet(path, columns=["ts_code", "manager_start_date_dashboard"])
    except Exception:
        return {}
    if "ts_code" not in df.columns or "manager_start_date_dashboard" not in df.columns:
        return {}
    rows = df.dropna(subset=["ts_code"]).copy()
    rows["manager_start_date_dashboard"] = pd.to_datetime(rows["manager_start_date_dashboard"], errors="coerce")
    return {
        str(row["ts_code"]).strip(): row["manager_start_date_dashboard"].strftime("%Y-%m-%d")
        for _, row in rows.dropna(subset=["manager_start_date_dashboard"]).iterrows()
    }


def clean_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _benchmark_sort_key(path: Path) -> tuple[int, str]:
    return (0 if path.stem == "000300.SH" else 1, path.stem)


def csv_data_files(directory: Path) -> list[Path]:
    return sorted(
        [path for path in directory.glob("*.csv") if not path.name.startswith(".")],
        key=_benchmark_sort_key,
    )


def load_fund_nav(path: Path) -> pd.Series:
    df = pd.read_csv(path, encoding="utf-8-sig")
    if "nav_date" not in df.columns or "adj_nav" not in df.columns:
        raise ValueError(f"基金净值缺少 nav_date/adj_nav 字段: {path.name}")
    dates = pd.to_datetime(df["nav_date"].astype(str), errors="coerce")
    values = pd.to_numeric(df["adj_nav"], errors="coerce")
    return clean_nav_series(pd.Series(values.values, index=dates, name=path.stem))


def load_index_nav(path: Path) -> pd.Series:
    df = pd.read_csv(path, encoding="utf-8-sig")
    if "trade_date" not in df.columns or "close" not in df.columns:
        raise ValueError(f"指数行情缺少 trade_date/close 字段: {path.name}")
    dates = pd.to_datetime(df["trade_date"].astype(str), errors="coerce")
    values = pd.to_numeric(df["close"], errors="coerce")
    return clean_nav_series(pd.Series(values.values, index=dates, name=path.stem))


def clean_nav_series(series: pd.Series) -> pd.Series:
    cleaned = pd.Series(pd.to_numeric(series.values, errors="coerce"), index=pd.to_datetime(series.index, errors="coerce"))
    cleaned = cleaned[~cleaned.index.isna()].dropna()
    cleaned = cleaned[cleaned > 0].sort_index()
    return cleaned[~cleaned.index.duplicated(keep="last")].astype(float)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/api/assets":
                self.send_json(store.assets_payload())
                return
            if parsed.path == "/api/strategies":
                self.send_json({"strategies": strategy_store.list()})
                return
            if parsed.path.startswith("/api/strategies/"):
                self.send_json(strategy_store.get(_strategy_id_from_path(parsed.path)))
                return
            if parsed.path == "/api/portfolios":
                ensure_legacy_strategy_portfolios()
                self.send_json({"portfolios": portfolio_store.list()})
                return
            if parsed.path.startswith("/api/portfolios/") and parsed.path.endswith("/active"):
                ensure_legacy_strategy_portfolios()
                portfolio_id = _portfolio_id_from_path(parsed.path.removesuffix("/active"))
                self.send_json(compact_portfolio_payload(portfolio_store.get(portfolio_id)))
                return
            if parsed.path.startswith("/api/portfolios/") and "/snapshots/" in parsed.path:
                ensure_legacy_strategy_portfolios()
                portfolio_id, frequency = _portfolio_snapshot_path(parsed.path)
                portfolio = portfolio_store.get(portfolio_id)
                snapshot = _backtest_snapshot_for_frequency(portfolio, frequency)
                if snapshot is None:
                    raise ValueError(f"{DATA_FREQUENCY_LABELS[_data_frequency(frequency)]}快照不存在")
                self.send_json({"snapshot": snapshot})
                return
            if parsed.path.startswith("/api/portfolios/"):
                ensure_legacy_strategy_portfolios()
                self.send_json(augment_portfolio_payload(portfolio_store.get(_portfolio_id_from_path(parsed.path))))
                return
            self.serve_static(parsed.path)
        except Exception as exc:
            self.send_error_json(exc)

    def do_POST(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/api/strategies":
                self.send_json(strategy_store.create(self.read_json()))
                return
            if parsed.path == "/api/portfolios":
                self.send_json(compact_portfolio_payload(portfolio_store.create(self.read_json())))
                return
            if parsed.path == "/api/portfolios/order":
                ensure_legacy_strategy_portfolios()
                payload = self.read_json()
                ordered = portfolio_store.reorder(payload.get("ids") if isinstance(payload.get("ids"), list) else [])
                self.send_json({"portfolios": ordered})
                return
            if parsed.path == "/api/rebalance-plan/import":
                self.send_json({"rebalancePlan": import_rebalance_plan(self.read_json())})
                return
            if parsed.path.startswith("/api/portfolios/") and parsed.path.endswith("/analysis-window"):
                portfolio_id = _portfolio_id_from_path(parsed.path.removesuffix("/analysis-window"))
                portfolio = portfolio_store.get(portfolio_id)
                self.send_json(run_analysis_window_payload(portfolio, self.read_json()))
                return
            if parsed.path.startswith("/api/portfolios/") and parsed.path.endswith("/backtest"):
                portfolio_id = _portfolio_id_from_path(parsed.path.removesuffix("/backtest"))
                portfolio = portfolio_store.get(portfolio_id)
                active_frequency = _data_frequency(portfolio.get("dataFrequency"))
                snapshots = run_backtest_payloads(portfolio, active_frequency)
                result = snapshots[active_frequency]
                updated = portfolio_store.save_backtest_snapshots(portfolio_id, snapshots, active_frequency)
                self.send_json({"portfolio": compact_portfolio_payload(updated), "result": result})
                return
            if parsed.path != "/api/backtest":
                self.send_error(404)
                return
            payload = self.read_json()
            self.send_json(run_backtest_payload(payload))
        except Exception as exc:
            self.send_error_json(exc)

    def do_PUT(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/strategies/"):
                self.send_json(strategy_store.update(_strategy_id_from_path(parsed.path), self.read_json()))
                return
            if parsed.path.startswith("/api/portfolios/"):
                self.send_json(compact_portfolio_payload(portfolio_store.update(_portfolio_id_from_path(parsed.path), self.read_json())))
                return
            if True:
                self.send_error(404)
                return
        except Exception as exc:
            self.send_error_json(exc)

    def do_DELETE(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/strategies/"):
                strategy_store.delete(_strategy_id_from_path(parsed.path))
                self.send_json({"ok": True})
                return
            if parsed.path.startswith("/api/portfolios/"):
                portfolio_store.delete(_portfolio_id_from_path(parsed.path))
                self.send_json({"ok": True})
                return
            if True:
                self.send_error(404)
                return
        except Exception as exc:
            self.send_error_json(exc)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def serve_static(self, path: str) -> None:
        if path in {"", "/"}:
            file_path = STATIC_DIR / "index.html"
        else:
            rel = unquote(path).lstrip("/")
            file_path = (STATIC_DIR / rel).resolve()
            if not file_path.is_relative_to(STATIC_DIR.resolve()):
                self.send_error(403)
                return
        if not file_path.exists() or not file_path.is_file():
            self.send_error(404)
            return
        body = file_path.read_bytes()
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, exc: Exception) -> None:
        traceback.print_exc()
        self.send_json({"error": str(exc)}, status=400)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), fmt % args))


def create_server(port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def _strategy_id_from_path(path: str) -> str:
    return unquote(path.rstrip("/").rsplit("/", 1)[-1])


def _portfolio_id_from_path(path: str) -> str:
    return unquote(path.rstrip("/").rsplit("/", 1)[-1])


def _portfolio_snapshot_path(path: str) -> tuple[str, str]:
    parts = path.strip("/").split("/")
    if len(parts) != 5 or parts[:2] != ["api", "portfolios"] or parts[3] != "snapshots":
        raise ValueError("非法组合快照路径")
    return unquote(parts[2]), _data_frequency(unquote(parts[4]))


def ensure_legacy_strategy_portfolios() -> None:
    global _legacy_strategy_portfolios_checked
    if _legacy_strategy_portfolios_checked:
        return
    portfolio_store.import_legacy_strategies(STRATEGY_DIR)
    _legacy_strategy_portfolios_checked = True


def run_backtest_payload(payload: dict[str, Any], *, include_exposure: bool = True) -> dict[str, Any]:
    request, core = _run_backtest_core_payload(payload)
    result = core["result"]
    if include_exposure:
        _attach_exposure_snapshot(result, request, core)
    return result


def _run_backtest_core_payload(payload: dict[str, Any]) -> tuple[BacktestRequest, dict[str, Any]]:
    store.ensure_loaded()
    request = build_backtest_request(payload)
    core = run_fund_backtest_core(request, store.funds, store.benchmarks)
    return request, core


def _attach_exposure_snapshot(result: dict[str, Any], request: BacktestRequest, core: dict[str, Any] | None = None) -> None:
    try:
        window = result.get("window", {})
        result["exposure"] = build_exposure_snapshot(
            DATA_DIR,
            result.get("funds") or [],
            result.get("weights") or {},
            request.sleeves,
            window.get("end") or request.end_date or "2026-06-30",
            start_date=window.get("start") or request.start_date,
            weight_history=core.get("end_weights") if isinstance(core, dict) else None,
        )
    except FileNotFoundError:
        result["exposure"] = None


def run_backtest_payloads(payload: dict[str, Any], required_frequency: str) -> dict[str, dict[str, Any]]:
    required = _data_frequency(required_frequency)
    snapshots: dict[str, dict[str, Any]] = {}
    cores: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    errors: dict[str, Exception] = {}
    for frequency in DATA_FREQUENCIES:
        try:
            _, core = _run_backtest_core_payload({**payload, "dataFrequency": frequency})
            cores[frequency] = core
            snapshots[frequency] = core["result"]
        except Exception as exc:
            errors[frequency] = exc
            failures.append(f"{DATA_FREQUENCY_LABELS[frequency]}回测失败：{exc}")
    if required not in snapshots:
        message = "；".join(failures) or f"{DATA_FREQUENCY_LABELS[required]}回测结果缺失"
        raise RuntimeError(message) from errors.get(required)
    if failures:
        warnings = snapshots[required].setdefault("warnings", [])
        if isinstance(warnings, list):
            warnings.extend(failures)
    request = build_backtest_request({**payload, "dataFrequency": required})
    _attach_exposure_snapshot(snapshots[required], request, cores.get(required))
    exposure = snapshots[required].get("exposure")
    for frequency, snapshot in snapshots.items():
        if frequency != required:
            snapshot["exposure"] = exposure
    return snapshots


def run_analysis_window_payload(portfolio: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    frequency = _data_frequency(payload.get("frequency") or payload.get("dataFrequency") or portfolio.get("dataFrequency"))
    start_date = payload.get("startDate") or payload.get("start_date") or portfolio.get("startDate")
    end_date = payload.get("endDate") or payload.get("end_date") or portfolio.get("endDate")
    store.ensure_loaded()
    request = build_backtest_request({**portfolio, "dataFrequency": frequency})
    return run_fund_backtest_analysis_window(
        request,
        store.funds,
        store.benchmarks,
        str(start_date or portfolio.get("startDate") or ""),
        str(end_date or portfolio.get("endDate") or ""),
    )


def latest_data_end_date() -> str:
    store.ensure_loaded()
    return store.benchmarks["000300.SH"].nav.index.max().strftime("%Y-%m-%d")


def build_backtest_request(payload: dict[str, Any]) -> BacktestRequest:
    rebalance_plan = payload.get("rebalancePlan") or payload.get("rebalance_plan")
    funds = payload.get("funds") or _funds_from_rebalance_plan(rebalance_plan)
    sleeves = payload.get("sleeves") or _sleeves_from_rebalance_plan(rebalance_plan)
    return BacktestRequest(
        funds=funds,
        sleeves=sleeves or None,
        weight_mode=payload.get("weightMode") or payload.get("weight_mode") or "equal",
        benchmark=payload.get("benchmark") or "000300.SH",
        start_date=payload.get("startDate") or payload.get("start_date") or "2021-01-01",
        end_date=payload.get("endDate") or payload.get("end_date") or latest_data_end_date(),
        rebalance_plan=rebalance_plan,
        initial_cash=_optional_float(payload.get("initialCash") if "initialCash" in payload else payload.get("initial_cash")),
        fee_config=payload.get("feeConfig") or payload.get("fee_config"),
        data_frequency=_data_frequency(payload.get("dataFrequency") or payload.get("data_frequency")),
    )


def _funds_from_rebalance_plan(rebalance_plan: Any) -> list[dict[str, Any]]:
    ids: list[str] = []
    for node in rebalance_plan or []:
        for holding in node.get("holdings", []) if isinstance(node, dict) else []:
            fund_id = str(holding.get("fundId") or holding.get("id") or holding.get("code") or "").strip()
            if fund_id and fund_id not in ids:
                ids.append(fund_id)
    return [{"id": fund_id} for fund_id in ids]


def _sleeves_from_rebalance_plan(rebalance_plan: Any) -> list[dict[str, Any]]:
    sleeve_names = {"defensive": "防守压仓", "balanced": "核心稳健", "growth": "进攻弹性"}
    grouped: dict[str, list[str]] = {}
    for node in rebalance_plan or []:
        if not isinstance(node, dict):
            continue
        for holding in node.get("holdings", []):
            fund_id = str(holding.get("fundId") or holding.get("id") or holding.get("code") or "").strip()
            sleeve_id = str(holding.get("sleeveId") or holding.get("sleeve") or "ungrouped").strip() or "ungrouped"
            if not fund_id:
                continue
            grouped.setdefault(sleeve_id, [])
            if fund_id not in grouped[sleeve_id]:
                grouped[sleeve_id].append(fund_id)
    return [
        {"id": sleeve_id, "name": sleeve_names.get(sleeve_id, sleeve_id), "funds": funds}
        for sleeve_id, funds in grouped.items()
    ]


def augment_portfolio_payload(portfolio: dict[str, Any]) -> dict[str, Any]:
    store.ensure_loaded()
    active_snapshot = _active_backtest_snapshot(portfolio)
    payload = {**portfolio}
    if active_snapshot is not None:
        payload["lastBacktestSnapshot"] = active_snapshot
    return {**payload, "latestHoldings": latest_holdings_payload(payload)}


def compact_portfolio_payload(portfolio: dict[str, Any], *, include_active_snapshot: bool = False) -> dict[str, Any]:
    store.ensure_loaded()
    active_snapshot = _active_backtest_snapshot(portfolio)
    payload = {
        key: value
        for key, value in portfolio.items()
        if key not in {"backtestSnapshots", "lastBacktestSnapshot", "latestHoldings"}
    }
    snapshots = portfolio.get("backtestSnapshots") if isinstance(portfolio.get("backtestSnapshots"), dict) else {}
    payload["hasBacktest"] = active_snapshot is not None
    available_frequencies = [frequency for frequency in DATA_FREQUENCIES if isinstance(snapshots.get(frequency), dict)]
    if active_snapshot is not None:
        active_frequency = _snapshot_frequency(portfolio, active_snapshot)
        if active_frequency not in available_frequencies:
            available_frequencies.append(active_frequency)
    payload["availableFrequencies"] = available_frequencies
    if include_active_snapshot and active_snapshot is not None:
        payload["lastBacktestSnapshot"] = active_snapshot
    payload["latestHoldings"] = latest_holdings_payload({**portfolio, "lastBacktestSnapshot": active_snapshot} if active_snapshot else portfolio)
    return payload


def _active_backtest_snapshot(portfolio: dict[str, Any]) -> dict[str, Any] | None:
    snapshots = portfolio.get("backtestSnapshots") if isinstance(portfolio.get("backtestSnapshots"), dict) else {}
    frequency = _data_frequency(portfolio.get("dataFrequency"))
    snapshot = snapshots.get(frequency)
    if isinstance(snapshot, dict):
        return snapshot
    last_snapshot = portfolio.get("lastBacktestSnapshot")
    return last_snapshot if isinstance(last_snapshot, dict) else None


def _backtest_snapshot_for_frequency(portfolio: dict[str, Any], frequency: str) -> dict[str, Any] | None:
    snapshots = portfolio.get("backtestSnapshots") if isinstance(portfolio.get("backtestSnapshots"), dict) else {}
    normalized = _data_frequency(frequency)
    snapshot = snapshots.get(normalized)
    if isinstance(snapshot, dict):
        return snapshot
    last_snapshot = portfolio.get("lastBacktestSnapshot")
    if isinstance(last_snapshot, dict) and _snapshot_frequency(portfolio, last_snapshot) == normalized:
        return last_snapshot
    return None


def _snapshot_frequency(portfolio: dict[str, Any], snapshot: dict[str, Any]) -> str:
    if "frequency" in snapshot:
        return _data_frequency(snapshot.get("frequency"))
    return _data_frequency(portfolio.get("dataFrequency"))


def latest_holdings_payload(portfolio: dict[str, Any]) -> list[dict[str, Any]]:
    snapshot = portfolio.get("lastBacktestSnapshot") if isinstance(portfolio.get("lastBacktestSnapshot"), dict) else {}
    frequency = _portfolio_frequency(portfolio, snapshot)
    target_weights = _latest_target_weights(portfolio)
    final_positions = snapshot.get("final_positions") if isinstance(snapshot.get("final_positions"), list) else []
    if final_positions:
        return [_latest_holding_from_position(position, frequency, target_weights) for position in final_positions if isinstance(position, dict)]
    plan = portfolio.get("rebalancePlan") if isinstance(portfolio.get("rebalancePlan"), list) else []
    nodes = [node for node in plan if isinstance(node, dict) and isinstance(node.get("holdings"), list)]
    if not nodes:
        return []
    latest = max(nodes, key=lambda node: str(node.get("date") or ""))
    rebalance_date = pd.to_datetime(latest.get("date"), errors="coerce")
    rows: list[dict[str, Any]] = []
    drift_values: list[float] = []
    for holding in latest.get("holdings") or []:
        fund_id = str(holding.get("fundId") or holding.get("id") or holding.get("code") or "").strip()
        fund = store.funds.get(fund_id)
        meta = getattr(store, "meta", {}).get(fund_id)
        if not fund:
            continue
        nav = _frequency_nav(fund.nav, frequency)
        latest_nav = _latest_nav_point(nav)
        target_weight = _weight_fraction(holding.get("weight"))
        drift_values.append(_drifted_holding_value(nav, rebalance_date, target_weight))
        rows.append(
            {
                "fundId": fund_id,
                "name": fund.name,
                "manager": fund.manager,
                "weight": target_weight,
                "targetWeight": target_weight,
                "latestNav": latest_nav[1],
                "navDate": latest_nav[0].strftime("%Y-%m-%d") if latest_nav[0] is not None else "",
                "dailyReturn": _period_return(nav),
                "ytdReturn": _ytd_return(nav),
                "sleeveId": holding.get("sleeveId") or "",
                "manager_start_date": getattr(meta, "manager_start_date", "") if meta else "",
            }
        )
    total_value = sum(drift_values)
    if total_value > 0:
        for row, value in zip(rows, drift_values):
            row["weight"] = value / total_value
    return rows


def _latest_target_weights(portfolio: dict[str, Any]) -> dict[str, float]:
    plan = portfolio.get("rebalancePlan") if isinstance(portfolio.get("rebalancePlan"), list) else []
    nodes = [node for node in plan if isinstance(node, dict) and isinstance(node.get("holdings"), list)]
    if not nodes:
        return {}
    latest = max(nodes, key=lambda node: str(node.get("date") or ""))
    weights: dict[str, float] = {}
    for holding in latest.get("holdings") or []:
        fund_id = str(holding.get("fundId") or holding.get("id") or holding.get("code") or "").strip()
        if fund_id:
            weights[fund_id] = _weight_fraction(holding.get("weight"))
    return weights


def _latest_holding_from_position(position: dict[str, Any], frequency: str = "daily", target_weights: dict[str, float] | None = None) -> dict[str, Any]:
    fund_id = str(position.get("fundId") or position.get("id") or "").strip()
    fund = store.funds.get(fund_id)
    nav = _frequency_nav(fund.nav, frequency) if fund else pd.Series(dtype=float)
    latest_nav = _latest_nav_point(nav) if fund else (None, None)
    target_weights = target_weights or {}
    return {
        "fundId": fund_id,
        "name": position.get("name") or (fund.name if fund else fund_id),
        "manager": position.get("manager") or (fund.manager if fund else ""),
        "weight": position.get("weight"),
        "targetWeight": target_weights.get(fund_id, position.get("targetWeight") or position.get("target_weight")),
        "quantity": position.get("quantity"),
        "marketValue": position.get("market_value"),
        "costAmount": position.get("cost_amount"),
        "costBasis": position.get("cost_basis"),
        "profit": position.get("profit"),
        "profitPct": position.get("profit_pct"),
        "latestNav": latest_nav[1],
        "navDate": latest_nav[0].strftime("%Y-%m-%d") if latest_nav[0] is not None else "",
        "dailyReturn": _period_return(nav) if fund else None,
        "ytdReturn": _ytd_return(nav) if fund else None,
        "sleeveId": position.get("sleeveId") or "",
    }


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _data_frequency(value: Any) -> str:
    frequency = str(value or "daily").strip().lower()
    return frequency if frequency in {"daily", "weekly", "monthly"} else "daily"


def _portfolio_frequency(portfolio: dict[str, Any], snapshot: dict[str, Any]) -> str:
    return _data_frequency(portfolio.get("dataFrequency") or snapshot.get("frequency"))


def _weight_fraction(value: Any) -> float:
    number = float(value or 0)
    return number / 100 if number > 1 else number


def _frequency_nav(nav: pd.Series, frequency: str) -> pd.Series:
    clean = nav.dropna().sort_index().astype(float)
    if frequency == "daily" or clean.empty:
        return clean
    period_freq = "W-FRI" if frequency == "weekly" else "M"
    return clean.groupby(clean.index.to_period(period_freq)).tail(1)


def _latest_nav_point(nav: pd.Series) -> tuple[pd.Timestamp | None, float | None]:
    clean = nav.dropna().sort_index()
    if clean.empty:
        return None, None
    return pd.Timestamp(clean.index[-1]), float(clean.iloc[-1])


def _drifted_holding_value(nav: pd.Series, rebalance_date: pd.Timestamp, target_weight: float) -> float:
    clean = nav.dropna().sort_index()
    if clean.empty or pd.isna(rebalance_date) or target_weight <= 0:
        return 0.0
    start_candidates = clean[clean.index >= rebalance_date]
    if start_candidates.empty:
        return 0.0
    start_value = float(start_candidates.iloc[0])
    latest_value = float(clean.iloc[-1])
    return target_weight * latest_value / start_value if start_value > 0 else 0.0


def _ytd_return(nav: pd.Series) -> float | None:
    clean = nav.dropna().sort_index()
    if clean.empty:
        return None
    latest_date = pd.Timestamp(clean.index[-1])
    latest_value = float(clean.iloc[-1])
    year_start = pd.Timestamp(year=latest_date.year, month=1, day=1)
    previous_year = clean[clean.index < year_start]
    if not previous_year.empty:
        base_value = float(previous_year.iloc[-1])
    else:
        current_year = clean[clean.index >= year_start]
        if current_year.empty:
            return None
        base_value = float(current_year.iloc[0])
    if base_value <= 0:
        return None
    return float(latest_value / base_value - 1.0)


def _period_return(nav: pd.Series) -> float | None:
    clean = nav.dropna().sort_index()
    if len(clean) < 2:
        return None
    previous = float(clean.iloc[-2])
    return float(clean.iloc[-1] / previous - 1.0) if previous > 0 else None


def import_rebalance_plan(payload: dict[str, Any]) -> list[dict[str, Any]]:
    store.ensure_loaded()
    filename = str(payload.get("filename") or "").lower()
    content = payload.get("contentBase64")
    if not filename or not content:
        raise ValueError("导入文件不能为空")
    raw = base64.b64decode(str(content))
    frame = read_rebalance_file(filename, raw)
    required = ["调仓日期", "子组合", "基金代码", "基金名称", "权重%"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError("导入模板缺少列: " + ", ".join(missing))

    grouped: dict[str, list[dict[str, Any]]] = {}
    for _, row in frame.iterrows():
        date = pd.to_datetime(row.get("调仓日期"), errors="coerce")
        if pd.isna(date):
            raise ValueError("日期非法")
        fund_id = match_fund_id(row.get("基金代码"), row.get("基金名称"))
        if not fund_id:
            raise ValueError(f"基金无法匹配: {clean_text(row.get('基金代码')) or clean_text(row.get('基金名称'))}")
        try:
            weight = float(row.get("权重%"))
        except (TypeError, ValueError):
            raise ValueError(f"权重非法: {fund_id}") from None
        date_key = date.strftime("%Y-%m-%d")
        grouped.setdefault(date_key, []).append(
            {
                "fundId": fund_id,
                "sleeveId": sleeve_id_from_label(row.get("子组合")),
                "weight": weight,
            }
        )

    plan = [
        {"date": date, "source": "excel_imported", "holdings": holdings}
        for date, holdings in sorted(grouped.items())
    ]
    validate_imported_rebalance_plan(plan)
    return plan


def read_rebalance_file(filename: str, raw: bytes) -> pd.DataFrame:
    buffer = BytesIO(raw)
    if filename.endswith(".csv"):
        for encoding in ["utf-8-sig", "gbk"]:
            buffer.seek(0)
            try:
                return pd.read_csv(buffer, encoding=encoding)
            except UnicodeDecodeError:
                continue
        raise ValueError("CSV 编码无法识别")
    if filename.endswith(".xlsx") or filename.endswith(".xls"):
        return pd.read_excel(buffer)
    raise ValueError("仅支持 xlsx、xls 或 csv")


def match_fund_id(code: Any, name: Any) -> str:
    clean_code = clean_text(code)
    if clean_code and clean_code in store.funds:
        return clean_code
    clean_name = clean_text(name)
    if clean_name:
        for fund_id, fund in store.funds.items():
            if fund.name == clean_name:
                return fund_id
    return ""


def sleeve_id_from_label(value: Any) -> str:
    label = clean_text(value)
    if label in {"防守", "防守压仓", "defensive"}:
        return "defensive"
    if label in {"稳健", "核心稳健", "balanced", "stable"}:
        return "balanced"
    if label in {"进攻", "进攻弹性", "进攻增强", "growth", "aggressive"}:
        return "growth"
    return "balanced"


def validate_imported_rebalance_plan(plan: list[dict[str, Any]]) -> None:
    if not plan:
        raise ValueError("空调仓日")
    for node in plan:
        holdings = node.get("holdings") or []
        if not holdings:
            raise ValueError(f"{node.get('date')} 空调仓日")
        seen: set[str] = set()
        total = 0.0
        for holding in holdings:
            fund_id = str(holding.get("fundId") or "")
            if fund_id in seen:
                raise ValueError(f"{node.get('date')} 重复基金: {fund_id}")
            seen.add(fund_id)
            total += float(holding.get("weight") or 0.0)
        if abs(total - 100.0) > 0.01:
            raise ValueError(f"{node.get('date')} 权重不等于 100%，当前 {total:.2f}%")


def main() -> None:
    global _legacy_strategy_portfolios_checked
    port = int(sys.argv[1])
    if not 1 <= port <= 65535:
        raise ValueError("端口须在 1 至 65535 之间")
    with create_server(port) as server:
        store.ensure_loaded()
        initialize_state()
        # Seeded portfolios already include the legacy strategy and its import marker.
        # Re-running the old importer on start could undo an edit to that portfolio.
        _legacy_strategy_portfolios_checked = True
        print(f"FOF 组合工作台已启动: http://127.0.0.1:{port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
