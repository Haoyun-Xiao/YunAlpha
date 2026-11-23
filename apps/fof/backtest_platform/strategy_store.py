from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


STRATEGY_ID_RE = re.compile(r"^strategy_[0-9A-Za-z_-]+$")


class StrategyStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def list(self) -> list[dict[str, Any]]:
        self.directory.mkdir(parents=True, exist_ok=True)
        items = [self._summary_for(self._read(path)) for path in sorted(self.directory.glob("strategy_*.json"))]
        return sorted(items, key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        strategy = self._build_strategy(payload)
        self._write(strategy)
        return strategy

    def get(self, strategy_id: str) -> dict[str, Any]:
        return self._read(self._path_for(strategy_id))

    def update(self, strategy_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        strategy = self.get(strategy_id)
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("策略名称不能为空")
        strategy["name"] = name
        strategy["description"] = str(payload.get("description") or "").strip()
        strategy["updated_at"] = _now_iso()
        self._write(strategy)
        return strategy

    def delete(self, strategy_id: str) -> None:
        self._path_for(strategy_id).unlink()

    def _build_strategy(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("策略名称不能为空")
        config = payload.get("config")
        if not isinstance(config, dict):
            raise ValueError("策略配置不能为空")
        result_snapshot = payload.get("result_snapshot")
        if not isinstance(result_snapshot, dict):
            raise ValueError("回测结果快照不能为空")
        _validate_result_snapshot(result_snapshot)

        now = _now_iso()
        strategy = {
            "id": _new_strategy_id(),
            "name": name,
            "description": str(payload.get("description") or "").strip(),
            "created_at": now,
            "updated_at": now,
            "config": config,
            "result_snapshot": result_snapshot,
            "summary": _extract_summary(config, result_snapshot),
        }
        return strategy

    def _path_for(self, strategy_id: str) -> Path:
        if not STRATEGY_ID_RE.fullmatch(strategy_id):
            raise ValueError("非法策略 ID")
        return self.directory / f"{strategy_id}.json"

    def _read(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError("策略不存在")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("策略文件格式错误")
        return data

    def _write(self, strategy: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path_for(str(strategy["id"]))
        path.write_text(json.dumps(strategy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _summary_for(strategy: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": strategy.get("id"),
            "name": strategy.get("name"),
            "description": strategy.get("description") or "",
            "created_at": strategy.get("created_at"),
            "updated_at": strategy.get("updated_at"),
            "summary": strategy.get("summary") or {},
        }


def _new_strategy_id() -> str:
    return "strategy_" + datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _extract_summary(config: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    metrics = result.get("metrics") or {}
    window = result.get("window") or {}
    funds = result.get("funds") or config.get("funds") or []
    start = window.get("start") or config.get("startDate") or ""
    end = window.get("end") or config.get("endDate") or ""
    return {
        "annual_return": metrics.get("annual_return"),
        "max_drawdown": metrics.get("max_drawdown"),
        "annual_volatility": metrics.get("annual_volatility"),
        "sharpe": metrics.get("sharpe"),
        "calmar": metrics.get("calmar"),
        "fund_count": len(funds) if isinstance(funds, list) else 0,
        "window": f"{start} 至 {end}" if start and end else "",
    }


def _validate_result_snapshot(result: dict[str, Any]) -> None:
    if result.get("error"):
        raise ValueError("回测结果快照格式错误")
    if not isinstance(result.get("metrics"), dict):
        raise ValueError("回测结果快照格式错误")
    if not isinstance(result.get("window"), dict):
        raise ValueError("回测结果快照格式错误")
    if not isinstance(result.get("funds"), list):
        raise ValueError("回测结果快照格式错误")
