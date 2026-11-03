"""
Entry point for the comfy-rest-ext CLI.

Usage: comfy-rest-ext-cli [--base-url URL] [--json] [--timeout SECONDS]
       <group> <command> [args]

Canonical groups: models, videos, images, downloads, deps, snapshot,
nodes, pr-cache, health. Legacy flat commands remain as aliases.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Optional

from .client import ApiError, ComfyRestClient
from . import commands
from .exit_codes import exit_code_for


def _exit_code(status: int) -> int:
    return exit_code_for(status)


def _print_api_error(error: ApiError, as_json: bool) -> int:
    code = _exit_code(error.status)
    if as_json:
        print(json.dumps({
            "ok": False,
            "error": {
                "code": "api_error",
                "status": error.status,
                "message": error.message,
                "exit_code": code,
            },
        }, ensure_ascii=False), file=sys.stderr)
    else:
        print(f"error: {error}", file=sys.stderr)
    return code


def _add_generate_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("prompt", help="text prompt")
    parser.add_argument("--image", action="append", default=None,
                        help="reference image for I2V/R2V (repeatable)")
    parser.add_argument("--video", action="append", default=None,
                        help="reference video for R2V (repeatable, max 3)")
    parser.add_argument("--model",
                        help="model id (default minimax-h3; aliases minimax-h3-t2v/i2v/r2v)")
    parser.add_argument("--width", type=int, help="frame width (default 1344)")
    parser.add_argument("--height", type=int, help="frame height (default 768)")
    parser.add_argument("--seconds", type=float, help="video length in seconds")
    parser.add_argument("--seed", type=int, help="generation seed")
    parser.add_argument("--speed", choices=["auto", "none", "te-speed", "sol-stack"],
                        help="acceleration mode (legacy; default auto)")
    parser.add_argument("--steps", type=int, help="sampling steps")
    parser.add_argument("--turbo",
                        help="Turbo/step-compression LoRA (validated by schema)")
    parser.add_argument("--block-cache",
                        help="block-level cache method (validated by schema)")
    parser.add_argument("--step-cache",
                        help="step-level cache method (validated by schema)")
    parser.add_argument("--attention",
                        help="attention kernel (validated by schema)")
    parser.add_argument("--speed-json",
                        help="full speed JSON object or @file, e.g. "
                             '\'{"steps":4,"block_cache":{"method":"fbc","params":{"threshold":0.12}}}\'')
    parser.add_argument("--speed-file",
                        help="read a full speed JSON object from a file")
    parser.add_argument("--preset",
                        help="named acceleration preset from /v2/extension/acceleration/schema")
    parser.add_argument("--dotted-param", dest="accel_params", action="append",
                        default=None, help=argparse.SUPPRESS)
    parser.add_argument("--multipart", action="store_true",
                        help="upload reference images as multipart instead of base64 content[]")


def _add_image_args(
    parser: argparse.ArgumentParser, default_model: str,
) -> None:
    parser.add_argument("prompt", help="text prompt")
    parser.add_argument("--model", default=default_model,
                        help=f"image model id (default {default_model})")
    parser.add_argument("--size", help="WIDTHxHEIGHT or auto")
    parser.add_argument("--width", type=int, help="output width")
    parser.add_argument("--height", type=int, help="output height")
    parser.add_argument("--aspect-ratio", help="e.g. 16:9 (Qwen-Image 2.1)")
    parser.add_argument("--megapixels", type=float, help="target megapixels (Qwen-Image 2.1)")
    parser.add_argument("--n", type=int, help="number of images")
    parser.add_argument("--negative", help="negative prompt")
    parser.add_argument("--steps", type=int, help="sampling steps")
    parser.add_argument("--cfg", type=float, help="CFG scale")
    parser.add_argument("--sampler", help="sampler name (default euler)")
    parser.add_argument("--scheduler", help="scheduler name (default simple)")
    parser.add_argument("--seed", type=int, help="generation seed")
    parser.add_argument("--response-format", choices=["b64_json", "url"],
                        default="b64_json", help="response format (default b64_json)")
    parser.add_argument("--quality", help="OpenAI quality field (accepted, informational)")
    parser.add_argument("--style", help="OpenAI style field (accepted, informational)")
    parser.add_argument("--out", default="image.png", help="output path (default image.png)")


def _add_image_edit_args(parser: argparse.ArgumentParser) -> None:
    _add_image_args(parser, default_model="qwen-image-2.1")
    parser.add_argument("--image", action="append", default=[],
                        help="reference image (repeatable; image1 is the image being edited)")


def _add_wait_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("video_id", help="video task id")
    parser.add_argument("--timeout", type=float,
                        help="max seconds to wait (default: wait indefinitely)")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress progress lines")


def _add_download_create_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("url", help="CivitAI / HuggingFace / direct URL")
    parser.add_argument("--folder", help="target folder (default checkpoints)")
    parser.add_argument("--filename", help="override output filename")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=os.path.basename(sys.argv[0]) or "comfy-rest-ext",
        description="Command-line client for comfy-rest-ext.",
    )
    parser.add_argument("--base-url", default=None,
                        help="ComfyUI base URL (overrides profile)")
    parser.add_argument("--profile", default=None,
                        help="server profile from config.toml")
    parser.add_argument("--api-key", default=None,
                        help="bearer API key (overrides profile; prefer env)")
    parser.add_argument("--json", action="store_true",
                        help="Emit machine-readable JSON output")
    parser.add_argument("--timeout", dest="http_timeout", type=float,
                        help="HTTP request timeout in seconds "
                             "(default: profile or 60)")
    sub = parser.add_subparsers(dest="command", required=True)

    # -- persistent config ----------------------------------------------
    p = sub.add_parser("config", help="manage persistent server profiles")
    config_sub = p.add_subparsers(dest="config_command", required=True)
    sp = config_sub.add_parser("list", help="list profiles (API keys redacted)")
    sp.set_defaults(func=commands.cmd_config_list)
    sp = config_sub.add_parser("use", help="select the default profile")
    sp.add_argument("profile")
    sp.set_defaults(func=commands.cmd_config_use)
    sp = config_sub.add_parser("set", help="set a profile value")
    sp.add_argument("profile")
    sp.add_argument("key", choices=["base_url", "api_key", "timeout", "default_model"])
    sp.add_argument("value", help="value; use '-' for api_key to read stdin")
    sp.set_defaults(func=commands.cmd_config_set)
    sp = config_sub.add_parser("get", help="show a profile or one value")
    sp.add_argument("profile")
    sp.add_argument("key", nargs="?")
    sp.set_defaults(func=commands.cmd_config_get)
    sp = config_sub.add_parser("path", help="show the config file path")
    sp.set_defaults(func=commands.cmd_config_path)

    # -- capabilities / provisioning ------------------------------------
    p = sub.add_parser("capabilities", help="show operational capability status")
    p.add_argument("--model", help="filter by model id")
    p.add_argument("--capability", help="filter by capability id, e.g. attention.flash")
    p.set_defaults(func=commands.cmd_capabilities)
    p = sub.add_parser("doctor", help="health and capability diagnostics")
    p.set_defaults(func=commands.cmd_doctor)
    p = sub.add_parser("provision", help="install selected capabilities")
    p.add_argument("capability", nargs="+", help="capability id(s), e.g. attention.flash")
    p.add_argument("--yes", action="store_true", help="explicitly authorize installation")
    p.add_argument("--retry-task", help="resume this blocked video task after install")
    p.set_defaults(func=commands.cmd_provision)
    p = sub.add_parser("restart", help="restart ComfyUI through an available adapter")
    p.add_argument("--yes", action="store_true", help="explicitly authorize restart")
    p.add_argument("--retry-task", help="blocked video task to retry after reconnect")
    p.add_argument("--reason", help="optional restart reason")
    p.add_argument("--timeout", type=float, default=300.0)
    p.add_argument("--interval", type=float, default=5.0)
    p.set_defaults(func=commands.cmd_restart)
    p = sub.add_parser("wait-reconnect", help="wait for a restarted server and optionally retry")
    p.add_argument("--retry-task", help="blocked video task to retry after reconnect")
    p.add_argument("--since-boot", help="require a different boot_id")
    p.add_argument("--timeout", type=float, default=300.0)
    p.add_argument("--interval", type=float, default=5.0)
    p.set_defaults(func=commands.cmd_wait_reconnect)

    # -- models ---------------------------------------------------------
    p = sub.add_parser("models", help="list / inspect / manage models")
    p.set_defaults(func=commands.cmd_models)
    models_sub = p.add_subparsers(dest="models_command")
    sp = models_sub.add_parser("list", help="list OpenAI model ids")
    sp.set_defaults(func=commands.cmd_models)
    sp = models_sub.add_parser("get", help="get one model descriptor")
    sp.add_argument("model_id")
    sp.set_defaults(func=commands.cmd_model_get)
    sp = models_sub.add_parser("all", help="recursively list model files")
    sp.add_argument("--folder", help="limit to one model folder")
    sp.add_argument("--hash", dest="include_hash", action="store_true",
                    help="compute file hashes (slow)")
    sp.add_argument("--metadata", dest="include_metadata", action="store_true",
                    help="include model metadata (large)")
    sp.set_defaults(func=commands.cmd_models_all)
    sp = models_sub.add_parser("info", help="show model file metadata")
    sp.add_argument("path", help="model path relative to a model folder, or absolute")
    sp.set_defaults(func=commands.cmd_model_info)
    sp = models_sub.add_parser("delete", help="delete a model file")
    sp.add_argument("path")
    sp.add_argument("--force", action="store_true",
                    help="skip protected-model checks")
    sp.set_defaults(func=commands.cmd_model_delete)

    # -- videos ---------------------------------------------------------
    p = sub.add_parser("videos", help="generate / inspect video tasks")
    videos_sub = p.add_subparsers(dest="videos_command", required=True)
    sp = videos_sub.add_parser("generate", help="generate a video")
    _add_generate_args(sp)
    sp.set_defaults(func=commands.cmd_generate)
    sp = videos_sub.add_parser("status", help="show a video task")
    sp.add_argument("video_id")
    sp.set_defaults(func=commands.cmd_status)
    sp = videos_sub.add_parser("list", help="list video tasks")
    sp.set_defaults(func=commands.cmd_list)
    sp = videos_sub.add_parser("wait", help="wait for a video task to finish")
    _add_wait_args(sp)
    sp.set_defaults(func=commands.cmd_wait)
    sp = videos_sub.add_parser("content", help="download a completed video")
    sp.add_argument("video_id")
    sp.add_argument("--out", help="output path (default <video_id>.mp4)")
    sp.set_defaults(func=commands.cmd_video_content)
    sp = videos_sub.add_parser("delete", help="delete a video task record")
    sp.add_argument("video_id")
    sp.set_defaults(func=commands.cmd_video_delete)
    sp = videos_sub.add_parser("retry", help="retry a provisioning-blocked video")
    sp.add_argument("video_id")
    sp.set_defaults(func=commands.cmd_video_retry)

    # -- images ---------------------------------------------------------
    p = sub.add_parser("images", help="generate / inspect image tasks")
    p.set_defaults(func=commands.cmd_images)
    images_sub = p.add_subparsers(dest="images_command")
    sp = images_sub.add_parser("generate", help="generate an image")
    _add_image_args(sp, default_model="anima-base")
    sp.set_defaults(func=commands.cmd_image)
    sp = images_sub.add_parser("edit", help="edit an image (Qwen-Image 2.1)")
    _add_image_edit_args(sp)
    sp.set_defaults(func=commands.cmd_image_edit)
    sp = images_sub.add_parser("list", help="list image tasks")
    sp.set_defaults(func=commands.cmd_images)
    sp = images_sub.add_parser("status", help="show an image task")
    sp.add_argument("image_id")
    sp.set_defaults(func=commands.cmd_image_status)
    sp = images_sub.add_parser("content", help="download one generated image")
    sp.add_argument("image_id")
    sp.add_argument("--index", type=int, default=0,
                    help="output index (default 0)")
    sp.add_argument("--out", help="output path (default <image_id>_<index>.png)")
    sp.set_defaults(func=commands.cmd_image_content)

    # -- downloads ------------------------------------------------------
    p = sub.add_parser("downloads", help="manage model download tasks")
    downloads_sub = p.add_subparsers(dest="downloads_command", required=True)
    sp = downloads_sub.add_parser("create", help="queue a model download")
    _add_download_create_args(sp)
    sp.set_defaults(func=commands.cmd_download)
    sp = downloads_sub.add_parser("status", help="show a download task")
    sp.add_argument("task_id")
    sp.set_defaults(func=commands.cmd_download_status)
    sp = downloads_sub.add_parser("list", help="list download tasks")
    sp.add_argument("--status", default="all",
                    help="status filter: all, or comma-separated "
                         "queued/downloading/completed/failed/cancelled")
    sp.set_defaults(func=commands.cmd_downloads_list)
    sp = downloads_sub.add_parser("cancel", help="cancel a download task")
    sp.add_argument("task_id")
    sp.set_defaults(func=commands.cmd_download_cancel)

    # -- deps -----------------------------------------------------------
    p = sub.add_parser("deps", help="workflow and node dependency operations")
    deps_sub = p.add_subparsers(dest="deps_command", required=True)
    sp = deps_sub.add_parser("install", help="install workflow dependencies")
    sp.add_argument("--workflow", required=True,
                    help="workflow JSON file, or - for stdin")
    sp.add_argument("--sync", action="store_true",
                    help="block until installation finishes")
    sp.set_defaults(func=commands.cmd_deps_install)
    sp = deps_sub.add_parser("status", help="show an install task")
    sp.add_argument("task_id")
    sp.set_defaults(func=commands.cmd_deps_status)
    sp = deps_sub.add_parser("check-workflow", help="check workflow dependencies")
    sp.add_argument("--workflow", required=True,
                    help="workflow JSON file, or - for stdin")
    sp.set_defaults(func=commands.cmd_deps_check_workflow)
    sp = deps_sub.add_parser("check-node", help="list a node's required packages")
    sp.add_argument("--node", required=True, help="node class name")
    sp.set_defaults(func=commands.cmd_deps_check_node)
    sp = deps_sub.add_parser("restore", help="restore node dependencies")
    sp.add_argument("--node", action="append", required=True,
                    help="node name (repeatable)")
    sp.add_argument("--sync", action="store_true",
                    help="run synchronously")
    sp.set_defaults(func=commands.cmd_deps_restore)

    # -- snapshots ------------------------------------------------------
    p = sub.add_parser("snapshot", help="snapshot export / import / diff")
    snapshot_sub = p.add_subparsers(dest="snapshot_command", required=True)
    sp = snapshot_sub.add_parser("export", help="export a snapshot")
    sp.add_argument("snapshot_id")
    sp.add_argument("--format", default="tarball", help="snapshot format")
    sp.add_argument("--include-models", action="store_true")
    sp.set_defaults(func=commands.cmd_snapshot_export)
    sp = snapshot_sub.add_parser("import", help="import a snapshot")
    sp.add_argument("--path", required=True)
    sp.add_argument("--no-restore-models", action="store_true")
    sp.add_argument("--no-restore-nodes", action="store_true")
    sp.set_defaults(func=commands.cmd_snapshot_import)
    sp = snapshot_sub.add_parser("diff", help="compare two snapshots")
    sp.add_argument("snapshot_a")
    sp.add_argument("snapshot_b")
    sp.set_defaults(func=commands.cmd_snapshot_diff)
    sp = snapshot_sub.add_parser("list", help="list snapshots")
    sp.set_defaults(func=commands.cmd_snapshot_list)

    # -- nodes ----------------------------------------------------------
    p = sub.add_parser("nodes", help="custom node inspection / packaging")
    nodes_sub = p.add_subparsers(dest="nodes_command", required=True)
    sp = nodes_sub.add_parser("list", help="list installed custom nodes")
    sp.set_defaults(func=commands.cmd_nodes_list)
    sp = nodes_sub.add_parser("pack", help="pack a custom node into a zip")
    sp.add_argument("node_name")
    sp.add_argument("--no-comfyignore", action="store_true",
                    help="do not apply .comfyignore rules")
    sp.set_defaults(func=commands.cmd_nodes_pack)
    sp = nodes_sub.add_parser("validate", help="validate a custom node")
    sp.add_argument("node_name")
    sp.set_defaults(func=commands.cmd_nodes_validate)
    sp = nodes_sub.add_parser("init", help="initialize a custom node project")
    sp.add_argument("path")
    sp.set_defaults(func=commands.cmd_nodes_init)

    # -- pr-cache -------------------------------------------------------
    p = sub.add_parser("pr-cache", help="frontend PR cache operations")
    pr_sub = p.add_subparsers(dest="pr_cache_command", required=True)
    sp = pr_sub.add_parser("list", help="list cache entries")
    sp.set_defaults(func=commands.cmd_pr_cache_list)
    sp = pr_sub.add_parser("size", help="show cache size")
    sp.set_defaults(func=commands.cmd_pr_cache_size)
    sp = pr_sub.add_parser("delete", help="delete one cache entry")
    sp.add_argument("pr")
    sp.set_defaults(func=commands.cmd_pr_cache_delete)
    sp = pr_sub.add_parser("clear", help="clear the whole cache")
    sp.add_argument("--confirm", action="store_true",
                    help="required safety confirmation")
    sp.set_defaults(func=commands.cmd_pr_cache_clear)

    # -- health ---------------------------------------------------------
    p = sub.add_parser("health", help="check extension health")
    p.set_defaults(func=commands.cmd_health)

    # -- legacy flat aliases -------------------------------------------
    p = sub.add_parser("generate", help="alias for videos generate")
    _add_generate_args(p)
    p.set_defaults(func=commands.cmd_generate)
    p = sub.add_parser("status", help="alias for videos status")
    p.add_argument("video_id")
    p.set_defaults(func=commands.cmd_status)
    p = sub.add_parser("list", help="alias for videos list")
    p.set_defaults(func=commands.cmd_list)
    p = sub.add_parser("wait", help="alias for videos wait")
    _add_wait_args(p)
    p.set_defaults(func=commands.cmd_wait)
    p = sub.add_parser("image", help="alias for images generate")
    _add_image_args(p, default_model="anima-base")
    p.set_defaults(func=commands.cmd_image)
    p = sub.add_parser("image-edit", help="alias for images edit")
    _add_image_edit_args(p)
    p.set_defaults(func=commands.cmd_image_edit)
    p = sub.add_parser("image-status", help="alias for images status")
    p.add_argument("image_id")
    p.set_defaults(func=commands.cmd_image_status)
    p = sub.add_parser("download", help="alias for downloads create")
    _add_download_create_args(p)
    p.set_defaults(func=commands.cmd_download)
    p = sub.add_parser("download-status", help="alias for downloads status")
    p.add_argument("task_id")
    p.set_defaults(func=commands.cmd_download_status)

    return parser


_DOT_PARAM_RE = re.compile(
    r"^--(?P<dim>[a-z_][a-z0-9_-]*)\."
    r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)(?:=(?P<value>.*))?$"
)
_NUMERIC_VALUE_RE = re.compile(
    r"^-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
)


def _rewrite_dot_params(argv: list[str]) -> list[str]:
    """Rewrite --block-cache.threshold 0.12 into --dotted-param block_cache.threshold=0.12."""
    out: list[str] = []
    index = 0
    while index < len(argv):
        match = _DOT_PARAM_RE.match(argv[index])
        if not match:
            out.append(argv[index])
            index += 1
            continue
        value = match.group("value")
        if value is None:
            if index + 1 >= len(argv):
                raise ValueError(
                    f"{argv[index]} requires a value"
                )
            candidate = argv[index + 1]
            if candidate.startswith("-") and not _NUMERIC_VALUE_RE.match(candidate):
                raise ValueError(
                    f"{argv[index]} requires a value (got option {candidate!r})"
                )
            value = candidate
            index += 2
        else:
            index += 1
        dim = match.group("dim").replace("-", "_")
        out.extend(["--dotted-param", f"{dim}.{match.group('key')}={value}"])
    return out


def main(argv: Any = None) -> int:
    from . import config

    parser = _build_parser()
    try:
        parsed_argv = _rewrite_dot_params(
            list(sys.argv[1:] if argv is None else argv)
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    args = parser.parse_args(parsed_argv)

    commands.OUTPUT_JSON = bool(args.json)
    if args.command == "config":
        try:
            return args.func(None, args)
        except ApiError as e:
            return _print_api_error(e, bool(args.json))
        finally:
            commands.OUTPUT_JSON = False

    try:
        data = config.load_config()
        resolved = config.resolve(
            data,
            profile=args.profile,
            base_url=args.base_url,
            api_key=args.api_key,
            timeout=args.http_timeout,
        )
    except config.ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if (getattr(args, "model", None) is None
            and resolved.get("default_model")):
        args.model = resolved["default_model"]
    client = ComfyRestClient(
        base_url=resolved["base_url"],
        timeout=resolved["timeout"],
        api_key=resolved["api_key"],
    )
    try:
        return args.func(client, args)
    except ApiError as e:
        return _print_api_error(e, bool(args.json))
    except KeyboardInterrupt:
        return 130
    finally:
        commands.OUTPUT_JSON = False


if __name__ == "__main__":
    sys.exit(main())
