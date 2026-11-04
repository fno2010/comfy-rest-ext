"""
Command implementations for the comfy-rest-ext CLI.

Each command takes (client, args) and returns an exit code. Human-readable
output goes to stdout; --json switches every command to raw JSON output.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from typing import Any, Dict, Optional, Sequence

from .client import ApiError, ComfyRestClient
from .exit_codes import EXIT_FAILURE, exit_code_for

POLL_INTERVAL = 5.0
OUTPUT_JSON = False


def _fmt_duration(seconds: float) -> str:
    """Format seconds as a compact duration string."""
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _emit(data: Any, as_json: bool) -> int:
    if as_json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def _emit_error(message: str, status: Optional[int] = None) -> int:
    if status is None:
        current = sys.exc_info()[1]
        if isinstance(current, ApiError):
            status = current.status
    code = exit_code_for(status) if status is not None else EXIT_FAILURE
    if OUTPUT_JSON:
        print(json.dumps({
            "ok": False,
            "error": {"code": "cli_error", "message": message, "exit_code": code},
        }, ensure_ascii=False), file=sys.stderr)
    else:
        print(f"error: {message}", file=sys.stderr)
    return code


def cmd_models(client: ComfyRestClient, args: Any) -> int:
    models = client.list_models()
    if args.json:
        return _emit(models, True)
    for m in models:
        print(f"{m['id']:<26} {m.get('modality', 'video'):<6} {m.get('owned_by', '')}")
    return 0


def _build_image_payload(args: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": args.model,
        "prompt": args.prompt,
    }
    optional = {
        "size": getattr(args, "size", None),
        "width": getattr(args, "width", None),
        "height": getattr(args, "height", None),
        "aspect_ratio": getattr(args, "aspect_ratio", None),
        "megapixels": getattr(args, "megapixels", None),
        "n": getattr(args, "n", None),
        "negative_prompt": getattr(args, "negative", None),
        "steps": getattr(args, "steps", None),
        "cfg": getattr(args, "cfg", None),
        "sampler": getattr(args, "sampler", None),
        "scheduler": getattr(args, "scheduler", None),
        "seed": getattr(args, "seed", None),
        "response_format": getattr(args, "response_format", None),
        "quality": getattr(args, "quality", None),
        "style": getattr(args, "style", None),
    }
    for key, value in optional.items():
        if value is not None:
            payload[key] = value
    return payload


def _write_image_response(response: Dict[str, Any], out: str) -> None:
    data = response.get("data") or []
    if not data:
        raise ApiError(0, "response contained no image data")
    root, ext = os.path.splitext(out)
    ext = ext or ".png"
    for index, item in enumerate(data):
        if item.get("b64_json"):
            dest = out if index == 0 else f"{root}_{index + 1}{ext}"
            with open(dest, "wb") as f:
                f.write(base64.b64decode(item["b64_json"]))
            print(f"saved:     {dest}")
        elif item.get("url"):
            print(f"url:       {item['url']}")
        else:
            raise ApiError(0, "response item has neither b64_json nor url")


def cmd_image(client: ComfyRestClient, args: Any) -> int:
    try:
        response = client.create_image(_build_image_payload(args))
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(response, True)
    _write_image_response(response, args.out)
    return 0


def cmd_image_edit(client: ComfyRestClient, args: Any) -> int:
    payload = _build_image_payload(args)
    files = []
    try:
        for path in args.image:
            with open(path, "rb") as f:
                files.append(("image", os.path.basename(path), f.read()))
    except OSError as e:
        return _emit_error(f"image file error: {e}")
    if not files:
        return _emit_error("at least one --image is required")
    try:
        response = client.create_image_edit(payload, files)
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(response, True)
    _write_image_response(response, args.out)
    return 0


def _read_json_file(path: str, label: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            parsed = json.load(f)
    except OSError as e:
        raise ApiError(400, f"{label}: cannot read {path}: {e.strerror}") from e
    except UnicodeDecodeError as e:
        raise ApiError(400, f"{label}: {path} is not valid UTF-8: {e}") from e
    except json.JSONDecodeError as e:
        raise ApiError(400, f"{label}: invalid JSON in {path}: {e}") from e
    if not isinstance(parsed, dict):
        raise ApiError(400, f"{label}: {path} must contain a JSON object")
    return parsed


def _read_speed_json(value: str) -> Dict[str, Any]:
    if value.startswith("@"):
        return _read_json_file(value[1:], "--speed-json")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as e:
        raise ApiError(400, f"--speed-json is not valid JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise ApiError(400, "--speed-json must decode to an object")
    return parsed


def _schema_method(schema: Dict[str, Any], dim: str, method_id: str) -> Dict[str, Any]:
    for dimension in schema.get("dimensions", []):
        if dimension.get("id") != dim:
            continue
        for method in dimension.get("methods", []):
            if method.get("id") == method_id:
                return method
    raise ApiError(400, f"method {method_id!r} is not registered for {dim}")


def _coerce_accel_param(
    dim: str, method: Dict[str, Any], key: str, raw: str,
) -> Any:
    spec = (method.get("params") or {}).get(key)
    method_id = method.get("id", "?")
    if spec is None:
        available = sorted((method.get("params") or {}).keys())
        raise ApiError(
            400,
            f"{dim}.{key} is not a parameter of {method_id}; "
            f"expected one of {available}",
        )
    kind = spec.get("type")
    if kind == "bool":
        lowered = raw.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            value: Any = True
        elif lowered in ("false", "0", "no", "off"):
            value = False
        else:
            raise ApiError(400, f"{dim}.{key} must be a boolean")
    elif kind == "int":
        try:
            value = int(raw)
        except ValueError:
            raise ApiError(400, f"{dim}.{key} must be an integer") from None
    elif kind == "float":
        try:
            value = float(raw)
        except ValueError:
            raise ApiError(400, f"{dim}.{key} must be a number") from None
    else:
        value = raw
    choices = spec.get("choices")
    if choices is not None and value not in choices:
        raise ApiError(400, f"{dim}.{key} must be one of {choices}")
    minimum = spec.get("min")
    maximum = spec.get("max")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if minimum is not None and value < minimum:
            raise ApiError(400, f"{dim}.{key} must be >= {minimum}")
        if maximum is not None and value > maximum:
            raise ApiError(400, f"{dim}.{key} must be <= {maximum}")
    return value


def _build_dotted_speed(
    schema: Dict[str, Any],
    layer_opts: Dict[str, Any],
    entries: Sequence[str],
    steps: Optional[int],
) -> Dict[str, Any]:
    grouped: Dict[str, Dict[str, str]] = {}
    for entry in entries:
        if "=" not in entry:
            raise ApiError(400, f"dot-path parameter {entry!r} must be DIM.KEY=VALUE")
        path, raw = entry.split("=", 1)
        if "." not in path:
            raise ApiError(400, f"dot-path parameter {entry!r} must be DIM.KEY=VALUE")
        dim, key = path.split(".", 1)
        dim = dim.replace("-", "_")
        grouped.setdefault(dim, {})[key] = raw

    speed_obj: Dict[str, Any] = {}
    for dim, params in grouped.items():
        method_id = layer_opts.get(dim)
        if method_id in (None, "none", "default"):
            raise ApiError(
                400,
                f"--{dim.replace('_', '-')} must select a method before setting .{next(iter(params))}",
            )
        method = _schema_method(schema, dim, method_id)
        if not method.get("implemented", True):
            raise ApiError(400, f"{dim}.{method_id} is not implemented")
        if method.get("available") is False:
            missing = method.get("missing_nodes") or []
            raise ApiError(
                400,
                f"{dim}.{method_id} is unavailable; missing nodes: {missing}",
            )
        coerced = {
            key: _coerce_accel_param(dim, method, key, raw)
            for key, raw in params.items()
        }
        speed_obj[dim] = {"method": method_id, "params": coerced}

    for dim, method_id in layer_opts.items():
        if method_id in (None, "none", "default"):
            continue
        speed_obj.setdefault(dim, method_id)
    if steps:
        speed_obj["steps"] = steps
    return speed_obj


def _build_video_payload(args: Any, schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"prompt": args.prompt}
    if args.model:
        payload["model"] = args.model
    if args.width:
        payload["width"] = args.width
    if args.height:
        payload["height"] = args.height
    if args.seconds:
        payload["seconds"] = args.seconds
    if args.seed is not None:
        payload["seed"] = args.seed
    video_format = getattr(args, "format", None)
    if video_format:
        payload["format"] = video_format
    video_codec = getattr(args, "codec", None)
    if video_codec:
        payload["codec"] = video_codec

    # Speed: legacy string XOR composable per-layer options. Passing both
    # is ambiguous — fail loudly instead of silently preferring one.
    layer_opts = {
        "turbo": getattr(args, "turbo", None),
        "block_cache": getattr(args, "block_cache", None),
        "step_cache": getattr(args, "step_cache", None),
        "attention": getattr(args, "attention", None),
    }
    steps = getattr(args, "steps", None)
    legacy_speed = getattr(args, "speed", None)
    active_layers = {
        dim: method_id
        for dim, method_id in layer_opts.items()
        if method_id not in (None, "none")
    }
    speed_json = getattr(args, "speed_json", None)
    speed_file = getattr(args, "speed_file", None)
    preset = getattr(args, "preset", None)
    accel_params = getattr(args, "accel_params", None) or []

    if schema and active_layers:
        for dim, method_id in active_layers.items():
            method = _schema_method(schema, dim, method_id)
            if not method.get("implemented", True):
                raise ApiError(400, f"{dim}.{method_id} is not implemented")
            if method.get("available") is False:
                missing = method.get("missing_nodes") or []
                raise ApiError(
                    400,
                    f"{dim}.{method_id} is unavailable; missing nodes: {missing}",
                )

    if speed_json and speed_file:
        raise ApiError(400, "--speed-json and --speed-file are mutually exclusive")
    if preset and (legacy_speed or speed_json or speed_file or steps
                   or active_layers or accel_params):
        raise ApiError(
            400,
            "--preset cannot be combined with other acceleration inputs",
        )
    if speed_json:
        if (legacy_speed or steps or active_layers or speed_file or preset
                or accel_params):
            raise ApiError(
                400,
                "--speed-json cannot be combined with --speed/--steps/"
                "--turbo/--block-cache/--step-cache/--attention/"
                "--speed-file/--preset/dot-path params",
            )
        payload["speed"] = _read_speed_json(speed_json)
        return payload
    if speed_file:
        if (legacy_speed or steps or active_layers or preset or accel_params):
            raise ApiError(
                400,
                "--speed-file cannot be combined with other acceleration inputs",
            )
        payload["speed"] = _read_json_file(speed_file, "--speed-file")
        return payload
    if preset:
        if not schema:
            raise ApiError(400, "--preset requires the acceleration schema")
        presets = schema.get("presets") or {}
        if preset not in presets:
            raise ApiError(
                400,
                f"unknown preset {preset!r}; expected one of {sorted(presets)}",
            )
        payload["speed"] = presets[preset]
        return payload
    if accel_params:
        if legacy_speed:
            raise ApiError(400, "--speed cannot be combined with dot-path params")
        if not schema:
            raise ApiError(400, "dot-path params require the acceleration schema")
        payload["speed"] = _build_dotted_speed(
            schema, layer_opts, accel_params, steps,
        )
        return payload
    if legacy_speed and legacy_speed != "auto" and (active_layers or steps):
        raise ApiError(
            400,
            "--speed conflicts with --steps/--turbo/--block-cache/--step-cache/"
            "--attention; use one or the other",
        )
    if active_layers or steps:
        speed_obj: Dict[str, Any] = {k: v for k, v in layer_opts.items() if v is not None}
        if steps:
            speed_obj["steps"] = steps
        payload["speed"] = speed_obj
    elif legacy_speed:
        payload["speed"] = legacy_speed
    return payload


def cmd_generate(client: ComfyRestClient, args: Any) -> int:
    try:
        schema = None
        flat_accel = any(
            getattr(args, name, None) not in (None, "none")
            for name in ("turbo", "block_cache", "step_cache", "attention")
        )
        if (getattr(args, "preset", None)
                or getattr(args, "accel_params", None)
                or flat_accel):
            schema = client.acceleration_schema()
        if getattr(args, "multipart", False):
            payload = _build_video_payload(args, schema=schema)
            files = []
            for path in getattr(args, "image", None) or []:
                with open(path, "rb") as f:
                    files.append(
                        ("input_reference", os.path.basename(path), f.read())
                    )
            for path in getattr(args, "video", None) or []:
                with open(path, "rb") as f:
                    files.append(("video", os.path.basename(path), f.read()))
            task = client.create_video_multipart(payload, files)
        else:
            task = client.create_video(_build_video_request(args, schema=schema))
    except OSError as e:
        if isinstance(e, FileNotFoundError):
            return _emit_error(f"image file not found: {e.filename}")
        return _emit_error(f"image file error: {e}")
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(task, True)
    print(f"task {task['id']} queued (status: {task['status']})")
    print(f"view later: {task['id']}")
    return 0


def _build_video_request(args: Any, schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build a content[]/input[] video request (industry-standard format).

    Text goes into an input_text item; uploaded images become input_image
    items. A single image is a first_frame (I2V) unless reference videos are
    also supplied; multiple images or any video make images reference_image
    items (R2V) referenced via <Picture N> / <Video N> tags.
    """
    payload = _build_video_payload(args, schema=schema)
    items: list = [{"type": "input_text", "text": args.prompt}]
    images = getattr(args, "image", None) or []
    videos = getattr(args, "video", None) or []
    if isinstance(images, str):
        images = [images]
    if images:
        model = getattr(args, "model", None)
        if len(images) > 1 or (model and model.endswith("-r2v")) or videos:
            role = "reference_image"
        else:
            role = "first_frame"
        for img in images:
            with open(img, "rb") as f:
                data = f.read()
            ext = os.path.splitext(img)[1].lower().lstrip(".") or "png"
            b64 = base64.b64encode(data).decode("ascii")
            items.append({
                "type": "input_image",
                "image_url": f"data:image/{ext};base64,{b64}",
                "role": role,
            })
    for vid in videos:
        with open(vid, "rb") as f:
            data = f.read()
        ext = os.path.splitext(vid)[1].lower().lstrip(".") or "mp4"
        b64 = base64.b64encode(data).decode("ascii")
        items.append({
            "type": "input_video",
            "video_url": f"data:video/{ext};base64,{b64}",
        })
    payload["input"] = items
    payload.pop("prompt", None)
    return payload


