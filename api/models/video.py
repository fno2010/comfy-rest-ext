"""
MiniMax-H3 video generation endpoints (/v2/extension/video/*).

Submits T2V/I2V generation tasks to the in-process ComfyUI queue,
tracks progress, and exposes the output video for download.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Optional

from aiohttp import web
from server import PromptServer

from ..schemas.requests import VideoGenerateRequest
from ..tasks.video_task import (
    VideoTask,
    frames_for_seconds,
    get_video_manager,
    submit_video_task,
    _record_to_task,
    comfyui_view_url,
)
from ..tasks.video_persistence import get_video_persistence


def _task_summary(task: VideoTask) -> dict:
    return {
        "task_id": task.task_id,
        "status": task.status,
        "task": task.task_type,
        "progress": task.progress,
        "created_at": task.created_at,
        "completed_at": task.completed_at,
        "view_url": comfyui_view_url(task.output_path),
    }

logger = logging.getLogger("comfy-rest-ext.video")

routes = PromptServer.instance.routes


async def _emit_websocket_event(event: str, data: dict) -> None:
    ws = PromptServer.instance
    if hasattr(ws, "broadcast"):
        await ws.broadcast({"event": event, "data": data})


@routes.post("/v2/extension/video/generate")
async def create_video_task(request: web.Request) -> web.Response:
    """Submit a MiniMax-H3 video generation task (T2V or I2V)."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    try:
        req = VideoGenerateRequest(**body)
    except Exception as e:
        return web.json_response({"error": f"Invalid request: {e}"}, status=400)

    task_id = str(uuid.uuid4())
    length = frames_for_seconds(req.duration)

    first_frame = None
    if req.first_frame:
        first_frame = await _resolve_first_frame(req.first_frame)

    task = VideoTask(
        task_id=task_id,
        status="queued",
        prompt=req.prompt,
        task_type=req.task,
        width=req.width,
        height=req.height,
        length=length,
        seed=req.seed,
        first_frame=first_frame,
        created_at=time.time(),
    )
    get_video_manager().create(task)

    asyncio.create_task(_run_video_task(task))

    await _emit_websocket_event(
        "extension-video-generation-queued",
        {"task_id": task_id, "task": req.task},
    )

    return web.json_response({
        "task_id": task_id,
        "status": "queued",
        "task": req.task,
        "prompt": req.prompt,
        "width": req.width,
        "height": req.height,
        "duration": req.duration,
        "length": length,
        "first_frame": first_frame,
    })


async def _resolve_first_frame(ref: str) -> Optional[str]:
    """Resolve a first_frame reference to a ComfyUI input filename.

    Accepts an existing input filename or a data:image/... URL.
    """
    import base64
    if ref.startswith("data:image/"):
        _, b64 = ref.split(",", 1)
        import folder_paths
        input_dir = folder_paths.get_input_directory()
        os.makedirs(input_dir, exist_ok=True)
        filename = f"first_frame_{uuid.uuid4().hex[:8]}.png"
        with open(os.path.join(input_dir, filename), "wb") as f:
            f.write(base64.b64decode(b64))
        return filename
    return ref if ref and not ref.startswith("http") else None


async def _run_video_task(task: VideoTask) -> None:
    manager = get_video_manager()
    try:
        await submit_video_task(task)
        if task.status == "completed":
            await _emit_websocket_event(
                "extension-video-generation-complete",
                {"task_id": task.task_id, "path": task.output_path},
            )
        elif task.status == "failed":
            await _emit_websocket_event(
                "extension-video-generation-failed",
                {"task_id": task.task_id, "error": task.error},
            )
    except Exception as e:
        manager.update(task.task_id, status="failed", error=str(e))
        await _emit_websocket_event(
            "extension-video-generation-failed",
            {"task_id": task.task_id, "error": str(e)},
        )


@routes.get("/v2/extension/video/{task_id}")
async def get_video_task_status(request: web.Request) -> web.Response:
    """Get the status of a video generation task."""
    task_id = request.match_info["task_id"]
    task = get_video_manager().get(task_id)
    if not task:
        record = get_video_persistence().get(task_id)
        if record is None:
            return web.json_response({"error": "Task not found"}, status=404)
        task = _record_to_task(record)
        get_video_manager().restore(task)

    return web.json_response({
        "task_id": task.task_id,
        "status": task.status,
        "task": task.task_type,
        "progress": task.progress,
        "prompt_id": task.prompt_id,
        "output_path": task.output_path,
        "view_url": _comfyui_view_url(task.output_path),
        "error": task.error,
        "created_at": task.created_at,
        "completed_at": task.completed_at,
    })


@routes.get("/v2/extension/video")
async def list_video_tasks(request: web.Request) -> web.Response:
    """List video generation tasks (active + history)."""
    manager = get_video_manager()
    seen = set()
    tasks = []
    for t in manager.list_all().values():
        seen.add(t.task_id)
        tasks.append(_task_summary(t))
    for record in get_video_persistence().list_active().values():
        if record.get("task_id") in seen:
            continue
        task = _record_to_task(record)
        manager.restore(task)
        tasks.append(_task_summary(task))
    return web.json_response({"tasks": tasks})
