#!/usr/bin/env bash
# Verify that every shipped artifact reports the version in VERSION.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
version="$(tr -d '[:space:]' < "$root/VERSION")"
if ! printf '%s' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$'; then
  echo "error: invalid VERSION: $version" >&2
  exit 1
fi

errors=0
check_eq() {
  local label="$1" actual="$2" expected="$3"
  if [ "$actual" != "$expected" ]; then
    echo "error: $label is '$actual', expected '$expected'" >&2
    errors=1
  else
    echo "ok: $label = $actual"
  fi
}

server_version="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$root/server/pyproject.toml" | head -n1)"
cli_version="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$root/cli/pyproject.toml" | head -n1)"
go_version="$(sed -n 's/^var version = "\(.*\)"$/\1/p' "$root/cli-go/main.go" | head -n1)"
skill_cli_version="$(tr -d '[:space:]' < "$root/skills/comfy-rest-ext/cli-version" 2>/dev/null || true)"

check_eq "server/pyproject.toml" "$server_version" "$version"
check_eq "cli/pyproject.toml" "$cli_version" "$version"
check_eq "cli-go/main.go" "$go_version" "$version"
check_eq "skills/comfy-rest-ext/cli-version" "$skill_cli_version" "$version"

if [ -n "${GITHUB_REF_NAME:-}" ]; then
  case "$GITHUB_REF_NAME" in
    v*) check_eq "GITHUB_REF_NAME" "$GITHUB_REF_NAME" "v$version" ;;
  esac
fi

if [ "$errors" -ne 0 ]; then
  echo "version check failed" >&2
  exit 1
fi
echo "version check: $version"
