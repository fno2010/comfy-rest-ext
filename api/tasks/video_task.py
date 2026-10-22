"""Generic generation task lifecycle for video and image models.

The task runner is model-agnostic: it asks the catalog for a ModelSpec,
lets the spec build an API-format ComfyUI workflow, submits it to the
in-process PromptQueue, tracks progress, and resolves output files.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from .video_persistence import get_video_persistence
from ..catalog import get_model
from ..workflows.h3 import (
    H3_PHASE_WEIGHTS,
    build_h3_workflow,
    frames_for_seconds,
)

logger = logging.getLogger("comfy-rest-ext.video")


@dataclass
class GenerationTask:
    """State shared by video and image generation jobs."""

    task_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    prompt: str
    task_type: str  # t2va | fl2va | r2v | t2i | edit
    width: int
    height: int
    length: int
    seed: int
    model_id: str = "minimax-h3"
    modality: str = "video"
    params: Dict[str, Any] = field(default_factory=dict)
    assets: Dict[str, str] = field(default_factory=dict)
    variant: Optional[str] = None
    first_frame: Optional[str] = None
    ref_images: Optional[list] = None
    speed: Optional[Any] = None  # legacy str or AccelConfig dict
    prompt_id: Optional[str] = None
    progress: float = 0.0
    current_node: Optional[str] = None
    node_progress: float = 0.0
    elapsed: float = 0.0
    eta: Optional[float] = None
    output_path: Optional[str] = None
    output_paths: List[str] = field(default_factory=list)
    output_mime: Optional[str] = None
    error: Optional[str] = None
    created_at: float = 0.0
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    node_errors: Optional[Dict[str, Any]] = None
    done: asyncio.Event = field(
        default_factory=asyncio.Event, repr=False, compare=False
    )


# Backward-compatible name: existing callers/tests import VideoTask.
VideoTask = GenerationTask


class GenerationTaskManager:
    """Write-through in-memory task cache backed by video persistence."""

    def __init__(self, persistence=None):
        self._tasks: Dict[str, GenerationTask] = {}
        self._lock = threading.RLock()
        self._persistence = persistence

    def _persist(self, task: GenerationTask) -> None:
        if self._persistence is None:
            return
        record = _task_to_record(task)
        record.pop("task_id", None)
        self._persistence.update(task.task_id, **record)

    def create(self, task: GenerationTask) -> GenerationTask:
        with self._lock:
            self._tasks[task.task_id] = task
            if self._persistence is not None:
                self._persistence.create(_task_to_record(task))
        return task

    def get(self, task_id: str) -> Optional[GenerationTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def update(self, task_id: str, **kwargs) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            for k, v in kwargs.items():
                if hasattr(task, k):
                    setattr(task, k, v)
            self._persist(task)
            return True

    def list_active(self) -> Dict[str, GenerationTask]:
        with self._lock:
            return {
                tid: t for tid, t in self._tasks.items()
                if t.status in ("queued", "running")
            }

    def list_all(self) -> Dict[str, GenerationTask]:
        with self._lock:
            return dict(self._tasks)

    def restore(self, task: GenerationTask) -> None:
        with self._lock:
            self._tasks[task.task_id] = task


# Backward-compatible alias.
VideoTaskManager = GenerationTaskManager


def _task_to_record(task: GenerationTask) -> dict:
    """Serialize a GenerationTask to a persistence-friendly dict."""
    return {
        "task_id": task.task_id,
        "status": task.status,
        "prompt": task.prompt,
        "task_type": task.task_type,
        "width": task.width,
        "height": task.height,
        "length": task.length,
        "seed": task.seed,
        "model_id": task.model_id,
        "modality": task.modality,
        "params": dict(task.params or {}),
        "first_frame": task.first_frame,
        "ref_images": task.ref_images,
        "speed": task.speed,
        "prompt_id": task.prompt_id,
        "progress": task.progress,
        "current_node": task.current_node,
        "node_progress": task.node_progress,
        "elapsed": task.elapsed,
        "eta": task.eta,
        "output_path": task.output_path,
        "output_paths": list(task.output_paths or []),
        "output_mime": task.output_mime,
        "error": task.error,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
    }


def _record_to_task(record: dict) -> GenerationTask:
    """Deserialize a persisted dict back into a GenerationTask."""
    output_path = record.get("output_path")
    output_paths = list(record.get("output_paths") or [])
    if output_path and output_path not in output_paths:
        output_paths.insert(0, output_path)
    return GenerationTask(
        task_id=record.get("task_id", ""),
        status=record.get("status", "failed"),
        prompt=record.get("prompt", ""),
        task_type=record.get("task_type", "t2va"),
        width=record.get("width", 1344),
        height=record.get("height", 768),
        length=record.get("length", 101),
        seed=record.get("seed", 0),
        model_id=record.get("model_id", "minimax-h3"),
        modality=record.get("modality", "video"),
        params=dict(record.get("params") or {}),
        first_frame=record.get("first_frame"),
        ref_images=record.get("ref_images"),
        speed=record.get("speed"),
        prompt_id=record.get("prompt_id"),
        progress=record.get("progress", 0.0),
        current_node=record.get("current_node"),
        node_progress=record.get("node_progress", 0.0),
        elapsed=record.get("elapsed", 0.0),
        eta=record.get("eta"),
        output_path=output_path or (output_paths[0] if output_paths else None),
        output_paths=output_paths,
        output_mime=record.get("output_mime"),
        error=record.get("error"),
        created_at=record.get("created_at", 0.0),
        started_at=record.get("started_at"),
        completed_at=record.get("completed_at"),
    )


async def restore_video_tasks_from_disk() -> int:
    """Load persisted tasks into the manager, marking interrupted ones failed."""
    persistence = get_video_persistence()
    manager = get_video_manager()
    count = 0
    for record in persistence.list_active().values():
        status = record.get("status", "failed")
        if status in ("queued", "running"):
            record["status"] = "failed"
            record["error"] = "Interrupted by ComfyUI restart"
        task = _record_to_task(record)
        manager.restore(task)
        count += 1
    for record in await persistence.list_history():
        task_id = record.get("task_id")
        if manager.get(task_id) is not None:
            continue
        task = _record_to_task(record)
        manager.restore(task)
        count += 1
    return count


# Generic name for the same migration path.
restore_generation_tasks_from_disk = restore_video_tasks_from_disk


_generation_manager: Optional[GenerationTaskManager] = None


def get_video_manager() -> GenerationTaskManager:
    global _generation_manager
    if _generation_manager is None:
        _generation_manager = GenerationTaskManager(
            persistence=get_video_persistence()
        )
    return _generation_manager


def get_generation_manager() -> GenerationTaskManager:
    return get_video_manager()


def _get_prompt_queue():
    """Get the in-process PromptQueue from ComfyUI's PromptServer."""
    from server import PromptServer
    return PromptServer.instance.prompt_queue


