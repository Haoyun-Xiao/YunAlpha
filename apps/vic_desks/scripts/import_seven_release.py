#!/usr/bin/env python3
"""Import the pinned seven-desk snapshot into independent YunAlpha files."""
import argparse
from collections import Counter
from hashlib import file_digest, sha256
import json
from pathlib import Path
import shutil
import stat
import subprocess
from tempfile import TemporaryDirectory


EVIDENCE = Path(__file__).resolve().parents[1] / "evidence/seven"


def digest(path):
    with path.open("rb") as handle:
        return file_digest(handle, "sha256").hexdigest()


def identity(path):
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"不是普通文件：{path}")
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_nlink


def copy_verified(src, dest):
    before = identity(src)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["cp", "-c", str(src), str(dest)], check=True, capture_output=True)
        mode = "cp-c"
    except subprocess.CalledProcessError:
        shutil.copyfile(src, dest)
        mode = "copy"
    after = identity(dest)
    if before[:2] == after[:2] or after[-1] != 1 or before[2] != after[2]:
        raise ValueError(f"副本不是独立普通文件：{dest}")
    checksum = digest(dest)
    if checksum != digest(src) or before != identity(src):
        raise ValueError(f"冻结输入复制验证失败：{src}")
    return mode, checksum, after[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="旧项目根，仅一次性读取")
    parser.add_argument("--inputs", type=Path, required=True, help="YUNALPHA_INPUTS 根目录")
    args = parser.parse_args()
    pin = json.loads((EVIDENCE / "source-snapshot.json").read_text())
    version = pin["version"]
    source = args.source / "output/seven-desks"
    version_dir = source / "versions" / version
    manifest_file = version_dir / "manifest.json"
    if digest(manifest_file) != pin["snapshot_manifest_sha256"]:
        parser.error("快照发布清单不匹配")
    if digest(source / "revision-qa/release-manifest.json") != pin["release_manifest_sha256"]:
        parser.error("源码发布清单不匹配")
    manifest = json.loads(manifest_file.read_text())
    assert manifest["data_version"] == version and manifest["cutoff"] == "2026-11-09"
    paths = sorted(p for p in version_dir.rglob("*") if p.name != ".DS_Store")
    if any(p.is_symlink() for p in paths):
        parser.error("发布目录含软链接")
    files = [p for p in paths if p.is_file()]
    expected = {f["relative"]: f["bytes"] for f in manifest["files"]}
    actual = {p.relative_to(version_dir / "data").as_posix(): p.stat().st_size
              for p in files if p.is_relative_to(version_dir / "data")}
    if actual != expected:
        parser.error("实际数据文件与冻结清单不符")
    parent = args.inputs / "vic_desks"
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / "seven"
    if target.exists():
        parser.error("目的地已存在，拒绝覆盖")
    with TemporaryDirectory(prefix=".seven-import-", dir=parent) as tmp:
        staged = Path(tmp) / "seven"
        checksums, size, modes = [], 0, Counter()
        for source_file in files:
            relative = source_file.relative_to(source)
            mode, expected_hash, count = copy_verified(source_file, staged / relative)
            checksums.append(f"{expected_hash}  {relative.as_posix()}\n")
            size += count
            modes[mode] += 1
        if digest(staged / "versions" / version / "manifest.json") != pin["snapshot_manifest_sha256"]:
            raise ValueError("复制期间发布清单变化")
        (staged / "active.json").write_text(json.dumps({"data_version": version}) + "\n")
        listing = "".join(sorted(checksums)).encode()
        (staged / "SHA256SUMS").write_bytes(listing)
        staged.rename(target)
    print(json.dumps({"version": version, "files": len(files), "data_files": len(actual),
                      "bytes": size, "copy_commands": modes, "sha256sums_sha256": sha256(listing).hexdigest()}))


if __name__ == "__main__":
    main()
