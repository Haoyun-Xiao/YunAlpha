"""Read-only release inputs and isolated per-worktree outputs."""
import os
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
WORKSPACE = PROJECT.parents[1]
RUNTIME = Path(os.environ.get("YUNALPHA_RUNTIME", WORKSPACE / ".runtime")).resolve()
INPUTS = Path(os.environ.get("YUNALPHA_INPUTS", RUNTIME / "phase2-inputs")).resolve()
FROZEN = INPUTS / "vic_desks/seven"
FROZEN_VERSION = "d451f099a954d584"
CUTOFF = "2026-11-09"
CACHE = RUNTIME / "phase2/vic_desks/seven"
LAKE = Path(os.environ.get("YUNALPHA_LAKE_ROOT", CACHE / "no-lake")).resolve()
SOURCE = LAKE / "meta/review/seven-followup-20261113"
