#!/usr/bin/env bash
# Build a self-contained CLI artifact. Run on each target OS/arch in CI.
set -euo pipefail
cd "$(dirname "$0")"

NAME="${NAME:-comfy-rest-ext}"
if command -v uvx >/dev/null 2>&1; then
    PYINSTALLER=(uvx --python 3.10 --from pyinstaller pyinstaller)
elif python3 -m PyInstaller --version >/dev/null 2>&1; then
    PYINSTALLER=(python3 -m PyInstaller)
else
    echo "error: install uv or pyinstaller" >&2
    exit 1
fi

rm -rf build dist
"${PYINSTALLER[@]}" \
    --onefile \
    --clean \
    --name "$NAME" \
    --distpath dist \
    --workpath build \
    --specpath build \
    --paths "$PWD" \
    standalone.py

echo "built: $PWD/dist/$NAME"
