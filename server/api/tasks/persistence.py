"""
Task persistence for download and dependency tasks.

Provides write-through caching for task state with periodic flush
and history management.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("comfy-rest-ext.tasks")


def get_state_dir() -> Path:
    """Get the directory for task state files."""
    state_dir = Path.home() / ".comfy-rest-ext"
    state_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"[TaskPersistence] State directory: {state_dir}")
    return state_dir


@dataclass
class TaskState:
    """Persisted task state."""
    task_id: str
    name: str
    status: str
    url: str
    local_path: Optional[str] = None
    created_at: float = 0.0
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    downloaded_bytes: int = 0
    total_bytes: Optional[int] = None
    error: Optional[str] = None
    progress: float = 0.0
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TaskState":
        return cls(**d)


class TaskPersistence:
    """
    Task state persistence with write-through caching.

    - Active tasks stored in memory for fast access
    - Periodically flushed to active_tasks.json
    - Completed tasks moved to history_tasks.jsonl (append-only)
    - Resume from persisted state on startup
    """

    def __init__(
        self,
        active_file: Optional[Path] = None,
        history_file: Optional[Path] = None,
        flush_interval: float = 30.0,
        max_active_tasks: int = 100,
        max_history_tasks: int = 1000,
    ):
        self._active_file = active_file or (get_state_dir() / "active_tasks.json")
        self._history_file = history_file or (get_state_dir() / "history_tasks.jsonl")
        self._flush_interval = flush_interval
        self._max_active_tasks = max_active_tasks
        self._max_history_tasks = max_history_tasks

        self._active: Dict[str, TaskState] = {}
        self._dirty: bool = False
        self._last_flush: float = datetime.now().timestamp()
        self._flush_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the persistence manager and load existing state."""
        await self._load_active()
        await self._cleanup_history()
        self._flush_task = asyncio.create_task(self._periodic_flush())

    async def stop(self) -> None:
        """Stop the persistence manager and flush remaining state."""
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self._flush_active()

    def get(self, task_id: str) -> Optional[TaskState]:
        """Get a task by ID."""
        return self._active.get(task_id)

    def list_active(self) -> Dict[str, TaskState]:
        """List all active tasks."""
        return dict(self._active)

    def create(self, state: TaskState) -> None:
        """Create a new task."""
        self._active[state.task_id] = state
        self._dirty = True

    def update(self, task_id: str, **kwargs) -> bool:
        """Update task fields. Returns True if task was found."""
        task = self._active.get(task_id)
        if not task:
            return False

        for key, value in kwargs.items():
            if hasattr(task, key):
                setattr(task, key, value)

        self._dirty = True
        return True

    def remove(self, task_id: str) -> bool:
        """Remove a task from active storage (call after moving to history)."""
        if task_id in self._active:
            del self._active[task_id]
            self._dirty = True
            return True
        return False

    async def complete_task(self, task_id: str, status: str = "completed") -> None:
        """Mark a task as completed/failed and move to history."""
        task = self._active.get(task_id)
        if not task:
            return

        task.status = status
        task.completed_at = datetime.now().timestamp()

        # Append to history
        await self._append_history(task)

        # Remove from active
        self.remove(task_id)

        # Flush immediately for completed tasks
        await self._flush_active()

    async def _load_active(self) -> None:
        """Load active tasks from file."""
        if not self._active_file.exists():
            return

        try:
            with open(self._active_file, "r") as f:
                data = json.load(f)

            for task_id, task_dict in data.get("tasks", {}).items():
                try:
                    task = TaskState.from_dict(task_dict)
                    # Only restore non-completed tasks
                    if task.status not in ("completed", "failed", "cancelled"):
                        self._active[task_id] = task
                    else:
                        # Completed tasks go to history
                        await self._append_history(task)
                except Exception as e:
                    logger.warning(f"Failed to load task {task_id}: {e}")

            logger.info(f"Loaded {len(self._active)} active tasks")
        except Exception as e:
            logger.error(f"Failed to load active tasks: {e}")

    async def _flush_active(self) -> None:
        """Flush active tasks to file."""
        if not self._dirty and not self._should_flush():
            return

        async with self._lock:
            try:
                data = {
                    "tasks": {tid: task.to_dict() for tid, task in self._active.items()},
                    "updated_at": datetime.now().timestamp(),
                }
                self._active_file.write_text(json.dumps(data, indent=2))
                self._dirty = False
                self._last_flush = datetime.now().timestamp()
            except Exception as e:
                logger.error(f"Failed to flush active tasks: {e}")

    async def _append_history(self, task: TaskState) -> None:
        """Append a completed task to history file."""
        try:
            with open(self._history_file, "a") as f:
                f.write(json.dumps(task.to_dict()) + "\n")
        except Exception as e:
            logger.error(f"Failed to append to history: {e}")

    async def _cleanup_history(self) -> None:
        """Trim history file to max size."""
        if not self._history_file.exists():
            return

        try:
            # Read all lines
            lines = self._history_file.read_text().strip().split("\n")
            if len(lines) <= self._max_history_tasks:
                return

            # Keep only the most recent lines
            lines = lines[-self._max_history_tasks:]
            self._history_file.write_text("\n".join(lines) + "\n")
            logger.info(f"Trimmed history to {len(lines)} entries")
        except Exception as e:
            logger.error(f"Failed to cleanup history: {e}")

    def _should_flush(self) -> bool:
        """Check if periodic flush is needed."""
        return (datetime.now().timestamp() - self._last_flush) >= self._flush_interval

    async def _periodic_flush(self) -> None:
        """Periodically flush dirty state."""
        while True:
            try:
                await asyncio.sleep(self._flush_interval)
                if self._dirty:
                    await self._flush_active()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Periodic flush error: {e}")

    async def get_or_restore(self, task_id: str) -> Optional[TaskState]:
        """
        Get a task, trying to restore from history if not in active.

        Useful for resume - check if a task was partially completed before restart.
        """
        # Check active first
        task = self._active.get(task_id)
        if task:
            return task

        # Try to find in history (for resume scenarios)
        if self._history_file.exists():
            try:
                # Read backwards to find most recent entry for this task
                with open(self._history_file, "r") as f:
                    lines = f.readlines()

                for line in reversed(lines):
                    try:
                        task_dict = json.loads(line)
                        if task_dict.get("task_id") == task_id:
                            # Restore as active task with partial progress
                            task = TaskState.from_dict(task_dict)
                            self._active[task_id] = task
                            self._dirty = True
                            return task
                    except json.JSONDecodeError:
                        continue
            except Exception as e:
                logger.warning(f"Failed to search history for task {task_id}: {e}")

        return None


# Global persistence instance
_persistence: Optional[TaskPersistence] = None


def get_persistence() -> TaskPersistence:
    """Get the global persistence instance."""
    global _persistence
    if _persistence is None:
        _persistence = TaskPersistence()
    return _persistence


async def init_persistence() -> TaskPersistence:
    """Initialize and start the persistence manager."""
    p = get_persistence()
    await p.start()
    return p


async def stop_persistence() -> None:
    """Stop the persistence manager."""
    global _persistence
    if _persistence:
        await _persistence.stop()
        _persistence = None
