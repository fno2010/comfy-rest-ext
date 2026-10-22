"""Anima Base/Turbo text-to-image workflow builder."""

from __future__ import annotations

import os
from typing import Any, Dict

from .base import BuildResult, require_asset

ANIMA_PHASE_WEIGHTS = {
    "cond": 0.08,
    "step": 0.87,
    "decode": 0.05,
    "save": 0.0,
}

DEFAULT_NEGATIVE = (
    "worst quality, low quality, score_1, score_2, score_3, artist name, "
    "blurry, jpeg artifacts, chromatic aberration"
)


def build_anima_task(task: Any) -> BuildResult:
    """Build an Anima T2I workflow from a generic GenerationTask."""
    turbo = task.model_id == "anima-turbo"
    params: Dict[str, Any] = task.params or {}

    unet = os.environ.get(
        "ANIMA_TURBO_UNET" if turbo else "ANIMA_UNET",
        "anima-turbo-v1.0.safetensors" if turbo else "anima-base-v1.0.safetensors",
    )
    text_encoder = os.environ.get("ANIMA_TE", "qwen_3_06b_base.safetensors")
    vae = os.environ.get("ANIMA_VAE", "qwen_image_vae.safetensors")
    lora = os.environ.get("ANIMA_TURBO_LORA", "").strip() if turbo else ""

    require_asset("diffusion_models", unet)
    require_asset("text_encoders", text_encoder)
    require_asset("vae", vae)
    if lora:
        require_asset("loras", lora)

    default_steps = 8 if turbo else 30
    default_cfg = 1.0 if turbo else 4.0
    steps = int(params.get("steps", default_steps))
    cfg = float(params.get("cfg", default_cfg))
    sampler = params.get("sampler") or "euler"
    scheduler = params.get("scheduler") or "simple"
    batch_size = max(1, int(params.get("n", 1)))

    negative = params.get("negative_prompt")
    if negative is None:
        negative = "" if turbo else DEFAULT_NEGATIVE

    workflow: Dict[str, Any] = {}

    def add(nid: str, class_type: str, inputs: Dict[str, Any]) -> str:
        workflow[nid] = {"class_type": class_type, "inputs": inputs}
        return nid

    add("1", "UNETLoader", {
        "unet_name": unet,
        "weight_dtype": "default",
    })
    add("2", "CLIPLoader", {
        "clip_name": text_encoder,
        "type": "stable_diffusion",
        "device": "default",
    })
    add("3", "VAELoader", {"vae_name": vae})

    model_ref = ["1", 0]
    if lora:
        add("4", "LoraLoaderModelOnly", {
            "model": model_ref,
            "lora_name": lora,
            "strength_model": float(params.get("lora_strength", 1.0)),
        })
        model_ref = ["4", 0]

    add("5", "CLIPTextEncode", {"clip": ["2", 0], "text": task.prompt})
    add("6", "CLIPTextEncode", {"clip": ["2", 0], "text": negative})
    add("7", "EmptyLatentImage", {
        "width": task.width,
        "height": task.height,
        "batch_size": batch_size,
    })
    add("8", "KSampler", {
        "model": model_ref,
        "seed": task.seed,
        "steps": steps,
        "cfg": cfg,
        "sampler_name": sampler,
        "scheduler": scheduler,
        "positive": ["5", 0],
        "negative": ["6", 0],
        "latent_image": ["7", 0],
        "denoise": 1.0,
    })
    add("9", "VAEDecode", {"samples": ["8", 0], "vae": ["3", 0]})
    add("10", "SaveImage", {
        "images": ["9", 0],
        "filename_prefix": f"image/comfy-rest-ext/{task.model_id}",
    })

    return BuildResult(
        workflow=workflow,
        output_prefix=f"image/comfy-rest-ext/{task.model_id}",
        phase_weights=dict(ANIMA_PHASE_WEIGHTS),
        step_class="KSampler",
        output_mime="image/png",
    )
