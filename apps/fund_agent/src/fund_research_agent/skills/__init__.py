from __future__ import annotations

import re
from pathlib import Path

import yaml


class SkillLoader:
    def __init__(self, root: Path) -> None:
        self.root = root

    def load(self, name: str) -> dict:
        path = self.root / f"{name}.md"
        text = path.read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
        if not match:
            raise ValueError(f"invalid skill document: {path}")
        return (yaml.safe_load(match.group(1)) or {}) | {"instructions": match.group(2).strip()}
