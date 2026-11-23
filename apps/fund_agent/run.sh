#!/bin/bash
set -euo pipefail

app="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
worktree="$(cd -- "$app/../.." && pwd -P)"
python="$app/.venv/bin/python"
export YUNALPHA_RUNTIME="${YUNALPHA_RUNTIME:-$worktree/.runtime}"
export YUNALPHA_INPUTS="${YUNALPHA_INPUTS:-$worktree/.runtime/phase2-inputs}"
export YUNALPHA_LAKE_ROOT="${YUNALPHA_LAKE_ROOT:-/path/to/local-user/项目/多资产智能投研平台-v2/lake}"

if [[ ! -x "$python" ]]; then
    echo "基金 Agent 未安装；见 apps/fund_agent/README.md" >&2
    exit 1
fi
# Resolve before creating anything, including when .runtime is a symlink.
scratch="$("$python" - "$worktree" "${YUNALPHA_RUNTIME}" <<'PY'
import sys
from pathlib import Path

worktree, runtime = map(Path, sys.argv[1:])
allowed = worktree / ".runtime"
state = (runtime.expanduser() / "phase2/fund_agent").resolve()
if not state.is_relative_to(allowed):
    raise SystemExit("基金 Agent 的运行目录须位于本工作树 .runtime 内")
print(state)
PY
)"
export FUND_AGENT_PROJECT_ROOT="$app"
export FUND_AGENT_CONFIG="$app/config/app.example.yaml"
export FUND_AGENT_VAR_ROOT="$scratch"
export FUND_AGENT_STATE_DB="$scratch/state/agent.sqlite"
export FUND_AGENT_CHECKPOINT_DB="$scratch/state/deep-agent-checkpoints.sqlite"
export FUND_AGENT_LAKE_DATABASE="$scratch/lake_bridge/duckdb/agent-lake.duckdb"
export FUND_AGENT_LEGACY_KB="$YUNALPHA_INPUTS/fund_agent/materials/kb-index.sqlite"
export FUND_AGENT_FUND_MANIFEST="$YUNALPHA_INPUTS/fund_agent/fund-manifest.csv"
export FUND_AGENT_FUND_UNIVERSE="$YUNALPHA_INPUTS/fund_agent/fund-universe.xlsx"
export FUND_AGENT_NAV_OVERLAY="$YUNALPHA_INPUTS/fund_agent/fund-nav-dashboard.parquet"
unset FUND_AGENT_REPORTS_OVERLAY
export FUND_AGENT_HOST=127.0.0.1
export FUND_AGENT_BASE_PATH=/agent-runtime
export TMPDIR="$scratch/tmp" PYTHONPYCACHEPREFIX="$scratch/pycache"
mkdir -p "$TMPDIR"
cd "$app"

case "${1:-}" in
    prepare) exec "$python" scripts/build_lake_bridge.py ;;
    api|worker)
        if [[ ! -s "$FUND_AGENT_LAKE_DATABASE" || ! -s "$scratch/lake_bridge/mcp.toml" || ! -x "$app/.venv/bin/mal" || ! -f "$YUNALPHA_INPUTS/fund_agent/manifest.json" ]]; then
            echo "基金 Agent 的冻结输入、MAL 或只读桥接未就绪；见 apps/fund_agent/README.md" >&2
            exit 1
        fi
        if [[ "$1" == api ]]; then
            if [[ $# != 2 || ! "$2" =~ ^[0-9]{1,5}$ ]] || (( 10#$2 < 1 || 10#$2 > 65535 )); then
                echo "用法: bash apps/fund_agent/run.sh api PORT" >&2
                exit 2
            fi
            export FUND_AGENT_PORT="$2"
            exec "$python" -m fund_research_agent.api.app
        fi
        exec "$python" -m fund_research_agent.workflows.report.worker
        ;;
    *) echo "用法: bash apps/fund_agent/run.sh prepare | api PORT | worker" >&2; exit 2 ;;
esac
