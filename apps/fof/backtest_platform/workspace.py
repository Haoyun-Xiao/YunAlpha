"""Resolve frozen inputs and seed this workspace's writable state on first start."""
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / ".runtime"
INPUTS = Path(os.environ.get("YUNALPHA_INPUTS") or RUNTIME / "phase2-inputs").absolute()
DATA_DIR = INPUTS / "fof" / "data"
STATE_DIR = Path(os.environ.get("YUNALPHA_RUNTIME") or RUNTIME).absolute() / "phase2" / "fof"


def initialize_state() -> None:
    if not (INPUTS / "fof" / "manifest.json").is_file():
        raise FileNotFoundError("缺少 FOF 冻结输入，请先完成迁入")
    if STATE_DIR.resolve() != STATE_DIR or not STATE_DIR.is_relative_to(RUNTIME):
        raise ValueError("FOF 状态必须位于本工作树 .runtime 内，且不能经过软链接")
    if STATE_DIR.exists():
        if any(not (STATE_DIR / name).is_dir() or (STATE_DIR / name).is_symlink() for name in ("portfolios", "strategies")):
            raise ValueError("FOF 状态目录不完整或指向软链接")
        return
    STATE_DIR.parent.mkdir(parents=True, exist_ok=True)
    seeds = INPUTS / "fof" / "seeds"
    with TemporaryDirectory(dir=STATE_DIR.parent, prefix=".fof-seed-") as temp:
        staging = Path(temp) / "fof"
        for name in ("portfolios", "strategies"):
            shutil.copytree(seeds / name, staging / name, copy_function=shutil.copyfile)
        # Publish both stores at once so interrupted copies cannot become working state.
        staging.rename(STATE_DIR)