def _resolve_output_path(value: dict) -> str:
    """Resolve a history output entry to an absolute path."""
    import folder_paths
    subfolder = value.get("subfolder", "")
    filename = value.get("filename", "")
    output_dir = folder_paths.get_output_directory()
    return os.path.join(output_dir, subfolder, filename)


def _collect_output_paths(outputs: dict) -> List[str]:
    """Collect output files from a ComfyUI history outputs mapping."""
    paths: List[str] = []
    for node_output in outputs.values():
        for value in node_output.get("images", []):
            if not isinstance(value, dict) or value.get("type") != "output":
                continue
            path = _resolve_output_path(value)
            if path not in paths:
                paths.append(path)
    return paths


# Weighted total-progress model. Only the sampler reports step-level
# progress; conditioning/decode/save phases are modeled with fixed weights.
COND_PHASE_WEIGHT = H3_PHASE_WEIGHTS["cond"]
KSAMPLER_PHASE_WEIGHT = H3_PHASE_WEIGHTS["step"]
DECODE_PHASE_WEIGHT = H3_PHASE_WEIGHTS["decode"]
SAVE_PHASE_WEIGHT = H3_PHASE_WEIGHTS["save"]
DECODE_SAVE_TAIL_ESTIMATE = 60.0  # seconds, empirical for H3 video
IMAGE_DECODE_SAVE_TAIL_ESTIMATE = 10.0

