"""
Task management modules.
"""

from .task_queue import TaskQueue, TaskInfo, TaskStatus, get_task_queue
from .registry import TaskRegistry, get_task_registry
from .persistence import TaskState, TaskPersistence, get_persistence, init_persistence, stop_persistence
from .video_persistence import (
    VideoPersistence,
    get_video_persistence,
    init_video_persistence,
    stop_video_persistence,
)

__all__ = [
    "TaskQueue",
    "TaskInfo",
    "TaskStatus",
    "get_task_queue",
    "TaskRegistry",
    "get_task_registry",
    "TaskState",
    "TaskPersistence",
    "get_persistence",
    "init_persistence",
    "stop_persistence",
    "VideoPersistence",
    "get_video_persistence",
    "init_video_persistence",
    "stop_video_persistence",
]
