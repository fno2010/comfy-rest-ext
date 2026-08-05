"""
OpenAI-compatible API layer (/v1/*) for MiniMax-H3 video generation.

Exposes GET /v1/models, POST /v1/videos (JSON + multipart),
GET /v1/videos/{id}, GET /v1/videos/{id}/content following the
OpenAI Videos API job lifecycle so agents using the official SDK
(custom base_url) can call it unmodified.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Optional

from aiohttp import web, MultipartReader
from server import PromptServer

from ..schemas.requests import VideoGenerateRequest
from ..tasks.video_task import (
    VideoTask,
    frames_for_seconds,
    get_video_manager,
    submit_video_task,
)

logger = logging.getLogger("comfy-rest-ext.openai")

routes = PromptServer.instance.routes

MODEL_IDS = [
    "minimax-h3-t2v",
    "minimax-h3-i2v",
]
SUPPORTED_TASKS = ("t2va", "fl2va")


def _model_card(model_id: str, created: int) -> dict:
    return {
        "id": model_id,
        "object": "model",
        "created": created,
        "owned_by": "comfy-rest-ext",
    }


def _video_response(task: VideoTask) -> dict:
    """OpenAI VideoResource-shaped job record."""
    status = task.status
    if status == "running":
        status = "in_progress"
    return {
        "id": task.task_id,
        "object": "video",
        "model": _model_for_task(task.task_type),
        "prompt": task.prompt,
        "status": status,
        "progress": int(task.progress * 100),
        "size": f"{task.width}x{task.height}",
        "seconds": str(task.length / 24),
        "created_at": int(task.created_at),
        "completed_at": int(task.completed_at) if task.completed_at else None,
        "media_type": "video/mp4",
        "file_name": task.output_path,
        "error": (
            {"code": "generation_error", "message": task.error}
            if task.error else None
        ),
    }


def _model_for_task(task_type: str) -> str:
    return "minimax-h3-i2v" if task_type == "fl2va" else "minimax-h3-t2v"


def _task_type_from_model(model: Optional[str]) -> str:
    if model and model.endswith("-i2v"):
        return "fl2va"
    return "t2va"


@routes.get("/v1/models")
async def show_available_models(request: web.Request) -> web.Response:
    """List available models (OpenAI shape)."""
    created = int(time.time())
    return web.json_response({
        "object": "list",
        "data": [_model_card(m, created) for m in MODEL_IDS],
    })


@routes.get("/v1/models/{model_id}")
async def retrieve_model(request: web.Request) -> web.Response:
    model_id = request.match_info["model_id"]
    if model_id not in MODEL_IDS:
        return web.json_response({"error": "Model not found"}, status=404)
    return web.json_response(_model_card(model_id, int(time.time())))


@routes.post("/v1/videos")
async def create_video(request: web.Request) -> web.Response:
    """Create an async video generation job (OpenAI Videos API)."""
    ctype = request.headers.get("Content-Type", "")
    if "multipart/form-data" in ctype:
        fields = await _parse_multipart(request)
    else:
        fields = await _parse_json(request)
    if isinstance(fields, web.Response):
        return fields

    prompt = fields.get("prompt")
    if not prompt:
        return web.json_response({"error": "prompt is required"}, status=400)

    model = fields.get("model")
    task_type = _task_type_from_model(model)
    if fields.get("task"):
        task_type = fields["task"]

    width = _parse_int(fields.get("width"), 1344)
    height = _parse_int(fields.get("height"), 768)
    seconds = _parse_float(fields.get("seconds"), 5.0)
    seed = _parse_int(fields.get("seed"), 0)

    first_frame = None
    if fields.get("_first_frame_data"):
        first_frame = await _upload_first_frame(fields["_first_frame_data"])
    elif fields.get("input_reference"):
        first_frame = await _upload_input_reference(fields["input_reference"])
    if task_type == "fl2va" and first_frame is None:
        return web.json_response(
            {"error": "fl2va requires input_reference image"}, status=400
        )

    task_id = f"video_{uuid.uuid4().hex}"
    length = frames_for_seconds(seconds)

    task = VideoTask(
        task_id=task_id,
        status="queued",
        prompt=prompt,
        task_type=task_type,
        width=width,
        height=height,
        length=length,
        seed=seed,
        first_frame=first_frame,
        created_at=time.time(),
    )
    get_video_manager().create(task)
    asyncio.create_task(submit_video_task(task))

    return web.json_response(_video_response(task))


async def _parse_json(request: web.Request):
    """Parse a JSON-body video request, accepting OpenAI + extension fields."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    return body


