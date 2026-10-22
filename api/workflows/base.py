"""Shared types for model-specific ComfyUI workflow builders."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


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
