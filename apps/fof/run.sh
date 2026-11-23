#!/usr/bin/env bash
set -euo pipefail
app_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$app_dir"
exec "$app_dir/.venv/bin/python" -B -m backtest_platform.server "${1:?usage: run.sh PORT}"
