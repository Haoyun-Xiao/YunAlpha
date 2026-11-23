from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fund_research_agent.adapters.model import ModelGateway
from fund_research_agent.config import HandbookSettings
from fund_research_agent.paths import WriteBoundary, sha256_file

FINAL_DISCLAIMER = "> 以上配置建议仅供参考，不构成投资建议。"
REQUIRED_JSON_KINDS = ("数据看板", "基金规模", "季度运作报告")


@dataclass(frozen=True)
class HandbookResult:
    path: Path
    quality: str
    warnings: list[str]
    candidate_used: bool
    source_manifest: dict[str, Any]


def _part_bodies(text: str) -> dict[int, str]:
    matches = list(re.finditer(r"(?m)^##\s+Part(10|[1-9])：", text))
    bodies = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        bodies[int(match.group(1))] = text[match.end() : end].strip()
    return bodies


def _repair_targets(problems: list[str]) -> list[int]:
    return sorted(
        {
            int(match.group(1))
            for problem in problems
            if (match := re.match(r"part(10|[1-9])_(?:count_|too_short)", problem))
        }
    )


def merge_repaired_parts(original: str, repaired: str, targets: list[int]) -> str:
    """Replace only explicitly targeted handbook parts and keep all other chapters."""
    original_bodies = _part_bodies(original)
    repaired_bodies = _part_bodies(repaired)
    missing = [number for number in targets if number not in repaired_bodies]
    if missing:
        raise ValueError(f"手册定向修复未返回 Part: {missing}")
    original_match = re.search(r"(?m)^##\s+Part(?:10|[1-9])：", original)
    preamble = original[: original_match.start()].strip() if original_match else ""
    sections = []
    for number in range(1, 11):
        body = repaired_bodies[number] if number in targets else original_bodies.get(number, "")
        if body:
            sections.append(f"## Part{number}：\n\n{body.strip()}")
    report = "\n\n".join(([preamble] if preamble else []) + sections).strip()
    report = re.sub(rf"\s*{re.escape(FINAL_DISCLAIMER)}\s*$", "", report).rstrip()
    return report + "\n\n" + FINAL_DISCLAIMER


def validate_handbook(text: str, manager: str, settings: HandbookSettings) -> dict[str, Any]:
    problems: list[str] = []
    warnings: list[str] = []
    bodies = _part_bodies(text)
    if manager not in text:
        problems.append("handbook_identity_missing")
    for number in range(1, 11):
        count = len(re.findall(rf"(?m)^##\s+Part{number}：", text))
        if count != 1:
            problems.append(f"part{number}_count_{count}")
        elif len(re.sub(r"\s+", "", bodies.get(number, ""))) < settings.min_part_chars:
            problems.append(f"part{number}_too_short")
    if "<!-- 待续" in text:
        problems.append("continuation_placeholder")
    if not text.rstrip().endswith(FINAL_DISCLAIMER):
        warnings.append("disclaimer_added")
    length = len(text)
    if length < settings.hard_min_chars:
        problems.append("below_hard_min_chars")
        quality = "failed"
    elif length < settings.soft_min_chars:
        warnings.append("degraded_handbook")
        quality = "degraded"
    elif length < settings.target_chars:
        warnings.append("short_handbook")
        quality = "short"
    else:
        quality = "target"
    return {"valid": not problems, "problems": problems, "warnings": warnings, "quality": quality, "characters": length}


