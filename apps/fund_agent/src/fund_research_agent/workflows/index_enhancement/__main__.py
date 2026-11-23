from __future__ import annotations

import argparse
import asyncio
import re
from datetime import date

from fund_research_agent.config import load_settings
from fund_research_agent.workflows.index_enhancement.workflow import run


def main() -> None:
    parser = argparse.ArgumentParser(description="独立指增Part1样章（不创建聊天或完整报告Job）")
    parser.add_argument("--fund", default="000311.OF", choices=["000311.OF"])
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 10, 26))
    parser.add_argument("--run-id")
    parser.add_argument("--refresh-materials", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    run_id = args.run_id or f"000311-{args.as_of:%Y%m%d}-part1"
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        parser.error("run-id只能包含字母、数字、下划线和连字符")
    settings = load_settings()
    asyncio.run(
        run(
            settings.var_root / "index_enhancement" / run_id,
            settings,
            args.fund,
            args.as_of,
            args.refresh_materials,
            args.resume,
        )
    )


if __name__ == "__main__":
    main()