def cmd_status(client: ComfyRestClient, args: Any) -> int:
    try:
        task = client.get_video(args.video_id)
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(task, True)
    print(f"id:        {task['id']}")
    print(f"status:    {task['status']}")
    print(f"progress:  {task.get('progress', 0)}%")
    node = task.get("current_node")
    if node:
        print(f"node:      {node} ({task.get('node_progress', 0)}%)")
    if task.get("elapsed") is not None:
        print(f"elapsed:   {_fmt_duration(task['elapsed'])}")
    if task.get("eta") is not None:
        print(f"eta:       {_fmt_duration(task['eta'])}")
    print(f"model:     {task.get('model', '')}")
    print(f"size:      {task.get('size', '')}")
    print(f"seconds:   {task.get('seconds', '')}")
    for key in ("reference_images", "reference_videos",
                "first_frame", "last_frame"):
        if task.get(key):
            value = task[key]
            if isinstance(value, list):
                value = ", ".join(value)
            print(f"{key + ':':<18} {value}")
    if task.get("error"):
        print(f"error:     {task['error'].get('message', task['error'])}")
    if task.get("view_url"):
        print(f"view_url:  {task['view_url']}")
    return 0


def cmd_list(client: ComfyRestClient, args: Any) -> int:
    try:
        tasks = client.list_videos()
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(tasks, True)
    for t in tasks:
        status = t["status"]
        print(f"{t['id']:<36} {status:<12} {t.get('model', ''):<16} "
              f"{t.get('size', ''):<10} {t.get('view_url') or ''}")
    return 0


