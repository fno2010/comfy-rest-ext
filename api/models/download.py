"""
Model download endpoints (Phase 1).

Supports downloading from CivitAI, HuggingFace, and direct URLs.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from aiohttp import web
from server import PromptServer

from ..schemas.requests import ModelDownloadRequest
from ..tasks import get_task_queue
from ..tasks.download_task import (
    DownloadTask,
    check_civitai_url,
    check_huggingface_url,
    resolve_civitai_download_url,
    resolve_huggingface_download_url,
    run_download_task,
)

logger = logging.getLogger("comfy-rest-ext.download")

routes = PromptServer.instance.routes

# In-memory task storage (shared with task queue)
_download_tasks: dict[str, DownloadTask] = {}


def _get_ws_manager():
    """Get WebSocket manager for progress推送."""
    return PromptServer.instance


async def _emit_websocket_event(event: str, data: dict) -> None:
    """Emit a WebSocket event to all connected clients."""
    ws = _get_ws_manager()
    if hasattr(ws, "broadcast"):
        await ws.broadcast(
            {"event": event, "data": data}
        )


@routes.post("/v2/extension/model/download")
async def create_download_task(request: web.Request) -> web.Response:
    """
    Create a new model download task.

    Supports:
    - CivitAI URLs (civitai.com/models/{id} or with version)
    - HuggingFace URLs (huggingface.co/{org}/{repo})
    - Direct HTTP/HTTPS URLs
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"error": "Invalid JSON body"},
            status=400,
        )

    try:
        req = ModelDownloadRequest(**body)
    except Exception as e:
        return web.json_response(
            {"error": f"Invalid request: {e}"},
            status=400,
        )

    url = req.url
    folder = req.folder
    filename = req.filename

    # Validate CivitAI URL
    is_civitai, is_api, model_id, version_id = check_civitai_url(url)
    is_hf, repo_id, hf_filename, hf_folder, branch = check_huggingface_url(url)

    if not is_civitai and not is_hf and not url.startswith("http"):
        return web.json_response(
            {"error": "URL must be a valid HTTP URL or CivitAI/HuggingFace URL"},
            status=400,
        )

    # Resolve filename if not provided
    if not filename:
        if is_civitai:
            try:
                _, filename, _ = await resolve_civitai_download_url(
                    model_id, version_id
                )
            except Exception as e:
                logger.warning(f"Could not resolve CivitAI filename: {e}")
                filename = None
        elif is_hf:
            filename = hf_filename

        if not filename:
            filename = url.split("/")[-1].split("?")[0] or "download"

    # Get ComfyUI model folder
    try:
        import sys
        sys.path.insert(0, '/comfy/mnt/ComfyUI')
        from folder_paths import get_folder_paths
        paths = get_folder_paths(folder or "checkpoints")
        if paths:
            output_dir = paths[0]
        else:
            output_dir = os.path.join(os.path.expanduser("~"), "ComfyUI", "models", folder)
    except Exception as e:
        logger.warning(f"Could not get ComfyUI folder paths: {e}")
        output_dir = os.path.join(os.path.expanduser("~"), "ComfyUI", "models", folder)

    os.makedirs(output_dir, exist_ok=True)

    # Create download task
    task_id = get_task_queue().create_task(
        coro=_download_coro(
            url=url,
            output_dir=output_dir,
            filename=filename,
            folder=folder,
        ),
        name=f"download:{url}",
    )

    # Store download task info
    _download_tasks[task_id] = DownloadTask(
        task_id=task_id,
        status="queued",
        url=url,
        created_at=0,
    )

    return web.json_response({
        "task_id": task_id,
        "status": "queued",
        "url": url,
        "folder": folder,
        "filename": filename,
    })


async def _download_coro(
    url: str,
    output_dir: str,
    filename: str,
    folder: str,
) -> dict:
    """Async coroutine that runs the download."""
    from ..tasks import get_task_queue

    task_id = get_task_queue()._tasks and list(get_task_queue()._tasks.keys())[-1]
    if not task_id:
        raise ValueError("No task found")

    queue = get_task_queue()
    cancel_event = queue.get_cancellation_event(task_id)

    # Update status
    if task_id in _download_tasks:
        _download_tasks[task_id].status = "downloading"

    try:
        local_path = await run_download_task(
            task_id,
            url,
            output_dir,
            filename,
            cancellation_event=cancel_event,
        )

        if task_id in _download_tasks:
            _download_tasks[task_id].status = "completed"
            _download_tasks[task_id].local_path = local_path
            _download_tasks[task_id].progress = 1.0

        await _emit_websocket_event(
            "extension-model-download-complete",
            {"task_id": task_id, "path": local_path},
        )

        return {"path": local_path}

    except asyncio.CancelledError:
        if task_id in _download_tasks:
            _download_tasks[task_id].status = "cancelled"
        await _emit_websocket_event(
            "extension-model-download-cancelled",
            {"task_id": task_id},
        )
        raise

    except Exception as e:
        logger.exception(f"Download failed: {e}")
        if task_id in _download_tasks:
            _download_tasks[task_id].status = "failed"
            _download_tasks[task_id].error = str(e)

        await _emit_websocket_event(
            "extension-model-download-failed",
            {"task_id": task_id, "error": str(e)},
        )
        raise


@routes.get("/v2/extension/model/download/{task_id}")
async def get_download_status(request: web.Request) -> web.Response:
    """Get the status of a download task."""
    task_id = request.match_info["task_id"]

    queue = get_task_queue()
    info = queue.get_info(task_id)
    download_task = _download_tasks.get(task_id)

    if not info and not download_task:
        return web.json_response(
            {"error": "Task not found"},
            status=404,
        )

    response = {
        "task_id": task_id,
        "status": download_task.status if download_task else info.status.value,
        "progress": info.progress if info else 0.0,
        "url": download_task.url if download_task else None,
    }

    if download_task:
        response["downloaded_bytes"] = download_task.downloaded_bytes
        response["total_bytes"] = download_task.total_bytes
        response["local_path"] = download_task.local_path
        response["error"] = download_task.error

    return web.json_response(response)


@routes.delete("/v2/extension/model/download/{task_id}")
async def cancel_download_task(request: web.Request) -> web.Response:
    """Cancel a running download task."""
    task_id = request.match_info["task_id"]

    queue = get_task_queue()

    if task_id in _download_tasks:
        _download_tasks[task_id].status = "cancelled"

    if queue.cancel(task_id):
        return web.json_response({"task_id": task_id, "status": "cancelled"})
    else:
        return web.json_response(
            {"error": "Task not found or already completed"},
            status=404,
        )


@routes.get("/v2/extension/model/download")
async def list_download_tasks(request: web.Request) -> web.Response:
    """List all download tasks."""
    queue = get_task_queue()
    tasks = []

    for task_id, info in queue.list_tasks().items():
        download_task = _download_tasks.get(task_id)
        tasks.append({
            "task_id": task_id,
            "name": info.name,
            "status": download_task.status if download_task else info.status.value,
            "progress": info.progress,
            "created_at": info.created_at,
            "url": download_task.url if download_task else None,
            "local_path": download_task.local_path if download_task else None,
        })

    return web.json_response({"tasks": tasks})
