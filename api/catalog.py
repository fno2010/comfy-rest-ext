"""Model catalog: maps OpenAI model ids to workflow builders and limits.

The catalog is the single place that knows which models this server can
serve, which modality/tasks each one supports, and which defaults apply.
Adding a model is a registration, not a new branch in the HTTP routes.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Literal, Optional, Tuple

from .workflows import anima, h3, qwen_image_21

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
    aliases: Tuple[str, ...] = ()
    alias_tasks: Tuple[Tuple[str, str], ...] = ()
    capabilities: frozenset = frozenset()
    size: Optional[SizePolicy] = None
    max_n: int = 1
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
    canonical = _ALIASES.get(model_id, model_id)
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
    capabilities=frozenset({"negative_prompt", "seed", "accel"}),
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
    output_mime="image/png",
    timeout_s=1800,
    description="Qwen-Image 2.1 Turbo 7B (8-step distilled schedule)",
))