def cmd_images(client: ComfyRestClient, args: Any) -> int:
    try:
        tasks = client.list_images()
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(tasks, True)
    for t in tasks:
        urls = t.get("view_urls") or []
        print(f"{t['id']:<40} {t['status']:<12} {t.get('model', ''):<22} "
              f"{t.get('size', ''):<10} {urls[0] if urls else ''}")
    return 0


def cmd_image_status(client: ComfyRestClient, args: Any) -> int:
    try:
        task = client.get_image(args.image_id)
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(task, True)
    print(f"id:        {task['id']}")
    print(f"status:    {task['status']}")
    print(f"model:     {task.get('model', '')}")
    print(f"progress:  {task.get('progress', 0)}%")
    print(f"size:      {task.get('size', '')}")
    print(f"n:         {task.get('n', 1)}")
    if task.get("error"):
        err = task["error"]
        print(f"error:     {err.get('message', err)}")
    for url in task.get("view_urls") or []:
        print(f"view_url:  {url}")
    return 0


def cmd_wait(client: ComfyRestClient, args: Any) -> int:
    deadline = time.time() + args.timeout if args.timeout else None
    while True:
        try:
            task = client.get_video(args.video_id)
        except ApiError as e:
            return _emit_error(str(e))
        status = task["status"]
        if not args.json and not args.quiet:
            line = f"[{task['id']}] {status} {task.get('progress', 0)}%"
            node = task.get("current_node")
            if node:
                line += f" @ {node} {task.get('node_progress', 0)}%"
            eta = task.get("eta")
            if eta is not None:
                line += f" (eta {_fmt_duration(eta)})"
            print(line, file=sys.stderr)
        if status in ("completed", "failed", "cancelled"):
            exit_code = 0 if status == "completed" else 1
            if args.json:
                _emit(task, True)
                return exit_code
            print(f"status:    {status}")
            if task.get("view_url"):
                print(f"view_url:  {task['view_url']}")
            return exit_code
        if deadline is not None and time.time() >= deadline:
            return _emit_error("timed out waiting for video task")
        time.sleep(POLL_INTERVAL)


