#!/usr/bin/env python3
"""Copy the verified old generation once; never modify its source."""
import argparse
from hashlib import file_digest, sha256
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory


EVIDENCE = Path(__file__).resolve().parents[1] / "evidence"


def file_hash(path):
    with path.open("rb") as handle:
        return file_digest(handle, "sha256").hexdigest()


def copy_file(source, dest):
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"发布输入必须是普通文件：{source}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    digest = file_hash(dest)
    if digest != file_hash(source):
        raise ValueError(f"复制期间来源变化：{source}")
    return digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="旧项目根目录，仅用于一次性迁入")
    parser.add_argument("--inputs", type=Path, required=True, help="YUNALPHA_INPUTS 根目录")
    args = parser.parse_args()
    provenance = json.loads((EVIDENCE / "source-release.json").read_text())
    version = provenance["snapshot_version"]
    fingerprint = provenance["input_fingerprint"]
    old = args.source / "output/cache/crowding"
    old_snapshot = old / version
    manifest_file = old_snapshot / "manifest.json"
    if file_hash(manifest_file) != provenance["source_manifest_sha256"]:
        parser.error("找不到已核对的发布清单，不能用其他版本代替")
    parent = args.inputs / "vic_desks"
    target = parent / "crowding"
    parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        parser.error("目的地已存在，拒绝覆盖冻结输入")
    paths = [Path(version) / "manifest.json", Path(f"snapshots/{fingerprint}.json.gz")]
    for path in sorted(old_snapshot.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"发布目录中存在软链接：{path}")
        if path.suffix == ".json" and path != manifest_file:
            paths.append(path.relative_to(old))
    with TemporaryDirectory(prefix=".crowding-import-", dir=parent) as tmp:
        staged = Path(tmp) / "crowding"
        staged.mkdir()
        lines = []
        size = 0
        for relative in paths:
            dest = staged / relative
            digest = copy_file(old / relative, dest)
            size += dest.stat().st_size
            lines.append(f"{digest}  {relative.as_posix()}\n")
        manifest = (staged / version / "manifest.json").read_bytes()
        if sha256(manifest).hexdigest() != provenance["source_manifest_sha256"]:
            raise ValueError("复制期间发布清单变化")
        if len(list((old_snapshot / "stock_market_inputs").glob("*.json"))) != 5583 or \
           len(list((old_snapshot / "stock_holder_inputs").glob("*.json"))) != 5583:
            raise ValueError("旧版逐股输入文件数不匹配")
        digest = sha256("".join(sorted(lines)).encode()).hexdigest()
        if digest != provenance["frozen_file_list_sha256"]:
            raise ValueError("发布文件清单或内容与冻结版本不匹配")
        (staged / "active.json").write_text(json.dumps({"version": version}) + "\n")
        (staged / "SHA256SUMS").write_text("".join(sorted(lines)))
        staged.rename(target)
    print(json.dumps({"version": version, "files": len(lines), "bytes": size, "sha256sums_sha256": digest}))


if __name__ == "__main__":
    main()
