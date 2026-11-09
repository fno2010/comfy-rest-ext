"""Paginated dashboard task listing.

The OpenAI list endpoints remain backwards-compatible and may return the
full history.  The dashboard uses this endpoint so its payload is bounded by
the visible page, not by the total number of tasks.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from aiohttp import web
from server import PromptServer

from ..openai.images import _image_task_summary
from ..openai.v1 import _video_response
from .video_persistence import get_video_persistence
from .video_task import _record_to_task, get_video_manager

routes = PromptServer.instance.routes

ACTIVE_STATUSES = frozenset(("queued", "running", "in_progress"))
SORTS = frozenset(("smart", "newest", "oldest", "model", "progress"))
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _all_tasks() -> List[Any]:
    manager = get_video_manager()
    known = manager.list_all()
    tasks = list(known.values())
    seen = set(known)
    for record in get_video_persistence().list_active().values():
        task_id = record.get("task_id")
        if not task_id or task_id in seen:
            continue
        task = _record_to_task(record)
        manager.restore(task)
        tasks.append(task)
        seen.add(task_id)
    return tasks


def _sort_key(task: Any, sort: str):
    created = float(task.created_at or 0)
    if sort == "oldest":
        return (created, task.task_id)
    if sort == "model":
        return (str(task.model_id or ""), -created, task.task_id)
    if sort == "progress":
        return (-float(task.progress or 0), -created, task.task_id)
    if sort == "newest":
        return (-created, task.task_id)
    # smart: active work first, then newest.
    return (0 if task.status in ACTIVE_STATUSES else 1, -created, task.task_id)


def _matches(task: Any, *, kind: str, status: str, model: str, query: str) -> bool:
    if kind != "all" and task.modality != kind:
        return False
    if model and (task.model_id or "") != model:
        return False
    if status == "active" and task.status not in ACTIVE_STATUSES:
        return False
    if status == "completed" and task.status != "completed":
        return False
    if status == "failed" and task.status != "failed":
        return False
    if query:
        needle = query.lower()
        haystack = (
            str(task.task_id or ""),
            str(task.model_id or ""),
            str(task.prompt or ""),
            str(task.status or ""),
        )
        if not any(needle in value.lower() for value in haystack):
            return False
    return True


def _summary(task: Any) -> Dict[str, Any]:
    if task.modality == "image":
        payload = _image_task_summary(task)
        payload["kind"] = "image"
        return payload
    if task.modality == "layers":
        # Lazy import avoids a route-module import cycle at startup.
        from ..layers import _summary as _layers_summary
        payload = _layers_summary(task)
        payload["kind"] = "layers"
        return payload
    payload = _video_response(task)
    payload["kind"] = "video"
    return payload


def _parse_int(value: Optional[str], default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


@routes.get("/v2/extension/tasks")
async def list_tasks(request: web.Request) -> web.Response:
    query = request.query
    limit = max(1, min(MAX_LIMIT, _parse_int(query.get("limit"), DEFAULT_LIMIT)))
    offset = max(0, _parse_int(query.get("offset"), 0))
    kind = query.get("kind", "all")
    if kind not in ("all", "video", "image", "layers"):
        kind = "all"
    status = query.get("status", "all")
    if status not in ("all", "active", "completed", "failed"):
        status = "all"
    model = query.get("model", "")
    text = query.get("q", "")
    sort = query.get("sort", "smart")
    if sort not in SORTS:
        sort = "smart"

    tasks = _all_tasks()
    stats = {
        "video_total": sum(1 for task in tasks if task.modality == "video"),
        "video_active": sum(
            1 for task in tasks
            if task.modality == "video" and task.status in ACTIVE_STATUSES
        ),
        "image_total": sum(1 for task in tasks if task.modality == "image"),
        "image_active": sum(
            1 for task in tasks
            if task.modality == "image" and task.status in ACTIVE_STATUSES
        ),
        "layers_total": sum(1 for task in tasks if task.modality == "layers"),
        "layers_active": sum(
            1 for task in tasks
            if task.modality == "layers" and task.status in ACTIVE_STATUSES
        ),
    }
    models = sorted({
        str(task.model_id) for task in tasks if task.model_id
    })
    filtered = [
        task for task in tasks
        if _matches(task, kind=kind, status=status, model=model, query=text)
    ]
    filtered.sort(key=lambda task: _sort_key(task, sort))
    page = filtered[offset:offset + limit]
    response = web.json_response({
        "object": "list",
        "data": [_summary(task) for task in page],
        "total": len(filtered),
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < len(filtered),
        "stats": stats,
        "models": models,
    })
    response.enable_compression()
    return response
