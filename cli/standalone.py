"""PyInstaller entry point for the standalone CLI artifact."""

from comfy_rest_ext_cli.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
