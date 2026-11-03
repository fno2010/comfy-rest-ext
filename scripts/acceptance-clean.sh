#!/usr/bin/env bash
# Clean-machine acceptance for the installed static CLI + agent skill.
# Requires: bash, curl, npx; a configured COMFY_REST_EXT_BASE_URL.
set -euo pipefail

: "${COMFY_REST_EXT_BASE_URL:?set COMFY_REST_EXT_BASE_URL}"
CLI="${COMFY_REST_EXT_CLI:-$(command -v comfy-rest-ext || true)}"
if [ -z "$CLI" ] || [ ! -x "$CLI" ]; then
  echo "error: set COMFY_REST_EXT_CLI to a static comfy-rest-ext binary" >&2
  exit 1
fi
SKILL_SOURCE="${SKILL_SOURCE:-fno2010/comfy-rest-ext}"
WORK="${ACCEPTANCE_WORK:-$(mktemp -d /tmp/comfy-rest-ext-accept.XXXXXX)}"
mkdir -p "$WORK"

echo "workdir: $WORK"
echo "cli:     $CLI"
"$CLI" --version
if [ "$(uname -s)" = Linux ]; then
  ldd_out="$(ldd "$CLI" 2>&1 || true)"
  if printf '%s\n' "$ldd_out" | grep -q 'not a dynamic executable'; then
    echo "static:  yes"
  else
    echo "error: CLI is not a static Linux executable" >&2
    printf '%s\n' "$ldd_out" >&2
    exit 1
  fi
fi

# Install the skill into an isolated project using the standard skills CLI.
mkdir -p "$WORK/project"
(
  cd "$WORK/project"
  npx --yes skills add "$SKILL_SOURCE" --skill comfy-rest-ext --yes
)

export COMFY_REST_EXT_CLI="$CLI"
export COMFY_REST_EXT_CONFIG="$WORK/config.toml"
export XDG_CONFIG_HOME="$WORK/xdg"
"$CLI" config set acceptance base_url "$COMFY_REST_EXT_BASE_URL" >/dev/null
"$CLI" config use acceptance >/dev/null
"$CLI" --json health >/dev/null
"$CLI" --json doctor >/dev/null
"$CLI" --json models >/dev/null
"$CLI" --json capabilities >/dev/null

"$WORK/project/.agents/skills/comfy-rest-ext/scripts/smoke.sh"
echo "acceptance: CLI + installed skill OK"
echo "artifacts: $WORK"
