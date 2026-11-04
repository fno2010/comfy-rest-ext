#!/usr/bin/env bash
# Contract test for the skill's pinned CLI resolver. No network required.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
version="$(tr -d '[:space:]' < "$root/VERSION")"
bootstrap="$root/skills/comfy-rest-ext/scripts/bootstrap-cli.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

case "$(uname -s)" in
  Linux) os=linux ;;
  Darwin) os=darwin ;;
  *) echo "skip: unsupported OS" >&2; exit 0 ;;
esac
case "$(uname -m)" in
  x86_64|amd64) arch=amd64 ;;
  arm64|aarch64) arch=arm64 ;;
  *) echo "skip: unsupported architecture" >&2; exit 0 ;;
esac
asset="comfy-rest-ext-${os}-${arch}"

mkdir -p "$tmp/www" "$tmp/cache" "$tmp/old-path"
cat > "$tmp/www/$asset" <<SH
#!/bin/sh
echo "$version"
SH
chmod +x "$tmp/www/$asset"
(cd "$tmp/www" && sha256sum "$asset" > "$asset.sha256")

cat > "$tmp/fake-match" <<SH
#!/bin/sh
echo "$version"
SH
cat > "$tmp/fake-old" <<'SH'
#!/bin/sh
echo "0.0.0-old"
SH
chmod +x "$tmp/fake-match" "$tmp/fake-old"

export XDG_CACHE_HOME="$tmp/cache"
export COMFY_REST_EXT_CLI_BASE_URL="file://$tmp/www"

# No candidate: check must be read-only and report missing.
if "$bootstrap" --check >/dev/null 2>&1; then
  echo "error: --check passed without a matching CLI" >&2
  exit 1
fi
[ ! -e "$tmp/cache/comfy-rest-ext/bin/$version/$asset" ] || {
  echo "error: --check downloaded a binary" >&2
  exit 1
}

# A mismatched explicit client must not be used; resolver downloads the pin.
got="$(COMFY_REST_EXT_CLI="$tmp/fake-old" "$bootstrap")"
[ "$got" = "$tmp/cache/comfy-rest-ext/bin/$version/$asset" ]
[ "$("$got" --version)" = "$version" ]

# Matching explicit client wins.
got="$(COMFY_REST_EXT_CLI="$tmp/fake-match" "$bootstrap")"
[ "$got" = "$tmp/fake-match" ]

# --check now sees the cache and succeeds.
"$bootstrap" --check >/dev/null

# --refresh re-downloads the pinned asset and still reports the right version.
got="$("$bootstrap" --refresh)"
[ "$got" = "$tmp/cache/comfy-rest-ext/bin/$version/$asset" ]
[ "$("$got" --version)" = "$version" ]

echo "bootstrap-cli contract: ok ($version)"
