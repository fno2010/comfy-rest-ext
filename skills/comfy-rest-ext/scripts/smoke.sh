#!/usr/bin/env bash
# Read-only smoke test. Requires a configured profile or COMFY_REST_EXT_* env.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
# Always resolve through bootstrap so PATH/env candidates are version-checked.
cli="$("$here/scripts/bootstrap-cli.sh")"
"$cli" --version
"$cli" --json doctor
"$cli" --json models >/dev/null
"$cli" --json capabilities >/dev/null
echo "smoke: ok"
