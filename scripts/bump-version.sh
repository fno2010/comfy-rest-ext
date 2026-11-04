#!/usr/bin/env bash
# Set the single source of truth and all shipped version fields together.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
version="${1:-}"
if ! printf '%s' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$'; then
  echo "usage: bump-version.sh X.Y.Z[-rc.N]" >&2
  exit 2
fi

printf '%s\n' "$version" > "$root/VERSION"
printf '%s\n' "$version" > "$root/skills/comfy-rest-ext/cli-version"
sed -E -i 's/^version = ".*"/version = "'"$version"'"/' \
  "$root/server/pyproject.toml" "$root/cli/pyproject.toml"
sed -E -i 's/^var version = ".*"/var version = "'"$version"'"/' "$root/cli-go/main.go"
"$root/scripts/check-version.sh"