def cmd_download(client: ComfyRestClient, args: Any) -> int:
    try:
        task = client.create_download(
            args.url, folder=args.folder, filename=args.filename
        )
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(task, True)
    print(f"download task {task.get('task_id', '')} queued "
          f"(status: {task.get('status', '')})")
    return 0


def cmd_download_status(client: ComfyRestClient, args: Any) -> int:
    try:
        task = client.get_download(args.task_id)
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(task, True)
    for key in ("task_id", "status", "progress", "url", "local_path", "error"):
        if task.get(key):
            print(f"{key:<12} {task[key]}")
    return 0


# ---------------------------------------------------------------------------
# Model inspection / management
# ---------------------------------------------------------------------------

def cmd_model_get(client: ComfyRestClient, args: Any) -> int:
    try:
        model = client.get_model(args.model_id)
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(model, True)
    print(f"id:          {model.get('id', '')}")
    print(f"object:      {model.get('object', '')}")
    print(f"modality:    {model.get('modality', '')}")
    print(f"tasks:       {', '.join(model.get('tasks') or [])}")
    print(f"capabilities:{' ' + ', '.join(sorted(model.get('capabilities') or []))}")
    print(f"description: {model.get('description', '')}")
    return 0


def cmd_models_all(client: ComfyRestClient, args: Any) -> int:
    try:
        result = client.list_all_models(
            include_hash=args.include_hash,
            include_metadata=args.include_metadata,
            folder=args.folder,
        )
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(result, True)
    models = result.get("models") or []
    print(f"total: {result.get('total', len(models))}")
    print(f"folders: {', '.join(result.get('folders') or [])}")
    for item in models:
        print(f"{item.get('folder', ''):<16} {item.get('path', ''):<60} "
              f"{item.get('size', 0)}")
        if args.include_hash and item.get("hash"):
            print(f"  hash: {item['hash']}")
        if args.include_metadata and item.get("metadata") is not None:
            print(f"  metadata: {json.dumps(item['metadata'], ensure_ascii=False)}")
    return 0