async def _parse_multipart(request: web.Request):
    """Parse a multipart/form-data video request (OpenAI SDK shape)."""
    reader = MultipartReader.from_response(request)
    fields: dict = {}
    first_frame_data: Optional[bytes] = None
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.headers.get("Content-Type", "").startswith(("image/", "video/")):
            first_frame_data = await part.read()
            fields["_first_frame_data"] = first_frame_data
        else:
            name = part.name
            value = (await part.read()).decode("utf-8", errors="replace")
            fields[name] = value
    return fields


def _parse_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def _upload_first_frame(data: bytes) -> Optional[str]:
    """Upload an in-memory image to ComfyUI's input dir; return its filename."""
    return await _save_upload(data, "first_frame")


async def _upload_input_reference(ref: str) -> Optional[str]:
    """Resolve an input_reference (file_id | image_url | data URL) to a filename."""
    import base64
    if ref.startswith("data:image/"):
        _, b64 = ref.split(",", 1)
        return await _save_upload(base64.b64decode(b64), "input_reference")
    if ref.startswith("http://") or ref.startswith("https://"):
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(ref)
            resp.raise_for_status()
            return await _save_upload(resp.content, "input_reference")
    return None


async def _save_upload(data: bytes, prefix: str) -> Optional[str]:
    """Write image bytes into ComfyUI input dir, returning the filename."""
    import folder_paths
    input_dir = folder_paths.get_input_directory()
    os.makedirs(input_dir, exist_ok=True)
    filename = f"{prefix}_{uuid.uuid4().hex[:8]}.png"
    with open(os.path.join(input_dir, filename), "wb") as f:
        f.write(data)
    return filename


@routes.get("/v1/videos/{video_id}")
async def retrieve_video(request: web.Request) -> web.Response:
    """Retrieve the job record for a video generation task."""
    video_id = request.match_info["video_id"]
    task = get_video_manager().get(video_id)
    if not task:
        return web.json_response({"error": "Video not found"}, status=404)
    return web.json_response(_video_response(task))


@routes.get("/v1/videos")
async def list_videos(request: web.Request) -> web.Response:
    """List video generation jobs."""
    tasks = [
        _video_response(t)
        for t in get_video_manager().list_active().values()
    ]
    return web.json_response({
        "object": "list",
        "data": tasks,
    })


@routes.get("/v1/videos/{video_id}/content")
async def download_video_content(request: web.Request) -> web.Response:
    """Download the generated MP4 for a completed job."""
    video_id = request.match_info["video_id"]
    task = get_video_manager().get(video_id)
    if not task:
        return web.json_response({"error": "Video not found"}, status=404)
    if task.status != "completed" or not task.output_path:
        return web.json_response(
            {"error": "Video generation still in progress"},
            status=409,
        )
    return web.FileResponse(task.output_path)


@routes.delete("/v1/videos/{video_id}")
async def delete_video(request: web.Request) -> web.Response:
    """Delete a video generation job record."""
    video_id = request.match_info["video_id"]
    task = get_video_manager().get(video_id)
    if not task:
        return web.json_response({"error": "Video not found"}, status=404)
    return web.json_response({
        "id": video_id,
        "deleted": True,
        "object": "video.deleted",
    })
