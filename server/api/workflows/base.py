"""Shared types for model-specific ComfyUI workflow builders."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict


def resolve_asset(task: Any, key: str, env_key: str, default: str) -> str:
    """Resolve a model asset name.

    Precedence: environment variable > model config `assets` > built-in
    default. This keeps existing env-based deployments working while
    letting models.json point a model at different checkpoint files.
    """
    env_value = os.environ.get(env_key)
    if env_value:
        return env_value
    assets = getattr(task, "assets", None) or {}
    return assets.get(key) or default


def is_turbo_variant(task: Any, builtin_turbo_ids: set) -> bool:
    """Return whether a task should use its turbo assets.

    An explicit task.variant (set from the model spec at submit time) is
    authoritative. Direct builder calls without a variant fall back to the
    exact built-in turbo model ids, never to a name suffix.
    """
    variant = getattr(task, "variant", None)
    if variant is not None:
        return variant == "turbo"
    return getattr(task, "model_id", "") in builtin_turbo_ids


def require_asset(folder: str, name: str) -> None:
    """Fail early with a clear message when a ComfyUI model file is absent.

    Outside ComfyUI (unit tests / CLI imports) folder_paths may not provide
    get_full_path; in that case the check is skipped and ComfyUI's normal
    validation reports the missing node/file.
    """
    try:
        import folder_paths
    except ImportError:
        return
    getter = getattr(folder_paths, "get_full_path", None)
    if getter is None:
        return
    if getter(folder, name) is None:
        raise ValueError(
            f"missing model file: {folder}/{name}; "
            f"run comfyui_multimodal_download.sh or link it into ComfyUI/models"
        )


@dataclass(frozen=True)
class BuildResult:
    """A fully built API-format workflow plus the metadata the generic
    task runner needs to track and serve it.

    phase_weights uses four logical phases: ``cond`` (text/conditioning),
    ``step`` (sampler), ``decode`` (VAE), ``save`` (container/PNG write).
    The values should sum to 1.0.
    """

    workflow: Dict[str, Any]
    output_prefix: str
    phase_weights: Dict[str, float] = field(
        default_factory=lambda: {
            "cond": 0.05,
            "step": 0.85,
            "decode": 0.10,
            "save": 0.0,
        }
    )
    step_class: str = "KSampler"
    output_mime: str = "image/png"
