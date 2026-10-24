"""MiniMax-H3 video workflow builder (T2V / I2V / R2V)."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from ..accel import (
    DASIWA_MAX_FRAMES,
    AccelConfig,
    apply_accel_layers,
    check_config_available,
    parse_accel_config,
    resolve_method_params,
    resolve_steps,
)
from .base import BuildResult, require_asset, resolve_asset

H3_PHASE_WEIGHTS = {
    "cond": 0.05,
    "step": 0.80,
    "decode": 0.08,
    "save": 0.07,
}


def frames_for_seconds(seconds: float) -> int:
    """Convert seconds to H3 frame count on the 17k+5 grid at 24 fps.

    H3 snapshots durations to the model's 17-frame-per-block grid:
    frame_count = 17 * k + 5. 124 frames ≈ 5s (trained range ~124-362).
    """
    fps = 24.0
    target = int(round(seconds * fps))
    target = max(target, 5)
    k = max(0, (target - 5 + 16) // 17)
    frames = 17 * k + 5
    return min(frames, 362)


def _apply_accel_layers(workflow, add, model_ref: list, accel, turbo_lora: str) -> list:
    """Apply acceleration layers in registry/dimension order."""
    return apply_accel_layers(
        workflow, add, model_ref, accel,
        context={"turbo_lora": turbo_lora},
    )


def build_h3_workflow(
    *,
    prompt: str,
    width: int,
    height: int,
    length: int,
    seed: int,
    first_frame_path: Optional[str] = None,
    last_frame_path: Optional[str] = None,
    ref_images: Optional[list] = None,
    ref_videos: Optional[list] = None,
    diffusion_model: str = "minimax_h3_fl2va_pruned_nvfp4.safetensors",
    text_encoder: str = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    video_vae: str = "minimax_h3_video_vae_fp16.safetensors",
    audio_vae: str = "minimax_h3_audio_vae_fp32.safetensors",
    ref2va_model: str = "minimax_h3_ref2va_pruned_nvfp4.safetensors",
    sampler_name: str = "euler",
    scheduler: str = "simple",
    steps: Optional[int] = None,
    te_speed: bool = False,
    sol_stack: bool = False,
    accel: Optional[AccelConfig] = None,
    ref_image_size: str = "match",
    turbo_lora: Optional[str] = None,
    filename_prefix: str = "video/comfy-rest-ext",
) -> Dict[str, Any]:
    """Build the API-format workflow for MiniMax-H3 T2V/I2V/R2V."""
    if accel is None:
        accel = AccelConfig(steps=20 if steps is None else steps)
        if te_speed:
            accel.block_cache = "te-speed"
        if sol_stack:
            accel.block_cache = "fbc"
            accel.attention = "sol"
    if steps is None:
        steps = accel.steps
    if turbo_lora is None:
        turbo_lora = (
            resolve_method_params(accel, "turbo").get("lora_name")
            or "minimax_h3_turbo_v4_step600_ema.safetensors"
        )

    workflow: Dict[str, Any] = {}

    def add(nid: str, class_type: str, inputs: Dict[str, Any]) -> str:
        workflow[nid] = {"class_type": class_type, "inputs": inputs}
        return nid

    is_ref = bool(ref_images or ref_videos)
    unet = ref2va_model if is_ref else diffusion_model
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

    model_out = _apply_accel_layers(workflow, add, ["1", 0], accel, turbo_lora)

    turbo_params = resolve_method_params(accel, "turbo")
    add("5", "MiniMaxH3SigmaShift", {
        "model": model_out,
        "shift_video": turbo_params.get("shift_video", 12.0),
        "shift_audio": turbo_params.get("shift_audio", 3.0),
    })

    nid = 6
    if is_ref:
        cond_inputs: Dict[str, Any] = {
            "clip": ["2", 0],
            "vae": ["3", 0],
            "audio_vae": ["4", 0],
            "prompt": prompt,
            "width": width,
            "height": height,
            "length": length,
            "ref_image_size": ref_image_size,
        }
        ref_slots: Dict[str, Any] = {}
        for i, ref in enumerate(ref_images or []):
            add(str(nid), "LoadImage", {"image": ref})
            ref_slots[f"ref_images.ref_image_{i}"] = [str(nid), 0]
            nid += 1
        cond_inputs.update(ref_slots)
        audio_slots: Dict[str, Any] = {}
        for i, ref in enumerate(ref_videos or []):
            load_id = str(nid)
            add(load_id, "LoadVideo", {"file": ref})
            nid += 1
            components_id = str(nid)
            add(components_id, "GetVideoComponents", {"video": [load_id, 0]})
            nid += 1
            ref_slots[f"ref_videos.ref_video_{i}"] = [components_id, 0]
            audio_slots[f"ref_video_audios.ref_video_audio_{i}"] = [components_id, 1]
        cond_inputs.update(ref_slots)
        cond_inputs.update(audio_slots)
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
        if last_frame_path:
            cond_inputs["last_frame"] = [str(nid), 0]
            add(str(nid), "LoadImage", {"image": last_frame_path})
            nid += 1
        cond_node = str(nid)
        add(cond_node, "MiniMaxH3ImageToVideo", cond_inputs)

    nid += 1
    sampler_id = str(nid)
    add(sampler_id, "KSampler", {
        "model": ["5", 0],
        "seed": seed,
        "steps": steps,
        "cfg": 1.0,
        "sampler_name": sampler_name,
        "scheduler": scheduler,
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
        "filename_prefix": filename_prefix,
        "format": "auto",
        "codec": "auto",
    })

    return workflow


def _accel_layer_snapshot(accel_cfg, layer: str, **extra: Any) -> Dict[str, Any]:
    method = getattr(accel_cfg, layer)
    if method in (None, "none"):
        return {"method": "none", "params": {}}
    params = resolve_method_params(accel_cfg, layer)
    params.update(extra)
    return {"method": method, "params": params}


def build_h3_task(task: Any) -> BuildResult:
    """Build an H3 workflow from a generic GenerationTask."""
    accel_cfg = parse_accel_config(task.speed)
    missing_node = check_config_available(accel_cfg)
    if missing_node:
        raise ValueError(missing_node)

    params = task.params or {}
    steps = resolve_steps(
        accel_cfg, task.speed, os.environ.get("H3_STEPS"), params.get("steps")
    )

    is_ref = bool(task.ref_images or task.ref_videos)
    unet = resolve_asset(
        task,
        "ref2va" if is_ref else "unet",
        "H3_REF2VA_MODEL" if is_ref else "H3_UNET",
        "minimax_h3_ref2va_pruned_nvfp4.safetensors"
        if is_ref else "minimax_h3_fl2va_pruned_nvfp4.safetensors",
    )
    text_encoder = resolve_asset(
        task, "text_encoder", "H3_TEXT_ENCODER",
        "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    )
    video_vae = resolve_asset(
        task, "video_vae", "H3_VIDEO_VAE",
        "minimax_h3_video_vae_fp16.safetensors",
    )
    audio_vae = resolve_asset(
        task, "audio_vae", "H3_AUDIO_VAE",
        "minimax_h3_audio_vae_fp32.safetensors",
    )
    turbo_params = resolve_method_params(accel_cfg, "turbo")
    turbo_lora = resolve_asset(
        task, "turbo_lora", "H3_TURBO_LORA",
        turbo_params.get("lora_name")
        or "minimax_h3_turbo_v4_step600_ema.safetensors",
    )

    require_asset("diffusion_models", unet)
    require_asset("text_encoders", text_encoder)
    require_asset("vae", video_vae)
    require_asset("vae", audio_vae)
    if accel_cfg.turbo != "none":
        require_asset("loras", turbo_lora)

    prefix = f"video/comfy-rest-ext_{(task.task_id or 'task')[:8]}"
    sampler_name = (
        turbo_params.get("sampler")
        or os.environ.get("H3_SAMPLER")
        or params.get("sampler")
        or "euler"
    )
    scheduler_name = (
        turbo_params.get("scheduler")
        or os.environ.get("H3_SCHEDULER")
        or params.get("scheduler")
        or "simple"
    )
    ref_image_size = (
        os.environ.get("H3_REF_SIZE")
        or params.get("ref_image_size")
        or "match"
    )
    shift_video = turbo_params.get("shift_video", 12.0)
    shift_audio = turbo_params.get("shift_audio", 3.0)

    workflow = build_h3_workflow(
        prompt=task.prompt,
        width=task.width,
        height=task.height,
        length=task.length,
        seed=task.seed,
        first_frame_path=task.first_frame,
        last_frame_path=task.last_frame,
        ref_images=task.ref_images,
        ref_videos=task.ref_videos,
        diffusion_model=unet,
        text_encoder=text_encoder,
        video_vae=video_vae,
        audio_vae=audio_vae,
        ref2va_model=unet,
        sampler_name=sampler_name,
        scheduler=scheduler_name,
        steps=steps,
        accel=accel_cfg,
        ref_image_size=ref_image_size,
        turbo_lora=turbo_lora,
        filename_prefix=prefix,
    )

    task.acceleration = {
        "steps": steps,
        "sampler": sampler_name,
        "scheduler": scheduler_name,
        "shift_video": shift_video,
        "shift_audio": shift_audio,
        "ref_image_size": ref_image_size,
        "turbo": _accel_layer_snapshot(
            accel_cfg, "turbo", lora_name=turbo_lora
        ),
        "attention": _accel_layer_snapshot(accel_cfg, "attention"),
        "block_cache": _accel_layer_snapshot(accel_cfg, "block_cache"),
        "step_cache": _accel_layer_snapshot(accel_cfg, "step_cache"),
        "vae_decode": {"method": accel_cfg.vae_decode, "applied": False},
        "precision": {
            "values": dict(accel_cfg.precision),
            "applied": False,
        },
    }

    return BuildResult(
        workflow=workflow,
        output_prefix=prefix,
        phase_weights=dict(H3_PHASE_WEIGHTS),
        step_class="KSampler",
        output_mime="video/mp4",
    )
