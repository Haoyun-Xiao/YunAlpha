#!/bin/sh
set -eu
app_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
case "${1:-}" in
    crowding) module=crowding.server ;;
    seven) module=seven_desks.server ;;
    *) echo "用法：run.sh {crowding|seven} PORT" >&2; exit 2 ;;
esac
case "${2:-}" in
    ''|*[!0-9]*) echo "请指定有效端口" >&2; exit 2 ;;
esac
export YUNALPHA_RUNTIME="${YUNALPHA_RUNTIME:-$app_dir/../../.runtime}"
export YUNALPHA_INPUTS="${YUNALPHA_INPUTS:-$YUNALPHA_RUNTIME/phase2-inputs}"
export PYTHONDONTWRITEBYTECODE=1
unset CROWDING_CACHE_BUILD CROWDING_CACHE_VERSION CROWDING_AS_OF
cd "$app_dir"
if [ "$module" = seven_desks.server ]; then
    if [ -n "${YUNALPHA_SEVEN_VERSION:-}" ]; then
        exec "$app_dir/.venv/bin/python" -m "$module" --port "$2" --version "$YUNALPHA_SEVEN_VERSION"
    fi
    exec "$app_dir/.venv/bin/python" -m "$module" --port "$2"
fi
exec "$app_dir/.venv/bin/python" -m "$module" "$2"
