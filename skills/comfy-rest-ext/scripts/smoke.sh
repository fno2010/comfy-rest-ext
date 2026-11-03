#!/usr/bin/env bash
# Read-only smoke test. Requires a configured profile or COMFY_REST_EXT_* env.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
cli="${COMFY_REST_EXT_CLI:-}"
if [ -z "$cli" ]; then
  cli="$("$here/scripts/bootstrap-cli.sh")"
fi
"$cli" --json doctor
"$cli" --json models >/dev/null
"$cli" --json capabilities >/dev/null
echo "smoke: ok"
