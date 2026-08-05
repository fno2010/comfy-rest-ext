"""
Entry point for the comfy-rest-ext CLI.

Usage: comfy-rest-ext-cli [--base-url URL] [--json] <command> [args]

Commands: models, generate, status, list, wait, download, download-status
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from .client import ApiError, ComfyRestClient
from . import commands


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="comfy-rest-ext-cli",
        description="Command-line client for comfy-rest-ext.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="ComfyUI base URL (default: http://127.0.0.1:8188)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("models", help="list available models")
    p.set_defaults(func=commands.cmd_models)

    p = sub.add_parser("generate", help="generate a video")
    p.add_argument("prompt", help="text prompt")
    p.add_argument("--image", help="input reference image for I2V")
    p.add_argument("--model", help="model id (minimax-h3-t2v / minimax-h3-i2v)")
    p.add_argument("--width", type=int, help="frame width (default 1344)")
    p.add_argument("--height", type=int, help="frame height (default 768)")
    p.add_argument("--seconds", type=float, help="video length in seconds")
    p.add_argument("--seed", type=int, help="generation seed")
    p.set_defaults(func=commands.cmd_generate)

    p = sub.add_parser("status", help="show a video task's status")
    p.add_argument("video_id", help="video task id")
    p.set_defaults(func=commands.cmd_status)

    p = sub.add_parser("list", help="list video tasks")
    p.set_defaults(func=commands.cmd_list)

    p = sub.add_parser("wait", help="wait for a video task to finish")
    p.add_argument("video_id", help="video task id")
    p.add_argument("--timeout", type=float, help="max seconds to wait")
    p.add_argument("--quiet", action="store_true",
                   help="suppress progress lines")
    p.set_defaults(func=commands.cmd_wait)

    p = sub.add_parser("download", help="queue a model download")
    p.add_argument("url", help="CivitAI / HuggingFace / direct URL")
    p.add_argument("--folder", help="target folder (default checkpoints)")
    p.add_argument("--filename", help="override output filename")
    p.set_defaults(func=commands.cmd_download)

    p = sub.add_parser("download-status", help="show a download task's status")
    p.add_argument("task_id", help="download task id")
    p.set_defaults(func=commands.cmd_download_status)

    return parser


def main(argv: Any = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    base_url = args.base_url or None
    client = ComfyRestClient(base_url=base_url) if base_url else ComfyRestClient()
    try:
        return args.func(client, args)
    except ApiError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
