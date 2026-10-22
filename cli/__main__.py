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
    p.add_argument("--image", action="append", default=None,
                   help="reference image for I2V/R2V (repeatable for R2V)")
    p.add_argument("--model", help="model id (default minimax-h3; aliases minimax-h3-t2v/i2v/r2v)")
    p.add_argument("--width", type=int, help="frame width (default 1344)")
    p.add_argument("--height", type=int, help="frame height (default 768)")
    p.add_argument("--seconds", type=float, help="video length in seconds")
    p.add_argument("--seed", type=int, help="generation seed")
    p.add_argument("--speed", choices=["auto", "none", "te-speed", "sol-stack"],
                   help="acceleration mode (legacy; default auto)")
    p.add_argument("--steps", type=int, help="sampling steps")
    p.add_argument("--turbo", choices=["none", "turbo-v4"],
                   help="Turbo LoRA step compression (default none)")
    p.add_argument("--block-cache", choices=["none", "te-speed", "fbc", "cachedit"],
                   help="block-level cache (default none; mutually exclusive within layer)")
    p.add_argument("--step-cache", choices=["none", "easycache", "lazycache", "teacache", "spectrum"],
                   help="step-level cache (default none; mutually exclusive within layer)")
    p.add_argument("--attention", choices=["none", "sol", "sage", "flash"],
                   help="attention kernel (default none; mutually exclusive within layer)")
    p.add_argument("--vae-decode", choices=["default", "batched"],
                   help="VAE decode strategy (default default)")
    p.set_defaults(func=commands.cmd_generate)

    p = sub.add_parser("image", help="generate an image (OpenAI Images API)")
    p.add_argument("prompt", help="text prompt")
    p.add_argument("--model", default="anima-base",
                   help="image model id (default anima-base)")
    p.add_argument("--size", help="WIDTHxHEIGHT or auto")
    p.add_argument("--width", type=int, help="output width")
    p.add_argument("--height", type=int, help="output height")
    p.add_argument("--aspect-ratio", help="e.g. 16:9 (Qwen-Image 2.1)")
    p.add_argument("--megapixels", type=float, help="target megapixels (Qwen-Image 2.1)")
    p.add_argument("--n", type=int, help="number of images")
    p.add_argument("--negative", help="negative prompt")
    p.add_argument("--steps", type=int, help="sampling steps")
    p.add_argument("--cfg", type=float, help="CFG scale")
    p.add_argument("--sampler", help="sampler name (default euler)")
    p.add_argument("--scheduler", help="scheduler name (default simple)")
    p.add_argument("--seed", type=int, help="generation seed")
    p.add_argument("--response-format", choices=["b64_json", "url"],
                   default="b64_json", help="response format (default b64_json)")
    p.add_argument("--quality", help="OpenAI quality field (accepted, informational)")
    p.add_argument("--style", help="OpenAI style field (accepted, informational)")
    p.add_argument("--out", default="image.png", help="output path (default image.png)")
    p.set_defaults(func=commands.cmd_image)

    p = sub.add_parser("image-edit", help="edit an image (Qwen-Image 2.1)")
    p.add_argument("prompt", help="edit instruction; reference images are <image1>..<imageN>")
    p.add_argument("--image", action="append", default=[],
                   help="reference image (repeatable; image1 is the image being edited)")
    p.add_argument("--model", default="qwen-image-2.1",
                   help="image model id (default qwen-image-2.1)")
    p.add_argument("--size", help="WIDTHxHEIGHT or auto")
    p.add_argument("--width", type=int, help="output width")
    p.add_argument("--height", type=int, help="output height")
    p.add_argument("--n", type=int, help="number of outputs")
    p.add_argument("--negative", help="negative prompt")
    p.add_argument("--steps", type=int, help="sampling steps")
    p.add_argument("--cfg", type=float, help="CFG scale")
    p.add_argument("--sampler", help="sampler name (default euler)")
    p.add_argument("--scheduler", help="scheduler name (default simple)")
    p.add_argument("--seed", type=int, help="generation seed")
    p.add_argument("--response-format", choices=["b64_json", "url"],
                   default="b64_json", help="response format (default b64_json)")
    p.add_argument("--quality", help="OpenAI quality field (accepted, informational)")
    p.add_argument("--style", help="OpenAI style field (accepted, informational)")
    p.add_argument("--out", default="image.png", help="output path (default image.png)")
    p.set_defaults(func=commands.cmd_image_edit)

    p = sub.add_parser("status", help="show a video task's status")
    p.add_argument("video_id", help="video task id")
    p.set_defaults(func=commands.cmd_status)

    p = sub.add_parser("list", help="list video tasks")
    p.set_defaults(func=commands.cmd_list)

    p = sub.add_parser("images", help="list image tasks")
    p.set_defaults(func=commands.cmd_images)

    p = sub.add_parser("image-status", help="show an image task's status")
    p.add_argument("image_id", help="image task id")
    p.set_defaults(func=commands.cmd_image_status)

    p = sub.add_parser("wait", help="wait for a video task to finish")
    p.add_argument("video_id", help="video task id")
    p.add_argument("--timeout", type=float,
                   help="max seconds to wait (default: wait indefinitely)")
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
