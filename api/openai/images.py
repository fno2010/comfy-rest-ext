"""OpenAI-compatible image generation endpoints.

POST /v1/images/generations is synchronous, matching the OpenAI Images
API. Long jobs can also be submitted to /v2/extension/image/generate,
which returns a job record and can be polled.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiohttp import web, MultipartReader
from server import PromptServer

from ..catalog import ModelSpec, get_model
from ..tasks.video_task import (
    GenerationTask,
    _record_to_task,
    comfyui_view_url,
    get_video_manager,
    submit_generation_task,
)
from ..tasks.video_persistence import get_video_persistence
from .v1 import _parse_content_items, _resolve_ref_image

logger = logging.getLogger("comfy-rest-ext.images")

routes = PromptServer.instance.routes


class BadRequest(Exception):
    def __init__(self, message: str, param: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.param = param


def _error(status: int, message: str, param: Optional[str] = None,
           err_type: str = "invalid_request_error") -> web.Response:
    return web.json_response({
        "error": {
            "message": message,
            "type": err_type,
            "param": param,
            "code": None,
        }
    }, status=status)


def _int_field(body: Dict[str, Any], key: str, default: int) -> int:
    if body.get(key) is None:
        return default
    try:
        return int(body[key])
    except (TypeError, ValueError):
        raise BadRequest(f"{key} must be an integer", key) from None


def _float_field(body: Dict[str, Any], key: str, default: float) -> float:
    if body.get(key) is None:
        return default
    try:
        return float(body[key])
    except (TypeError, ValueError):
        raise BadRequest(f"{key} must be a number", key) from None


def _size_of(task: GenerationTask) -> str:
    return f"{task.width}x{task.height}"


def _image_task_summary(task: GenerationTask) -> dict:
    return {
        "id": task.task_id,
        "object": "image.generation",
        "model": task.model_id,
        "prompt": task.prompt,
        "status": task.status,
        "progress": int(task.progress * 100),
        "size": _size_of(task),
        "n": int((task.params or {}).get("n", 1)),
        "current_node": task.current_node,
        "node_progress": int(task.node_progress * 100),
        "eta": round(task.eta, 1) if task.eta is not None else None,
        "created_at": int(task.created_at),
        "completed_at": int(task.completed_at) if task.completed_at else None,
        "view_urls": [comfyui_view_url(p) for p in task.output_paths],
        "error": (
            {"code": "generation_error", "message": task.error}
            if task.error else None
        ),
    }


def _normalize(spec: ModelSpec, body: Dict[str, Any], *, edit: bool) -> dict:
    prompt = body.get("prompt")
    if not prompt or not isinstance(prompt, str):
        raise BadRequest("prompt is required", "prompt")

    if edit and "edit" not in spec.tasks:
        raise BadRequest(
            f"model {spec.id!r} does not support image edits", "model"
        )
    if not edit and "t2i" not in spec.tasks and "edit" not in spec.tasks:
        raise BadRequest(f"model {spec.id!r} does not support image generation", "model")

    n = _int_field(body, "n", 1)
    if n < 1 or n > spec.max_n:
        raise BadRequest(
            f"n must be between 1 and {spec.max_n} for {spec.id}", "n"
        )

    if spec.size is None:
        raise BadRequest(f"model {spec.id!r} has no size policy", "size")
    try:
        width, height = spec.size.normalize(
            width=body.get("width"),
            height=body.get("height"),
            size=body.get("size"),
            aspect_ratio=body.get("aspect_ratio"),
            megapixels=body.get("megapixels"),
        )
    except ValueError as e:
        raise BadRequest(str(e), "size") from e

    response_format = body.get("response_format") or "b64_json"
    if response_format not in ("b64_json", "url"):
        raise BadRequest(
            "response_format must be 'b64_json' or 'url'", "response_format"
        )

    output_format = body.get("output_format")
    if output_format and output_format != "png":
        raise BadRequest(
            "only output_format='png' is supported (alpha channel)", "output_format"
        )
    for unsupported in ("stream", "partial_images"):
        if body.get(unsupported):
            raise BadRequest(
                f"{unsupported} is not supported", unsupported
            )

    quality = body.get("quality")
    if quality not in (None, "auto", "standard", "high", "hd", "medium", "low"):
        raise BadRequest("unsupported quality", "quality")

    steps = _int_field(body, "steps", spec.defaults["steps"])
    if steps <= 0:
        raise BadRequest("steps must be a positive integer", "steps")

    return {
        "prompt": prompt,
        "n": n,
        "width": width,
        "height": height,
        "seed": _int_field(body, "seed", 0),
        "steps": steps,
        "cfg": _float_field(body, "cfg", spec.defaults.get("cfg", 1.0)),
        "sampler": body.get("sampler") or spec.defaults.get("sampler", "euler"),
        "scheduler": body.get("scheduler") or spec.defaults.get("scheduler", "simple"),
        "negative_prompt": body.get("negative_prompt", spec.defaults.get("negative_prompt")),
        "resolution": _int_field(
            body, "resolution", spec.defaults.get("resolution", 1024)
        ),
        "response_format": response_format,
        "quality": quality,
        "style": body.get("style"),
    }


async def _resolve_refs(body: Dict[str, Any]) -> List[str]:
    """Resolve JSON image references to ComfyUI input filenames."""
    refs: List[Any] = []
    raw = body.get("ref_images")
    if raw:
        refs.extend(raw if isinstance(raw, list) else [raw])
    image = body.get("image")
    if image:
        refs.extend(image if isinstance(image, list) else [image])
    if body.get("input") or body.get("content"):
        parsed = _parse_content_items(body)
        refs.extend(parsed.get("ref_image_urls") or [])
        for key in ("first_frame_url", "last_frame_url"):
            if parsed.get(key):
                refs.append(parsed[key])

    resolved: List[str] = []
    for ref in refs:
        if not isinstance(ref, str) or not ref:
            continue
        name = await _resolve_ref_image(ref)
        if name:
            resolved.append(name)
    return resolved


async def _parse_multipart(request: web.Request) -> tuple[Dict[str, Any], List[str]]:
    """Parse an OpenAI Images edit request.

    Image parts are saved into ComfyUI's input directory and returned as
    filenames; text fields are decoded into the request dict.
    """
    reader = MultipartReader.from_response(request)
    fields: Dict[str, Any] = {}
    refs: List[str] = []
    while True:
        part = await reader.next()
        if part is None:
            break
        ctype = part.headers.get("Content-Type", "")
        name = part.name or ""
        data = await part.read()
        if ctype.startswith("image/") or name.startswith("image"):
            if name == "mask":
                raise BadRequest(
                    "mask is not supported for Qwen-Image edits; "
                    "paint the region into the reference image instead",
                    "mask",
                )
            from ..input_assets import save_input_asset
            filename = save_input_asset(data, "image_edit_ref")
            if filename:
                refs.append(filename)
        elif name == "mask":
            raise BadRequest(
                "mask is not supported for Qwen-Image edits; "
                "paint the region into the reference image instead",
                "mask",
            )
        else:
            fields[name] = data.decode("utf-8", errors="replace")
    return fields, refs


async def _create_task(
    spec: ModelSpec,
    body: Dict[str, Any],
    *,
    edit: bool,
    refs: Optional[List[str]] = None,
) -> GenerationTask:
    norm = _normalize(spec, body, edit=edit)
    if edit and not refs:
        raise BadRequest("image edits require at least one reference image", "image")
    if len(refs or []) > 16:
        raise BadRequest("at most 16 reference images are supported", "image")

    task = GenerationTask(
        task_id=f"image_{uuid.uuid4().hex}",
        status="queued",
        prompt=norm["prompt"],
        task_type="edit" if edit else "t2i",
        width=norm["width"],
        height=norm["height"],
        length=1,
        seed=norm["seed"],
        model_id=spec.id,
        modality="image",
        params={
            "n": norm["n"],
            "steps": norm["steps"],
            "cfg": norm["cfg"],
            "sampler": norm["sampler"],
            "scheduler": norm["scheduler"],
            "negative_prompt": norm["negative_prompt"],
            "resolution": norm["resolution"],
            "response_format": norm["response_format"],
            "quality": norm["quality"],
            "style": norm["style"],
        },
        ref_images=refs or None,
        created_at=time.time(),
    )
    get_video_manager().create(task)
    asyncio.create_task(submit_generation_task(task))
    return task


def _absolute_view_url(request: web.Request, output_path: str) -> Optional[str]:
    rel = comfyui_view_url(output_path)
    if not rel:
        return None
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if not base:
        base = str(request.url.origin())
    return f"{base}{rel}"


def _image_response(request: web.Request, task: GenerationTask,
                    response_format: str) -> dict:
    data: List[dict] = []
    for path in task.output_paths:
        item: Dict[str, Any] = {"revised_prompt": task.prompt}
        if response_format == "url":
            item["url"] = _absolute_view_url(request, path)
        else:
            item["b64_json"] = base64.b64encode(
                Path(path).read_bytes()
            ).decode("ascii")
        data.append(item)
    return {
        "created": int(task.created_at),
        "model": task.model_id,
        "size": _size_of(task),
        "data": data,
    }


async def _run_sync(request: web.Request, body: Dict[str, Any],
                    *, edit: bool, refs: Optional[List[str]] = None) -> web.Response:
    model = body.get("model")
    if not model:
        return _error(400, "model is required", "model")
    try:
        spec = get_model(model)
    except KeyError:
        return _error(404, f"model {model!r} is not served", "model")
    if spec.modality != "image":
        return _error(400, f"model {model!r} is not an image model", "model")

    try:
        resolved_refs = refs if edit else await _resolve_refs(body)
        if resolved_refs and not edit:
            if "edit" not in spec.tasks:
                return _error(
                    400,
                    f"model {model!r} does not support image edits",
                    "image",
                )
            edit = True
        task = await _create_task(spec, body, edit=edit, refs=resolved_refs)
    except BadRequest as e:
        return _error(400, e.message, e.param)

    try:
        await asyncio.wait_for(task.done.wait(), timeout=spec.timeout_s)
    except asyncio.TimeoutError:
        return _error(
            504,
            f"image generation exceeded {spec.timeout_s}s; "
            f"poll /v2/extension/image/{task.task_id}",
            err_type="server_error",
        )

    if task.status != "completed":
        return _error(
            500, task.error or "image generation failed",
            err_type="server_error",
        )
    if len(task.output_paths) != int((task.params or {}).get("n", 1)):
        return _error(
            500,
            f"expected {task.params.get('n', 1)} outputs, "
            f"got {len(task.output_paths)}",
            err_type="server_error",
        )

    response_format = (task.params or {}).get("response_format", "b64_json")
    try:
        payload = _image_response(request, task, response_format)
    except OSError as e:
        return _error(
            500, f"output file unreadable: {e}", err_type="server_error"
        )
    return web.json_response(payload)


@routes.post("/v1/images/generations")
async def create_image(request: web.Request) -> web.Response:
    """OpenAI Images API text-to-image endpoint (synchronous)."""
    try:
        body = await request.json()
    except Exception:
        return _error(400, "Invalid JSON body")
    if not isinstance(body, dict):
        return _error(400, "JSON body must be an object")
    return await _run_sync(request, body, edit=False)


@routes.post("/v1/images/edits")
async def edit_image(request: web.Request) -> web.Response:
    """OpenAI Images API edit endpoint (Qwen-Image 2.1, multipart)."""
    ctype = request.headers.get("Content-Type", "")
    if "multipart/form-data" not in ctype:
        return _error(400, "image edits require multipart/form-data")
    try:
        fields, refs = await _parse_multipart(request)
    except BadRequest as e:
        return _error(400, e.message, e.param)
    return await _run_sync(request, fields, edit=True, refs=refs)


@routes.post("/v2/extension/image/generate")
async def create_image_task(request: web.Request) -> web.Response:
    """Async image generation endpoint (JSON, returns a job record)."""
    try:
        body = await request.json()
    except Exception:
        return _error(400, "Invalid JSON body")
    if not isinstance(body, dict):
        return _error(400, "JSON body must be an object")

    model = body.get("model")
    if not model:
        return _error(400, "model is required", "model")
    try:
        spec = get_model(model)
    except KeyError:
        return _error(404, f"model {model!r} is not served", "model")
    if spec.modality != "image":
        return _error(400, f"model {model!r} is not an image model", "model")

    try:
        refs = await _resolve_refs(body)
        if refs and not body.get("prompt"):
            parsed = _parse_content_items(body)
            if parsed.get("prompt"):
                body["prompt"] = parsed["prompt"]
        task = await _create_task(spec, body, edit=bool(refs), refs=refs)
    except BadRequest as e:
        return _error(400, e.message, e.param)
    return web.json_response(_image_task_summary(task))


@routes.get("/v2/extension/image/{task_id}")
async def get_image_task(request: web.Request) -> web.Response:
    task_id = request.match_info["task_id"]
    task = get_video_manager().get(task_id)
    if not task:
        record = get_video_persistence().get(task_id)
        if record is None:
            return _error(404, "Task not found")
        task = _record_to_task(record)
        get_video_manager().restore(task)
    return web.json_response(_image_task_summary(task))


@routes.get("/v2/extension/image")
async def list_image_tasks(request: web.Request) -> web.Response:
    manager = get_video_manager()
    seen = set()
    tasks = []
    for task in manager.list_all().values():
        if task.modality != "image":
            continue
        seen.add(task.task_id)
        tasks.append(_image_task_summary(task))
    for record in get_video_persistence().list_active().values():
        if record.get("task_id") in seen:
            continue
        if record.get("modality", "video") != "image":
            continue
        task = _record_to_task(record)
        manager.restore(task)
        tasks.append(_image_task_summary(task))
    return web.json_response({"tasks": tasks})
