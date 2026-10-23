"""Model catalog: maps OpenAI model ids to workflow builders and limits.

The catalog is the single place that knows which models this server can
serve, which modality/tasks each one supports, and which defaults apply.
Adding a model is a registration, not a new branch in the HTTP routes.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Literal, Optional, Tuple

from .workflows import anima, h3, qwen_image_21

logger = logging.getLogger("comfy-rest-ext.catalog")

Modality = Literal["video", "image"]


@dataclass(frozen=True)
class SizePolicy:
    """Dimension limits and normalization for image models."""

    default_width: int
    default_height: int
    min_width: int
    min_height: int
    max_width: int
    max_height: int
    step: int = 8
    max_pixels: int = 0
    default_megapixels: Optional[float] = None

    def normalize(
        self,
        width: Optional[int] = None,
        height: Optional[int] = None,
        size: Optional[str] = None,
        aspect_ratio: Optional[str] = None,
        megapixels: Optional[float] = None,
    ) -> Tuple[int, int]:
        """Resolve a request to (width, height), rounded to ``step``.

        Precedence: explicit width/height > size=WxH > aspect_ratio +
        megapixels > defaults. Raises ValueError for unsupported values.
        """
        if (width is None) != (height is None):
            raise ValueError("width and height must be provided together")

        if width is not None and height is not None:
            w, h = int(width), int(height)
        elif size and str(size).lower() != "auto":
            match = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", str(size))
            if not match:
                raise ValueError(
                    f"invalid size {size!r}; expected WIDTHxHEIGHT or 'auto'"
                )
            w, h = int(match.group(1)), int(match.group(2))
        elif aspect_ratio or megapixels is not None:
            ratio_w, ratio_h = self._parse_ratio(aspect_ratio) if aspect_ratio else (
                self.default_width,
                self.default_height,
            )
            mp = megapixels if megapixels is not None else self.default_megapixels
            if mp is None:
                mp = (self.default_width * self.default_height) / (1024 * 1024)
            ratio = ratio_w / ratio_h
            pixels = float(mp) * 1024 * 1024
            w = round(math.sqrt(pixels * ratio))
            h = round(math.sqrt(pixels / ratio))
        else:
            w, h = self.default_width, self.default_height

        w = self._round_dim(w)
        h = self._round_dim(h)
        self._validate(w, h)
        return w, h

    def _round_dim(self, value: int) -> int:
        step = max(1, int(self.step))
        return max(step, int(round(value / step)) * step)

    def _validate(self, w: int, h: int) -> None:
        if not (self.min_width <= w <= self.max_width):
            raise ValueError(
                f"width {w} outside supported range "
                f"[{self.min_width}, {self.max_width}]"
            )
        if not (self.min_height <= h <= self.max_height):
            raise ValueError(
                f"height {h} outside supported range "
                f"[{self.min_height}, {self.max_height}]"
            )
        if self.max_pixels and w * h > self.max_pixels:
            raise ValueError(
                f"{w}x{h} exceeds max_pixels={self.max_pixels} "
                f"({self.max_pixels / (1024 * 1024):.2f} MP)"
            )

    @staticmethod
    def _parse_ratio(value: str) -> Tuple[int, int]:
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[:xX/]\s*(\d+(?:\.\d+)?)\s*", str(value))
        if not match:
            raise ValueError(
                f"invalid aspect_ratio {value!r}; expected e.g. '16:9'"
            )
        w, h = float(match.group(1)), float(match.group(2))
        if w <= 0 or h <= 0:
            raise ValueError("aspect_ratio values must be positive")
        return int(w), int(h)


@dataclass(frozen=True)
class ModelSpec:
    """Everything the generic task runner needs to serve one model."""

    id: str
    modality: Modality
    family: str
    tasks: Tuple[str, ...]
    build: Callable[[Any], Any]
    defaults: Dict[str, Any] = field(default_factory=dict)
    assets: Dict[str, str] = field(default_factory=dict)
    aliases: Tuple[str, ...] = ()
    alias_tasks: Tuple[Tuple[str, str], ...] = ()
    capabilities: frozenset = frozenset()
    size: Optional[SizePolicy] = None
    max_n: int = 1
    variant: str = "base"
    output_mime: str = "image/png"
    timeout_s: int = 900
    description: str = ""

    def task_types(self) -> Tuple[str, ...]:
        return self.tasks

    def alias_task_map(self) -> Dict[str, str]:
        return dict(self.alias_tasks)


_SPECS: Dict[str, ModelSpec] = {}
_ALIASES: Dict[str, str] = {}


def register(spec: ModelSpec) -> ModelSpec:
    if spec.id in _SPECS:
        raise ValueError(f"model {spec.id!r} is already registered")
    _SPECS[spec.id] = spec
    for alias in spec.aliases:
        if alias in _ALIASES or alias in _SPECS:
            raise ValueError(f"model alias {alias!r} is already registered")
        _ALIASES[alias] = spec.id
    return spec


def get_model(model_id: Optional[str]) -> ModelSpec:
    """Resolve a model id or alias. Raises KeyError for unknown ids."""
    if not model_id:
        raise KeyError(model_id)
    canonical = model_id if model_id in _SPECS else _ALIASES.get(model_id)
    if canonical is None:
        raise KeyError(model_id)
    try:
        return _SPECS[canonical]
    except KeyError:
        raise KeyError(model_id) from None


def canonical_model_id(model_id: Optional[str]) -> str:
    if not model_id:
        raise KeyError(model_id)
    return _ALIASES.get(model_id, model_id)


def alias_task_type(model_id: Optional[str]) -> Optional[str]:
    if not model_id:
        return None
    spec = get_model(model_id)
    return spec.alias_task_map().get(model_id)


def list_models() -> Iterable[ModelSpec]:
    return tuple(_SPECS.values())


def all_model_ids() -> Tuple[str, ...]:
    """Canonical ids first, then aliases, in registration order."""
    ids = list(_SPECS.keys())
    ids.extend(_ALIASES.keys())
    return tuple(ids)


# ---------------------------------------------------------------------------
# Registered models
# ---------------------------------------------------------------------------

H3_SPEC = register(ModelSpec(
    id="minimax-h3",
    modality="video",
    family="h3",
    tasks=("t2va", "fl2va", "r2v"),
    build=h3.build_h3_task,
    defaults={
        "width": 1344,
        "height": 768,
        "seconds": 5.0,
        "steps": 20,
        "sampler": "euler",
        "scheduler": "simple",
    },
    aliases=("minimax-h3-t2v", "minimax-h3-i2v", "minimax-h3-r2v"),
    alias_tasks=(
        ("minimax-h3-t2v", "t2va"),
        ("minimax-h3-i2v", "fl2va"),
        ("minimax-h3-r2v", "r2v"),
    ),
    capabilities=frozenset({"negative_prompt", "seed", "accel", "reference_video"}),
    max_n=1,
    output_mime="video/mp4",
    timeout_s=3600,
    description="MiniMax-H3 text/image/reference to video with audio",
))

ANIMA_NEGATIVE = (
    "worst quality, low quality, score_1, score_2, score_3, artist name, "
    "blurry, jpeg artifacts, chromatic aberration"
)

ANIMA_BASE_SPEC = register(ModelSpec(
    id="anima-base",
    modality="image",
    family="anima",
    tasks=("t2i",),
    build=anima.build_anima_task,
    defaults={
        "steps": 30,
        "cfg": 4.0,
        "sampler": "euler",
        "scheduler": "simple",
        "negative_prompt": ANIMA_NEGATIVE,
    },
    capabilities=frozenset({"negative_prompt", "n", "seed"}),
    size=SizePolicy(
        default_width=1024,
        default_height=1024,
        min_width=512,
        min_height=512,
        max_width=1536,
        max_height=1536,
        step=16,
        max_pixels=1536 * 1536,
        default_megapixels=1.0,
    ),
    max_n=8,
    output_mime="image/png",
    timeout_s=900,
    description="Anima Base v1.0 2B anime/illustration text-to-image",
))

ANIMA_TURBO_SPEC = register(ModelSpec(
    id="anima-turbo",
    modality="image",
    family="anima",
    tasks=("t2i",),
    build=anima.build_anima_task,
    defaults={
        "steps": 8,
        "cfg": 1.0,
        "sampler": "euler",
        "scheduler": "simple",
        "negative_prompt": "",
    },
    capabilities=frozenset({"negative_prompt", "n", "seed"}),
    size=SizePolicy(
        default_width=1024,
        default_height=1024,
        min_width=512,
        min_height=512,
        max_width=1536,
        max_height=1536,
        step=16,
        max_pixels=1536 * 1536,
        default_megapixels=1.0,
    ),
    max_n=8,
    variant="turbo",
    output_mime="image/png",
    timeout_s=900,
    description="Anima Turbo v1.0 2B anime text-to-image (8 steps)",
))

QWEN_IMAGE_POLICY = SizePolicy(
    default_width=1024,
    default_height=1024,
    min_width=256,
    min_height=256,
    max_width=2048,
    max_height=2048,
    step=8,
    max_pixels=2048 * 2048,
    default_megapixels=1.0,
)

QWEN_IMAGE_SPEC = register(ModelSpec(
    id="qwen-image-2.1",
    modality="image",
    family="qwen_image_21",
    tasks=("t2i", "edit"),
    build=qwen_image_21.build_qwen_image_task,
    defaults={
        "steps": 25,
        "cfg": 1.0,
        "sampler": "euler",
        "scheduler": "simple",
        "negative_prompt": "",
        "resolution": 1024,
    },
    capabilities=frozenset({"negative_prompt", "n", "seed", "alpha", "edit"}),
    size=QWEN_IMAGE_POLICY,
    max_n=4,
    output_mime="image/png",
    timeout_s=1800,
    description="Qwen-Image 2.1 7B text-to-image and instruction editing",
))

QWEN_IMAGE_TURBO_SPEC = register(ModelSpec(
    id="qwen-image-2.1-turbo",
    modality="image",
    family="qwen_image_21",
    tasks=("t2i", "edit"),
    build=qwen_image_21.build_qwen_image_task,
    defaults={
        "steps": 8,
        "cfg": 1.0,
        "sampler": "euler",
        "scheduler": "simple",
        "negative_prompt": "",
        "resolution": 1024,
    },
    capabilities=frozenset({"negative_prompt", "n", "seed", "alpha", "edit"}),
    size=QWEN_IMAGE_POLICY,
    max_n=4,
    variant="turbo",
    output_mime="image/png",
    timeout_s=1800,
    description="Qwen-Image 2.1 Turbo 7B (8-step distilled schedule)",
))


# ---------------------------------------------------------------------------
# Config-driven extensions
# ---------------------------------------------------------------------------

BUILDERS: Dict[str, Callable[[Any], Any]] = {
    "h3": h3.build_h3_task,
    "anima": anima.build_anima_task,
    "qwen_image_21": qwen_image_21.build_qwen_image_task,
}

BUILDER_MODALITY = {
    "h3": "video",
    "anima": "image",
    "qwen_image_21": "image",
}

BUILDER_TASKS = {
    "h3": frozenset({"t2va", "fl2va", "r2v"}),
    "anima": frozenset({"t2i"}),
    "qwen_image_21": frozenset({"t2i", "edit"}),
}

BUILDER_DEFAULTS_KEYS = {
    "h3": frozenset({
        "width", "height", "seconds", "steps", "sampler", "scheduler",
        "seed", "ref_image_size",
    }),
    "anima": frozenset({
        "steps", "cfg", "sampler", "scheduler", "negative_prompt",
        "lora_strength", "n", "seed",
    }),
    "qwen_image_21": frozenset({
        "steps", "cfg", "sampler", "scheduler", "negative_prompt",
        "resolution", "n", "seed",
    }),
}

BUILDER_ASSET_KEYS = {
    "h3": frozenset({
        "unet", "ref2va", "text_encoder", "video_vae", "audio_vae",
        "turbo_lora",
    }),
    "anima": frozenset({
        "unet", "turbo_unet", "text_encoder", "vae", "turbo_lora",
    }),
    "qwen_image_21": frozenset({
        "unet", "turbo_unet", "text_encoder", "vae",
    }),
}

_MODEL_KEYS = {
    "builder", "modality", "tasks", "aliases", "alias_tasks",
    "defaults", "capabilities", "size", "variant", "assets",
    "max_n", "output_mime", "timeout_s", "description",
}

CONFIG_ENV = "COMFY_REST_MODEL_CONFIG"

_SIZE_FIELDS = {
    "default_width", "default_height", "min_width", "min_height",
    "max_width", "max_height", "step", "max_pixels", "default_megapixels",
}


class ModelConfigError(ValueError):
    """Raised when models.json cannot be applied."""


def config_path() -> Path:
    """Path to the optional model config file.

    `COMFY_REST_MODEL_CONFIG` overrides the default location under
    ComfyUI's system user directory. The file is read once at startup.
    """
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override).expanduser()
    try:
        import folder_paths
        base = Path(folder_paths.get_system_user_directory("comfy_rest_ext"))
    except Exception:
        base = Path.home() / ".comfy-rest-ext"
    return base / "models.json"


def _string_list(value: Any, field_name: str) -> Tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ModelConfigError(f"{field_name} must be a list of non-empty strings")
    return tuple(value)


_INT_SIZE_FIELDS = {
    "default_width", "default_height", "min_width", "min_height",
    "max_width", "max_height", "step", "max_pixels",
}
_REQUIRED_SIZE_FIELDS = {
    "default_width", "default_height", "min_width", "min_height",
    "max_width", "max_height",
}


def _size_policy(
    value: Any, model_id: str, base: Optional[SizePolicy] = None
) -> SizePolicy:
    if not isinstance(value, dict):
        raise ModelConfigError(f"{model_id}.size must be an object")
    unknown = set(value) - _SIZE_FIELDS
    if unknown:
        raise ModelConfigError(
            f"{model_id}.size has unknown keys: {sorted(unknown)}"
        )
    data = {}
    if base is not None:
        for key in _SIZE_FIELDS:
            data[key] = getattr(base, key)
    data.update(value)

    missing = _REQUIRED_SIZE_FIELDS - set(data)
    if missing:
        raise ModelConfigError(
            f"{model_id}.size is missing keys: {sorted(missing)}"
        )
    for key in _INT_SIZE_FIELDS:
        if key in data and (
            not isinstance(data[key], int) or isinstance(data[key], bool)
        ):
            raise ModelConfigError(f"{model_id}.size.{key} must be an integer")
    if "default_megapixels" in data and data["default_megapixels"] is not None:
        if not isinstance(data["default_megapixels"], (int, float)) or isinstance(
            data["default_megapixels"], bool
        ):
            raise ModelConfigError(
                f"{model_id}.size.default_megapixels must be a number"
            )
        if data["default_megapixels"] <= 0:
            raise ModelConfigError(
                f"{model_id}.size.default_megapixels must be positive"
            )
    if data.get("step", 8) <= 0:
        raise ModelConfigError(f"{model_id}.size.step must be positive")
    if data.get("max_pixels", 0) < 0:
        raise ModelConfigError(f"{model_id}.size.max_pixels must be >= 0")
    if data["min_width"] > data["max_width"] or data["min_height"] > data["max_height"]:
        raise ModelConfigError(f"{model_id}.size min bounds exceed max bounds")
    if not (data["min_width"] <= data["default_width"] <= data["max_width"]):
        raise ModelConfigError(f"{model_id}.size default_width outside bounds")
    if not (data["min_height"] <= data["default_height"] <= data["max_height"]):
        raise ModelConfigError(f"{model_id}.size default_height outside bounds")
    try:
        return SizePolicy(**data)
    except TypeError as e:
        raise ModelConfigError(f"{model_id}.size is invalid: {e}") from e


def _build_spec(model_id: str, entry: Any) -> ModelSpec:
    if not isinstance(model_id, str) or not model_id.strip():
        raise ModelConfigError("model ids must be non-empty strings")
    if not isinstance(entry, dict):
        raise ModelConfigError(f"model {model_id!r} must be an object")
    unknown = set(entry) - _MODEL_KEYS
    if unknown:
        raise ModelConfigError(
            f"{model_id} has unknown keys: {sorted(unknown)}"
        )
    existing = _SPECS.get(model_id)

    builder = entry.get("builder", existing.family if existing else None)
    if builder not in BUILDERS:
        raise ModelConfigError(
            f"{model_id}.builder must be one of {sorted(BUILDERS)}; got {builder!r}"
        )

    modality = entry.get("modality", existing.modality if existing else None)
    if modality not in ("video", "image"):
        raise ModelConfigError(
            f"{model_id}.modality must be 'video' or 'image'; got {modality!r}"
        )
    if modality != BUILDER_MODALITY[builder]:
        raise ModelConfigError(
            f"{model_id}: builder {builder!r} serves "
            f"{BUILDER_MODALITY[builder]}, not {modality}"
        )

    tasks_value = entry.get("tasks", existing.tasks if existing else None)
    tasks = _string_list(tasks_value, f"{model_id}.tasks")
    if not tasks:
        raise ModelConfigError(f"{model_id}.tasks must not be empty")
    unsupported = set(tasks) - BUILDER_TASKS[builder]
    if unsupported:
        raise ModelConfigError(
            f"{model_id}: tasks {sorted(unsupported)} are not supported by "
            f"builder {builder!r}"
        )

    aliases_provided = "aliases" in entry
    aliases = _string_list(
        entry.get("aliases", existing.aliases if existing else []),
        f"{model_id}.aliases",
    )
    if model_id in aliases:
        raise ModelConfigError(f"{model_id}.aliases must not contain the model id")

    alias_tasks_value = entry.get("alias_tasks")
    if alias_tasks_value is None:
        inherited = dict(existing.alias_tasks) if existing else {}
        if aliases_provided:
            inherited = {
                alias: task for alias, task in inherited.items()
                if alias in aliases
            }
        alias_tasks_value = inherited
    if not isinstance(alias_tasks_value, dict) or not all(
        isinstance(k, str) and isinstance(v, str)
        for k, v in alias_tasks_value.items()
    ):
        raise ModelConfigError(f"{model_id}.alias_tasks must be a string map")
    for alias, task in alias_tasks_value.items():
        if alias not in aliases:
            raise ModelConfigError(
                f"{model_id}.alias_tasks references unknown alias {alias!r}"
            )
        if task not in tasks:
            raise ModelConfigError(
                f"{model_id}.alias_tasks[{alias!r}]={task!r} is not in tasks"
            )

    defaults = dict(existing.defaults) if existing else {}
    defaults_value = entry.get("defaults", {})
    if not isinstance(defaults_value, dict):
        raise ModelConfigError(f"{model_id}.defaults must be an object")
    unknown_defaults = set(defaults_value) - BUILDER_DEFAULTS_KEYS[builder]
    if unknown_defaults:
        raise ModelConfigError(
            f"{model_id}.defaults has unknown keys for builder {builder!r}: "
            f"{sorted(unknown_defaults)}"
        )
    defaults.update(defaults_value)
    if existing is not None and builder != existing.family:
        # Inherited defaults belong to the old builder; drop keys the new
        # builder does not understand instead of failing the override.
        defaults = {
            key: value for key, value in defaults.items()
            if key in BUILDER_DEFAULTS_KEYS[builder]
        }
    for key in ("sampler", "scheduler", "negative_prompt", "ref_image_size"):
        if key in defaults and not isinstance(defaults[key], str):
            raise ModelConfigError(f"{model_id}.defaults.{key} must be a string")
    if "lora_strength" in defaults and (
        not isinstance(defaults["lora_strength"], (int, float))
        or isinstance(defaults["lora_strength"], bool)
    ):
        raise ModelConfigError(f"{model_id}.defaults.lora_strength must be a number")
    if "steps" in defaults and (
        not isinstance(defaults["steps"], int)
        or isinstance(defaults["steps"], bool)
        or defaults["steps"] <= 0
    ):
        raise ModelConfigError(f"{model_id}.defaults.steps must be a positive integer")
    if "cfg" in defaults and (
        not isinstance(defaults["cfg"], (int, float))
        or isinstance(defaults["cfg"], bool)
    ):
        raise ModelConfigError(f"{model_id}.defaults.cfg must be a number")
    for key in ("width", "height", "seed", "n", "resolution"):
        if key in defaults and (
            not isinstance(defaults[key], int) or isinstance(defaults[key], bool)
        ):
            raise ModelConfigError(f"{model_id}.defaults.{key} must be an integer")
    if "seconds" in defaults and (
        not isinstance(defaults["seconds"], (int, float))
        or isinstance(defaults["seconds"], bool)
    ):
        raise ModelConfigError(f"{model_id}.defaults.seconds must be a number")
    if existing is None and modality == "image" and "steps" not in defaults:
        raise ModelConfigError(
            f"{model_id}.defaults.steps is required for new image models"
        )

    assets = dict(existing.assets) if existing else {}
    assets_value = entry.get("assets", {})
    if not isinstance(assets_value, dict) or not all(
        isinstance(k, str) and isinstance(v, str)
        for k, v in assets_value.items()
    ):
        raise ModelConfigError(f"{model_id}.assets must be a string map")
    assets.update(assets_value)
    unknown_assets = set(assets) - BUILDER_ASSET_KEYS[builder]
    if unknown_assets:
        raise ModelConfigError(
            f"{model_id}.assets has unknown keys for builder {builder!r}: "
            f"{sorted(unknown_assets)}"
        )

    capabilities_value = entry.get(
        "capabilities", existing.capabilities if existing else []
    )
    if not isinstance(capabilities_value, (list, tuple, set, frozenset)) or not all(
        isinstance(item, str) for item in capabilities_value
    ):
        raise ModelConfigError(f"{model_id}.capabilities must be a string list")

    variant = entry.get("variant", existing.variant if existing else "base")
    if variant not in ("base", "turbo"):
        raise ModelConfigError(
            f"{model_id}.variant must be 'base' or 'turbo'; got {variant!r}"
        )
    if builder == "h3" and variant != "base":
        raise ModelConfigError(
            f"{model_id}.variant {variant!r} is not supported by builder 'h3'"
        )

    if "size" in entry:
        size = _size_policy(
            entry["size"], model_id, existing.size if existing else None
        )
    else:
        size = existing.size if existing else None
    if modality == "image" and size is None:
        raise ModelConfigError(f"{model_id}.size is required for new image models")

    try:
        max_n = int(entry.get("max_n", existing.max_n if existing else 1))
        timeout_s = int(entry.get(
            "timeout_s",
            existing.timeout_s if existing else (
                3600 if modality == "video" else 1800
            ),
        ))
    except (TypeError, ValueError) as e:
        raise ModelConfigError(f"{model_id}: max_n/timeout_s must be integers") from e
    if max_n < 1 or timeout_s < 1:
        raise ModelConfigError(f"{model_id}: max_n and timeout_s must be positive")
    if "n" in defaults and not (1 <= defaults["n"] <= max_n):
        raise ModelConfigError(f"{model_id}: defaults.n must be in [1, max_n]")

    output_mime = entry.get(
        "output_mime",
        existing.output_mime if existing else (
            "video/mp4" if modality == "video" else "image/png"
        ),
    )
    description = entry.get(
        "description", existing.description if existing else ""
    )
    if not isinstance(output_mime, str) or not output_mime:
        raise ModelConfigError(f"{model_id}.output_mime must be a non-empty string")
    if not isinstance(description, str):
        raise ModelConfigError(f"{model_id}.description must be a string")

    return ModelSpec(
        id=model_id,
        modality=modality,
        family=builder,
        tasks=tasks,
        build=BUILDERS[builder],
        defaults=defaults,
        assets=assets,
        aliases=aliases,
        alias_tasks=tuple(alias_tasks_value.items()),
        capabilities=frozenset(capabilities_value),
        size=size,
        max_n=max_n,
        variant=variant,
        output_mime=output_mime,
        timeout_s=timeout_s,
        description=description,
    )


def _validate_final_registry(
    specs: Dict[str, ModelSpec], aliases: Dict[str, str]
) -> None:
    overlap = set(specs) & set(aliases)
    if overlap:
        raise ModelConfigError(
            f"model ids are also used as aliases: {sorted(overlap)}"
        )
    for spec in specs.values():
        for alias in spec.aliases:
            owner = aliases.get(alias)
            if owner is not None and owner != spec.id:
                raise ModelConfigError(
                    f"alias {alias!r} is claimed by both {owner!r} and {spec.id!r}"
                )
    for alias, owner in aliases.items():
        if owner not in specs:
            raise ModelConfigError(
                f"alias {alias!r} points at unknown model {owner!r}"
            )


def load_model_config(path: Optional[Path] = None) -> Tuple[str, ...]:
    """Apply an optional models.json, returning the loaded model ids.

    The file is applied atomically: validation runs against copies of the
    registry and only a fully consistent result is committed. Existing
    built-in models can be overridden; new ids are appended.
    """
    config_file = Path(path) if path is not None else config_path()
    if not config_file.exists():
        return ()
    try:
        payload = json.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ModelConfigError(f"cannot read {config_file}: {e}") from e
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, dict):
        raise ModelConfigError(f"{config_file}: top-level 'models' object is required")

    specs = [_build_spec(model_id, entry) for model_id, entry in models.items()]

    final_specs = dict(_SPECS)
    final_specs.update({spec.id: spec for spec in specs})
    final_aliases: Dict[str, str] = {}
    for spec in final_specs.values():
        for alias in spec.aliases:
            if alias in final_specs:
                raise ModelConfigError(
                    f"alias {alias!r} conflicts with an existing model id"
                )
            owner = final_aliases.get(alias)
            if owner is not None and owner != spec.id:
                raise ModelConfigError(
                    f"alias {alias!r} is claimed by both {owner!r} and {spec.id!r}"
                )
            final_aliases[alias] = spec.id
    _validate_final_registry(final_specs, final_aliases)

    _SPECS.clear()
    _SPECS.update(final_specs)
    _ALIASES.clear()
    _ALIASES.update(final_aliases)
    logger.info("Loaded %d model override(s) from %s", len(specs), config_file)
    return tuple(spec.id for spec in specs)


try:
    load_model_config()
except Exception as _config_error:  # pragma: no cover - depends on user file
    logger.error("Model config ignored: %s", _config_error)