def cmd_model_info(client: ComfyRestClient, args: Any) -> int:
    try:
        info = client.get_model_info(args.path)
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(info, True)
    for key in ("name", "path", "full_path", "size", "hash",
                "created", "modified"):
        if info.get(key) is not None:
            print(f"{key:<12} {info[key]}")
    return 0


def cmd_model_delete(client: ComfyRestClient, args: Any) -> int:
    try:
        result = client.delete_model(args.path, force=args.force)
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(result, True)
    print(f"deleted: {result.get('deleted', True)}")
    print(f"path:    {result.get('path', args.path)}")
    return 0


# ---------------------------------------------------------------------------
# Media content
# ---------------------------------------------------------------------------

def _save_bytes(data: Any, out: str, announce: bool = True) -> int:
    if not isinstance(data, (bytes, bytearray)):
        raise ApiError(0, "server did not return binary content")
    with open(out, "wb") as f:
        f.write(data)
    if announce:
        print(f"saved:     {out}")
        print(f"bytes:     {len(data)}")
    return len(data)


def cmd_video_content(client: ComfyRestClient, args: Any) -> int:
    out = args.out
    if not out:
        ext = ".mp4"
        try:
            task = client.get_video(args.video_id)
            ext = {
                "video/webm": ".webm",
                "video/x-matroska": ".mkv",
            }.get(task.get("media_type"), ext)
        except ApiError:
            pass
        out = f"{args.video_id}{ext}"
    try:
        data = client.get_video_content(args.video_id)
        if args.json:
            size = _save_bytes(data, out, announce=False)
            return _emit({"path": out, "bytes": size}, True)
        _save_bytes(data, out)
        return 0
    except ApiError as e:
        return _emit_error(str(e))
    except OSError as e:
        return _emit_error(f"cannot write {out}: {e}")


def cmd_video_retry(client: ComfyRestClient, args: Any) -> int:
    try:
        task = client.retry_video(args.video_id)
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(task, True)
    print(f"{task.get('id')} {task.get('status')}")
    return 0


def cmd_video_delete(client: ComfyRestClient, args: Any) -> int:
    try:
        result = client.delete_video(args.video_id)
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(result, True)
    print(f"deleted: {result.get('deleted', True)}")
    print(f"id:      {args.video_id}")
    return 0


def cmd_image_content(client: ComfyRestClient, args: Any) -> int:
    out = args.out or f"{args.image_id}_{args.index}.png"
    try:
        data = client.get_image_content(args.image_id, index=args.index)
        if args.json:
            size = _save_bytes(data, out, announce=False)
            return _emit({"path": out, "bytes": size}, True)
        _save_bytes(data, out)
        return 0
    except ApiError as e:
        return _emit_error(str(e))
    except OSError as e:
        return _emit_error(f"cannot write {out}: {e}")


# ---------------------------------------------------------------------------
# Download tasks
# ---------------------------------------------------------------------------

def cmd_downloads_list(client: ComfyRestClient, args: Any) -> int:
    try:
        tasks = client.list_downloads(status=getattr(args, "status", "all"))
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(tasks, True)
    for task in tasks:
        print(f"{task.get('task_id', ''):<36} {task.get('status', ''):<12} "
              f"{task.get('progress', 0):>6} {task.get('url', '')}")
    return 0


def cmd_download_cancel(client: ComfyRestClient, args: Any) -> int:
    try:
        result = client.cancel_download(args.task_id)
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(result, True)
    print(f"cancelled: {result.get('task_id', args.task_id)}")
    print(f"status:    {result.get('status', '')}")
    return 0


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def cmd_capabilities(client: ComfyRestClient, args: Any) -> int:
    try:
        result = client.capabilities(
            model=getattr(args, "model", None),
            capability=getattr(args, "capability", None),
        )
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(result, True)
    summary = result.get("summary") or {}
    print("capabilities: " + ", ".join(f"{k}={v}" for k, v in sorted(summary.items())))
    for item in result.get("capabilities") or []:
        print(f"{item['status']:<24} {item['id']}")
        if item.get("missing_packages"):
            print(f"  missing packages: {item['missing_packages']}")
        if item.get("missing_nodes"):
            print(f"  missing nodes: {item['missing_nodes']}")
        for action in item.get("actions") or []:
            print(f"  action: {action}")
    return 0


