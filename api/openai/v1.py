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
    _record_to_task,
    comfyui_view_url,
)
from ..tasks.video_persistence import get_video_persistence

logger = logging.getLogger("comfy-rest-ext.openai")

routes = PromptServer.instance.routes

CANONICAL_MODEL_ID = "minimax-h3"
MODEL_IDS = [
    CANONICAL_MODEL_ID,
    "minimax-h3-t2v",
    "minimax-h3-i2v",
    "minimax-h3-r2v",
]
# Legacy aliases -> task_type; the canonical id maps to None (infer).
MODEL_TASK_ALIASES = {
    "minimax-h3-t2v": "t2va",
    "minimax-h3-i2v": "fl2va",
    "minimax-h3-r2v": "r2v",
}
SUPPORTED_TASKS = ("t2va", "fl2va", "r2v")


def _model_card(model_id: str, created: int) -> dict:
    task_type = MODEL_TASK_ALIASES.get(model_id)
    return {
        "id": model_id,
        "object": "model",
        "created": created,
        "owned_by": "comfy-rest-ext",
        "task_type": task_type or "auto",
    }


def _task_type_from_model(model: Optional[str]) -> Optional[str]:
    """Map a requested model id to a task type.

    Returns the task type for legacy aliases (minimax-h3-i2v -> fl2va),
    None for the canonical id or no model (caller infers from request
    content), and raises KeyError for unknown ids (-> 400 mismatch).
    """
    if not model:
        return None
    if model == CANONICAL_MODEL_ID:
        return None
    if model in MODEL_TASK_ALIASES:
        return MODEL_TASK_ALIASES[model]
    raise KeyError(model)


def _infer_task_type(fields: dict) -> str:
    """Infer task type from request content (image presence/count)."""
    images = fields.get("_ref_images_data") or fields.get("ref_images") or []
    if len(images) > 1:
        return "r2v"
    if (images or fields.get("_first_frame_data") or fields.get("input_reference")
            or fields.get("first_frame_url")):
        return "fl2va"
    return "t2va"


def _video_response(task: VideoTask) -> dict:
    """OpenAI VideoResource-shaped job record."""
    status = task.status
    if status == "running":
        status = "in_progress"
    inference_time_s = None
    if task.completed_at and task.created_at:
        inference_time_s = round(task.completed_at - task.created_at, 2)
    elapsed_s = None
    if task.started_at:
        base = task.completed_at or time.time()
        elapsed_s = round(base - task.started_at, 2)
    return {
        "id": task.task_id,
        "object": "video",
        "model": _model_for_task(task.task_type),
        "prompt": task.prompt,
        "status": status,
        "progress": int(task.progress * 100),
        "current_node": task.current_node,
        "node_progress": int(task.node_progress * 100),
        "elapsed": elapsed_s,
        "eta": round(task.eta, 1) if task.eta is not None else None,
        "size": f"{task.width}x{task.height}",
        "seconds": str(task.length / 24),
        "created_at": int(task.created_at),
        "completed_at": int(task.completed_at) if task.completed_at else None,
        "media_type": "video/mp4",
        "file_name": task.output_path,
        "view_url": comfyui_view_url(task.output_path),
        "inference_time_s": inference_time_s,
        "error": (
            {"code": "generation_error", "message": task.error}
            if task.error else None
        ),
    }


def _model_for_task(task_type: str) -> str:
    return CANONICAL_MODEL_ID


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

    content_items = None
    if not ctype.startswith("multipart/") and (fields.get("input") or fields.get("content")):
        content_items = _parse_content_items(fields)
        if content_items["prompt"]:
            fields["prompt"] = content_items["prompt"]
        if content_items["ref_image_urls"]:
            fields["ref_images"] = content_items["ref_image_urls"]
        if content_items["first_frame_url"]:
            fields["first_frame_url"] = content_items["first_frame_url"]
        if content_items["last_frame_url"]:
            fields["last_frame_url"] = content_items["last_frame_url"]

    prompt = fields.get("prompt")
    if not prompt:
        return web.json_response({"error": "prompt is required"}, status=400)

    model = fields.get("model")
    try:
        model_task_type = _task_type_from_model(model)
    except KeyError:
        return web.json_response(
            {"error": f"Model mismatch: '{model}' is not served. "
                      f"Use '{CANONICAL_MODEL_ID}'."},
            status=400,
        )

    if fields.get("task"):
        task_type = fields["task"]
    elif model_task_type is not None:
        task_type = model_task_type
    else:
        task_type = _infer_task_type(fields)
    if task_type not in SUPPORTED_TASKS:
        return web.json_response({"error": f"unsupported task: {task_type}"}, status=400)

    speed = fields.get("speed", "auto")
    try:
        from ..accel import check_config_available, parse_accel_config
        accel_cfg = parse_accel_config(speed)
    except ValueError as e:
        return web.json_response({"error": f"invalid speed: {e}"}, status=400)
    missing_node = check_config_available(accel_cfg)
    if missing_node:
        return web.json_response({"error": f"invalid speed: {missing_node}"}, status=400)

    width = _parse_int(fields.get("width"), 1344)
    height = _parse_int(fields.get("height"), 768)
    seconds = _parse_float(fields.get("seconds"), 5.0)
    seed = _parse_int(fields.get("seed"), 0)

    first_frame = None
    ref_images = None
    if task_type == "r2v":
        if fields.get("_ref_images_data"):
            ref_images = [
                await _save_upload(data, "ref_image") for data in fields["_ref_images_data"]
            ]
        elif fields.get("ref_images"):
            ref_images = [
                await _resolve_ref_image(ref) for ref in fields["ref_images"]
            ]
        if not ref_images:
            return web.json_response(
                {"error": "r2v requires at least one reference image"}, status=400
            )
    else:
        if fields.get("_first_frame_data"):
            first_frame = await _upload_first_frame(fields["_first_frame_data"])
        elif fields.get("first_frame_url"):
            first_frame = await _resolve_ref_image(fields["first_frame_url"])
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
        ref_images=ref_images,
        speed=speed,
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


