"""See-through layer decomposition workflow builder.

The actual inference nodes live in the maintained ComfyUI-See-through
custom-node package.  This adapter only builds the API-format graph and
keeps the task runner/model catalog independent of that package.
"""

from __future__ import annotations

from typing import Any, Dict

from .base import BuildResult


LAYERDIFF_MODEL = "layerdifforg/seethroughv0.0.2_layerdiff3d"
DEPTH_MODEL = "24yearsold/seethroughv0.0.1_marigold"


def _bool_param(params: Dict[str, Any], key: str, default: bool) -> bool:
    value = params.get(key, default)
    if isinstance(value, str):
        return value.lower() not in ("0", "false", "no", "off")
    return bool(value)


def build_see_through_task(task: Any) -> BuildResult:
    """Build a See-through image-to-layers workflow for one input image."""
    params: Dict[str, Any] = dict(task.params or {})
    refs = list(task.ref_images or [])
    if len(refs) != 1:
        raise ValueError("See-through requires exactly one input image")

    resolution = int(params.get("resolution", 1280))
    steps = int(params.get("steps", 30))
    seed = int(params.get("seed", task.seed))
    resolution_depth = int(params.get("resolution_depth", -1))
    tblr_split = _bool_param(params, "tblr_split", True)
    use_lama = _bool_param(params, "use_lama", True)
    quant_mode = str(params.get("quant_mode", "none"))
    group_offload = _bool_param(params, "group_offload", False)
    layerdiff_model = str(params.get("layerdiff_model") or LAYERDIFF_MODEL)
    depth_model = str(params.get("depth_model") or DEPTH_MODEL)
    exclude_value = params.get("exclude_tags") or ""
    if isinstance(exclude_value, (list, tuple)):
        exclude_tags = ",".join(str(item).strip() for item in exclude_value if str(item).strip())
    else:
        exclude_tags = str(exclude_value).strip()

    prefix = f"comfy-rest-ext_layers_{(task.task_id or 'task')[:8]}"
    workflow: Dict[str, Any] = {
        "1": {
            "class_type": "LoadImage",
            "inputs": {"image": refs[0]},
        },
        "2": {
            "class_type": "SeeThrough_LoadLayerDiffModel",
            "inputs": {
                "model": layerdiff_model,
                "quant_mode": quant_mode,
                "cache_tag_embeds": True,
                "group_offload": group_offload,
                "auto_download": True,
            },
        },
        "3": {
            "class_type": "SeeThrough_LoadDepthModel",
            "inputs": {
                "model": depth_model,
                "quant_mode": quant_mode,
                "cache_tag_embeds": True,
                "group_offload": group_offload,
                "auto_download": True,
            },
        },
        "4": {
            "class_type": "SeeThrough_GenerateLayers",
            "inputs": {
                "image": ["1", 0],
                "layerdiff_model": ["2", 0],
                "seed": seed,
                "resolution": resolution,
                "num_inference_steps": steps,
            },
        },
        "5": {
            "class_type": "SeeThrough_GenerateDepth",
            "inputs": {
                "layers": ["4", 0],
                "depth_model": ["3", 0],
                "seed": seed,
                "resolution_depth": resolution_depth,
            },
        },
        "6": {
            "class_type": "SeeThrough_PostProcess",
            "inputs": {
                "layers_depth": ["5", 0],
                "tblr_split": tblr_split,
                "use_lama": use_lama,
            },
        },
        "7": {
            "class_type": "SeeThrough_SavePSD",
            "inputs": {
                "parts": ["6", 0],
                "filename_prefix": prefix,
            },
        },
        # A standard ComfyUI image output gives the task runner a preview and
        # makes the workflow visible in the normal history/outputs path.
        "8": {
            "class_type": "SaveImage",
            "inputs": {
                "images": ["4", 1],
                "filename_prefix": prefix + "_preview",
            },
        },
    }
    if exclude_tags:
        workflow["4"]["inputs"]["exclude_tags"] = exclude_tags
    return BuildResult(
        workflow=workflow,
        output_prefix=prefix,
        phase_weights={"cond": 0.05, "step": 0.80, "decode": 0.10, "save": 0.05},
        step_class="SeeThrough_GenerateLayers",
        output_mime="application/zip",
    )
