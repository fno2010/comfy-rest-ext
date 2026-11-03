#!/usr/bin/env bash
# Download a pinned static comfy-rest-ext CLI plus checksum.
set -euo pipefail

if [ -n "${COMFY_REST_EXT_CLI:-}" ] && [ -x "${COMFY_REST_EXT_CLI}" ]; then
  printf '%s\n' "${COMFY_REST_EXT_CLI}"
  exit 0
fi
if command -v comfy-rest-ext >/dev/null 2>&1; then
  command -v comfy-rest-ext
  exit 0
fi

version="${COMFY_REST_EXT_CLI_VERSION:-latest}"
case "$(uname -s)" in
  Linux) os=linux ;;
  Darwin) os=darwin ;;
  *) echo "error: unsupported OS $(uname -s); install the CLI manually" >&2; exit 1 ;;
esac
case "$(uname -m)" in
  x86_64|amd64) arch=amd64 ;;
  arm64|aarch64) arch=arm64 ;;
  *) echo "error: unsupported architecture $(uname -m)" >&2; exit 1 ;;
esac

asset="comfy-rest-ext-${os}-${arch}"
[ "$os" = windows ] && asset="${asset}.exe"
if [ -n "${COMFY_REST_EXT_CLI_BASE_URL:-}" ]; then
  base="${COMFY_REST_EXT_CLI_BASE_URL%/}"
elif [ "$version" = latest ]; then
  base="https://github.com/fno2010/comfy-rest-ext/releases/latest/download"
else
  base="https://github.com/fno2010/comfy-rest-ext/releases/download/v${version#v}"
fi
cache="${XDG_CACHE_HOME:-$HOME/.cache}/comfy-rest-ext/bin/${version}"
dest="${cache}/${asset}"
mkdir -p "$cache"

if [ ! -x "$dest" ]; then
  tmp="${dest}.part"
  echo "downloading ${base}/${asset}" >&2
  if ! curl -fL --retry 8 --retry-delay 3 -o "$tmp" "${base}/${asset}"; then
    echo "error: no static CLI release asset at ${base}/${asset}" >&2
    echo "Set COMFY_REST_EXT_CLI to an installed binary or publish a release." >&2
    rm -f "$tmp"
    exit 1
  fi
  if ! curl -fL --retry 8 --retry-delay 3 -o "${tmp}.sha256" "${base}/${asset}.sha256"; then
    echo "error: no checksum at ${base}/${asset}.sha256" >&2
    rm -f "$tmp" "${tmp}.sha256"
    exit 1
  fi
  expected="$(awk '{print $1}' "${tmp}.sha256")"
  actual="$(sha256sum "$tmp" 2>/dev/null | awk '{print $1}' || shasum -a 256 "$tmp" | awk '{print $1}')"
  if [ -z "$expected" ] || [ "$expected" != "$actual" ]; then
    rm -f "$tmp" "${tmp}.sha256"
    echo "error: checksum verification failed for ${asset}" >&2
    exit 1
  fi
  chmod 0755 "$tmp"
  mv "$tmp" "$dest"
  rm -f "${tmp}.sha256"
fi

printf '%s\n' "$dest"
