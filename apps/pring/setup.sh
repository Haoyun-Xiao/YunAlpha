#!/usr/bin/env bash
set -euo pipefail
app_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
workspace_dir="$(cd -- "$app_dir/../.." && pwd)"
python_bin="${WORKSPACE_PYTHON:-/opt/homebrew/bin/python3.12}"
uv_bin="${UV_BIN:-$workspace_dir/.runtime/tools/bin/uv}"
if [[ ! -x "$uv_bin" ]]; then
  "$python_bin" -m venv "$workspace_dir/.runtime/tools"
  "$workspace_dir/.runtime/tools/bin/python" -m pip install uv==0.12.5
  uv_bin="$workspace_dir/.runtime/tools/bin/uv"
fi
cd -- "$app_dir"
"$uv_bin" sync --frozen --python "$python_bin"
