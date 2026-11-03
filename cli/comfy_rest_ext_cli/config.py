"""Persistent CLI profiles.

The CLI is an HTTP-only client, so profiles are intentionally small: base
URL, optional bearer token, request timeout, and an optional default model.
Environment variables override the selected profile; explicit CLI flags
override both.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_PROFILE = "default"
_PROFILE_RE = re.compile(r"[A-Za-z0-9_.-]{1,80}")
_FIELDS = {"base_url", "api_key", "timeout", "default_model"}
_ENV = {
    "base_url": "COMFY_REST_EXT_BASE_URL",
    "api_key": "COMFY_REST_EXT_API_KEY",
    "timeout": "COMFY_REST_EXT_TIMEOUT",
    "default_model": "COMFY_REST_EXT_DEFAULT_MODEL",
}


class ConfigError(ValueError):
    pass


def config_path() -> Path:
    override = os.environ.get("COMFY_REST_EXT_CONFIG")
    if override:
        return Path(override).expanduser()
    root = os.environ.get("XDG_CONFIG_HOME")
    if root:
        return Path(root).expanduser() / "comfy-rest-ext" / "config.toml"
    return Path.home() / ".config" / "comfy-rest-ext" / "config.toml"


def _parse_value(raw: str) -> Any:
    value = raw.strip()
    if not value:
        raise ConfigError("empty TOML value")
    if value.startswith('"'):
        return json.loads(value)
    if value.startswith("'"):
        if not value.endswith("'"):
            raise ConfigError("unterminated TOML string")
        return value[1:-1]
    if value in ("true", "false"):
        return value == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        raise ConfigError(f"unsupported TOML value: {raw}") from None


def _parse_fallback(text: str) -> Dict[str, Any]:
    """Parse the tiny TOML subset used by this CLI."""
    result: Dict[str, Any] = {"profiles": {}}
    current: Optional[Dict[str, Any]] = None
    for line_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            if not line.endswith("]"):
                raise ConfigError(f"invalid section on line {line_no}")
            section = line[1:-1].strip()
            parts = section.split(".")
            if len(parts) != 2 or parts[0] != "profiles" or not _PROFILE_RE.fullmatch(parts[1]):
                raise ConfigError(f"invalid section on line {line_no}")
            current = result["profiles"].setdefault(parts[1], {})
            continue
        if "=" not in line:
            raise ConfigError(f"invalid assignment on line {line_no}")
        key, raw = line.split("=", 1)
        key = key.strip()
        if current is None:
            if key == "default_profile":
                value = _parse_value(raw)
                if not isinstance(value, str) or not _PROFILE_RE.fullmatch(value):
                    raise ConfigError("invalid default_profile")
                result[key] = value
            else:
                raise ConfigError(f"unknown top-level key: {key}")
        else:
            if key not in _FIELDS:
                raise ConfigError(f"unknown profile key: {key}")
            current[key] = _parse_value(raw)
    return result


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or config_path()
    if not path.exists():
        return {"default_profile": DEFAULT_PROFILE, "profiles": {}}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"cannot read config {path}: {e}") from e
    try:
        import tomllib  # type: ignore
    except ImportError:
        data = _parse_fallback(text)
    else:
        try:
            data = tomllib.loads(text)
        except Exception as e:  # tomllib.TOMLDecodeError
            raise ConfigError(f"invalid TOML in {path}: {e}") from e
    profiles = data.get("profiles") or {}
    if not isinstance(profiles, dict):
        raise ConfigError("profiles must be a table")
    for name, values in profiles.items():
        if not isinstance(name, str) or not _PROFILE_RE.fullmatch(name):
            raise ConfigError(f"invalid profile name: {name}")
        if not isinstance(values, dict):
            raise ConfigError(f"profile {name} must be a table")
        for key in values:
            if key not in _FIELDS:
                raise ConfigError(f"unknown profile key: {key}")
    default = data.get("default_profile", DEFAULT_PROFILE)
    if not isinstance(default, str) or not _PROFILE_RE.fullmatch(default):
        raise ConfigError("invalid default_profile")
    return {"default_profile": default, "profiles": profiles}


def _toml_string(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def save_config(data: Dict[str, Any], path: Optional[Path] = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"default_profile = {_toml_string(data.get('default_profile', DEFAULT_PROFILE))}", ""]
    for name, values in sorted((data.get("profiles") or {}).items()):
        if not _PROFILE_RE.fullmatch(name):
            raise ConfigError(f"invalid profile name: {name}")
        lines.append(f"[profiles.{name}]")
        for key in sorted(values):
            value = values[key]
            if key not in _FIELDS:
                raise ConfigError(f"unknown profile key: {key}")
            if key == "timeout":
                lines.append(f"timeout = {float(value)}")
            else:
                lines.append(f"{key} = {_toml_string(value)}")
        lines.append("")
    body = "\n".join(lines).rstrip() + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".config.", suffix=".toml", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def selected_profile(data: Dict[str, Any]) -> str:
    return (
        os.environ.get("COMFY_REST_EXT_PROFILE")
        or data.get("default_profile")
        or DEFAULT_PROFILE
    )


def profile_values(data: Dict[str, Any], name: Optional[str] = None) -> Dict[str, Any]:
    selected = name or selected_profile(data)
    return dict((data.get("profiles") or {}).get(selected) or {})


def resolve(
    data: Dict[str, Any],
    *,
    profile: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Resolve explicit flag > environment > profile > default."""
    values = profile_values(data, profile)
    result: Dict[str, Any] = {}
    for key in _FIELDS:
        env = os.environ.get(_ENV[key])
        if key == "base_url":
            value = base_url or env or values.get(key) or "http://127.0.0.1:8188"
        elif key == "api_key":
            value = api_key or env or values.get(key) or ""
        elif key == "timeout":
            raw = timeout if timeout is not None else env if env is not None else values.get(key)
            try:
                value = float(raw) if raw not in (None, "") else 60.0
            except (TypeError, ValueError):
                raise ConfigError("timeout must be a number") from None
        else:
            value = env or values.get(key) or ""
        result[key] = value
    return result


def redact(data: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"default_profile": data.get("default_profile", DEFAULT_PROFILE), "profiles": {}}
    for name, values in sorted((data.get("profiles") or {}).items()):
        copy = dict(values)
        if copy.get("api_key"):
            copy["api_key"] = "***"
        out["profiles"][name] = copy
    return out
