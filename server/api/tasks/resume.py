"""Resume video tasks that were blocked on capability provisioning."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from ..accel import check_config_available, parse_accel_config
from ..capabilities import capability_report
from .video_persistence import get_video_persistence
from .video_task import get_video_manager, submit_video_task, _record_to_task


class ResumeError(Exception):
    def __init__(self, status: int, message: str, plan: Optional[dict] = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.plan = plan


def _capability_ids(accel_cfg) -> list:
    return [
        f"{layer}.{getattr(accel_cfg, layer)}"
        for layer in ("turbo", "attention", "block_cache", "step_cache")
        if getattr(accel_cfg, layer) not in (None, "none")
    ]


def get_video_task(video_id: str):
    manager = get_video_manager()
    task = manager.get(video_id)
    if task is not None:
        return task, manager
    record = get_video_persistence().get(video_id)
    if record is None:
        return None, manager
    task = _record_to_task(record)
    manager.restore(task)
    return task, manager


def record_provisioning(
    video_id: str,
    actions: list,
    restart_required: bool = False,
) -> None:
    task, manager = get_video_task(video_id)
    if task is None:
        raise ResumeError(404, "Video not found")
    task.provisioning_actions = list(actions) + [{
        "restart_required": bool(restart_required),
    }]
    manager.update(
        video_id, provisioning_actions=task.provisioning_actions,
    )


async def resume_blocked_video_task(video_id: str) -> Any:
    task, manager = get_video_task(video_id)
    if task is None:
        raise ResumeError(404, "Video not found")
    if task.status != "blocked_on_provisioning":
        raise ResumeError(409, "task is not blocked on provisioning")
    try:
        accel_cfg = parse_accel_config(task.speed)
    except ValueError as e:
        raise ResumeError(400, f"invalid speed: {e}") from e
    missing = check_config_available(accel_cfg)
    if missing:
        plan = capability_report(capability_ids=_capability_ids(accel_cfg))
        manager.update(
            task.task_id,
            error=missing,
            capability_plan=plan,
        )
        raise ResumeError(409, missing, plan)

    task.status = "queued"
    task.error = None
    task.capability_plan = None
    task.progress = 0.0
    task.node_progress = 0.0
    task.current_node = None
    task.eta = None
    task.started_at = None
    task.completed_at = None
    manager.update(
        task.task_id,
        status="queued",
        error=None,
        capability_plan=None,
        progress=0.0,
        node_progress=0.0,
        current_node=None,
        eta=None,
        started_at=None,
        completed_at=None,
    )
    asyncio.create_task(submit_video_task(task))
    return task
