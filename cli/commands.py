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
        print(f"{m['id']:<24} {m.get('owned_by', '')}")
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
    if getattr(args, "speed", None):
        payload["speed"] = args.speed
    return payload


def cmd_generate(client: ComfyRestClient, args: Any) -> int:
    try:
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