def cmd_doctor(client: ComfyRestClient, args: Any) -> int:
    try:
        health = client.health()
        caps = client.capabilities()
    except ApiError as e:
        return _emit_error(str(e))
    provisioners = caps.get("provisioners")
    if not provisioners:
        try:
            provisioners = getattr(client, "provisioners", lambda: {})()
        except ApiError:
            provisioners = {}
    if args.json:
        return _emit(
            {
                "health": health,
                "capabilities": caps,
                "provisioners": provisioners,
            },
            True,
        )
    print(f"health: {health.get('status', '')}")
    for backend in provisioners.get("backends") or []:
        flags = []
        if backend.get("available") is False:
            flags.append("unavailable")
        if backend.get("pinned_only"):
            flags.append("pinned only")
        if backend.get("version"):
            flags.append(str(backend["version"]))
        if backend.get("reason"):
            flags.append(str(backend["reason"]))
        suffix = f" ({', '.join(flags)})" if flags else ""
        print(f"provisioner: {backend.get('id', '')}{suffix}")
    summary = caps.get("summary") or {}
    print("capabilities: " + ", ".join(f"{k}={v}" for k, v in sorted(summary.items())))
    missing = [
        item["id"] for item in caps.get("capabilities") or []
        if item.get("status") not in ("available",)
    ]
    if missing:
        print("needs attention: " + ", ".join(missing))
    return 0


def cmd_provision(client: ComfyRestClient, args: Any) -> int:
    try:
        result = client.provision(
            list(args.capability),
            authorized=bool(getattr(args, "yes", False)),
            retry_task_id=getattr(args, "retry_task", None),
        )
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(result, True)
    print(f"status: {result.get('status', '')}")
    for action in result.get("actions") or []:
        print(f"installed: {action}")
    if result.get("retry_task"):
        print(f"retry: {result['retry_task']['id']} {result['retry_task']['status']}")
    if result.get("restart_required"):
        print("restart required; run wait-reconnect after restart")
    return 0


def _wait_for_health(client: ComfyRestClient, args: Any, since_boot=None) -> int:
    import time
    deadline = time.time() + args.timeout if args.timeout else None
    while True:
        try:
            health = client.health()
            if not since_boot or health.get("boot_id") != since_boot:
                return 0
        except Exception:
            pass
        if deadline is not None and time.time() >= deadline:
            return _emit_error("server did not reconnect before timeout")
        time.sleep(args.interval)


def cmd_restart(client: ComfyRestClient, args: Any) -> int:
    try:
        result = client.restart(
            authorized=bool(getattr(args, "yes", False)),
            retry_task_id=getattr(args, "retry_task", None),
            reason=getattr(args, "reason", None),
        )
    except ApiError as e:
        return _emit_error(str(e))
    old_boot = result.get("boot_id")
    if not old_boot:
        return _emit_error("restart response did not include a boot_id")
    code = _wait_for_health(client, args, since_boot=old_boot)
    if code:
        return code
    if args.retry_task:
        try:
            task = client.retry_video(args.retry_task)
        except ApiError as e:
            return _emit_error(str(e))
        if args.json:
            return _emit(task, True)
        print(f"{task.get('id')} {task.get('status')}")
        return 0
    if args.json:
        return _emit({"status": "restarted", "backend": result.get("backend")}, True)
    print(f"restarted via {result.get('backend', '')}")
    return 0


def cmd_wait_reconnect(client: ComfyRestClient, args: Any) -> int:
    code = _wait_for_health(
        client, args, since_boot=getattr(args, "since_boot", None)
    )
    if code:
        return code
    if args.retry_task:
        try:
            task = client.retry_video(args.retry_task)
        except ApiError as e:
            return _emit_error(str(e))
        if args.json:
            return _emit(task, True)
        print(f"{task.get('id')} {task.get('status')}")
        return 0
    if args.json:
        return _emit({"status": "connected"}, True)
    print("connected")
    return 0


def cmd_health(client: ComfyRestClient, args: Any) -> int:
    try:
        result = client.health()
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(result, True)
    print(f"status:    {result.get('status', '')}")
    print(f"extension: {result.get('extension', '')}")
    return 0


# ---------------------------------------------------------------------------
# Workflow / node dependencies
# ---------------------------------------------------------------------------

def _load_workflow(path: str) -> Dict[str, Any]:
    if path == "-":
        data = json.load(sys.stdin)
    else:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    if isinstance(data, dict) and isinstance(data.get("prompt"), dict):
        data = data["prompt"]
    if not isinstance(data, dict):
        raise ValueError("workflow must be a JSON object")
    return data


def _workflow_arg(args: Any) -> Dict[str, Any]:
    return _load_workflow(args.workflow)


def cmd_deps_install(client: ComfyRestClient, args: Any) -> int:
    try:
        workflow = _workflow_arg(args)
        result = client.install_workflow_deps(
            workflow, async_install=not args.sync
        )
        if args.sync and result.get("task_id"):
            while result.get("status") not in ("completed", "failed", "cancelled"):
                time.sleep(POLL_INTERVAL)
                result = client.get_deps_status(result["task_id"])
    except (OSError, ValueError, json.JSONDecodeError) as e:
        return _emit_error(f"workflow error: {e}")
    except ApiError as e:
        return _emit_error(str(e))
    if args.sync:
        exit_code = 0 if (
            result.get("status") == "completed" and not result.get("failed")
        ) else 1
    else:
        exit_code = 0 if result.get("status") != "failed" else 1
    if args.json:
        _emit(result, True)
        return exit_code
    print(f"task:      {result.get('task_id') or '-'}")
    print(f"status:    {result.get('status', '')}")
    packages = result.get("packages_to_install") or []
    if packages:
        print(f"packages:  {', '.join(packages)}")
    if result.get("failed"):
        print(f"failed:    {', '.join(result['failed'])}")
    return exit_code


