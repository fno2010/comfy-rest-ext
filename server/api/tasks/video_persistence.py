"""
Video task persistence for MiniMax-H3 generation tasks.

Mirrors the write-through pattern of TaskPersistence (active JSON +
append-only history JSONL with periodic flush), but stores VideoTask
records in a dedicated state directory so video tasks never mix with
download/deps tasks.

State lives under ComfyUI's system-user directory
(`<user>/__comfy_rest_ext/`), the canonical location for extension
state per folder_paths.get_system_user_directory(). The `__` prefix
keeps it hidden from ComfyUI's HTTP /userdata endpoints.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("comfy-rest-ext.video")


def get_video_state_dir() -> Path:
    """Get the directory for video task state files."""
    import folder_paths
    state_dir = Path(folder_paths.get_system_user_directory("comfy_rest_ext"))
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


class VideoPersistence:
    """
    Video task state persistence with write-through caching.

    - Active tasks in memory for fast access
    - Periodically flushed to video_active_tasks.json
    - Completed/failed tasks moved to video_history_tasks.jsonl
    - Restored from disk on startup
    """

    def __init__(
        self,
        state_dir: Optional[Path] = None,
        flush_interval: float = 30.0,
        max_active_tasks: int = 100,
        max_history_tasks: int = 1000,
    ):
        state_dir = state_dir or get_video_state_dir()
        self._active_file = state_dir / "video_active_tasks.json"
        self._history_file = state_dir / "video_history_tasks.jsonl"
        self._flush_interval = flush_interval
        self._max_active_tasks = max_active_tasks
        self._max_history_tasks = max_history_tasks

        self._active: Dict[str, Dict[str, Any]] = {}
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

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get a task record by ID."""
        return self._active.get(task_id)

    def list_active(self) -> Dict[str, Dict[str, Any]]:
        """List all active task records."""
        return dict(self._active)

    def create(self, record: Dict[str, Any]) -> None:
        """Create a new task record."""
        self._active[record["task_id"]] = dict(record)
        self._dirty = True

    def update(self, task_id: str, **kwargs) -> bool:
        """Update task record fields. Returns True if found."""
        record = self._active.get(task_id)
        if not record:
            return False
        record.update(kwargs)
        self._dirty = True
        return True

    def remove(self, task_id: str) -> bool:
        """Remove a task from active storage."""
        if task_id in self._active:
            del self._active[task_id]
            self._dirty = True
            return True
        return False

    async def complete_task(self, task_id: str, status: str = "completed") -> None:
        """Mark a task completed/failed and move to history."""
        record = self._active.get(task_id)
        if not record:
            return
        record["status"] = status
        record["completed_at"] = datetime.now().timestamp()
        await self._append_history(record)
        self.remove(task_id)
        await self._flush_active()

    async def _load_active(self) -> None:
        """Load active tasks from disk, splitting done ones to history."""
        if not self._active_file.exists():
            return
        try:
            with open(self._active_file, "r") as f:
                data = json.load(f)
            for task_id, record in data.get("tasks", {}).items():
                try:
                    status = record.get("status", "queued")
                    if status in ("completed", "failed", "cancelled"):
                        await self._append_history(record)
                    else:
                        self._active[task_id] = record
                except Exception as e:
                    logger.warning(f"Failed to load video task {task_id}: {e}")
            logger.info(f"Loaded {len(self._active)} active video tasks")
        except Exception as e:
            logger.error(f"Failed to load video tasks: {e}")

    async def _flush_active(self) -> None:
        """Flush active tasks to disk."""
        if not self._dirty:
            return
        async with self._lock:
            try:
                data = {
                    "tasks": dict(self._active),
                    "updated_at": datetime.now().timestamp(),
                }
                self._active_file.write_text(json.dumps(data, indent=2))
                self._dirty = False
                self._last_flush = datetime.now().timestamp()
            except Exception as e:
                logger.error(f"Failed to flush video tasks: {e}")

    async def _append_history(self, record: Dict[str, Any]) -> None:
        """Append a finished task to the history file."""
        try:
            with open(self._history_file, "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.error(f"Failed to append video task history: {e}")

    async def _cleanup_history(self) -> None:
        """Trim history file to max size."""
        if not self._history_file.exists():
            return
        try:
            lines = self._history_file.read_text().strip().split("\n")
            if len(lines) <= self._max_history_tasks:
                return
            lines = lines[-self._max_history_tasks:]
            self._history_file.write_text("\n".join(lines) + "\n")
            logger.info(f"Trimmed video task history to {len(lines)} entries")
        except Exception as e:
            logger.error(f"Failed to cleanup video task history: {e}")

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

    async def list_history(self) -> list[Dict[str, Any]]:
        """Read all history records (most recent last)."""
        if not self._history_file.exists():
            return []
        records = []
        try:
            for line in self._history_file.read_text().strip().split("\n"):
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            logger.error(f"Failed to read video task history: {e}")
        return records


_video_persistence: Optional[VideoPersistence] = None


def get_video_persistence() -> VideoPersistence:
    """Get the global video persistence instance."""
    global _video_persistence
    if _video_persistence is None:
        _video_persistence = VideoPersistence()
    return _video_persistence


async def init_video_persistence() -> VideoPersistence:
    """Initialize and start the video persistence manager."""
    p = get_video_persistence()
    await p.start()
    return p


async def stop_video_persistence() -> None:
    """Stop the video persistence manager."""
    global _video_persistence
    if _video_persistence:
        await _video_persistence.stop()
        _video_persistence = None
