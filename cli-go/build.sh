#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p dist
CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o dist/comfy-rest-ext .
echo "built: $PWD/dist/comfy-rest-ext"