_DECODE_CLASSES = {"VAEDecode", "VAEDecodeAudio"}
_SAVE_CLASSES = {"CreateVideo", "SaveVideo", "SaveImage"}


def _class_type_of(workflow: Dict[str, Any], node_id: str) -> Optional[str]:
    node = workflow.get(node_id)
    if node:
        return node.get("class_type")
    return None


def _read_progress(
    task: GenerationTask,
    workflow: Dict[str, Any],
    phase_weights: Optional[Dict[str, float]] = None,
    step_class: str = "KSampler",
) -> Optional[Dict[str, Any]]:
    """Read real progress from ComfyUI's in-process ProgressRegistry."""
    try:
        from comfy_execution.progress import get_progress_state
    except ImportError:
        return None

    try:
        registry = get_progress_state()
    except Exception:
        return None
    if registry is None or getattr(registry, "prompt_id", None) != task.prompt_id:
        return None

    nodes = getattr(registry, "nodes", None)
    if not nodes:
        return None

    running = [
        nid for nid, st in nodes.items()
        if getattr(st.get("state"), "value", st.get("state")) == "running"
    ]
    if not running:
        return None
    node_id = running[0]
    state = nodes[node_id]
    max_value = state.get("max") or 0
    value = state.get("value") or 0
    node_progress = (value / max_value) if max_value else 0.0
    class_type = _class_type_of(workflow, node_id) or node_id

    weights = phase_weights or H3_PHASE_WEIGHTS
    cond = weights.get("cond", 0.0)
    step = weights.get("step", 0.0)
    decode = weights.get("decode", 0.0)
    save = weights.get("save", 0.0)

    if class_type == step_class:
        total = cond + step * node_progress
    elif class_type in _DECODE_CLASSES:
        total = cond + step + decode * node_progress
    elif class_type in _SAVE_CLASSES:
        total = cond + step + decode + save * node_progress
    else:
        total = cond * node_progress

    return {
        "current_node": class_type,
        "node_progress": node_progress,
        "progress": min(total, 1.0),
        "steps": (value, max_value) if class_type == step_class else None,
    }


def _estimate_eta(task: GenerationTask, progress: Dict[str, Any]) -> Optional[float]:
    """Estimate remaining seconds via step-level linear extrapolation."""
    elapsed = time.time() - (task.started_at or task.created_at)
    if elapsed <= 0 or progress["progress"] <= 0:
        return None
    steps = progress.get("steps")
    tail = (
        IMAGE_DECODE_SAVE_TAIL_ESTIMATE
        if task.modality == "image"
        else DECODE_SAVE_TAIL_ESTIMATE
    )
    if steps and steps[1] > 0 and steps[0] > 0:
        step_avg = elapsed / steps[0]
        remaining = step_avg * (steps[1] - steps[0]) + tail
        return round(remaining, 1)
    remaining = elapsed / progress["progress"] * (1 - progress["progress"])
    return round(remaining, 1)


# One generation at a time. ComfyUI executes prompts serially anyway;
# holding this gate lets us unload the previous model family before the
# next prompt is enqueued instead of letting two families thrash VRAM.
_family_gate = asyncio.Lock()
_last_family: Optional[str] = None


def _maybe_unload_family(family: str) -> None:
    global _last_family
    if os.environ.get("COMFY_REST_UNLOAD_ON_FAMILY_SWITCH", "1") == "0":
        _last_family = family
        return
    if _last_family and _last_family != family:
        try:
            import comfy.model_management as model_management
            model_management.unload_all_models()
            model_management.soft_empty_cache()
            logger.info(
                "Unloaded models for family switch: %s -> %s",
                _last_family, family,
            )
        except Exception as e:  # pragma: no cover - only inside ComfyUI
            logger.debug("Family unload skipped: %s", e)
    _last_family = family


