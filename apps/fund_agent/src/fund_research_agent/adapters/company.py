from __future__ import annotations

from pathlib import Path

import yaml


class CompanyShortNameResolver:
    def __init__(self, config_path: Path) -> None:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        self.suffixes = sorted(raw.get("suffixes", []), key=len, reverse=True)
        self.aliases = raw.get("aliases", {})

    def resolve(self, full_name: str) -> str:
        compact = "".join(str(full_name).split())
        if not compact:
            return ""
        if compact in self.aliases:
            return str(self.aliases[compact]).strip()
        for suffix in self.suffixes:
            if compact.endswith(suffix):
                compact = compact[: -len(suffix)]
                break
        compact = compact.removesuffix("基金").removesuffix("资管")
        return compact or full_name

    def queries(self, company_full_name: str, manager_name: str) -> list[str]:
        short = self.resolve(company_full_name)
        return [f"{short} {manager_name} {keyword}" for keyword in ("路演", "交流", "访谈")]

