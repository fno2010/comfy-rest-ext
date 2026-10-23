"""
Composable acceleration configuration for video generation.

The public surface is the legacy ``speed`` string or a JSON object with
per-dimension selections.  Method implementations live in a small registry
(``AccelMethod``) so the workflow builder no longer needs an if/elif branch
per method.  Dimensions are orthogonal; options within a dimension are
mutually exclusive.

Dimensions:
  turbo       - step compression (Turbo LoRA)
  attention   - attention kernel (Sol-Attn / Sage / Flash)
  block_cache - block-level caching (TE-Speed / FBC / CacheDiT)
  step_cache  - step-level caching (EasyCache / LazyCache / Spectrum / TeaCache)
  vae_decode  - VAE decode strategy (default / batched) [not yet applied]
  precision   - load-time model dtype (default / int8 / fp8 / bf16) [not yet applied]
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

ACCEL_DIMENSIONS = ("turbo", "attention", "block_cache", "step_cache")
PRECISION_OPTIONS = ("default", "int8", "fp8", "bf16")
VAE_DECODE_OPTIONS = ("default", "batched")

# Legacy speed string -> equivalent config (backward compatibility).
_LEGACY_SPEED_MAP = {
    "none": {"block_cache": "none", "attention": "none"},
    "te-speed": {"block_cache": "te-speed", "attention": "none"},
    "sol-stack": {"block_cache": "fbc", "attention": "sol"},
    "auto": {"block_cache": "none", "attention": "none"},
}


@dataclass(frozen=True)
class ParamSpec:
    """Type/range metadata for one method parameter."""

    kind: Any
    default: Any
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    choices: Optional[Tuple[Any, ...]] = None


@dataclass(frozen=True)
class AccelMethod:
    """One selectable acceleration method.

    ``apply`` receives the workflow dict, the node-add callback, the current
    model reference, merged parameters, a node-id allocator, and a context
    dict (e.g. resolved Turbo LoRA filename).  It returns the new model ref.
    """

    id: str
    dimension: str
    required_nodes: Tuple[str, ...] = ()
    params_schema: Mapping[str, ParamSpec] = field(default_factory=dict)
    implemented: bool = True
    default_steps: Optional[int] = None
    apply: Optional[Callable] = None

    def defaults(self) -> Dict[str, Any]:
        return {key: spec.default for key, spec in self.params_schema.items()}


_METHODS: Dict[str, AccelMethod] = {}


def register_method(method: AccelMethod) -> AccelMethod:
    """Register an acceleration method (unique id per process)."""
    if method.dimension not in ACCEL_DIMENSIONS:
        raise ValueError(
            f"unknown acceleration dimension {method.dimension!r}; "
            f"expected one of {list(ACCEL_DIMENSIONS)}"
        )
    if method.id in _METHODS:
        raise ValueError(f"acceleration method {method.id!r} is already registered")
    if method.implemented and method.apply is None:
        raise ValueError(f"implemented method {method.id!r} needs an apply callable")
    _METHODS[method.id] = method
    return method


def get_method(method_id: str) -> Optional[AccelMethod]:
    return _METHODS.get(method_id)


def methods_for_dimension(dimension: str) -> Tuple[AccelMethod, ...]:
    return tuple(m for m in _METHODS.values() if m.dimension == dimension)


def _float(default: float, minimum: Optional[float] = None,
           maximum: Optional[float] = None) -> ParamSpec:
    return ParamSpec(float, default, minimum, maximum)


def _int(default: int, minimum: Optional[float] = None,
         maximum: Optional[float] = None) -> ParamSpec:
    return ParamSpec(int, default, minimum, maximum)


def _bool(default: bool) -> ParamSpec:
    return ParamSpec(bool, default)


def _single_node(node_class: str) -> Callable:
    def apply(workflow, add, model_out, params, alloc, context):
        nid = alloc()
        inputs: Dict[str, Any] = {"model": model_out}
        inputs.update(params)
        add(nid, node_class, inputs)
        return [nid, 0]
    return apply


_TURBO_BUILDER_KEYS = {"lora_name", "shift_video", "shift_audio", "sampler", "scheduler"}


def _apply_turbo(workflow, add, model_out, params, alloc, context):
    nid = alloc()
    inputs: Dict[str, Any] = {
        "model": model_out,
        "lora_name": context.get("turbo_lora") or params.get("lora_name", ""),
    }
    inputs.update({
        key: value for key, value in params.items()
        if key not in _TURBO_BUILDER_KEYS
    })
    add(nid, "MiniMaxH3TurboLoRA", inputs)
    return [nid, 0]


# --- Built-in methods -------------------------------------------------------

register_method(AccelMethod(
    "turbo-v4", "turbo",
    required_nodes=("MiniMaxH3TurboLoRA",),
    params_schema={
        "strength": _float(1.0, minimum=-10.0, maximum=10.0),
        "low_vram": _bool(False),
        "shift_video": _float(12.0, minimum=0.01, maximum=100.0),
        "shift_audio": _float(3.0, minimum=0.01, maximum=100.0),
    },
    apply=_apply_turbo,
))

LIGHTX2V_4STEP_LORA = (
    "minimax_h3_fl2v_turbo_4step_v1.3_768p_comfyui_bf16.safetensors"
)
register_method(AccelMethod(
    "lightx2v-4step-v1.3", "turbo",
    required_nodes=("MiniMaxH3TurboLoRA",),
    params_schema={
        "lora_name": ParamSpec(str, LIGHTX2V_4STEP_LORA),
        "strength": _float(1.0, minimum=-10.0, maximum=10.0),
        "low_vram": _bool(False),
        "shift_video": _float(6.0, minimum=0.01, maximum=100.0),
        "shift_audio": _float(3.0, minimum=0.01, maximum=100.0),
        "sampler": ParamSpec(str, "euler"),
        "scheduler": ParamSpec(str, "simple"),
    },
    default_steps=4,
    apply=_apply_turbo,
))

register_method(AccelMethod(
    "sol", "attention",
    required_nodes=("SolAttnMiniMaxH3Patcher",),
    params_schema={
        "enabled": _bool(True),
        "tau": _float(1.0, minimum=0.1, maximum=5.0),
        "thresh_type": ParamSpec(str, "diag", choices=("diag", "exact")),
    },
    apply=_single_node("SolAttnMiniMaxH3Patcher"),
))
register_method(AccelMethod(
    "sage", "attention",
    required_nodes=("PathchSageAttentionKJ",),
    params_schema={
        "sage_attention": ParamSpec(
            str, "auto",
            choices=(
                "disabled", "auto", "sageattn_qk_int8_pv_fp16_cuda",
                "sageattn_qk_int8_pv_fp16_triton",
                "sageattn_qk_int8_pv_fp8_cuda",
                "sageattn_qk_int8_pv_fp8_cuda++",
                "sageattn3", "sageattn3_per_block_mean",
            ),
        ),
    },
    apply=_single_node("PathchSageAttentionKJ"),
))
register_method(AccelMethod(
    "flash", "attention",
    required_nodes=("PatchFlashAttentionKJ",),
    params_schema={},
    apply=_single_node("PatchFlashAttentionKJ"),
))

register_method(AccelMethod(
    "te-speed", "block_cache",
    required_nodes=("TESpeedMiniMaxH3",),
    params_schema={
        "processing_control_value": _float(0.12, minimum=0.0, maximum=1.0),
        "processing_percent_1": _float(0.1, minimum=0.0, maximum=0.49),
        "processing_percent_2": _float(0.9, minimum=0.51, maximum=1.0),
        "mcs": _int(2, minimum=0, maximum=10),
        "device": ParamSpec(str, "auto", choices=("auto", "cpu", "gpu")),
        "cache_depth": _float(0.75, minimum=0.0, maximum=0.95),
    },
    apply=_single_node("TESpeedMiniMaxH3"),
))
register_method(AccelMethod(
    "fbc", "block_cache",
    required_nodes=("H3FirstBlockCache",),
    params_schema={
        "threshold": _float(0.08, minimum=0.0, maximum=1.0),
        "start_step": _int(2, minimum=0, maximum=100),
        "end_dense_steps": _int(2, minimum=0, maximum=100),
        "max_consecutive_skips": _int(2, minimum=1, maximum=20),
    },
    apply=_single_node("H3FirstBlockCache"),
))
register_method(AccelMethod(
    "cachedit", "block_cache",
    required_nodes=("Cachedith3",),
    implemented=False,
))

register_method(AccelMethod(
    "easycache", "step_cache",
    required_nodes=("EasyCache",),
    params_schema={
        "reuse_threshold": _float(0.2, minimum=0.0, maximum=3.0),
        "start_percent": _float(0.15, minimum=0.0, maximum=1.0),
        "end_percent": _float(1.0, minimum=0.0, maximum=1.0),
        "verbose": _bool(False),
    },
    apply=_single_node("EasyCache"),
))
register_method(AccelMethod(
    "lazycache", "step_cache",
    required_nodes=("LazyCache",),
    params_schema={
        "reuse_threshold": _float(0.2, minimum=0.0, maximum=3.0),
        "start_percent": _float(0.15, minimum=0.0, maximum=1.0),
        "end_percent": _float(1.0, minimum=0.0, maximum=1.0),
        "verbose": _bool(False),
    },
    apply=_single_node("LazyCache"),
))
register_method(AccelMethod(
    "spectrum", "step_cache",
    required_nodes=("SpectrumApplyMiniMaxH3",),
    params_schema={
        "enabled": _bool(True),
        "blend_weight": _float(0.50, minimum=0.0, maximum=1.0),
        "degree": _int(1, minimum=1, maximum=16),
        "ridge_lambda": _float(0.10, minimum=0.0, maximum=10.0),
        "window_size": _float(2.0, minimum=1.0, maximum=16.0),
        "flex_window": _float(0.75, minimum=0.0, maximum=8.0),
        "warmup_steps": _int(1, minimum=0, maximum=64),
        "tail_actual_steps": _int(1, minimum=0, maximum=64),
        "max_history": _int(4, minimum=2, maximum=64),
        "debug": _bool(False),
    },
    apply=_single_node("SpectrumApplyMiniMaxH3"),
))
register_method(AccelMethod(
    "teacache", "step_cache",
    required_nodes=("MiniMaxH3TeaCache",),
    implemented=False,
))

# Derived public option lists. Keep "none" first for legacy semantics.
TURBO_OPTIONS = ("none",) + tuple(m.id for m in methods_for_dimension("turbo"))
ATTENTION_OPTIONS = ("none",) + tuple(m.id for m in methods_for_dimension("attention"))
BLOCK_CACHE_OPTIONS = ("none",) + tuple(m.id for m in methods_for_dimension("block_cache"))
STEP_CACHE_OPTIONS = ("none",) + tuple(m.id for m in methods_for_dimension("step_cache"))

# Backward-compatible view used by tests and callers.
_OPTION_NODE_REQUIREMENTS = {
    method.id: method.required_nodes
    for method in _METHODS.values()
    if method.required_nodes
}


# Node availability is cached lazily. Outside ComfyUI (unit tests) the
# registry is unavailable, so enforcement is skipped and only the runtime
# path rejects missing nodes.
_node_cache = None


def _available_nodes() -> set:
    global _node_cache
    if _node_cache is not None:
        return _node_cache
    try:
        import nodes as _nodes
        _node_cache = set(_nodes.NODE_CLASS_MAPPINGS.keys())
    except Exception:
        return set()
    return _node_cache


def check_option_available(layer: str, value: str) -> Optional[str]:
    """Return an error message if (layer, value) cannot be used."""
    if value in ("none", "default"):
        return None
    method = get_method(value)
    if method is None:
        return f"unknown acceleration method {value!r}"
    if method.dimension != layer:
        return (
            f"acceleration method {value!r} belongs to {method.dimension!r}, "
            f"not {layer!r}"
        )
    if not method.implemented:
        return (
            f"{layer}={value} is registered but not implemented in the "
            f"H3 workflow builder yet"
        )
    available = _available_nodes()
    if not available:
        return None
    missing = [n for n in method.required_nodes if n not in available]
    if missing:
        return (
            f"{layer}={value} requires node(s) {missing} which are not "
            f"installed in ComfyUI; install the plugin or choose another value"
        )
    return None


def check_config_available(cfg: "AccelConfig") -> Optional[str]:
    """Validate that every requested method is implemented and installed."""
    for layer in ACCEL_DIMENSIONS:
        err = check_option_available(layer, getattr(cfg, layer))
        if err:
            return err
    return None


class AccelConfigError(ValueError):
    """Raised for invalid acceleration configuration."""


def _validate_value(field_name: str, value: str, allowed: tuple) -> None:
    if value not in allowed:
        raise AccelConfigError(
            f"invalid {field_name}={value!r}; expected one of {list(allowed)}"
        )


def _validate_params(method: AccelMethod, params: Mapping[str, Any]) -> None:
    schema = method.params_schema
    unknown = set(params) - set(schema)
    if unknown:
        raise AccelConfigError(
            f"{method.id} has unknown parameter(s): {sorted(unknown)}"
        )
    for key, value in params.items():
        spec = schema[key]
        if spec.kind is bool:
            valid = isinstance(value, bool)
        elif spec.kind is int:
            valid = isinstance(value, int) and not isinstance(value, bool)
        elif spec.kind is float:
            valid = (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
            )
        elif spec.kind is str:
            valid = isinstance(value, str)
        else:
            valid = isinstance(value, spec.kind)
        if not valid:
            raise AccelConfigError(
                f"{method.id}.{key} must be {getattr(spec.kind, '__name__', spec.kind)}"
            )
        if spec.kind is float and not math.isfinite(value):
            raise AccelConfigError(f"{method.id}.{key} must be finite")
        if spec.choices is not None and value not in spec.choices:
            raise AccelConfigError(
                f"{method.id}.{key} must be one of {list(spec.choices)}"
            )
        if spec.minimum is not None and value < spec.minimum:
            raise AccelConfigError(
                f"{method.id}.{key} must be >= {spec.minimum}"
            )
        if spec.maximum is not None and value > spec.maximum:
            raise AccelConfigError(
                f"{method.id}.{key} must be <= {spec.maximum}"
            )


def _parse_layer(layer: str, raw: Any, default: str = "none"):
    """Normalize a layer selection to (method_id, explicit_params)."""
    if raw is None:
        raw = default
    if isinstance(raw, str):
        method_id = raw
        params: Dict[str, Any] = {}
    elif isinstance(raw, dict):
        unknown = set(raw) - {"method", "params"}
        if unknown:
            raise AccelConfigError(
                f"{layer} has unknown keys: {sorted(unknown)}; "
                f"expected 'method' and optional 'params'"
            )
        method_id = raw.get("method")
        if not isinstance(method_id, str):
            raise AccelConfigError(f"{layer}.method must be a string")
        params_value = raw.get("params", {})
        if params_value is None:
            params_value = {}
        if not isinstance(params_value, dict):
            raise AccelConfigError(f"{layer}.params must be an object")
        params = dict(params_value)
    else:
        raise AccelConfigError(
            f"{layer} must be a method string or {{'method': ..., 'params': ...}}"
        )

    if method_id == "none":
        if params:
            raise AccelConfigError(f"{layer}=none does not accept params")
        return "none", {}
    method = get_method(method_id)
    if method is None:
        raise AccelConfigError(
            f"unknown {layer} method {method_id!r}; "
            f"expected one of {list(_options_for_layer(layer))}"
        )
    if method.dimension != layer:
        raise AccelConfigError(
            f"{method_id!r} belongs to {method.dimension!r}, not {layer!r}"
        )
    _validate_params(method, params)
    return method_id, params


def _options_for_layer(layer: str) -> tuple:
    if layer not in ACCEL_DIMENSIONS:
        raise AccelConfigError(f"unknown acceleration dimension {layer!r}")
    return ("none",) + tuple(m.id for m in methods_for_dimension(layer))


@dataclass
class AccelConfig:
    """Validated acceleration configuration."""

    steps: int = 20
    turbo: str = "none"
    block_cache: str = "none"
    step_cache: str = "none"
    attention: str = "none"
    vae_decode: str = "default"
    precision: Dict[str, str] = field(default_factory=dict)
    params: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "steps": self.steps,
            "turbo": self.turbo,
            "block_cache": self.block_cache,
            "step_cache": self.step_cache,
            "attention": self.attention,
            "vae_decode": self.vae_decode,
            "precision": dict(self.precision),
        }
        if self.params:
            result["params"] = {
                layer: dict(values) for layer, values in self.params.items()
            }
        return result


def parse_accel_config(raw: Any, default_steps: int = 20) -> AccelConfig:
    """Parse a speed value (legacy string or JSON object) into AccelConfig."""
    if raw is None:
        raw = "auto"

    if isinstance(raw, str):
        key = raw.lower()
        if key in _LEGACY_SPEED_MAP:
            cfg = AccelConfig(steps=default_steps, **_LEGACY_SPEED_MAP[key])
        else:
            # Unknown legacy strings retain the historical auto behavior.
            cfg = AccelConfig(steps=default_steps)
        return _apply_env_defaults(cfg)

    if not isinstance(raw, dict):
        raise AccelConfigError(
            f"speed must be a string or object, got {type(raw).__name__}"
        )

    steps = raw.get("steps", default_steps)
    if not isinstance(steps, int) or isinstance(steps, bool) or steps <= 0:
        raise AccelConfigError(f"steps must be a positive integer, got {steps!r}")

    cfg = AccelConfig(steps=steps)
    for layer in ACCEL_DIMENSIONS:
        method_id, params = _parse_layer(layer, raw.get(layer, "none"))
        setattr(cfg, layer, method_id)
        if params:
            cfg.params[layer] = params

    vae_decode = raw.get("vae_decode", "default")
    _validate_value("vae_decode", vae_decode, VAE_DECODE_OPTIONS)
    cfg.vae_decode = vae_decode

    precision = raw.get("precision", {})
    if not isinstance(precision, dict):
        raise AccelConfigError("precision must be an object")
    for key, value in precision.items():
        if key not in ("unet", "text_encoder", "vae"):
            raise AccelConfigError(f"unknown precision key {key!r}")
        _validate_value(f"precision.{key}", value, PRECISION_OPTIONS)
    cfg.precision = dict(precision)

    if "steps" not in raw:
        method = get_method(cfg.turbo)
        if method is not None and method.default_steps is not None:
            cfg.steps = method.default_steps

    _validate_combo(cfg)
    return cfg


def _apply_env_defaults(cfg: AccelConfig) -> AccelConfig:
    """Apply H3_* env overrides for legacy/auto configs."""
    import os

    if cfg.block_cache == "none" and os.environ.get("H3_TE_SPEED", "0") == "1":
        cfg.block_cache = "te-speed"
    if cfg.attention == "none" and os.environ.get("H3_SOL_STACK", "0") == "1":
        cfg.attention = "sol"
    return cfg


def _validate_combo(cfg: AccelConfig) -> None:
    """Cross-layer combination rules (Cartesian-product filtering)."""
    if cfg.turbo != "none" and cfg.step_cache != "none":
        raise AccelConfigError(
            "turbo + step_cache is not recommended: step-cache warmup "
            "dominates at low step counts (measured on GB10)"
        )


class _NodeIds:
    """Allocate ``1b``, ``1c``... in the order methods are applied."""

    def __init__(self) -> None:
        self.index = 0

    def __call__(self) -> str:
        self.index += 1
        if self.index > 25:
            raise AccelConfigError("too many acceleration methods in one pipeline")
        return "1" + chr(ord("a") + self.index)


def resolve_method_params(
    cfg: "AccelConfig", layer: str,
) -> Dict[str, Any]:
    """Return merged method params for a selected layer, or {}."""
    method_id = getattr(cfg, layer, "none")
    if method_id in (None, "none"):
        return {}
    method = get_method(method_id)
    if method is None:
        return {}
    params = method.defaults()
    params.update((cfg.params or {}).get(layer, {}))
    return params


def resolve_steps(
    cfg: "AccelConfig", speed: Any, env_steps: Any, param_steps: Any,
) -> int:
    """Resolve sampler steps for a task.

    Explicit request steps win; otherwise a turbo method's trained default
    (e.g. LightX2V 4-step) wins over the server env/request default.
    """
    if isinstance(speed, dict) and "steps" in speed:
        return cfg.steps
    method = get_method(cfg.turbo)
    if method is not None and method.default_steps is not None:
        return method.default_steps
    return int(env_steps or param_steps or 20)


def apply_accel_layers(
    workflow: Dict[str, Any],
    add: Callable,
    model_ref: list,
    cfg: AccelConfig,
    context: Optional[Dict[str, Any]] = None,
) -> list:
    """Apply selected methods in dimension order through the registry."""
    model_out = list(model_ref)
    alloc = _NodeIds()
    context = context or {}
    for layer in ACCEL_DIMENSIONS:
        method_id = getattr(cfg, layer)
        if method_id in (None, "none"):
            continue
        method = get_method(method_id)
        if method is None:
            raise AccelConfigError(f"unknown {layer} method {method_id!r}")
        if not method.implemented or method.apply is None:
            raise AccelConfigError(
                f"{layer}={method_id} is registered but not implemented"
            )
        overrides = (cfg.params or {}).get(layer, {})
        _validate_params(method, overrides)
        params = method.defaults()
        params.update(overrides)
        model_out = method.apply(
            workflow, add, model_out, params, alloc, context
        )
    return model_out