def cmd_deps_status(client: ComfyRestClient, args: Any) -> int:
    try:
        result = client.get_deps_status(args.task_id)
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(result, True)
    for key in ("task_id", "status", "progress", "package",
                "restart_required", "pip_output"):
        if result.get(key) is not None:
            print(f"{key:<16} {result[key]}")
    for key in ("installed", "failed"):
        if result.get(key):
            print(f"{key:<16} {', '.join(result[key])}")
    return 0


def cmd_deps_check_workflow(client: ComfyRestClient, args: Any) -> int:
    try:
        workflow = _workflow_arg(args)
        result = client.check_workflow_deps(workflow)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        return _emit_error(f"workflow error: {e}")
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(result, True)
    print(f"can_run:   {result.get('can_run')}")
    print(f"gpu_type:  {result.get('gpu_type', '')}")
    for key in ("missing", "already_satisfied"):
        print(f"{key}: {', '.join(result.get(key) or [])}")
    return 0


def cmd_deps_check_node(client: ComfyRestClient, args: Any) -> int:
    try:
        result = client.check_node_deps(args.node)
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(result, True)
    print(f"node:      {result.get('node', args.node)}")
    print(f"packages:  {', '.join(result.get('required_packages') or [])}")
    return 0


def cmd_deps_restore(client: ComfyRestClient, args: Any) -> int:
    try:
        result = client.restore_node_deps(
            args.node, async_mode=not args.sync
        )
    except ApiError as e:
        return _emit_error(str(e))
    if "returncode" in result:
        if args.json:
            _emit(result, True)
            return 0 if result["returncode"] == 0 else 1
        if result.get("stdout"):
            print(result["stdout"].rstrip())
        if result.get("stderr"):
            print(result["stderr"].rstrip(), file=sys.stderr)
        return 0 if result["returncode"] == 0 else 1
    if args.json:
        return _emit(result, True)
    print(f"task:      {result.get('task_id') or '-'}")
    print(f"status:    {result.get('status', '')}")
    return 0


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

def cmd_snapshot_export(client: ComfyRestClient, args: Any) -> int:
    try:
        result = client.export_snapshot(
            args.snapshot_id,
            format=args.format,
            include_models=args.include_models,
        )
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(result, True)
    print(f"success:   {result.get('success')}")
    print(f"snapshot:  {result.get('snapshot_id', args.snapshot_id)}")
    print(f"path:      {result.get('path', '')}")
    return 0


def cmd_snapshot_import(client: ComfyRestClient, args: Any) -> int:
    try:
        result = client.import_snapshot(
            args.path,
            restore_models=not args.no_restore_models,
            restore_nodes=not args.no_restore_nodes,
        )
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(result, True)
    print(f"success:   {result.get('success')}")
    if result.get("stdout"):
        print(result["stdout"].rstrip())
    return 0


def cmd_snapshot_diff(client: ComfyRestClient, args: Any) -> int:
    try:
        result = client.diff_snapshots(args.snapshot_a, args.snapshot_b)
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(result, True)
    for key in ("custom_nodes", "pip_packages"):
        section = result.get(key) or {}
        print(f"{key}:")
        for change in ("added", "removed", "common"):
            values = section.get(change) or []
            print(f"  {change}: {', '.join(values)}")
    return 0


def cmd_snapshot_list(client: ComfyRestClient, args: Any) -> int:
    try:
        result = client.list_snapshots()
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(result, True)
    for item in result.get("snapshots") or []:
        print(f"{item.get('name', ''):<48} {item.get('size', 0)} "
              f"{item.get('path', '')}")
    return 0


# ---------------------------------------------------------------------------
# Custom nodes
# ---------------------------------------------------------------------------

def cmd_nodes_list(client: ComfyRestClient, args: Any) -> int:
    try:
        result = client.list_nodes()
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(result, True)
    for item in result.get("nodes") or []:
        print(f"{item.get('name', ''):<40} git={item.get('is_git')} "
              f"init={item.get('has_init')} {item.get('path', '')}")
    return 0


def cmd_nodes_pack(client: ComfyRestClient, args: Any) -> int:
    try:
        result = client.pack_node(
            args.node_name, respect_comfyignore=not args.no_comfyignore
        )
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(result, True)
    print(f"path:      {result.get('path', '')}")
    print(f"size:      {result.get('size', 0)}")
    print(f"files:     {result.get('files_included', 0)}")
    return 0


def cmd_nodes_validate(client: ComfyRestClient, args: Any) -> int:
    try:
        result = client.validate_node(args.node_name)
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        _emit(result, True)
        return 0 if result.get("valid") else 1
    print(f"valid:     {result.get('valid')}")
    print(f"checked:   {result.get('files_checked', 0)}")
    for key in ("errors", "warnings"):
        for line in result.get(key) or []:
            print(f"{key[:-1]}: {line}")
    return 0 if result.get("valid") else 1


