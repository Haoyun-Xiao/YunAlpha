"""One-time copy of the actual Pring workbook/CSV inputs and saved strategy seed."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

BASELINE = "70ab331c7c3233d378d97c1c4f4baf80ef80a018"


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def migrate(source, inputs):
    if subprocess.check_output(["git", "-C", source, "rev-parse", "HEAD"], text=True).strip() != BASELINE:
        raise ValueError("普林格旧源码基线已变化")
    destination = inputs / "pring"
    if destination.exists():
        raise FileExistsError("冻结输入已存在，不覆盖")
    files = {}
    for folder in ("基金", "指数"):
        for category in ("股票", "商品", "可转债", "纯债"):
            for path in sorted((source / folder / category).rglob("*.xlsx")):
                if not path.name.startswith("~$"):
                    files[Path("data") / path.relative_to(source)] = path
    for pattern in ("*.csv", "*.xlsx"):
        for path in sorted((source / "基金/基金经理").glob(pattern)):
            files[Path("data") / path.relative_to(source)] = path
    for category in ("股票", "商品", "可转债", "纯债"):
        for path in sorted((source / "增强" / category).glob("*.xlsx")):
            files[Path("data") / path.relative_to(source)] = path
    for name in (
        "策略代码/普林格周期判断表_逐月_补全Stage7Stage8.xlsx",
        "指数/股票/行业轮动/行业景气度看板.csv",
    ):
        files[Path("data") / name] = source / name
    for name in ("lake_asset_updates.csv", "pring_data_version.json"):
        path = source / "backtest_platform/data" / name
        if path.exists():
            files[Path("data/backtest_platform/data") / name] = path
    files[Path("seeds/saved_strategies.json")] = source / "backtest_platform/data/saved_strategies.json"
    inputs.mkdir(parents=True, exist_ok=True)
    records = []
    with TemporaryDirectory(dir=inputs, prefix=".pring-import-") as tmp:
        staging = Path(tmp) / "pring"
        for relative, old in sorted(files.items()):
            new = staging / relative
            new.parent.mkdir(parents=True, exist_ok=True)
            before = digest(old)
            shutil.copyfile(old, new)
            assert digest(new) == before == digest(old) and not old.samefile(new)
            new.chmod(0o444)
            records.append({"path": relative.as_posix(), "source": old.relative_to(source).as_posix(),
                            "sha256": before, "bytes": new.stat().st_size})
        (staging / "manifest.json").write_text(
            json.dumps({"source_commit": BASELINE, "files": records}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        staging.rename(destination)
    print(json.dumps({"files": len(records), "bytes": sum(record["bytes"] for record in records)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    args = parser.parse_args()
    migrate(args.source.resolve(), args.inputs.resolve())
