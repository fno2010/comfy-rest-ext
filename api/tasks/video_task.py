"""
Video generation task implementation for MiniMax-H3.

Submits an API-format ComfyUI workflow (T2V/I2V) to the in-process
PromptQueue, polls history until completion, and resolves the output
video file path for downstream serving.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional

from .video_persistence import get_video_persistence

logger = logging.getLogger("comfy-rest-ext.video")


@dataclass
class VideoTask:
    """Video generation task state."""
    task_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    prompt: str
    task_type: str  # t2va | fl2va | r2v
    width: int
    height: int
    length: int
    seed: int
    first_frame: Optional[str] = None
    ref_images: Optional[list] = None
    prompt_id: Optional[str] = None
    progress: float = 0.0
    output_path: Optional[str] = None
    error: Optional[str] = None
    created_at: float = 0.0
    completed_at: Optional[float] = None
    node_errors: Optional[Dict[str, Any]] = None


def frames_for_seconds(seconds: float) -> int:
    """Convert seconds to H3 frame count on the 17k+5 grid at 24 fps.

    H3 snapshots durations to the model's 17-frame-per-block grid:
    frame_count = 17 * k + 5. 124 frames ≈ 5s (trained range ~124-362).
    """
    fps = 24.0
    target = int(round(seconds * fps))
    # Snap UP to nearest 17k+5 >= target, clamped to trained range
    target = max(target, 5)
    k = max(0, (target - 5 + 16) // 17)
    frames = 17 * k + 5
    return min(frames, 362)


def build_h3_workflow(
    *,
    prompt: str,
    width: int,
    height: int,
    length: int,
    seed: int,
    first_frame_path: Optional[str] = None,
    ref_images: Optional[list] = None,
    diffusion_model: str = "minimax_h3_fl2va_pruned_nvfp4.safetensors",
    text_encoder: str = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    video_vae: str = "minimax_h3_video_vae_fp16.safetensors",
    audio_vae: str = "minimax_h3_audio_vae_fp32.safetensors",
    ref2va_model: str = "minimax_h3_ref2va_pruned_nvfp4.safetensors",
) -> Dict[str, Any]:
    """Build the API-format workflow for MiniMax-H3 T2V/I2V/R2V.

    Node graph:
      UNETLoader -> MiniMaxH3SigmaShift -> KSampler -> SaveVideo
      CLIPLoader -> conditioning node (cond+latent) -> KSampler
      VAELoader (video) -> VAE decode
      VAELoader (audio) -> audio decode (inside pipeline latent)

    R2V (ref_images given) uses the ref2va checkpoint and the
    MiniMaxH3ReferenceToVideo node; the prompt references images via
    <Picture N> tags in insertion order.
    """
    workflow: Dict[str, Any] = {}

    def add(nid: str, class_type: str, inputs: Dict[str, Any]) -> str:
        workflow[nid] = {"class_type": class_type, "inputs": inputs}
        return nid

    is_r2v = bool(ref_images)
    unet = ref2va_model if is_r2v else diffusion_model
    add("1", "UNETLoader", {
        "unet_name": unet,
        "weight_dtype": "default",
    })
    add("2", "CLIPLoader", {
        "clip_name": text_encoder,
        "type": "minimax",
    })
    add("3", "VAELoader", {"vae_name": video_vae})
    add("4", "VAELoader", {"vae_name": audio_vae})

    add("5", "MiniMaxH3SigmaShift", {
        "model": ["1", 0],
        "shift_video": 12.0,
        "shift_audio": 3.0,
    })

    nid = 6
    if is_r2v:
        cond_inputs: Dict[str, Any] = {
            "clip": ["2", 0],
            "vae": ["3", 0],
            "audio_vae": ["4", 0],
            "prompt": prompt,
            "width": width,
            "height": height,
            "length": length,
            "ref_image_size": "match",
        }
        ref_slots: Dict[str, Any] = {}
        for i, ref in enumerate(ref_images or []):
            add(str(nid), "LoadImage", {"image": ref})
            ref_slots[f"ref_image_{i}"] = [str(nid), 0]
            nid += 1
        cond_inputs["ref_images"] = ref_slots
        cond_node = str(nid)
        add(cond_node, "MiniMaxH3ReferenceToVideo", cond_inputs)
    else:
        cond_inputs = {
            "clip": ["2", 0],
            "vae": ["3", 0],
            "prompt": prompt,
            "width": width,
            "height": height,
            "length": length,
        }
        if first_frame_path:
            cond_inputs["first_frame"] = [str(nid), 0]
            add(str(nid), "LoadImage", {"image": first_frame_path})
            nid += 1
        cond_node = str(nid)
        add(cond_node, "MiniMaxH3ImageToVideo", cond_inputs)

    nid += 1
    sampler_id = str(nid)
    add(sampler_id, "KSampler", {
        "model": ["5", 0],
        "seed": seed,
        "steps": 20,
        "cfg": 1.0,
        "sampler_name": "euler",
        "scheduler": "simple",
        "positive": [cond_node, 0],
        "negative": [cond_node, 0],
        "latent_image": [cond_node, 1],
        "denoise": 1.0,
    })

    nid += 1
    vae_decode_id = str(nid)
    add(vae_decode_id, "VAEDecode", {"samples": [sampler_id, 0], "vae": ["3", 0]})
    nid += 1
    vae_decode_audio_id = str(nid)
    add(vae_decode_audio_id, "VAEDecodeAudio", {"samples": [sampler_id, 0], "vae": ["4", 0]})

    nid += 1
    create_video_id = str(nid)
    add(create_video_id, "CreateVideo", {
        "images": [vae_decode_id, 0],
        "fps": 24,
        "audio": [vae_decode_audio_id, 0],
        "bit_depth": 8,
    })

    nid += 1
    add(str(nid), "SaveVideo", {
        "video": [create_video_id, 0],
        "filename_prefix": "video/comfy-rest-ext",
        "format": "auto",
        "codec": "auto",
    })

    return workflow


class VideoTaskManager:
    """Manages video generation tasks with persistence.

    In-memory dict is a write-through cache: every create/update
    mutates the persisted record via VideoPersistence so tasks survive
    ComfyUI restarts.
    """

    def __init__(self, persistence=None):
        self._tasks: Dict[str, VideoTask] = {}
        self._lock = threading.RLock()
        self._persistence = persistence

    def _persist(self, task: VideoTask) -> None:
        if self._persistence is None:
            return
        record = _task_to_record(task)
        record.pop("task_id", None)
        self._persistence.update(task.task_id, **record)

    def create(self, task: VideoTask) -> VideoTask:
        with self._lock:
            self._tasks[task.task_id] = task
            if self._persistence is not None:
                self._persistence.create(_task_to_record(task))
        return task

    def get(self, task_id: str) -> Optional[VideoTask]:
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

    def list_active(self) -> Dict[str, VideoTask]:
        with self._lock:
            return {
                tid: t for tid, t in self._tasks.items()
                if t.status in ("queued", "running")
            }

    def list_all(self) -> Dict[str, VideoTask]:
        with self._lock:
            return dict(self._tasks)

    def restore(self, task: VideoTask) -> None:
        """Load a task back into memory from persisted state."""
        with self._lock:
            self._tasks[task.task_id] = task


def _task_to_record(task: VideoTask) -> dict:
    """Serialize a VideoTask to a persistence-friendly dict."""
    return {
        "task_id": task.task_id,
        "status": task.status,
        "prompt": task.prompt,
        "task_type": task.task_type,
        "width": task.width,
        "height": task.height,
        "length": task.length,
        "seed": task.seed,
        "first_frame": task.first_frame,
        "ref_images": task.ref_images,
        "prompt_id": task.prompt_id,
        "progress": task.progress,
        "output_path": task.output_path,
        "error": task.error,
        "created_at": task.created_at,
        "completed_at": task.completed_at,
    }


def _record_to_task(record: dict) -> VideoTask:
    """Deserialize a persisted dict back into a VideoTask."""
    return VideoTask(
        task_id=record.get("task_id", ""),
        status=record.get("status", "failed"),
        prompt=record.get("prompt", ""),
        task_type=record.get("task_type", "t2va"),
        width=record.get("width", 1344),
        height=record.get("height", 768),
        length=record.get("length", 101),
        seed=record.get("seed", 0),
        first_frame=record.get("first_frame"),
        ref_images=record.get("ref_images"),
        prompt_id=record.get("prompt_id"),
        progress=record.get("progress", 0.0),
        output_path=record.get("output_path"),
        error=record.get("error"),
        created_at=record.get("created_at", 0.0),
        completed_at=record.get("completed_at"),
    )


async def restore_video_tasks_from_disk() -> int:
    """Load persisted tasks into the manager.

    Completed/failed tasks from history are restored as-is. Tasks that
    were queued/running when ComfyUI stopped are marked failed because
    their in-flight prompt is gone (PromptQueue history is memory-only).

    Returns the number of tasks restored.
    """
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


_video_manager: Optional[VideoTaskManager] = None


def get_video_manager() -> VideoTaskManager:
    global _video_manager
    if _video_manager is None:
        _video_manager = VideoTaskManager(persistence=get_video_persistence())
    return _video_manager


def _get_prompt_queue():
    """Get the in-process PromptQueue from ComfyUI's PromptServer."""
    from server import PromptServer
    return PromptServer.instance.prompt_queue