def cmd_nodes_init(client: ComfyRestClient, args: Any) -> int:
    try:
        result = client.init_node(args.path)
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(result, True)
    print(f"path:      {result.get('path', args.path)}")
    print(f"node:      {result.get('node_name', '')}")
    print(f"files:     {', '.join(result.get('files_created') or [])}")
    return 0


# ---------------------------------------------------------------------------
# Frontend PR cache
# ---------------------------------------------------------------------------

def cmd_pr_cache_list(client: ComfyRestClient, args: Any) -> int:
    try:
        result = client.list_pr_cache()
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(result, True)
    print(f"count:     {result.get('count', 0)}")
    print(f"total:     {result.get('total_size', 0)}")
    for item in result.get("cache") or []:
        print(f"{item.get('name', ''):<48} {item.get('size', 0)}")
    return 0


def cmd_pr_cache_size(client: ComfyRestClient, args: Any) -> int:
    try:
        result = client.get_pr_cache_size()
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(result, True)
    print(f"count:     {result.get('count', 0)}")
    print(f"total:     {result.get('total_size', 0)}")
    return 0


def cmd_pr_cache_delete(client: ComfyRestClient, args: Any) -> int:
    try:
        result = client.delete_pr_cache_item(args.pr)
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(result, True)
    print(f"deleted:   {result.get('deleted')}")
    print(f"name:      {result.get('name', args.pr)}")
    return 0


def cmd_pr_cache_clear(client: ComfyRestClient, args: Any) -> int:
    if not args.confirm:
        return _emit_error("refusing to clear PR cache without --confirm")
    try:
        result = client.clear_pr_cache(confirm=True)
    except ApiError as e:
        return _emit_error(str(e))
    errors = result.get("errors") or []
    if args.json:
        _emit(result, True)
        return 1 if errors else 0
    for err in errors:
        print(f"error:     {err.get('item', '')}: {err.get('error', '')}",
              file=sys.stderr)
    print(f"cleared:   {result.get('cleared')}")
    print(f"removed:   {result.get('entries_removed', 0)}")
    return 1 if errors else 0


# ---------------------------------------------------------------------------
# Persistent config
# ---------------------------------------------------------------------------

def _config_or_error():
    from . import config
    try:
        return config, config.load_config()
    except config.ConfigError as e:
        raise ApiError(2, str(e)) from e


def cmd_config_list(client: ComfyRestClient, args: Any) -> int:
    config, data = _config_or_error()
    output = config.redact(data)
    if args.json:
        return _emit(output, True)
    print(f"default profile: {output['default_profile']}")
    for name, values in output["profiles"].items():
        details = ", ".join(f"{k}={v}" for k, v in sorted(values.items()) if v != "")
        print(f"{name}" + (f"  ({details})" if details else ""))
    if not output["profiles"]:
        print("(no profiles configured)")
    return 0


def cmd_config_use(client: ComfyRestClient, args: Any) -> int:
    config, data = _config_or_error()
    if args.profile not in (data.get("profiles") or {}):
        return _emit_error(f"profile not found: {args.profile}")
    data["default_profile"] = args.profile
    path = config.save_config(data)
    if args.json:
        return _emit({"default_profile": args.profile, "path": str(path)}, True)
    print(f"default profile: {args.profile}")
    return 0


def cmd_config_set(client: ComfyRestClient, args: Any) -> int:
    config, data = _config_or_error()
    key = args.key
    if key not in config._FIELDS:
        return _emit_error(f"unknown config key: {key}")
    value = args.value
    if key == "api_key" and value == "-":
        value = sys.stdin.readline().rstrip("\n")
    if key == "timeout":
        try:
            value = float(value)
        except ValueError:
            return _emit_error("timeout must be a number")
    profiles = data.setdefault("profiles", {})
    profile = profiles.setdefault(args.profile, {})
    profile[key] = value
    path = config.save_config(data)
    if args.json:
        return _emit({"profile": args.profile, "key": key, "path": str(path)}, True)
    print(f"set {key} for profile {args.profile}")
    return 0


def cmd_config_get(client: ComfyRestClient, args: Any) -> int:
    config, data = _config_or_error()
    values = (data.get("profiles") or {}).get(args.profile)
    if values is None:
        return _emit_error(f"profile not found: {args.profile}")
    if args.key:
        if args.key not in values:
            return _emit_error(f"key not set: {args.key}")
        value = "***" if args.key == "api_key" and values[args.key] else values[args.key]
        if args.json:
            return _emit({"profile": args.profile, "key": args.key, "value": value}, True)
        print(value)
        return 0
    output = config.redact({"default_profile": args.profile, "profiles": {args.profile: values}})
    if args.json:
        return _emit(output["profiles"][args.profile], True)
    for key, value in sorted(output["profiles"][args.profile].items()):
        print(f"{key}={value}")
    return 0


def cmd_config_path(client: ComfyRestClient, args: Any) -> int:
    from . import config
    path = config.config_path()
    if args.json:
        return _emit({"path": str(path)}, True)
    print(path)
    return 0
