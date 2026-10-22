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

POLL_INTERVAL = 5.0


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


def _emit_error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


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


def _build_video_payload(args: Any) -> Dict[str, Any]:
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

    # Speed: legacy string XOR composable per-layer options. Passing both
    # is ambiguous — fail loudly instead of silently preferring one.
    layer_opts = {
        "turbo": getattr(args, "turbo", None),
        "block_cache": getattr(args, "block_cache", None),
        "step_cache": getattr(args, "step_cache", None),
        "attention": getattr(args, "attention", None),
        "vae_decode": getattr(args, "vae_decode", None),
    }
    steps = getattr(args, "steps", None)
    legacy_speed = getattr(args, "speed", None)
    active_layers = {k: v for k, v in layer_opts.items() if v not in (None, "none", "default")}
    if legacy_speed and legacy_speed != "auto" and (active_layers or steps):
        raise ApiError(
            400,
            "--speed conflicts with --steps/--turbo/--block-cache/--step-cache/"
            "--attention/--vae-decode; use one or the other",
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
        if getattr(args, "multipart", False):
            payload = _build_video_payload(args)
            if isinstance(payload.get("speed"), dict):
                return _emit_error(
                    "--multipart supports legacy --speed strings only; "
                    "omit --steps/--turbo/--block-cache/--step-cache/"
                    "--attention/--vae-decode or use JSON mode"
                )
            files = []
            for path in getattr(args, "image", None) or []:
                with open(path, "rb") as f:
                    files.append(
                        ("input_reference", os.path.basename(path), f.read())
                    )
            task = client.create_video_multipart(payload, files)
        else:
            task = client.create_video(_build_video_request(args))
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


def _build_video_request(args: Any) -> Dict[str, Any]:
    """Build a content[]/input[] video request (industry-standard format).

    Text goes into an input_text item; uploaded images become input_image
    items. A single image is a first_frame (I2V); multiple images are
    reference_image items (R2V) referenced via <Picture N> tags.
    """
    payload = _build_video_payload(args)
    items: list = [{"type": "input_text", "text": args.prompt}]
    images = getattr(args, "image", None) or []
    if isinstance(images, str):
        images = [images]
    if images:
        model = getattr(args, "model", None)
        if len(images) > 1 or (model and model.endswith("-r2v")):
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
    out = args.out or f"{args.video_id}.mp4"
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
