"""
Tests for video task persistence (VideoPersistence).

Loads the module directly via importlib because the project root
directory contains a hyphen (`comfy-rest-ext`), which is not a valid
Python package name — ComfyUI loads it via custom_nodes scanning +
comfy_entrypoint(), not via package import.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(PROJECT_ROOT))


def _load_module(name, rel_path):
    """Load a project module by file path, bypassing package import."""
    path = PROJECT_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


video_persistence = _load_module(
    "comfy_rest_ext_video_persistence", "api/tasks/video_persistence.py"
)
VideoPersistence = video_persistence.VideoPersistence


@pytest.fixture
def persistence(tmp_path):
    """A VideoPersistence instance rooted in a temp dir."""
    return VideoPersistence(state_dir=tmp_path, flush_interval=0.1)


def _record(task_id="video_abc", status="queued"):
    return {
        "task_id": task_id,
        "status": status,
        "prompt": "a red cube",
        "task_type": "t2va",
        "width": 1344,
        "height": 768,
        "length": 101,
        "seed": 42,
        "first_frame": None,
        "prompt_id": None,
        "progress": 0.0,
        "output_path": None,
        "error": None,
        "created_at": 1000.0,
        "completed_at": None,
    }


class TestVideoPersistence:
    """Tests for VideoPersistence write-through storage."""

    def test_create_and_get(self, persistence):
        persistence.create(_record())
        got = persistence.get("video_abc")
        assert got is not None
        assert got["prompt"] == "a red cube"
        assert got["status"] == "queued"

    def test_update_fields(self, persistence):
        persistence.create(_record())
        assert persistence.update("video_abc", status="running", prompt_id="p1")
        got = persistence.get("video_abc")
        assert got["status"] == "running"
        assert got["prompt_id"] == "p1"

    def test_update_missing_task_returns_false(self, persistence):
        assert persistence.update("nope", status="failed") is False

    def test_remove(self, persistence):
        persistence.create(_record())
        assert persistence.remove("video_abc") is True
        assert persistence.get("video_abc") is None
        assert persistence.remove("video_abc") is False

    @pytest.mark.asyncio
    async def test_flush_and_reload(self, persistence):
        persistence.create(_record(status="running"))
        persistence._dirty = True
        await persistence._flush_active()

        fresh = VideoPersistence(state_dir=tmp_path_of(persistence))
        await fresh._load_active()
        got = fresh.get("video_abc")
        assert got is not None
        assert got["status"] == "running"

    @pytest.mark.asyncio
    async def test_complete_task_moves_to_history(self, persistence):
        persistence.create(_record())
        await persistence.complete_task("video_abc", "completed")
        assert persistence.get("video_abc") is None

        history = await persistence.list_history()
        assert len(history) == 1
        assert history[0]["status"] == "completed"
        assert history[0]["completed_at"] is not None

    @pytest.mark.asyncio
    async def test_load_active_splits_done_to_history(self, persistence):
        persistence.create(_record())
        await persistence.complete_task("video_abc", "completed")

        fresh = VideoPersistence(state_dir=tmp_path_of(persistence))
        await fresh._load_active()
        assert fresh.get("video_abc") is None
        history = await fresh.list_history()
        assert len(history) == 1

    @pytest.mark.asyncio
    async def test_cleanup_history_trims(self, persistence):
        for i in range(5):
            rec = _record(task_id=f"video_{i}", status="completed")
            persistence.create(rec)
            await persistence.complete_task(f"video_{i}", "completed")

        fresh = VideoPersistence(
            state_dir=tmp_path_of(persistence), max_history_tasks=3
        )
        await fresh._cleanup_history()
        history = await fresh.list_history()
        assert len(history) == 3

    @pytest.mark.asyncio
    async def test_periodic_flush_writes_dirty(self, persistence):
        persistence.create(_record(status="running"))
        await persistence.start()
        await _wait_dirty_flush(persistence)
        await persistence.stop()

        assert persistence._active_file.exists()
        data = json.loads(persistence._active_file.read_text())
        assert "video_abc" in data["tasks"]


def tmp_path_of(persistence):
    """Return the state dir used by a persistence instance."""
    return persistence._active_file.parent


async def _wait_dirty_flush(persistence):
    """Wait until the periodic flush task persists the dirty state."""
    import asyncio
    for _ in range(20):
        if not persistence._dirty:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("dirty state was never flushed")
