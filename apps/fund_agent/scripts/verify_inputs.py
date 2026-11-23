#!/usr/bin/env python3
"""Verify the frozen inventory without reading credentials or touching sources."""

import argparse
import hashlib
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path)
    args = parser.parse_args()
    root = args.inputs.resolve() / "fund_agent"
    manifest = json.loads((root / "manifest.json").read_text())
    for name, entry in manifest["files"].items():
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise SystemExit(f"冻结输入出现链接或越界路径：{name}")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != entry["sha256"] or path.stat().st_size != entry["bytes"]:
            raise SystemExit(f"冻结输入已改变：{name}")
    print(f"冻结输入校验通过：{len(manifest['files'])} 个文件")


if __name__ == "__main__":
    main()
