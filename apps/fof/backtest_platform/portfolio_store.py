from __future__ import annotations

import json
import re
from calendar import monthrange
from datetime import datetime
from pathlib import Path
from typing import Any


PORTFOLIO_ID_RE = re.compile(r"^portfolio_[0-9A-Za-z_-]+$")
LEGACY_IMPORT_MARKER = ".legacy_strategy_imports.json"
LEGACY_INITIAL_CASH = 200_000.0
DEFAULT_FEE_CONFIG = {"purchase": 0.0, "redemption": 0.0, "transaction": 0.0}
BACKTEST_INPUT_FIELDS = ("rebalancePlan", "startDate", "endDate", "benchmark", "initialCash", "feeConfig")


class PortfolioStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def list(self) -> list[dict[str, Any]]:
        self.directory.mkdir(parents=True, exist_ok=True)
        items = [self._summary_for(self._read(path)) for path in sorted(self.directory.glob("portfolio_*.json"))]
        ordered = [item for item in items if item.get("displayOrder") is not None]
        unordered = [item for item in items if item.get("displayOrder") is None]
        ordered.sort(key=lambda item: (_number(item.get("displayOrder")), str(item.get("id") or "")))
        unordered.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return ordered + unordered

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        portfolio = self._build_portfolio(payload)
        self._write(portfolio)
        return portfolio

    def get(self, portfolio_id: str) -> dict[str, Any]:
        return self._read(self._path_for(portfolio_id))

    def update(self, portfolio_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        existing = self.get(portfolio_id)
        merged = {**payload, "id": portfolio_id, "created_at": existing.get("created_at")}
        if "displayOrder" not in payload and existing.get("displayOrder") is not None:
            merged["displayOrder"] = existing["displayOrder"]
        portfolio = self._build_portfolio(merged)
        inputs_changed = _backtest_inputs_changed(existing, portfolio)
        if inputs_changed:
            portfolio["backtestSnapshots"] = {}
            portfolio["lastBacktestSnapshot"] = None
        elif "backtestSnapshots" not in payload and isinstance(existing.get("backtestSnapshots"), dict):
            portfolio["backtestSnapshots"] = existing["backtestSnapshots"]
        if not inputs_changed and "lastBacktestSnapshot" not in payload and isinstance(existing.get("lastBacktestSnapshot"), dict):
            portfolio["lastBacktestSnapshot"] = existing["lastBacktestSnapshot"]
        portfolio["updated_at"] = _now_iso()
        self._write(portfolio)
        return portfolio

    def delete(self, portfolio_id: str) -> None:
        self._path_for(portfolio_id).unlink()

    def reorder(self, portfolio_ids: list[str]) -> list[dict[str, Any]]:
        self.directory.mkdir(parents=True, exist_ok=True)
        requested_ids = [str(portfolio_id) for portfolio_id in portfolio_ids if str(portfolio_id or "").strip()]
        if not requested_ids:
            raise ValueError("排序列表不能为空")
        if len(set(requested_ids)) != len(requested_ids):
            raise ValueError("排序列表包含重复组合")

        portfolios = {path.stem: self._read(path) for path in sorted(self.directory.glob("portfolio_*.json"))}
        missing = [portfolio_id for portfolio_id in requested_ids if portfolio_id not in portfolios]
        if missing:
            raise FileNotFoundError("组合不存在")

        existing_order = [item["id"] for item in self.list() if item.get("id") in portfolios]
        remaining = [portfolio_id for portfolio_id in existing_order if portfolio_id not in requested_ids]
        for index, portfolio_id in enumerate(requested_ids + remaining):
            portfolios[portfolio_id]["displayOrder"] = index
            self._write(portfolios[portfolio_id])
        return self.list()

    def save_backtest_snapshot(self, portfolio_id: str, result: dict[str, Any]) -> dict[str, Any]:
        frequency = _data_frequency(result.get("frequency"))
        return self.save_backtest_snapshots(portfolio_id, {frequency: result}, frequency)

    def save_backtest_snapshots(
        self,
        portfolio_id: str,
        snapshots: dict[str, dict[str, Any]],
        active_frequency: str,
    ) -> dict[str, Any]:
        portfolio = self.get(portfolio_id)
        cleaned = _backtest_snapshots(snapshots)
        frequency = _data_frequency(active_frequency)
        active_snapshot = cleaned.get(frequency)
        if active_snapshot is None and cleaned:
            frequency, active_snapshot = next(iter(cleaned.items()))
        portfolio["dataFrequency"] = frequency
        portfolio["backtestSnapshots"] = cleaned
        portfolio["lastBacktestSnapshot"] = active_snapshot
        portfolio["updated_at"] = _now_iso()
        self._write(portfolio)
        return portfolio

    def import_legacy_strategies(self, strategy_dir: Path) -> int:
        if not strategy_dir.exists():
            return 0
        self.directory.mkdir(parents=True, exist_ok=True)
        imported = self._read_import_marker()
        changed = False
        count = 0
        for path in sorted(strategy_dir.glob("strategy_*.json")):
            strategy = self._read_legacy_strategy(path)
            strategy_id = str(strategy.get("id") or path.stem)
            portfolio_id = imported.get(strategy_id) or _legacy_portfolio_id(strategy_id)
            if strategy_id in imported:
                portfolio_path = self._path_for(portfolio_id)
                if portfolio_path.exists():
                    portfolio = self._read(portfolio_path)
                    if _ensure_legacy_cash_defaults(portfolio):
                        self._write(portfolio)
                continue
            if self._path_for(portfolio_id).exists():
                portfolio = self.get(portfolio_id)
                if _ensure_legacy_cash_defaults(portfolio):
                    self._write(portfolio)
                imported[strategy_id] = portfolio_id
                changed = True
                continue
            portfolio = _portfolio_from_legacy_strategy(strategy, portfolio_id)
            self._write(portfolio)
            imported[strategy_id] = portfolio_id
            changed = True
            count += 1
        if changed:
            self._write_import_marker(imported)
        return count

    def _build_portfolio(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("组合名称不能为空")
        benchmark = str(payload.get("benchmark") or "").strip()
        if not benchmark:
            raise ValueError("基准不能为空")
        rebalance_plan = payload.get("rebalancePlan")
        if not isinstance(rebalance_plan, list) or not rebalance_plan:
            raise ValueError("调仓计划不能为空")

        now = _now_iso()
        portfolio = {
            "id": str(payload.get("id") or _new_portfolio_id()),
            "name": name,
            "benchmark": benchmark,
            "startDate": str(payload.get("startDate") or "").strip(),
            "endDate": str(payload.get("endDate") or "").strip(),
            "dataFrequency": _data_frequency(payload.get("dataFrequency")),
            "rebalanceFrequency": _rebalance_frequency(payload.get("rebalanceFrequency")),
            "globalTemplate": str(payload.get("globalTemplate") or "").strip(),
            "rebalancePlan": rebalance_plan,
            "displayOrder": _display_order(payload.get("displayOrder"), self._next_display_order()),
            "backtestSnapshots": _backtest_snapshots(payload.get("backtestSnapshots")),
            "lastBacktestSnapshot": payload.get("lastBacktestSnapshot"),
            "created_at": str(payload.get("created_at") or now),
            "updated_at": str(payload.get("updated_at") or now),
        }
        if payload.get("initialCash") not in (None, ""):
            portfolio["initialCash"] = _number(payload.get("initialCash"))
        fee_config = payload.get("feeConfig")
        if isinstance(fee_config, dict):
            portfolio["feeConfig"] = {
                "purchase": max(_number(fee_config.get("purchase")), 0.0),
                "redemption": max(_number(fee_config.get("redemption")), 0.0),
                "transaction": max(_number(fee_config.get("transaction")), 0.0),
            }
        return portfolio

    def _path_for(self, portfolio_id: str) -> Path:
        if not PORTFOLIO_ID_RE.fullmatch(portfolio_id):
            raise ValueError("非法组合 ID")
        return self.directory / f"{portfolio_id}.json"

    def _read(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError("组合不存在")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("组合文件格式错误")
        return data

    def _write(self, portfolio: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path_for(str(portfolio["id"]))
        path.write_text(json.dumps(portfolio, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _read_import_marker(self) -> dict[str, str]:
        path = self.directory / LEGACY_IMPORT_MARKER
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}

    def _write_import_marker(self, imported: dict[str, str]) -> None:
        path = self.directory / LEGACY_IMPORT_MARKER
        path.write_text(json.dumps(imported, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _read_legacy_strategy(path: Path) -> dict[str, Any]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("策略文件格式错误")
        return data

    @staticmethod
    def _summary_for(portfolio: dict[str, Any]) -> dict[str, Any]:
        plan = portfolio.get("rebalancePlan") if isinstance(portfolio.get("rebalancePlan"), list) else []
        latest = max((str(node.get("date") or "") for node in plan if isinstance(node, dict)), default="")
        last_snapshot = portfolio.get("lastBacktestSnapshot") if isinstance(portfolio.get("lastBacktestSnapshot"), dict) else None
        return {
            "id": portfolio.get("id"),
            "name": portfolio.get("name"),
            "benchmark": portfolio.get("benchmark"),
            "startDate": portfolio.get("startDate"),
            "endDate": portfolio.get("endDate"),
            "dataFrequency": _data_frequency(portfolio.get("dataFrequency")),
            "rebalanceFrequency": _rebalance_frequency(portfolio.get("rebalanceFrequency")),
            "globalTemplate": portfolio.get("globalTemplate"),
            "cashMode": portfolio.get("initialCash") not in (None, ""),
            "initialCash": portfolio.get("initialCash"),
            "latestHoldingDate": latest,
            "rebalanceCount": len(plan),
            "hasBacktest": last_snapshot is not None,
            "summary": _snapshot_summary(last_snapshot),
            "displayOrder": _display_order(portfolio.get("displayOrder")),
            "created_at": portfolio.get("created_at"),
            "updated_at": portfolio.get("updated_at"),
        }

    def _next_display_order(self) -> int:
        orders = []
        for path in self.directory.glob("portfolio_*.json"):
            try:
                value = _display_order(self._read(path).get("displayOrder"))
            except Exception:
                value = None
            if value is not None:
                orders.append(value)
        return int(max(orders, default=-1) + 1)


def _new_portfolio_id() -> str:
    return "portfolio_" + datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")


def _data_frequency(value: Any) -> str:
    frequency = str(value or "daily").strip().lower()
    return frequency if frequency in {"daily", "weekly", "monthly"} else "daily"


def _rebalance_frequency(value: Any) -> str:
    frequency = str(value or "quarterly").strip().lower()
    return frequency if frequency in {"none", "monthly", "quarterly", "semiannual"} else "quarterly"


def _backtest_snapshots(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    snapshots: dict[str, dict[str, Any]] = {}
    for key, snapshot in value.items():
        frequency = _data_frequency(key)
        if isinstance(snapshot, dict):
            snapshots[frequency] = snapshot
    return snapshots


def _backtest_inputs_changed(existing: dict[str, Any], updated: dict[str, Any]) -> bool:
    return any(_stable_json(existing.get(field)) != _stable_json(updated.get(field)) for field in BACKTEST_INPUT_FIELDS)


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _portfolio_from_legacy_strategy(strategy: dict[str, Any], portfolio_id: str) -> dict[str, Any]:
    config = strategy.get("config") if isinstance(strategy.get("config"), dict) else {}
    payload = config.get("backtestPayload") if isinstance(config.get("backtestPayload"), dict) else {}
    result = strategy.get("result_snapshot") if isinstance(strategy.get("result_snapshot"), dict) else None
    window = result.get("window") if isinstance(result, dict) and isinstance(result.get("window"), dict) else {}
    start_date = str(config.get("startDate") or payload.get("startDate") or window.get("start") or "2021-01-01")
    end_date = str(config.get("endDate") or payload.get("endDate") or window.get("end") or "2026-06-30")
    holdings = _legacy_holdings(config, payload)
    sleeve_targets = _legacy_sleeve_targets(config.get("sleeves"), holdings)
    return {
        "id": portfolio_id,
        "name": str(strategy.get("name") or "旧策略导入组合").strip(),
        "benchmark": str(config.get("benchmark") or payload.get("benchmark") or "000300.SH"),
        "startDate": start_date,
        "endDate": end_date,
        "dataFrequency": _data_frequency(config.get("dataFrequency") or payload.get("dataFrequency")),
        "globalTemplate": _legacy_global_template(config.get("weightMode"), sleeve_targets),
        "rebalancePlan": [
            {
                "date": date,
                "source": "initial" if index == 0 else "template",
                "sleeveTargets": sleeve_targets,
                "holdings": holdings,
            }
            for index, date in enumerate(_legacy_rebalance_dates(start_date, end_date))
        ],
        "initialCash": LEGACY_INITIAL_CASH,
        "feeConfig": dict(DEFAULT_FEE_CONFIG),
        "lastBacktestSnapshot": result,
        "created_at": str(strategy.get("created_at") or _now_iso()),
        "updated_at": str(strategy.get("updated_at") or _now_iso()),
    }


def _ensure_legacy_cash_defaults(portfolio: dict[str, Any]) -> bool:
    changed = False
    if portfolio.get("initialCash") in (None, ""):
        portfolio["initialCash"] = LEGACY_INITIAL_CASH
        changed = True
    fee_config = portfolio.get("feeConfig")
    if not isinstance(fee_config, dict):
        portfolio["feeConfig"] = dict(DEFAULT_FEE_CONFIG)
        changed = True
    else:
        for key, default in DEFAULT_FEE_CONFIG.items():
            if key not in fee_config:
                fee_config[key] = default
                changed = True
    return changed


def _legacy_holdings(config: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    sleeves = config.get("sleeves") if isinstance(config.get("sleeves"), list) else []
    sleeve_by_fund: dict[str, str] = {}
    for sleeve in sleeves:
        sleeve_id = str(sleeve.get("id") or "").strip() if isinstance(sleeve, dict) else ""
        for fund in sleeve.get("funds", []) if isinstance(sleeve, dict) else []:
            fund_id = _fund_id(fund)
            if fund_id:
                sleeve_by_fund[fund_id] = sleeve_id or "balanced"

    payload_funds = payload.get("funds") if isinstance(payload.get("funds"), list) else []
    rows = [
        {
            "fundId": _fund_id(fund),
            "sleeveId": sleeve_by_fund.get(_fund_id(fund), "balanced"),
            "weight": _number(fund.get("weight") if isinstance(fund, dict) else 0),
        }
        for fund in payload_funds
        if _fund_id(fund)
    ]
    if not rows:
        rows = _legacy_holdings_from_sleeves(sleeves)
    return _normalize_holding_weights(rows)


def _legacy_holdings_from_sleeves(sleeves: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sleeve in sleeves:
        if not isinstance(sleeve, dict):
            continue
        funds = [fund for fund in sleeve.get("funds", []) if _fund_id(fund)]
        if not funds:
            continue
        sleeve_weight = _number(sleeve.get("weight"))
        equal_weight = sleeve_weight / len(funds) if sleeve_weight > 0 else 0
        for fund in funds:
            rows.append({"fundId": _fund_id(fund), "sleeveId": str(sleeve.get("id") or "balanced"), "weight": equal_weight})
    return rows


def _legacy_sleeve_targets(raw_sleeves: Any, holdings: list[dict[str, Any]]) -> dict[str, float]:
    targets = {"defensive": 0.0, "balanced": 0.0, "growth": 0.0}
    if isinstance(raw_sleeves, list):
        for sleeve in raw_sleeves:
            if isinstance(sleeve, dict) and str(sleeve.get("id") or "") in targets:
                targets[str(sleeve.get("id"))] = _number(sleeve.get("weight"))
    if abs(sum(targets.values()) - 100.0) <= 0.01:
        return targets
    targets = {"defensive": 0.0, "balanced": 0.0, "growth": 0.0}
    for holding in holdings:
        sleeve_id = str(holding.get("sleeveId") or "balanced")
        if sleeve_id in targets:
            targets[sleeve_id] += _number(holding.get("weight"))
    return {key: round(value, 4) for key, value in targets.items()}


def _legacy_global_template(weight_mode: Any, targets: dict[str, float]) -> str:
    mode = str(weight_mode or "")
    if mode == "template_defensive":
        return "defensive"
    if mode == "template_stable":
        return "stable"
    if mode == "template_aggressive":
        return "aggressive"
    ratios = {
        "defensive": {"defensive": 50.0, "balanced": 40.0, "growth": 10.0},
        "stable": {"defensive": 30.0, "balanced": 40.0, "growth": 30.0},
        "aggressive": {"defensive": 10.0, "balanced": 30.0, "growth": 60.0},
        "equal": {"defensive": 33.3333, "balanced": 33.3333, "growth": 33.3334},
    }
    for name, ratio in ratios.items():
        if all(abs(_number(targets.get(key)) - value) < 0.1 for key, value in ratio.items()):
            return name
    return "equal"


def _legacy_rebalance_dates(start_date: str, end_date: str) -> list[str]:
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        return [start_date]
    dates = [start.isoformat()]
    for year in range(start.year, end.year + 1):
        for month in (1, 4, 7, 10):
            day = monthrange(year, month)[1]
            current = datetime(year, month, day).date()
            if start < current <= end:
                dates.append(current.isoformat())
    return sorted(dict.fromkeys(dates))


def _normalize_holding_weights(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        raise ValueError("旧策略没有可导入基金")
    total = sum(_number(row.get("weight")) for row in rows)
    if total <= 0:
        equal = 100.0 / len(rows)
        for row in rows:
            row["weight"] = equal
    elif abs(total - 100.0) > 0.01:
        for row in rows:
            row["weight"] = _number(row.get("weight")) / total * 100.0
    used = 0.0
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        weight = round(100.0 - used, 4) if index == len(rows) - 1 else round(_number(row.get("weight")), 4)
        used += weight
        normalized.append({"fundId": str(row.get("fundId")), "sleeveId": str(row.get("sleeveId") or "balanced"), "weight": weight})
    return normalized


def _fund_id(fund: Any) -> str:
    return str(fund.get("id") or fund.get("fundId") or fund.get("code") or "").strip() if isinstance(fund, dict) else ""


def _number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _display_order(value: Any, default: Any = None) -> int | None:
    raw = default if value in (None, "") else value
    if raw in (None, ""):
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _legacy_portfolio_id(strategy_id: str) -> str:
    return "portfolio_from_" + re.sub(r"[^0-9A-Za-z_-]", "_", strategy_id)


def _snapshot_summary(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not snapshot:
        return {}
    metrics = snapshot.get("metrics") or {}
    window = snapshot.get("window") or {}
    return {
        "annual_return": metrics.get("annual_return"),
        "max_drawdown": metrics.get("max_drawdown"),
        "sharpe": metrics.get("sharpe"),
        "finalValue": metrics.get("final_value"),
        "totalProfit": metrics.get("total_profit"),
        "cashBalance": metrics.get("cash_balance"),
        "window": f"{window.get('start')} 至 {window.get('end')}" if window.get("start") and window.get("end") else "",
    }
