"""
Video generation task implementation for MiniMax-H3.

Submits an API-format ComfyUI workflow (T2V/I2V) to the in-process
PromptQueue, polls history until completion, and resolves the output
video file path for downstream serving.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional

logger = logging.getLogger("comfy-rest-ext.video")


@dataclass
class VideoTask:
    """Video generation task state."""
    task_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    prompt: str
    task_type: str  # t2va | fl2va
    width: int
    height: int
    length: int
    seed: int
    first_frame: Optional[str] = None
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
    diffusion_model: str = "minimax_h3_fl2va_pruned_nvfp4.safetensors",
    text_encoder: str = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    video_vae: str = "minimax_h3_video_vae_fp16.safetensors",
    audio_vae: str = "minimax_h3_audio_vae_fp32.safetensors",
) -> Dict[str, Any]:
    """Build the API-format workflow for MiniMax-H3 T2V/I2V.

    Node graph:
      UNETLoader -> MiniMaxH3SigmaShift -> KSampler -> SaveVideo
      CLIPLoader -> MiniMaxH3ImageToVideo (cond+latent) -> KSampler
      VAELoader (video) -> VAE decode
      VAELoader (audio) -> audio decode (inside pipeline latent)
    """
    workflow: Dict[str, Any] = {}

    def add(nid: str, class_type: str, inputs: Dict[str, Any]) -> str:
        workflow[nid] = {"class_type": class_type, "inputs": inputs}
        return nid

    add("1", "UNETLoader", {
        "unet_name": diffusion_model,
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

    cond_inputs: Dict[str, Any] = {
        "clip": ["2", 0],
        "vae": ["3", 0],
        "prompt": prompt,
        "width": width,
        "height": height,
        "length": length,
    }
    if first_frame_path:
        cond_inputs["first_frame"] = ["6", 0]
        add("6", "LoadImage", {"image": first_frame_path})
    add("7", "MiniMaxH3ImageToVideo", cond_inputs)

    add("8", "KSampler", {
        "model": ["5", 0],
        "seed": seed,
        "steps": 20,
        "cfg": 1.0,
        "sampler_name": "euler",
        "scheduler": "simple",
        "positive": ["7", 0],
        "negative": ["7", 0],
        "latent_image": ["7", 1],
        "denoise": 1.0,
    })

    add("10", "VAEDecode", {"samples": ["8", 0], "vae": ["3", 0]})
    add("11", "VAEDecodeAudio", {"samples": ["8", 0], "vae": ["4", 0]})

    add("12", "CreateVideo", {
        "images": ["10", 0],
        "fps": 24,
        "audio": ["11", 0],
        "bit_depth": 8,
    })

    add("9", "SaveVideo", {
        "video": ["12", 0],
        "filename_prefix": "video/comfy-rest-ext",
        "format": "auto",
        "codec": "auto",
    })

    return workflow


class VideoTaskManager:
    """Manages video generation tasks in-process."""

    def __init__(self):
        self._tasks: Dict[str, VideoTask] = {}
        self._lock = threading.RLock()

    def create(self, task: VideoTask) -> VideoTask:
        with self._lock:
            self._tasks[task.task_id] = task
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
            return True

    def list_active(self) -> Dict[str, VideoTask]:
        with self._lock:
            return {
                tid: t for tid, t in self._tasks.items()
                if t.status in ("queued", "running")
            }


_video_manager: Optional[VideoTaskManager] = None


def get_video_manager() -> VideoTaskManager:
    global _video_manager
    if _video_manager is None:
        _video_manager = VideoTaskManager()
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


async def submit_video_task(task: VideoTask) -> VideoTask:
    """Submit the task to ComfyUI's in-process queue and track until done."""
    from server import PromptServer

    prompt_queue = _get_prompt_queue()
    server = PromptServer.instance

    workflow = build_h3_workflow(
        prompt=task.prompt,
        width=task.width,
        height=task.height,
        length=task.length,
        seed=task.seed,
        first_frame_path=task.first_frame,
    )

    prompt_id = str(uuid.uuid4())
    task.prompt_id = prompt_id

    try:
        from execution import validate_prompt
        valid = await validate_prompt(prompt_id, workflow, None)
        if not valid[0]:
            task.status = "failed"
            task.error = valid[1]
            task.node_errors = valid[3]
            return task
    except Exception as e:
        task.status = "failed"
        task.error = f"validate_prompt failed: {e}"
        return task

    task.status = "running"
    number = getattr(server, "number", 0)
    outputs_to_execute = valid[2] if valid[2] is not None else []
    prompt_queue.put((number, prompt_id, workflow, {}, outputs_to_execute, {}))

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
            return task
        for node_id, out in outputs.items():
            for value in out.get("images", []):
                if value.get("type") == "output":
                    task.output_path = _resolve_output_path(value)
                    task.status = "completed"
                    task.completed_at = time.time()
                    task.progress = 1.0
                    return task
        # completed but no video output yet
        task.status = "completed"
        task.completed_at = time.time()
        task.progress = 1.0
        return task

    task.status = "failed"
    task.error = "Timeout waiting for video generation"
    return task
