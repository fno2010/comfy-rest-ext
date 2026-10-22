"""
Composable acceleration configuration for video generation.

Replaces the single `speed` enum (te-speed | sol-stack | none | auto) with a
structured config covering all acceleration layers. Layers are orthogonal
(cross-layer stacking allowed); options within a layer are mutually
exclusive (same ComfyUI hook slot).

Layers:
  turbo       - step compression (Turbo LoRA)
  block_cache - block-level caching (TE-Speed / FBC / CacheDiT)
  step_cache  - step-level caching (EasyCache / LazyCache / TeaCache / Spectrum)
  attention   - attention kernel (Sol-Attn / Sage / Flash)
  vae_decode  - VAE decode strategy (default / batched)
  precision   - load-time model dtype (default / int8 / fp8 / bf16)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

# Valid values per layer (in-layer mutual exclusion enforced by validation).
TURBO_OPTIONS = ("none", "turbo-v4")
BLOCK_CACHE_OPTIONS = ("none", "te-speed", "fbc", "cachedit")
STEP_CACHE_OPTIONS = ("none", "easycache", "lazycache", "teacache", "spectrum")
ATTENTION_OPTIONS = ("none", "sol", "sage", "flash")
VAE_DECODE_OPTIONS = ("default", "batched")
PRECISION_OPTIONS = ("default", "int8", "fp8", "bf16")

# Legacy speed string -> equivalent config (backward compatibility).
_LEGACY_SPEED_MAP = {
    "none": {"block_cache": "none", "attention": "none"},
    "te-speed": {"block_cache": "te-speed", "attention": "none"},
    "sol-stack": {"block_cache": "fbc", "attention": "sol"},
    "auto": {"block_cache": "none", "attention": "none"},
}


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

    def as_dict(self) -> Dict[str, Any]:
        return {
            "steps": self.steps,
            "turbo": self.turbo,
            "block_cache": self.block_cache,
            "step_cache": self.step_cache,
            "attention": self.attention,
            "vae_decode": self.vae_decode,
            "precision": dict(self.precision),
        }


# Node class required for each option value. The node must be installed in
# ComfyUI for the option to actually take effect; validated at runtime via
# /object_info so an uninstalled node is reported instead of silently ignored.
_OPTION_NODE_REQUIREMENTS = {
    "turbo-v4": ("MiniMaxH3TurboLoRA",),
    "te-speed": ("TESpeedMiniMaxH3",),
    "fbc": ("H3FirstBlockCache",),
    "cachedit": ("Cachedith3",),  # community plugin; not installed in this env
    "easycache": ("EasyCache",),
    "lazycache": ("LazyCache",),
    "teacache": ("MiniMaxH3TeaCache",),  # community plugin; not installed
    "spectrum": ("SpectrumApplyMiniMaxH3",),
    "sol": ("SolAttnMiniMaxH3Patcher",),
    "sage": ("PathchSageAttentionKJ",),
    "flash": ("PatchFlashAttentionKJ",),
}

# Cache of available node classes, refreshed lazily at runtime.
_node_cache = None


def _available_nodes() -> set:
    """Return installed node class names.

    In-process: read ComfyUI's global node registry directly. Using HTTP
    here would deadlock (aiohttp single event loop blocked by a sync
    request). Outside ComfyUI (unit tests) returns an empty set so
    availability checks are skipped there and only enforced at runtime.
    """
    global _node_cache
    if _node_cache is not None:
        return _node_cache
    try:
        import nodes as _nodes
        _node_cache = set(_nodes.NODE_CLASS_MAPPINGS.keys())
    except Exception:
        # Not inside ComfyUI (e.g. unit tests) — skip enforcement.
        return set()
    return _node_cache


def check_option_available(layer: str, value: str) -> Optional[str]:
    """Return an error message if the node for (layer, value) is missing.

    Returns None when the option has no node requirement, or the node is
    installed, or we cannot query ComfyUI (unit-test environment).
    """
    if value == "none" or value == "default":
        return None
    required = _OPTION_NODE_REQUIREMENTS.get(value)
    if not required:
        return None
    available = _available_nodes()
    if not available:
        # Cannot query (not inside ComfyUI) — skip enforcement in tests.
        return None
    missing = [n for n in required if n not in available]
    if missing:
        return (
            f"{layer}={value} requires node(s) {missing} which are not "
            f"installed in ComfyUI; install the plugin or choose another value"
        )
    return None


def check_config_available(cfg: AccelConfig) -> Optional[str]:
    """Validate that every requested option's node is installed."""
    for layer, value in (
        ("turbo", cfg.turbo),
        ("block_cache", cfg.block_cache),
        ("step_cache", cfg.step_cache),
        ("attention", cfg.attention),
    ):
        err = check_option_available(layer, value)
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


def parse_accel_config(raw: Any, default_steps: int = 20) -> AccelConfig:
    """Parse a speed value (legacy string or JSON object) into AccelConfig.

    Raises AccelConfigError on invalid input so callers return a clear 400.
    """
    if raw is None:
        raw = "auto"

    # Legacy string (backward compatible) or env-driven "auto".
    if isinstance(raw, str):
        key = raw.lower()
        if key in _LEGACY_SPEED_MAP:
            cfg = AccelConfig(steps=default_steps, **_LEGACY_SPEED_MAP[key])
        else:
            # Unknown string: fall back to auto (env-driven) semantics.
            cfg = AccelConfig(steps=default_steps)
        return _apply_env_defaults(cfg)

    if not isinstance(raw, dict):
        raise AccelConfigError(
            f"speed must be a string or object, got {type(raw).__name__}"
        )

    steps = raw.get("steps", default_steps)
    if not isinstance(steps, int) or steps <= 0:
        raise AccelConfigError(f"steps must be a positive integer, got {steps!r}")

    turbo = raw.get("turbo", "none")
    block_cache = raw.get("block_cache", "none")
    step_cache = raw.get("step_cache", "none")
    attention = raw.get("attention", "none")
    vae_decode = raw.get("vae_decode", "default")
    precision = raw.get("precision", {})

    _validate_value("turbo", turbo, TURBO_OPTIONS)
    _validate_value("block_cache", block_cache, BLOCK_CACHE_OPTIONS)
    _validate_value("step_cache", step_cache, STEP_CACHE_OPTIONS)
    _validate_value("attention", attention, ATTENTION_OPTIONS)
    _validate_value("vae_decode", vae_decode, VAE_DECODE_OPTIONS)

    if not isinstance(precision, dict):
        raise AccelConfigError("precision must be an object")
    for k, v in precision.items():
        if k not in ("unet", "text_encoder", "vae"):
            raise AccelConfigError(f"unknown precision key {k!r}")
        _validate_value(f"precision.{k}", v, PRECISION_OPTIONS)

    cfg = AccelConfig(
        steps=steps,
        turbo=turbo,
        block_cache=block_cache,
        step_cache=step_cache,
        attention=attention,
        vae_decode=vae_decode,
        precision=dict(precision),
    )
    _validate_combo(cfg)
    return cfg


def _apply_env_defaults(cfg: AccelConfig) -> AccelConfig:
    """Apply H3_* env overrides for legacy/auto configs (existing behavior)."""
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
