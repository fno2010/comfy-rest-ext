#!/usr/bin/env bash
# Resolve the exact comfy-rest-ext CLI version pinned by this skill.
#
# Default mode returns a matching CLI path, downloading it if necessary.
# --check reports candidates without changing anything.
# --refresh re-downloads the pinned version (repair, not upgrade).
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
manifest="$script_dir/../cli-version"

mode=resolve
case "${1:-}" in
  "")
    ;;
  --check)
    mode=check
    ;;
  --refresh)
    mode=refresh
    ;;
  -h|--help)
    cat >&2 <<'USAGE'
usage: bootstrap-cli.sh [--check|--refresh]

  (default)   return a CLI matching the version pinned in ../cli-version;
              download the pinned release only if no matching binary exists
  --check     report required version and candidate paths; no download
  --refresh   force re-download of the pinned version; repair only
USAGE
    exit 0
    ;;
  *)
    echo "error: unknown argument: $1" >&2
    echo "usage: bootstrap-cli.sh [--check|--refresh]" >&2
    exit 2
    ;;
esac
if [ "$#" -gt 1 ]; then
  echo "error: at most one argument is supported" >&2
  exit 2
fi

if [ -n "${COMFY_REST_EXT_CLI_VERSION:-}" ]; then
  requested="${COMFY_REST_EXT_CLI_VERSION#v}"
  requested_by="COMFY_REST_EXT_CLI_VERSION"
elif [ -r "$manifest" ]; then
  requested="$(tr -d '[:space:]' < "$manifest")"
  requested="$(printf '%s' "$requested" | sed 's/^v//')"
  requested_by="../cli-version"
else
  echo "error: missing CLI version manifest: $manifest" >&2
  exit 1
fi

if [ -z "$requested" ]; then
  echo "error: empty CLI version in $requested_by" >&2
  exit 1
fi

case "$requested" in
  latest) version_key=latest ;;
  [0-9]*.[0-9]*.[0-9]*) version_key="$requested" ;;
  *)
    echo "error: invalid CLI version '$requested' from $requested_by" >&2
    exit 1
    ;;
esac

case "$(uname -s)" in
  Linux) os=linux ;;
  Darwin) os=darwin ;;
  *) echo "error: unsupported OS $(uname -s); install the CLI manually" >&2; exit 1 ;;
esac
case "$(uname -m)" in
  x86_64|amd64) arch=amd64 ;;
  arm64|aarch64) arch=arm64 ;;
  *) echo "error: unsupported architecture $(uname -m); install the CLI manually" >&2; exit 1 ;;
esac
asset="comfy-rest-ext-${os}-${arch}"
[ "$os" = windows ] && asset="${asset}.exe"

if [ -n "${COMFY_REST_EXT_CLI_BASE_URL:-}" ]; then
  base="${COMFY_REST_EXT_CLI_BASE_URL%/}"
elif [ "$version_key" = latest ]; then
  base="https://github.com/fno2010/comfy-rest-ext/releases/latest/download"
else
  base="https://github.com/fno2010/comfy-rest-ext/releases/download/v${version_key}"
fi

cache="${XDG_CACHE_HOME:-$HOME/.cache}/comfy-rest-ext/bin/${version_key}"
dest="${cache}/${asset}"

version_of() {
  local path="$1"
  [ -x "$path" ] || return 0
  "$path" --version 2>/dev/null | sed -n '1p' | tr -d '\r' || true
}

# latest is an explicit, mutable opt-in; exact pins are the default.
matches_requested() {
  local path="$1" actual
  [ -x "$path" ] || return 1
  if [ "$requested" = latest ]; then
    return 0
  fi
  actual="$(version_of "$path")"
  [ "$actual" = "$requested" ]
}

warn_mismatch() {
  local label="$1" path="$2" actual="$3"
  echo "warning: $label $path reports '$actual', skill requires '$requested'; ignoring it" >&2
}

