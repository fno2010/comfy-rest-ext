"""See-through layered asset endpoints.

This API is intentionally separate from the OpenAI image API: a See-through
task consumes one image and returns layers/PSD-oriented artifacts, not a
text prompt or a plain image.  The generic GenerationTask manager, persistence,
and dashboard discovery are reused.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
import zipfile
from typing import Any, Dict, List, Optional

from aiohttp import web
from server import PromptServer

from .catalog import get_model
from .openai.images import _parse_multipart
from .tasks.video_persistence import get_video_persistence
from .tasks.video_task import (
    GenerationTask,
    _record_to_task,
    get_video_manager,
    submit_generation_task,
)

routes = PromptServer.instance.routes
ACTIVE = frozenset(("queued", "running", "in_progress"))
EXCLUDABLE_TAGS = frozenset({"tail", "wings", "objects"})


def _exclude_tags(value: Optional[str]) -> str:
    """Normalize optional comma-separated body tags, rejecting unsafe ones."""
    tags = {
        part.strip().lower()
        for part in str(value or "").split(",")
        if part.strip()
    }
    invalid = tags - EXCLUDABLE_TAGS
    if invalid:
        raise ValueError(
            "exclude_tags only accepts optional body tags "
            f"{sorted(EXCLUDABLE_TAGS)}; got {sorted(invalid)}"
        )
    return ",".join(sorted(tags))


def _error(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


def _bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return str(value).lower() not in ("0", "false", "no", "off")


def _int(value: Optional[str], default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _get_task(task_id: str) -> Optional[GenerationTask]:
    manager = get_video_manager()
    task = manager.get(task_id)
    if task is None:
        record = get_video_persistence().get(task_id)
        if record is None:
            return None
        task = _record_to_task(record)
        manager.restore(task)
    return task if task.modality == "layers" else None


def _preview_path(task: GenerationTask) -> Optional[str]:
    for path in task.output_paths or []:
        name = os.path.basename(path).lower()
        if path.endswith(".png") and "_preview" in name:
            return path
    for path in task.output_paths or []:
        if path.endswith(".png") and "_depth" not in os.path.basename(path):
            return path
    return None


def _layer_count(task: GenerationTask) -> int:
    # SavePSD writes a manifest next to the layer PNGs.
    for path in task.output_paths or []:
        if not path.endswith("_layers.json"):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                return len(json.load(fh).get("layers") or [])
        except (OSError, ValueError):
            break
    return sum(
        1 for path in (task.output_paths or [])
        if path.endswith(".png")
        and "_depth" not in os.path.basename(path)
        and "_preview" not in os.path.basename(path)
    )


def _summary(task: GenerationTask) -> dict:
    preview = _preview_path(task)
    base = f"/v2/extension/layers/{task.task_id}"
    return {
        "id": task.task_id,
        "object": "layers.decomposition",
        "model": task.model_id,
        "status": task.status,
        "progress": int((task.progress or 0) * 100),
        "current_node": task.current_node,
        "node_progress": int((task.node_progress or 0) * 100),
        "created_at": int(task.created_at or 0),
        "completed_at": int(task.completed_at) if task.completed_at else None,
        "elapsed": round(task.completed_at - task.started_at, 1)
        if task.completed_at and task.started_at else None,
        "layer_count": _layer_count(task) if task.status == "completed" else 0,
        "params": task.params or {},
        "input_image_url": f"{base}/assets/image/0" if task.ref_images else None,
        "preview_url": f"{base}/preview" if preview else None,
        "archive_url": f"{base}/content" if task.output_paths else None,
        "psd_url": f"{base}/psd" if task.status == "completed" and task.output_paths else None,
        "error": {"message": task.error} if task.error else None,
    }


@routes.post("/v2/extension/layers/decompose")
async def decompose(request: web.Request) -> web.Response:
    ctype = request.headers.get("Content-Type", "")
    if "multipart/form-data" not in ctype:
        return _error(400, "layers decompose requires multipart/form-data")
    try:
        fields, refs = await _parse_multipart(request)
    except Exception as e:
        return _error(400, str(e))
    if not refs:
        return _error(400, "layers decompose requires one input image")
    if len(refs) != 1:
        return _error(400, "See-through accepts exactly one input image")

    model_id = fields.get("model") or "see-through"
    try:
        spec = get_model(model_id)
    except KeyError:
        return _error(404, f"model {model_id!r} is not served")
    if spec.modality != "layers":
        return _error(400, f"model {model_id!r} is not a layers model")

    params: Dict[str, Any] = dict(spec.defaults)
    for key in ("resolution", "steps", "resolution_depth", "seed"):
        if key in fields:
            params[key] = _int(fields.get(key), int(params.get(key, 0)))
    for key in ("tblr_split", "use_lama", "group_offload"):
        if key in fields:
            params[key] = _bool(fields.get(key), bool(params.get(key, True)))
    for key in ("quant_mode", "layerdiff_model", "depth_model"):
        if fields.get(key):
            params[key] = fields[key]
    try:
        exclude_tags = _exclude_tags(fields.get("exclude_tags"))
    except ValueError as e:
        return _error(400, str(e))
    if exclude_tags:
        params["exclude_tags"] = exclude_tags

    task = GenerationTask(
        task_id=f"layers_{uuid.uuid4().hex}",
        status="queued",
        prompt="",
        task_type="decompose",
        width=int(params.get("resolution", 1280)),
        height=int(params.get("resolution", 1280)),
        length=1,
        seed=int(params.get("seed", 0)),
        model_id=spec.id,
        modality="layers",
        params=params,
        ref_images=[refs[0]],
        output_mime=spec.output_mime,
        created_at=time.time(),
    )
    get_video_manager().create(task)
    asyncio.create_task(submit_generation_task(task))
    return web.json_response(
        _summary(task),
        status=202,
        headers={"Location": f"/v2/extension/layers/{task.task_id}"},
    )


@routes.get("/v2/extension/layers")
async def list_layers(request: web.Request) -> web.Response:
    manager = get_video_manager()
    seen = set()
    tasks: List[GenerationTask] = []
    for task in manager.list_all().values():
        if task.modality != "layers":
            continue
        seen.add(task.task_id)
        tasks.append(task)
    for record in get_video_persistence().list_active().values():
        task_id = record.get("task_id")
        if not task_id or task_id in seen or record.get("modality") != "layers":
            continue
        task = _record_to_task(record)
        manager.restore(task)
        tasks.append(task)
    tasks.sort(key=lambda t: t.created_at or 0, reverse=True)
    return web.json_response({"tasks": [_summary(t) for t in tasks]})


@routes.get("/v2/extension/layers/{task_id}")
async def get_layer_task(request: web.Request) -> web.Response:
    task = _get_task(request.match_info["task_id"])
    if task is None:
        return _error(404, "Layers task not found")
    return web.json_response(_summary(task))


def _final_preview_bytes(task: GenerationTask) -> Optional[bytes]:
    """Flatten post-processed layer PNGs onto white for the dashboard preview.

    The workflow's SaveImage preview is emitted before depth sorting and
    post-processing.  This composite uses the final layer files (the same
    files included in the ZIP and PSD), so the dashboard shows the finished
    stack rather than an intermediate sampling preview.
    """
    _, manifest = _manifest(task)
    if not manifest:
        return None
    try:
        from PIL import Image
        import io
    except ImportError:
        return None
    width = int(manifest.get("width") or task.width or 0)
    height = int(manifest.get("height") or task.height or 0)
    if width <= 0 or height <= 0:
        return None
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    by_name = {os.path.basename(path): path for path in task.output_paths or []}
    for entry in manifest.get("layers") or []:
        filename = entry.get("filename")
        path = by_name.get(filename)
        if not path or not os.path.isfile(path):
            continue
        try:
            layer = Image.open(path).convert("RGBA")
        except OSError:
            continue
        canvas.alpha_composite(layer, (int(entry.get("left", 0)), int(entry.get("top", 0))))
    flat = Image.new("RGB", (width, height), (255, 255, 255))
    flat.paste(canvas, mask=canvas.getchannel("A"))
    buffer = io.BytesIO()
    flat.save(buffer, format="PNG")
    return buffer.getvalue()


@routes.get("/v2/extension/layers/{task_id}/preview")
async def get_layer_preview(request: web.Request) -> web.Response:
    task = _get_task(request.match_info["task_id"])
    if task is None:
        return _error(404, "Layers task not found")
    if task.status != "completed":
        return _error(409, "Preview is not available yet")
    flattened = _final_preview_bytes(task)
    if flattened is not None:
        return web.Response(
            body=flattened,
            content_type="image/png",
            headers={"Content-Disposition": "inline"},
        )
    preview = _preview_path(task)
    if not preview or not os.path.isfile(preview):
        return _error(409, "Preview is not available yet")
    return web.FileResponse(preview, headers={"Content-Disposition": "inline"})


@routes.get("/v2/extension/layers/{task_id}/content")
async def get_layer_archive(request: web.Request) -> web.Response:
    task = _get_task(request.match_info["task_id"])
    if task is None:
        return _error(404, "Layers task not found")
    if task.status != "completed" or not task.output_paths:
        return _error(409, "Layers task is not completed")
    import io
    buffer = io.BytesIO()
    used = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in task.output_paths:
            if not os.path.isfile(path) or path in used:
                continue
            used.add(path)
            zf.write(path, arcname=os.path.basename(path))
    return web.Response(
        body=buffer.getvalue(),
        content_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{task.task_id}.zip"',
        },
    )




def _manifest(task: GenerationTask):
    for path in task.output_paths or []:
        if path.endswith("_layers.json"):
            try:
                with open(path, encoding="utf-8") as fh:
                    return path, json.load(fh)
            except (OSError, ValueError):
                return path, None
    return None, None


@routes.get("/v2/extension/layers/{task_id}/psd")
async def get_layer_psd(request: web.Request) -> web.Response:
    task = _get_task(request.match_info["task_id"])
    if task is None:
        return _error(404, "Layers task not found")
    if task.status != "completed" or not task.output_paths:
        return _error(409, "Layers task is not completed")
    _, manifest = _manifest(task)
    if not manifest:
        return _error(500, "Layer manifest is unavailable")
    try:
        from psd_tools import PSDImage
        from PIL import Image
        import numpy as np
    except ImportError:
        return _error(501, "psd-tools is not installed")

    width, height = int(manifest["width"]), int(manifest["height"])
    psd = PSDImage.new("RGBA", (width, height), 0)
    by_name = {os.path.basename(path): path for path in task.output_paths}
    for entry in manifest.get("layers") or []:
        filename = entry.get("filename")
        path = by_name.get(filename)
        if not path or not os.path.isfile(path):
            continue
        try:
            arr = np.asarray(Image.open(path).convert("RGBA"))
        except OSError:
            continue
        layer = psd.create_pixel_layer_from_array(
            arr,
            name=str(entry.get("name") or "layer"),
            top=int(entry.get("top", 0)),
            left=int(entry.get("left", 0)),
        )
        psd.append(layer)
    import io
    buf = io.BytesIO()
    try:
        psd.save(buf)
    except Exception as e:
        return _error(500, f"PSD export failed: {e}")
    return web.Response(
        body=buf.getvalue(),
        content_type="image/vnd.adobe.photoshop",
        headers={"Content-Disposition": f'attachment; filename="{task.task_id}.psd"'},
    )


@routes.get("/v2/extension/layers/{task_id}/layers")
async def list_layer_files(request: web.Request) -> web.Response:
    task = _get_task(request.match_info["task_id"])
    if task is None:
        return _error(404, "Layers task not found")
    if task.status != "completed" or not task.output_paths:
        return _error(409, "Layers task is not completed")
    _, manifest = _manifest(task)
    if not manifest:
        return _error(500, "Layer manifest is unavailable")
    by_name = {os.path.basename(path): path for path in task.output_paths}
    entries = []
    for index, entry in enumerate(manifest.get("layers") or []):
        filename = entry.get("filename")
        if not filename or not by_name.get(filename):
            continue
        entries.append({
            "index": index,
            "name": str(entry.get("name") or f"layer {index}"),
            "filename": filename,
            "left": int(entry.get("left", 0)),
            "top": int(entry.get("top", 0)),
            "right": int(entry.get("right", 0)),
            "bottom": int(entry.get("bottom", 0)),
            "depth": entry.get("depth"),
            "url": f"/v2/extension/layers/{task.task_id}/layer/{index}/content",
        })
    return web.json_response({
        "id": task.task_id,
        "width": int(manifest.get("width") or task.width or 0),
        "height": int(manifest.get("height") or task.height or 0),
        "layers": entries,
    })


@routes.get("/v2/extension/layers/{task_id}/layer/{index}/content")
async def get_layer_file(request: web.Request) -> web.Response:
    task = _get_task(request.match_info["task_id"])
    if task is None:
        return _error(404, "Layers task not found")
    if task.status != "completed" or not task.output_paths:
        return _error(409, "Layers task is not completed")
    try:
        wanted = int(request.match_info["index"])
    except ValueError:
        return _error(400, "Invalid layer index")
    _, manifest = _manifest(task)
    entries = (manifest or {}).get("layers") or []
    if wanted < 0 or wanted >= len(entries):
        return _error(404, "Layer not found")
    filename = entries[wanted].get("filename")
    by_name = {os.path.basename(path): path for path in task.output_paths}
    path = by_name.get(filename)
    if not path or not os.path.isfile(path):
        return _error(404, "Layer not found")
    return web.FileResponse(path, headers={"Content-Disposition": "inline"})


@routes.get("/v2/extension/layers/{task_id}/assets/image/{index}")
async def get_layer_input(request: web.Request) -> web.Response:
    task = _get_task(request.match_info["task_id"])
    if task is None:
        return _error(404, "Layers task not found")
    try:
        index = int(request.match_info["index"])
    except ValueError:
        return _error(400, "Invalid asset index")
    names = task.ref_images or []
    if index < 0 or index >= len(names):
        return _error(404, "Asset not found")
    import folder_paths
    input_dir = os.path.realpath(folder_paths.get_input_directory())
    path = os.path.realpath(os.path.join(input_dir, names[index]))
    try:
        if os.path.commonpath([input_dir, path]) != input_dir:
            return _error(404, "Asset not found")
    except ValueError:
        return _error(404, "Asset not found")
    if not os.path.isfile(path):
        return _error(404, "Asset not found")
    return web.FileResponse(path, headers={"Content-Disposition": "inline"})
