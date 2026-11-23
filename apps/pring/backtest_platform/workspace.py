"""Separate frozen business inputs from this workspace's saved strategies."""
import os
from pathlib import Path
import shutil


WORKSPACE = Path(__file__).resolve().parents[3]
RUNTIME = Path(os.environ.get("YUNALPHA_RUNTIME") or WORKSPACE / ".runtime").resolve()
INPUTS = Path(os.environ.get("YUNALPHA_INPUTS") or WORKSPACE / ".runtime/phase2-inputs").resolve() / "pring"
DATA_ROOT = INPUTS / "data"
STATE_FILE = RUNTIME / "phase2/pring/saved_strategies.json"


def initialize_state():
    if not RUNTIME.is_relative_to(WORKSPACE / ".runtime"):
        raise ValueError("普林格状态须在当前工作树 .runtime 内")
    if not (INPUTS / "manifest.json").is_file():
        raise FileNotFoundError("普林格冻结输入尚未迁入")
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        shutil.copyfile(INPUTS / "seeds/saved_strategies.json", STATE_FILE)
