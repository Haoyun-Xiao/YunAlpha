#!/bin/sh
set -eu
TASK_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TASK_PYTHON=${WORKSPACE_PYTHON:-/opt/homebrew/bin/python3.12}
exec "$TASK_PYTHON" "$TASK_ROOT/scripts/workspace.py" "$@"