def apply_model_spec(task: GenerationTask, spec) -> None:
    """Copy catalog-owned model settings onto a task before building.

    Config is the source of truth for assets/variant/defaults; per-request
    task.params win over model defaults.
    """
    task.assets = dict(spec.assets)
    task.variant = spec.variant
    params = dict(spec.defaults)
    params.update(task.params or {})
    task.params = params


async def _submit_generation_task_inner(task: GenerationTask) -> GenerationTask:
    """Build, validate, submit, and track one generation task."""
    from server import PromptServer

    prompt_queue = _get_prompt_queue()
    server = PromptServer.instance
    manager = get_video_manager()

    def sync_status():
        record = _task_to_record(task)
        record.pop("task_id", None)
        manager.update(task.task_id, **record)

    try:
        spec = get_model(task.model_id)
        apply_model_spec(task, spec)
        built = spec.build(task)
    except Exception as e:
        task.status = "failed"
        task.error = str(e)
        sync_status()
        task.done.set()
        return task

    if task.modality != spec.modality:
        task.status = "failed"
        task.error = (
            f"model {spec.id!r} is {spec.modality}, not {task.modality}"
        )
        sync_status()
        task.done.set()
        return task

    task.output_mime = spec.output_mime or built.output_mime

    async with _family_gate:
        try:
            _maybe_unload_family(spec.family)

            prompt_id = str(uuid.uuid4())
            task.prompt_id = prompt_id
            sync_status()

            from execution import validate_prompt
            valid = await validate_prompt(prompt_id, built.workflow, None)
            if not valid[0]:
                task.status = "failed"
                task.error = valid[1]
                task.node_errors = valid[3]
                sync_status()
                return task
        except Exception as e:
            task.status = "failed"
            task.error = f"validate_prompt failed: {e}"
            sync_status()
            return task

        task.status = "running"
        number = getattr(server, "number", 0)
        outputs_to_execute = valid[2] if valid[2] is not None else []
        prompt_queue.put((
            number, prompt_id, built.workflow, {},
            outputs_to_execute, {},
        ))
        task.started_at = time.time()
        sync_status()

        deadline = time.time() + spec.timeout_s
        while time.time() < deadline:
            await asyncio.sleep(1.0)
            if task.status == "running":
                progress = _read_progress(
                    task, built.workflow, built.phase_weights, built.step_class
                )
                if progress is not None:
                    task.progress = progress["progress"]
                    task.current_node = progress["current_node"]
                    task.node_progress = progress["node_progress"]
                    task.elapsed = time.time() - task.started_at
                    task.eta = _estimate_eta(task, progress)
                    sync_status()

            history = prompt_queue.get_history()
            if prompt_id not in history:
                continue
            entry = history[prompt_id]
            status = entry.get("status", {})
            status_str = status.get("status_str", "success")
            outputs = entry.get("outputs", {})
            if status_str == "error":
                task.status = "failed"
                task.error = str(status.get("messages", []))
                sync_status()
                return task

            paths = _collect_output_paths(outputs)
            if paths:
                task.output_paths = paths
                task.output_path = paths[0]
                task.status = "completed"
                task.completed_at = time.time()
                task.progress = 1.0
                task.eta = None
                sync_status()
                return task

            if task.modality == "image":
                task.status = "failed"
                task.error = "generation completed without an image output"
                sync_status()
                return task

            task.status = "completed"
            task.completed_at = time.time()
            task.progress = 1.0
            task.eta = None
            sync_status()
            return task

        task.status = "failed"
        task.error = "Timeout waiting for generation"
        sync_status()
        return task


async def submit_generation_task(task: GenerationTask) -> GenerationTask:
    """Run one generation task and always signal completion."""
    try:
        return await _submit_generation_task_inner(task)
    finally:
        task.done.set()


async def submit_video_task(task: GenerationTask) -> GenerationTask:
    """Backward-compatible wrapper for the generic submit path."""
    return await submit_generation_task(task)
