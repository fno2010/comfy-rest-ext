#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p dist
version="$(tr -d '[:space:]' < ../VERSION)"
CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -X main.version=${version}" -o dist/comfy-rest-ext .
"$PWD/dist/comfy-rest-ext" --version
echo "built: $PWD/dist/comfy-rest-ext"
