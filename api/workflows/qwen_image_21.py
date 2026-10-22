"""Qwen-Image 2.1 text-to-image / edit workflow builder.

Requires ComfyUI >= 0.37.0: TextEncodeQwenImage21, QwenImage21Cache and
the QwenImage21 latent format were introduced with Qwen-Image 2.1.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from .base import BuildResult, require_asset, resolve_asset

QWEN_PHASE_WEIGHTS = {
    "cond": 0.10,
    "step": 0.85,
    "decode": 0.05,
    "save": 0.0,
}

# Fixed schedule from the official Qwen-Image 2.1 Turbo template. The
# distilled checkpoint is trained around this 8-step sigma list; do not
# replace it with a plain steps=8 KSampler.
QWEN_TURBO_SIGMAS = (
    "1.0, 0.978453, 0.95418, 0.926626, 0.89508, "
    "0.845148, 0.704534, 0.414568, 0"
)


def _add(workflow: Dict[str, Any], nid: str, class_type: str,
         inputs: Dict[str, Any]) -> str:
    workflow[nid] = {"class_type": class_type, "inputs": inputs}
    return nid


def _next_id(state: List[int]) -> str:
    state[0] += 1
    return str(state[0])


def _loaders(
    workflow: Dict[str, Any],
    unet: str,
    text_encoder: str,
    vae: str,
) -> None:
    _add(workflow, "1", "UNETLoader", {
        "unet_name": unet,
        "weight_dtype": "default",
    })
    _add(workflow, "2", "CLIPLoader", {
        "clip_name": text_encoder,
        "type": "qwen_image",
        "device": "default",
    })
    _add(workflow, "3", "VAELoader", {"vae_name": vae})


def _maybe_cache(
    workflow: Dict[str, Any],
    state: List[int],
    model_ref: List[Any],
) -> List[Any]:
    """Insert the experimental prefix-KV cache node unless disabled."""
    if os.environ.get("QWEN_IMAGE_21_CACHE", "1") == "0":
        return model_ref
    nid = _next_id(state)
    _add(workflow, nid, "QwenImage21Cache", {
        "model": model_ref,
        "device": os.environ.get("QWEN_IMAGE_21_CACHE_DEVICE", "auto"),
        "dtype": os.environ.get("QWEN_IMAGE_21_CACHE_DTYPE", "default"),
    })
    return [nid, 0]


def _text_encode(
    workflow: Dict[str, Any],
    state: List[int],
    prompt: str,
    negative: str,
    resolution: int,
    ref_images: Optional[List[str]] = None,
) -> str:
    nid = _next_id(state)
    inputs: Dict[str, Any] = {
        "clip": ["2", 0],
        "prompt": prompt,
        "negative_prompt": negative,
        "resolution": int(resolution),
    }
    if ref_images:
        inputs["vae"] = ["3", 0]
        for index, filename in enumerate(ref_images, start=1):
            load_id = _next_id(state)
            _add(workflow, load_id, "LoadImage", {"image": filename})
            inputs[f"images.image_{index}"] = [load_id, 0]
    _add(workflow, nid, "TextEncodeQwenImage21", inputs)
    return nid


def _add_sampler(
    workflow: Dict[str, Any],
    state: List[int],
    *,
    model_ref: List[Any],
    positive: List[Any],
    negative: List[Any],
    latent: List[Any],
    turbo: bool,
    seed: int,
    steps: int,
    cfg: float,
    sampler: str,
    scheduler: str,
) -> Tuple[List[Any], str]:
    """Add either a KSampler or the advanced ManualSigmas sampler path."""
    if not turbo:
        nid = _next_id(state)
        _add(workflow, nid, "KSampler", {
            "model": model_ref,
            "seed": seed,
            "steps": steps,
            "cfg": cfg,
            "sampler_name": sampler,
            "scheduler": scheduler,
            "positive": positive,
            "negative": negative,
            "latent_image": latent,
            "denoise": 1.0,
        })
        return [nid, 0], "KSampler"

    noise_id = _next_id(state)
    _add(workflow, noise_id, "RandomNoise", {"noise_seed": seed})
    guider_id = _next_id(state)
    _add(workflow, guider_id, "CFGGuider", {
        "model": model_ref,
        "positive": positive,
        "negative": negative,
        "cfg": cfg,
    })
    select_id = _next_id(state)
    _add(workflow, select_id, "KSamplerSelect", {"sampler_name": sampler})
    sigmas_id = _next_id(state)
    _add(workflow, sigmas_id, "ManualSigmas", {"sigmas": QWEN_TURBO_SIGMAS})
    sampler_id = _next_id(state)
    _add(workflow, sampler_id, "SamplerCustomAdvanced", {
        "noise": [noise_id, 0],
        "guider": [guider_id, 0],
        "sampler": [select_id, 0],
        "sigmas": [sigmas_id, 0],
        "latent_image": latent,
    })
    return [sampler_id, 0], "SamplerCustomAdvanced"


def build_qwen_image_task(task: Any) -> BuildResult:
    """Build a Qwen-Image 2.1 T2I or edit workflow."""
    turbo = getattr(task, "variant", "base") == "turbo" or task.model_id.endswith("-turbo")
    params: Dict[str, Any] = task.params or {}
    is_edit = task.task_type == "edit"

    unet = resolve_asset(
        task,
        "turbo_unet" if turbo else "unet",
        "QWEN_IMAGE_21_TURBO_UNET" if turbo else "QWEN_IMAGE_21_UNET",
        "qwen_image_2.1_turbo_int8_convrot.safetensors"
        if turbo else "qwen_image_2.1_int8_convrot.safetensors",
    )
    text_encoder = resolve_asset(
        task, "text_encoder", "QWEN_IMAGE_21_TE",
        "qwen3vl_8b_int8_convrot.safetensors",
    )
    vae = resolve_asset(
        task, "vae", "QWEN_IMAGE_21_VAE",
        "qwen_image_2.1_vae_bf16.safetensors",
    )

    require_asset("diffusion_models", unet)
    require_asset("text_encoders", text_encoder)
    require_asset("vae", vae)

    steps = int(params.get("steps", 8 if turbo else 25))
    cfg = float(params.get("cfg", 1.0))
    sampler = params.get("sampler") or "euler"
    scheduler = params.get("scheduler") or "simple"
    negative = params.get("negative_prompt", "") or ""
    batch_size = max(1, int(params.get("n", 1)))
    resolution = int(params.get("resolution", 1024))

    workflow: Dict[str, Any] = {}
    _loaders(workflow, unet, text_encoder, vae)
    state = [3]

    if is_edit:
        ref_images = list(task.ref_images or [])
        if not ref_images:
            raise ValueError("Qwen-Image edit requires at least one reference image")
        if len(ref_images) > 16:
            raise ValueError("Qwen-Image edit supports at most 16 reference images")

    te_id = _text_encode(
        workflow,
        state,
        task.prompt,
        negative,
        resolution,
        ref_images=list(task.ref_images or []) if is_edit else None,
    )

    if is_edit:
        latent: List[Any] = [te_id, 2]
        if batch_size > 1:
            repeat_id = _next_id(state)
            _add(workflow, repeat_id, "RepeatLatentBatch", {
                "samples": latent,
                "amount": batch_size,
            })
            latent = [repeat_id, 0]
    else:
        latent_id = _next_id(state)
        _add(workflow, latent_id, "EmptyLatentImage", {
            "width": task.width,
            "height": task.height,
            "batch_size": batch_size,
        })
        latent = [latent_id, 0]

    model_ref = _maybe_cache(workflow, state, ["1", 0])
    samples, step_class = _add_sampler(
        workflow,
        state,
        model_ref=model_ref,
        positive=[te_id, 0],
        negative=[te_id, 1],
        latent=latent,
        turbo=turbo,
        seed=task.seed,
        steps=steps,
        cfg=cfg,
        sampler=sampler,
        scheduler=scheduler,
    )

    decode_id = _next_id(state)
    _add(workflow, decode_id, "VAEDecode", {
        "samples": samples,
        "vae": ["3", 0],
    })
    save_id = _next_id(state)
    prefix = f"image/comfy-rest-ext/{task.model_id}_{(task.task_id or 'task')[:8]}"
    if is_edit:
        prefix += "_edit"
    _add(workflow, save_id, "SaveImage", {
        "images": [decode_id, 0],
        "filename_prefix": prefix,
    })

    return BuildResult(
        workflow=workflow,
        output_prefix=prefix,
        phase_weights=dict(QWEN_PHASE_WEIGHTS),
        step_class=step_class,
        output_mime="image/png",
    )
