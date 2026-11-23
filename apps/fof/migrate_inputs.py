"""Copy FOF runtime inputs once, without running or writing the old application."""
from __future__ import annotations

import argparse
import csv
from hashlib import file_digest
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

BASELINE = "4926507b7f8a32aa34e0580240461bbc170dfa94"
QUERY_FILES = (
    "fund_master.parquet",
    "fund_portfolio_reports.parquet",
    "fund_holdings.parquet",
    "fund_style_portraits.parquet",
    "fund_industry_portraits.parquet",
    "stock_industry_map.parquet",
    "industry_category_map.csv",
)


def sha256(path: Path) -> str:
    with path.open("rb") as file:
        return file_digest(file, "sha256").hexdigest()


def source_files(data: Path) -> dict[Path, Path]:
    master = Path("fund_master.csv")
    with (data / master).open(encoding="utf-8-sig", newline="") as file:
        nav = [Path(row.get("nav_file") or f"fund_nav/{row['ts_code']}.csv") for row in csv.DictReader(file)]
    if any(path.parent != Path("fund_nav") or path.suffix != ".csv" for path in nav):
        raise ValueError("基金主表引用了基金净值目录之外的文件")
    index = sorted(path.relative_to(data) for path in (data / "index_nav").glob("*.csv") if not path.name.startswith("."))
    if len(set(nav)) != 111 or len(index) != 10:
        raise ValueError("冻结数据须有 111 只基金净值和 10 条指数基准")
    inputs = [master, Path("fund_labels.csv"), *nav, *index, *(Path("query") / name for name in QUERY_FILES)]
    portfolios = sorted((data / "portfolios").glob("portfolio_*.json"))
    strategies = sorted((data / "strategies").glob("strategy_*.json"))
    if len(portfolios) != 4 or len(strategies) != 1:
        raise ValueError("冻结种子须有四份组合和一份旧策略")
    # Display order is stored in each portfolio JSON; this marker prevents duplicate legacy imports.
    seeds = [*portfolios, *strategies, data / "portfolios/.legacy_strategy_imports.json"]
    return {
        **{Path("data") / name: data / name for name in inputs},
        **{Path("seeds") / path.relative_to(data): path for path in seeds},
    }


def migrate(source: Path, inputs_root: Path) -> Path:
    head = subprocess.check_output(["git", "-C", source, "rev-parse", "HEAD"], text=True).strip()
    if head != BASELINE:
        raise ValueError(f"旧项目不在确认的基线 {BASELINE}")
    dest = inputs_root / "fof"
    if dest.exists() or dest.is_symlink():
        raise FileExistsError(f"冻结输入已存在，未覆盖：{dest}")
    inputs_root.mkdir(parents=True, exist_ok=True)
    data = (source / "data").resolve(strict=True)
    files = source_files(data)
    with TemporaryDirectory(dir=inputs_root, prefix=".fof-import-") as temp:
        staging = Path(temp) / "fof"
        records = []
        for name, old in sorted(files.items()):
            if old.resolve(strict=True) != old or not old.is_file():
                raise ValueError(f"冻结来源必须是普通文件：{old}")
            before = sha256(old)
            new = staging / name
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(old, new)
            if sha256(new) != before or sha256(old) != before or old.samefile(new):
                raise ValueError(f"复制校验失败：{name}")
            new.chmod(0o444)
            records.append({"path": name.as_posix(), "bytes": new.stat().st_size, "sha256": before})
        manifest = staging / "manifest.json"
        manifest.write_text(
            json.dumps({"source_commit": BASELINE, "files": records}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest.chmod(0o444)
        staging.rename(dest)
    return dest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--inputs", required=True, type=Path)
    args = parser.parse_args()
    print(migrate(args.source.resolve(strict=True), args.inputs.resolve()))
