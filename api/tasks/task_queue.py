"""
Task queue infrastructure for async background tasks.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, Optional
from uuid import uuid4

logger = logging.getLogger("comfy-rest-ext.tasks")


class TaskStatus(str, Enum):
    """Task execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskInfo:
    """Immutable task metadata."""
    task_id: str
    name: str
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=lambda: datetime.now().timestamp())
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None
    result: Optional[Any] = None
    progress: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


class TaskQueue:
    """
    Simple in-memory async task queue.

    Tasks run as asyncio tasks in the background. Status is tracked
    and can be queried by task_id.
    """

    def __init__(self):
        self._tasks: Dict[str, asyncio.Task] = {}
        self._infos: Dict[str, TaskInfo] = {}
        self._cancellations: Dict[str, asyncio.Event] = {}

    def create_task(
        self,
        coro: Coroutine,
        name: str,
        task_id: Optional[str] = None,
    ) -> str:
        """
        Queue a coroutine for background execution.

        Returns the task_id.
        """
        if task_id is None:
            task_id = str(uuid4())

        info = TaskInfo(task_id=task_id, name=name)
        self._infos[task_id] = info
        self._cancellations[task_id] = asyncio.Event()

        async def run():
            info.status = TaskStatus.RUNNING
            info.started_at = datetime.now().timestamp()
            try:
                result = await coro
                info.status = TaskStatus.COMPLETED
                info.result = result
            except asyncio.CancelledError:
                info.status = TaskStatus.CANCELLED
                logger.info(f"Task {task_id} ({name}) was cancelled")
            except Exception as e:
                info.status = TaskStatus.FAILED
                info.error = str(e)
                logger.exception(f"Task {task_id} ({name}) failed")
            finally:
                info.completed_at = datetime.now().timestamp()

        self._tasks[task_id] = asyncio.create_task(run(), name=name)
        logger.debug(f"Created task {task_id} ({name})")
        return task_id

    def get_info(self, task_id: str) -> Optional[TaskInfo]:
        """Get task info by id."""
        return self._infos.get(task_id)

    def list_tasks(self) -> Dict[str, TaskInfo]:
        """List all tasks."""
        return dict(self._infos)

    def cancel(self, task_id: str) -> bool:
        """
        Request cancellation of a task.

        Returns True if the task was found and cancellation was requested.
        """
        task = self._tasks.get(task_id)
        if task is None:
            return False

        cancel_event = self._cancellations.get(task_id)
        if cancel_event:
            cancel_event.set()

        if not task.done():
            task.cancel()
            logger.info(f"Cancelled task {task_id}")
            return True
        return False

    def get_cancellation_event(self, task_id: str) -> Optional[asyncio.Event]:
        """Get the cancellation event for a task (for cooperative cancellation)."""
        return self._cancellations.get(task_id)

    def update_progress(self, task_id: str, progress: float) -> None:
        """Update task progress (0.0 - 1.0)."""
        info = self._infos.get(task_id)
        if info:
            info.progress = max(0.0, min(1.0, progress))


# Global task queue instance
_task_queue: Optional[TaskQueue] = None


def get_task_queue() -> TaskQueue:
    """Get the global task queue instance."""
    global _task_queue
    if _task_queue is None:
        _task_queue = TaskQueue()
    return _task_queue