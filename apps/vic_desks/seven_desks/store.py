from __future__ import annotations
import datetime as dt
import decimal
import hashlib
import json
import math
import re
import shutil
import subprocess
import threading
from pathlib import Path
import duckdb

from seven_desks.settings import CACHE, CUTOFF, FROZEN, FROZEN_VERSION, PROJECT

RUNTIME_MODULES = (
    "__init__.py", "settings.py", "store.py", "server.py", "daily.py",
    "adapters/__init__.py", "adapters/contrarian.py", "adapters/market.py", "adapters/owners.py",
)


def code_hashes():
    return {
        f"seven_desks/{name}": hashlib.sha256((PROJECT / "seven_desks" / name).read_bytes()).hexdigest()
        for name in RUNTIME_MODULES
    }


def version_root(version):
    if not isinstance(version, str) or not re.fullmatch(r"[a-f0-9]{16}", version):
        raise ValueError("invalid data_version")
    parent = FROZEN if version == FROZEN_VERSION else CACHE
    return parent / "versions" / version


def connect(config=None):
    temp = CACHE / "duckdb-tmp"
    temp.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(config={"threads": 2, "memory_limit": "1GB", **(config or {}), "temp_directory": str(temp)})


def clean(v):
    if isinstance(v, dict):
        return {str(k): clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return clean(float(v))
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def create_version(base_version=None):
    """A revision inherits immutable inputs; never silently adopts live lake updates."""
    base = version_root(base_version) / "data"
    if not (base.parent / "manifest.json").is_file():
        raise ValueError("base version not published")
    base_meta = json.loads((base.parent / "manifest.json").read_text())
    files = []
    for source in sorted(base.rglob("*")):
        if source.is_file() and source.suffix in (".parquet", ".json") and not any(x in ("_prepare_tmp", "tmp") for x in source.relative_to(base).parts):
            st = source.stat()
            files.append(dict(relative=str(source.relative_to(base)), source_path=str(source), bytes=st.st_size, mtime_ns=st.st_mtime_ns))
    for group in ("market", "owners", "contrarian"):
        folder = CACHE / ("revision-" + group)
        for source in sorted(folder.rglob("*")):
            if source.is_file() and source.suffix in (".parquet", ".json") and "spill" not in source.parts:
                st = source.stat()
                rel = "revision/" + group + "/" + str(source.relative_to(folder))
                files = [f for f in files if f["relative"] != rel]
                files.append(dict(relative=rel, source_path=str(source), bytes=st.st_size, mtime_ns=st.st_mtime_ns))
    code = sorted(code_hashes().items())
    version = hashlib.sha256(json.dumps([base_version, files, code], sort_keys=True).encode()).hexdigest()[:16]
    root = CACHE / "versions" / version
    if not (root / "manifest.json").exists():
        root.mkdir(parents=True, exist_ok=True)
        for f in files:
            src, dest = Path(f["source_path"]), root / "data" / f["relative"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                try:
                    subprocess.run(["cp", "-c", str(src), str(dest)], check=True, capture_output=True)
                except subprocess.CalledProcessError:
                    shutil.copy2(src, dest)
            st = src.stat()
            if (st.st_size, st.st_mtime_ns) != (f["bytes"], f["mtime_ns"]):
                raise RuntimeError("Revision inputs changed during snapshot")
        meta = dict(data_version=version, base_version=base_version, cutoff=base_meta.get("cutoff", CUTOFF),
                    valuation_cutoff=base_meta.get("valuation_cutoff", CUTOFF), files=files, code_hashes=code,
                    source=str(base), created_at=dt.datetime.now(dt.timezone.utc).isoformat(), snapshot_type="frozen_baseline_plus_local_revision")
        tmp = root / "manifest.tmp"
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2)); tmp.replace(root / "manifest.json")
    return version


class Store:
    def __init__(self, version):
        self.version = version
        root = version_root(version)
        self.root = root / "data"
        manifest = root / "manifest.json"
        metadata = json.loads(manifest.read_text()) if manifest.is_file() else {}
        self.valuation_cutoff = metadata.get("valuation_cutoff", CUTOFF)
        self.daily_boards = metadata.get("daily_boards", {})
        self.lock = threading.RLock()

    def path(self, relative):
        p = Path(relative)
        if p.is_absolute():
            try:
                p = p.relative_to(self.root)
            except ValueError:
                raise ValueError("outside frozen inputs") from None
        resolved = (self.root / p).resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            raise ValueError("path escape")
        return str(resolved)

    def query(self, sql, params=None):
        with self.lock:
            c = connect()
            try:
                result = c.execute(sql, params or [])
                cols = [x[0] for x in result.description]
                return [clean(dict(zip(cols, r))) for r in result.fetchall()]
            finally:
                c.close()

    def read(self, relative_path, columns=None, where=None, params=None):
        p = self.path(relative_path)
        if not Path(p).exists() and "*" not in p:
            return []
        cols = "*"
        if columns:
            if any(not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", x) for x in columns):
                raise ValueError("invalid column")
            cols = ",".join('"' + x + '"' for x in columns)
        return self.query(
            f"SELECT {cols} FROM read_parquet('{p.replace(chr(39), chr(39) * 2)}',hive_partitioning=false)"
            + (f" WHERE {where}" if where else ""),
            params,
        )
