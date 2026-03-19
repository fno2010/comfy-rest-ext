"""
Model download endpoints (Phase 1).

Supports downloading from CivitAI, HuggingFace, and direct URLs.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Optional

from aiohttp import web
from server import PromptServer

from ..schemas.requests import ModelDownloadRequest
from ..tasks import get_task_queue
from ..tasks.download_task import (
    check_civitai_url,
    check_huggingface_url,
    resolve_civitai_download_url,
    resolve_huggingface_download_url,
    run_download_task,
)
from ..tasks.persistence import TaskState, get_persistence

logger = logging.getLogger("comfy-rest-ext.download")

routes = PromptServer.instance.routes


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

    # Check for existing file with same name (for resume)
    output_path = os.path.join(output_dir, filename)
    existing_size = 0
    if os.path.exists(output_path):
        existing_size = os.path.getsize(output_path)
        logger.info(f"Found existing file {output_path} ({existing_size} bytes)")

    # Create download task via task queue
    task_id = get_task_queue().create_task(
        coro=_download_coro(
            url=url,
            output_dir=output_dir,
            filename=filename,
            folder=folder,
            existing_size=existing_size,
        ),
        name=f"download:{url}",
    )

    # Persist task state
    persistence = get_persistence()
    persistence.create(TaskState(
        task_id=task_id,
        name=f"download:{url}",
        status="queued",
        url=url,
        local_path=output_path,
        created_at=datetime.now().timestamp(),
        downloaded_bytes=existing_size,
    ))

    return web.json_response({
        "task_id": task_id,
        "status": "queued",
        "url": url,
        "folder": folder,
        "filename": filename,
        "existing_size": existing_size if existing_size > 0 else None,
    })


async def _download_coro(
    url: str,
    output_dir: str,
    filename: str,
    folder: str,
    existing_size: int = 0,
) -> dict:
    """Async coroutine that runs the download."""
    from ..tasks import get_task_queue
    persistence = get_persistence()

    task_id = get_task_queue()._tasks and list(get_task_queue()._tasks.keys())[-1]
    if not task_id:
        raise ValueError("No task found")

    queue = get_task_queue()
    cancel_event = queue.get_cancellation_event(task_id)

    # Update status to downloading
    persistence.update(task_id, status="downloading", started_at=datetime.now().timestamp())

    try:
        local_path = await run_download_task(
            task_id,
            url,
            output_dir,
            filename,
            cancellation_event=cancel_event,
        )

        # Get final file size
        final_size = os.path.getsize(local_path) if os.path.exists(local_path) else 0

        persistence.update(
            task_id,
            status="completed",
            progress=1.0,
            downloaded_bytes=final_size,
            local_path=local_path,
        )

        await _emit_websocket_event(
            "extension-model-download-complete",
            {"task_id": task_id, "path": local_path},
        )

        # Move to history
        await persistence.complete_task(task_id, "completed")

        return {"path": local_path}

    except asyncio.CancelledError:
        # Keep task in active state for resume
        persistence.update(task_id, status="cancelled")
        await _emit_websocket_event(
            "extension-model-download-cancelled",
            {"task_id": task_id},
        )
        raise

    except Exception as e:
        logger.exception(f"Download failed: {e}")
        persistence.update(task_id, status="failed", error=str(e))

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
    persistence = get_persistence()
    task_state = persistence.get(task_id)

    if not info and not task_state:
        return web.json_response(
            {"error": "Task not found"},
            status=404,
        )

    response = {
        "task_id": task_id,
        "status": task_state.status if task_state else info.status.value,
        "progress": info.progress if info else (task_state.progress if task_state else 0.0),
        "url": task_state.url if task_state else None,
    }

    if task_state:
        response["downloaded_bytes"] = task_state.downloaded_bytes
        response["total_bytes"] = task_state.total_bytes
        response["local_path"] = task_state.local_path
        response["error"] = task_state.error

    return web.json_response(response)


@routes.delete("/v2/extension/model/download/{task_id}")
async def cancel_download_task(request: web.Request) -> web.Response:
    """Cancel a running download task."""
    task_id = request.match_info["task_id"]

    queue = get_task_queue()
    persistence = get_persistence()
    task_state = persistence.get(task_id)

    if task_state:
        persistence.update(task_id, status="cancelled")

    if queue.cancel(task_id):
        return web.json_response({"task_id": task_id, "status": "cancelled"})
    else:
        return web.json_response(
            {"error": "Task not found or already completed"},
            status=404,
        )


@routes.get("/v2/extension/model/download")
async def list_download_tasks(request: web.Request) -> web.Response:
    """List all download tasks (active and recent)."""
    queue = get_task_queue()
    persistence = get_persistence()
    tasks = []

    # Get active tasks from persistence
    active_tasks = persistence.list_active()

    for task_id, info in queue.list_tasks().items():
        task_state = active_tasks.get(task_id)
        tasks.append({
            "task_id": task_id,
            "name": info.name,
            "status": task_state.status if task_state else info.status.value,
            "progress": info.progress,
            "created_at": info.created_at,
            "url": task_state.url if task_state else None,
            "local_path": task_state.local_path if task_state else None,
            "downloaded_bytes": task_state.downloaded_bytes if task_state else 0,
            "total_bytes": task_state.total_bytes if task_state else None,
        })

    # Also include tasks from persistence that are not in queue
    # (e.g., cancelled tasks waiting for resume or cleanup)
    for task_id, task_state in active_tasks.items():
        if task_state.status == "cancelled" and task_id not in queue.list_tasks():
            tasks.append({
                "task_id": task_id,
                "name": task_state.name,
                "status": task_state.status,
                "progress": task_state.progress,
                "created_at": task_state.created_at,
                "url": task_state.url,
                "local_path": task_state.local_path,
                "downloaded_bytes": task_state.downloaded_bytes,
                "total_bytes": task_state.total_bytes,
            })

    return web.json_response({"tasks": tasks})
