from __future__ import annotations

from pathlib import Path

import yaml


def mounted_skills(root: Path) -> list[dict]:
    """List the same canonical files that Deep Agents mounts under /skills/."""
    items = []
    for path in sorted(root.glob("*/SKILL.md")):
        marker, frontmatter, body = path.read_text(encoding="utf-8").split("---", 2)
        if marker.strip():
            continue
        meta = yaml.safe_load(frontmatter)
        skill_id = meta["name"]
        metadata = meta.get("metadata") or {}
        first_line = body.strip().splitlines()[0]
        item = {
            "id": skill_id,
            "name": metadata.get("display-name")
            or (first_line.lstrip("# ").strip() if first_line.startswith("# ") else skill_id),
            "description": meta["description"],
            "instructions": body.strip(),
        }
        if meta.get("allowed-tools"):
            item["tools"] = meta["allowed-tools"].split()
        if metadata.get("starter-prompt"):
            item["starter_prompt"] = metadata["starter-prompt"]
        references = sorted(path.parent.glob("references/*.md"))
        if references:
            item["references"] = [
                {
                    "name": reference.relative_to(path.parent).as_posix(),
                    "content": reference.read_text(encoding="utf-8").strip(),
                }
                for reference in references
            ]
        items.append(item)
    return items