def _resolve_output_path(value: dict) -> str:
    """Resolve a history output entry to an absolute path."""
    import os
    import folder_paths
    subfolder = value.get("subfolder", "")
    filename = value.get("filename", "")
    output_dir = folder_paths.get_output_directory()
    return os.path.join(output_dir, subfolder, filename)


def comfyui_view_url(output_path: Optional[str]) -> Optional[str]:
    """Build a ComfyUI /view URL for a completed output file."""
    if not output_path:
        return None
    import os
    from urllib.parse import urlencode
    import folder_paths
    output_dir = folder_paths.get_output_directory()
    rel = os.path.relpath(output_path, output_dir)
    parts = rel.split(os.sep)
    filename = parts[-1]
    subfolder = os.sep.join(parts[:-1]) if len(parts) > 1 else ""
    params = urlencode({"filename": filename, "type": "output", "subfolder": subfolder})
    return f"/view?{params}"


async def submit_video_task(task: VideoTask) -> VideoTask:
    """Submit the task to ComfyUI's in-process queue and track until done."""
    from server import PromptServer

    prompt_queue = _get_prompt_queue()
    server = PromptServer.instance
    manager = get_video_manager()

    def sync_status():
        record = _task_to_record(task)
        record.pop("task_id", None)
        manager.update(task.task_id, **record)

    workflow = build_h3_workflow(
        prompt=task.prompt,
        width=task.width,
        height=task.height,
        length=task.length,
        seed=task.seed,
        first_frame_path=task.first_frame,
        ref_images=task.ref_images,
    )

    prompt_id = str(uuid.uuid4())
    task.prompt_id = prompt_id
    sync_status()

    try:
        from execution import validate_prompt
        valid = await validate_prompt(prompt_id, workflow, None)
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
    prompt_queue.put((number, prompt_id, workflow, {}, outputs_to_execute, {}))
    sync_status()

    deadline = time.time() + 3600
    while time.time() < deadline:
        await asyncio.sleep(1.0)
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
        for node_id, out in outputs.items():
            for value in out.get("images", []):
                if value.get("type") == "output":
                    task.output_path = _resolve_output_path(value)
                    task.status = "completed"
                    task.completed_at = time.time()
                    task.progress = 1.0
                    sync_status()
                    return task
        task.status = "completed"
        task.completed_at = time.time()
        task.progress = 1.0
        sync_status()
        return task

    task.status = "failed"
    task.error = "Timeout waiting for video generation"
    sync_status()
    return task