check_candidate() {
  local label="$1" path="$2" actual status
  if [ ! -e "$path" ]; then
    printf '%-10s %s (missing)\n' "$label:" "$path"
    return 1
  fi
  if [ ! -x "$path" ]; then
    printf '%-10s %s (not executable)\n' "$label:" "$path"
    return 1
  fi
  actual="$(version_of "$path")"
  if [ "$requested" = latest ] || [ "$actual" = "$requested" ]; then
    status=match
    found_match=1
  else
    status=mismatch
  fi
  printf '%-10s %s (version %s, %s)\n' "$label:" "$path" "${actual:-unknown}" "$status"
  [ "$status" = match ]
}

if [ "$mode" = check ]; then
  echo "required: $requested (from $requested_by)"
  found_match=0
  if [ -n "${COMFY_REST_EXT_CLI:-}" ]; then
    check_candidate "env" "${COMFY_REST_EXT_CLI}" || true
  else
    printf '%-10s (not set)\n' "env:"
  fi
  if command -v comfy-rest-ext >/dev/null 2>&1; then
    check_candidate "PATH" "$(command -v comfy-rest-ext)" || true
  else
    printf '%-10s (not found)\n' "PATH:"
  fi
  check_candidate "cache" "$dest" || true
  if [ "$found_match" -eq 1 ]; then
    echo "status: ok"
    exit 0
  fi
  echo "status: missing a matching CLI; run $script_dir/bootstrap-cli.sh"
  exit 1
fi

if [ "$mode" = refresh ]; then
  echo "refreshing CLI $requested (cache: $dest)" >&2
else
  if [ -n "${COMFY_REST_EXT_CLI:-}" ] && [ -x "${COMFY_REST_EXT_CLI}" ]; then
    actual="$(version_of "${COMFY_REST_EXT_CLI}")"
    if [ "$requested" = latest ] || [ "$actual" = "$requested" ]; then
      printf '%s\n' "${COMFY_REST_EXT_CLI}"
      exit 0
    fi
    warn_mismatch "COMFY_REST_EXT_CLI" "${COMFY_REST_EXT_CLI}" "${actual:-unknown}"
  fi
  if [ -x "$dest" ] && matches_requested "$dest"; then
    printf '%s\n' "$dest"
    exit 0
  fi
  if command -v comfy-rest-ext >/dev/null 2>&1; then
    path_bin="$(command -v comfy-rest-ext)"
    if matches_requested "$path_bin"; then
      printf '%s\n' "$path_bin"
      exit 0
    fi
    warn_mismatch "PATH binary" "$path_bin" "$(version_of "$path_bin")"
  fi
  if [ -x "$dest" ]; then
    warn_mismatch "cached binary" "$dest" "$(version_of "$dest")"
  fi
fi

mkdir -p "$cache"
tmp="${dest}.part.$$"
sha_file="${tmp}.sha256"
cleanup() {
  rm -f "$tmp" "$sha_file"
}
trap cleanup EXIT

echo "downloading ${base}/${asset}" >&2
if ! curl -fL --silent --show-error --retry 8 --retry-delay 3 -o "$tmp" "${base}/${asset}"; then
  echo "error: no static CLI release asset at ${base}/${asset}" >&2
  echo "Set COMFY_REST_EXT_CLI to a matching binary or publish the release." >&2
  exit 1
fi
if ! curl -fL --silent --show-error --retry 8 --retry-delay 3 -o "$sha_file" "${base}/${asset}.sha256"; then
  echo "error: no checksum at ${base}/${asset}.sha256" >&2
  exit 1
fi
expected="$(awk '{print $1}' "$sha_file")"
actual_sha="$(sha256sum "$tmp" 2>/dev/null | awk '{print $1}' || shasum -a 256 "$tmp" | awk '{print $1}')"
if [ -z "$expected" ] || [ "$expected" != "$actual_sha" ]; then
  echo "error: checksum verification failed for ${asset}" >&2
  exit 1
fi
chmod 0755 "$tmp"
if [ "$requested" != latest ]; then
  downloaded_version="$(version_of "$tmp")"
  if [ "$downloaded_version" != "$requested" ]; then
    echo "error: ${asset} reports '${downloaded_version:-unknown}', expected '$requested'" >&2
    exit 1
  fi
fi
mv "$tmp" "$dest"
rm -f "$sha_file"
trap - EXIT
printf '%s\n' "$dest"
