"""
Command implementations for the comfy-rest-ext CLI.

Each command takes (client, args) and returns an exit code. Human-readable
output goes to stdout; --json switches every command to raw JSON output.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Dict, Optional, Sequence

from .client import ApiError, ComfyRestClient

POLL_INTERVAL = 5.0


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
    return payload


def cmd_generate(client: ComfyRestClient, args: Any) -> int:
    payload = _build_video_payload(args)
    try:
        if args.image:
            with open(args.image, "rb") as f:
                file_bytes = f.read()
            task = client.create_video_multipart(
                payload,
                file_field="first_frame",
                filename=args.image,
                file_bytes=file_bytes,
            )
        else:
            task = client.create_video(payload)
    except FileNotFoundError:
        return _emit_error(f"image file not found: {args.image}")
    except ApiError as e:
        return _emit_error(str(e))
    if args.json:
        return _emit(task, True)
    print(f"task {task['id']} queued (status: {task['status']})")
    print(f"view later: {task['id']}")
    return 0


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
    deadline = time.time() + (args.timeout or 600)
    while True:
        try:
            task = client.get_video(args.video_id)
        except ApiError as e:
            return _emit_error(str(e))
        status = task["status"]
        if not args.json and not args.quiet:
            print(f"[{task['id']}] {status} "
                  f"{task.get('progress', 0)}%", file=sys.stderr)
        if status in ("completed", "failed", "cancelled"):
            exit_code = 0 if status == "completed" else 1
            if args.json:
                _emit(task, True)
                return exit_code
            print(f"status:    {status}")
            if task.get("view_url"):
                print(f"view_url:  {task['view_url']}")
            return exit_code
        if time.time() >= deadline:
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