class HandbookWorkflow:
    def __init__(
        self,
        model: ModelGateway,
        settings: HandbookSettings,
        boundary: WriteBoundary,
        skill_root: Path,
    ) -> None:
        self.model = model
        self.settings = settings
        self.boundary = boundary
        self.skill_root = skill_root

    def find_formal(self, formal_root: Path, manager: str) -> Path | None:
        candidates = sorted(formal_root.glob(f"*{manager}*"))
        candidates.extend(sorted(formal_root.glob(f"{manager}_report.*")))
        for path in candidates:
            if path.suffix.lower() in {".md", ".txt", ".docx"}:
                return path.resolve()
        return None

    @staticmethod
    def read_handbook(path: Path) -> str:
        if path.suffix.lower() in {".md", ".txt"}:
            return path.read_text(encoding="utf-8-sig", errors="ignore")
        import docx

        document = docx.Document(path)
        return "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip())

    async def build(
        self,
        manager_fund: str,
        json_files: dict[str, str],
        material_files: list[Path],
        output_dir: Path,
    ) -> HandbookResult:
        manager = manager_fund.split("_", 1)[0]
        missing = [kind for kind in REQUIRED_JSON_KINDS if kind not in json_files]
        if missing:
            raise ValueError("缺少投资手册必需 JSON：" + "、".join(missing))
        inputs = [Path(json_files[kind]) for kind in REQUIRED_JSON_KINDS] + material_files
        source_manifest = {
            "manager_fund": manager_fund,
            "inputs": [
                {"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}
                for path in inputs
            ],
        }
        sources = []
        for path in inputs:
            text = path.read_text(encoding="utf-8-sig", errors="ignore")
            sources.append(f"\n===== {path.name} =====\n{text}")
        source_text = "".join(sources)
        skill_files = [self.skill_root / "SKILL.md", *sorted((self.skill_root / "references").glob("*.md"))]
        skill = "\n\n".join(f"===== {path.relative_to(self.skill_root)} =====\n" + path.read_text(encoding="utf-8") for path in skill_files)
        batch_input_hash = hashlib.sha256(json.dumps({"inputs": source_manifest, "skill": {str(p): sha256_file(p) for p in skill_files}, "model": asdict(self.model.role("handbook"))}, sort_keys=True).encode()).hexdigest()
        batches = [
            "输出标题、投资摘要，以及完整 Part1 至 Part5，不要输出其他 Part。",
            "仅输出完整 Part6 至 Part8，不要重复标题、摘要或前批内容。",
            f"仅输出完整 Part9、Part10，并以固定声明“{FINAL_DISCLAIMER}”结束。",
        ]
        outputs: list[str] = []
        for index, instruction in enumerate(batches, start=1):
            checkpoint = output_dir / f"handbook_batch_{index}.md"
            key_path = checkpoint.with_suffix(".inputs.json")
            expected_key = hashlib.sha256((batch_input_hash + "".join(outputs)).encode()).hexdigest()
            if checkpoint.exists() and key_path.exists() and key_path.read_text() == expected_key:
                outputs.append(checkpoint.read_text(encoding="utf-8"))
                continue
            response = await self.model.chat(
                "handbook",
                [
                    {"role": "system", "content": skill},
                    {
                        "role": "user",
                        "content": (
                            f"为 {manager_fund} 生成投资手册。只能使用下列输入，不联网，不编造。"
                            f"{instruction}\n\n{source_text}\n\n已有批次：\n" + "\n".join(outputs)
                        ),
                    },
                ],
                operation=f"handbook_batch_{index}",
            )
            text = response["content"].strip()
            self.boundary.write_text(checkpoint, text + "\n")
            self.boundary.write_text(key_path, expected_key)
            outputs.append(text)
        report = "\n\n".join(outputs).strip()
        if not report.endswith(FINAL_DISCLAIMER):
            report += "\n\n" + FINAL_DISCLAIMER
        validation = validate_handbook(report, manager, self.settings)
        # Two targeted repair rounds. Soft length warnings never trigger a repair.
        for repair_round in range(self.settings.max_repair_rounds):
            if validation["valid"]:
                break
            targets = _repair_targets(validation["problems"])
            if targets:
                target_text = "、".join(f"Part{number}" for number in targets)
                repair_request = (
                    f"只修复并输出 {target_text}，对应硬错误：{validation['problems']}。"
                    "不得输出其他 Part，不联网，不编造，每章保留标准 Markdown 标题。"
                    f"\n\n原始输入：\n{source_text}"
                )
            else:
                repair_request = (
                    f"修复下面投资手册的硬错误：{validation['problems']}。"
                    "保留正确内容，只输出修复后的完整 Markdown。\n\n" + report
                )
            response = await self.model.chat(
                "handbook",
                [
                    {"role": "system", "content": skill},
                    {"role": "user", "content": repair_request},
                ],
                operation=f"handbook_repair_{repair_round + 1}",
            )
            repaired = response["content"].strip()
            report = (
                merge_repaired_parts(report, repaired, targets)
                if targets
                else repaired
            )
            if not report.endswith(FINAL_DISCLAIMER):
                report += "\n\n" + FINAL_DISCLAIMER
            validation = validate_handbook(report, manager, self.settings)
            self.boundary.write_text(output_dir / f"handbook_repair_{repair_round + 1}.md", report)
        self.boundary.write_text(output_dir / "handbook_validation.json", json.dumps(validation, ensure_ascii=False, indent=2) + "\n")
        self.boundary.write_text(output_dir / "source_manifest.json", json.dumps(source_manifest, ensure_ascii=False, indent=2) + "\n")
        if not validation["valid"]:
            raise ValueError("投资手册硬校验失败：" + "、".join(validation["problems"]))
        path = self.boundary.write_text(output_dir / "handbook.md", report + "\n")
        manifest = {
            "candidate_used": True,
            "handbook_quality": validation["quality"],
            "handbook_hash": hashlib.sha256((report + "\n").encode("utf-8")).hexdigest(),
            "warnings": validation["warnings"],
            "source_hashes": [item["sha256"] for item in source_manifest["inputs"]],
        }
        self.boundary.write_text(output_dir / "handbook_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        return HandbookResult(path, validation["quality"], validation["warnings"], True, source_manifest)
