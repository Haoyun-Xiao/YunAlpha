#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "$ROOT/apps/vic_desks/.venv/bin/python" "$ROOT/scripts/refresh_desks.py" "$@"
