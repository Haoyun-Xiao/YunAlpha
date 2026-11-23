"""YunAlpha-owned frozen reads and per-worktree build output."""
import os
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[3]
RUNTIME = Path(os.environ.get("YUNALPHA_RUNTIME", WORKSPACE / ".runtime")).expanduser().resolve()
INPUTS = Path(os.environ.get("YUNALPHA_INPUTS", RUNTIME / "phase2-inputs")).expanduser().resolve()
STATE = RUNTIME / "phase2/vic_desks/crowding"
BUILD_MODE = os.environ.get("CROWDING_CACHE_BUILD") == "1"
FROZEN_VERSION = "a8bbbe0793ac426c"
SERVING_VERSION = os.environ.get("YUNALPHA_CROWDING_VERSION") or FROZEN_VERSION
CANDIDATE_ROOT = STATE / "versions"
FROZEN_ROOT = INPUTS / "vic_desks/crowding"
LAKE_ROOT = Path(os.environ.get("YUNALPHA_LAKE_ROOT") or STATE / "no-lake").expanduser().resolve()