def _parse_content_items(body: dict) -> dict:
    """Extract prompt text and role-tagged image refs from input[]/content[].

    Accepts the industry-standard media array (OpenAI Responses input items,
    MiniMax V2 content[]): items with type=input_text contribute prompt text;
    items with type=input_image carry image_url plus an optional role
    (reference_image | first_frame | last_frame).

    Returns a normalized dict with keys: prompt, ref_image_urls (list),
    first_frame_url (str|None), last_frame_url (str|None).
    """
    items = body.get("input") or body.get("content") or []
    if not isinstance(items, list):
        return {"prompt": None, "ref_image_urls": [], "first_frame_url": None,
                "last_frame_url": None}

    texts: list = []
    ref_urls: list = []
    first_frame_url = None
    last_frame_url = None
    for item in items:
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype in ("input_text", "text"):
            t = item.get("text")
            if t:
                texts.append(t)
        elif itype in ("input_image", "image_url"):
            url = item.get("image_url")
            if isinstance(url, dict):
                url = url.get("url")
            if not url:
                continue
            role = item.get("role", "reference_image")
            if role == "first_frame":
                first_frame_url = url
            elif role == "last_frame":
                last_frame_url = url
            else:
                ref_urls.append(url)
    return {
        "prompt": "\n".join(texts) if texts else None,
        "ref_image_urls": ref_urls,
        "first_frame_url": first_frame_url,
        "last_frame_url": last_frame_url,
    }


async def _parse_multipart(request: web.Request):
    """Parse a multipart/form-data video request (OpenAI SDK shape).

    Image parts are collected into `_ref_images_data` (ordered); for
    backward compatibility the first image is also stored in
    `_first_frame_data`.
    """
    reader = MultipartReader.from_response(request)
    fields: dict = {}
    ref_images_data: list = []
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.headers.get("Content-Type", "").startswith(("image/", "video/")):
            data = await part.read()
            ref_images_data.append(data)
            if not fields.get("_first_frame_data"):
                fields["_first_frame_data"] = data
        else:
            name = part.name
            value = (await part.read()).decode("utf-8", errors="replace")
            fields[name] = value
    if ref_images_data:
        fields["_ref_images_data"] = ref_images_data
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
    from ..input_assets import save_input_asset
    return save_input_asset(data, prefix)


async def _resolve_ref_image(ref: str) -> Optional[str]:
    """Resolve a JSON r2v reference to a ComfyUI input filename.

    Accepts an existing input filename, a data:image/... URL, or an
    http(s) URL (downloaded and saved to the input dir).
    """
    if ref.startswith("data:image/"):
        return await _upload_input_reference(ref)
    if ref.startswith("http://") or ref.startswith("https://"):
        return await _upload_input_reference(ref)
    return ref


def _get_task_or_restore(video_id: str) -> Optional[VideoTask]:
    """Get a task from memory, restoring from persistence if needed."""
    manager = get_video_manager()
    task = manager.get(video_id)
    if task is not None:
        return task
    record = get_video_persistence().get(video_id)
    if record is None:
        return None
    task = _record_to_task(record)
    manager.restore(task)
    return task


@routes.get("/v1/videos/{video_id}")
async def retrieve_video(request: web.Request) -> web.Response:
    """Retrieve the job record for a video generation task."""
    video_id = request.match_info["video_id"]
    task = _get_task_or_restore(video_id)
    if not task:
        return web.json_response({"error": "Video not found"}, status=404)
    return web.json_response(_video_response(task))


@routes.get("/v1/videos")
async def list_videos(request: web.Request) -> web.Response:
    """List video generation jobs (active + history)."""
    tasks = [
        _video_response(t)
        for t in get_video_manager().list_all().values()
    ]
    history = get_video_persistence().list_active()
    seen = {t.task_id for t in get_video_manager().list_all().values()}
    for record in history.values():
        if record.get("task_id") in seen:
            continue
        task = _record_to_task(record)
        get_video_manager().restore(task)
        tasks.append(_video_response(task))
    return web.json_response({
        "object": "list",
        "data": tasks,
    })


@routes.get("/v1/videos/{video_id}/content")
async def download_video_content(request: web.Request) -> web.Response:
    """Download the generated MP4 for a completed job."""
    video_id = request.match_info["video_id"]
    task = _get_task_or_restore(video_id)
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
    task = _get_task_or_restore(video_id)
    if not task:
        return web.json_response({"error": "Video not found"}, status=404)
    return web.json_response({
        "id": video_id,
        "deleted": True,
        "object": "video.deleted",
    })
