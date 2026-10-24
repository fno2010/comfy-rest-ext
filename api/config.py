"""Process-level configuration for request-time H3 settings.

The endpoint only mutates a small whitelist of environment variables that the
workflow/task builder reads at request time. Import-time settings (notably the
H3 batched-VAE monkeypatch) are reported with ``restart_required: true`` and
cannot be changed through the API.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from aiohttp import web
from server import PromptServer

routes = PromptServer.instance.routes


@dataclass(frozen=True)
class Setting:
    key: str
    kind: str
    default: Any
    restart_required: bool = False
    minimum: Optional[int] = None
    maximum: Optional[int] = None

    def schema(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "type": self.kind,
            "default": self.default,
            "restart_required": self.restart_required,
            "editable": not self.restart_required,
        }
        if self.minimum is not None:
            result["min"] = self.minimum
        if self.maximum is not None:
            result["max"] = self.maximum
        return result


SETTINGS: Dict[str, Setting] = {
    "H3_STEPS": Setting("H3_STEPS", "int", 20, minimum=1, maximum=200),
    "H3_SAMPLER": Setting("H3_SAMPLER", "str", "euler"),
    "H3_SCHEDULER": Setting("H3_SCHEDULER", "str", "simple"),
    "H3_REF_SIZE": Setting("H3_REF_SIZE", "str", "match"),
    "H3_TE_SPEED": Setting("H3_TE_SPEED", "bool", False),
    "H3_SOL_STACK": Setting("H3_SOL_STACK", "bool", False),
    "COMFY_REST_UNLOAD_ON_FAMILY_SWITCH": Setting(
        "COMFY_REST_UNLOAD_ON_FAMILY_SWITCH", "bool", True,
    ),
    # Read at import; changing requires a process restart.
    "H3_VAE_BATCHED": Setting(
        "H3_VAE_BATCHED", "bool", True, restart_required=True,
    ),
}


class ConfigError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _current(setting: Setting) -> Any:
    raw = os.environ.get(setting.key)
    if raw is None:
        return setting.default
    if setting.kind == "bool":
        if raw == "1":
            return True
        if raw == "0":
            return False
        # Default-true consumers treat any non-"0" as enabled; default-false
        # consumers require "1". Preserve the setting's effective default.
        return setting.default
    if setting.kind == "int":
        try:
            return int(raw)
        except ValueError:
            return setting.default
    return raw


def config_payload() -> Dict[str, Any]:
    settings: Dict[str, Any] = {}
    for key, setting in SETTINGS.items():
        entry = setting.schema()
        entry["value"] = _current(setting)
        entry["source"] = "env" if key in os.environ else "default"
        settings[key] = entry
    return {"settings": settings}


def _coerce(setting: Setting, value: Any) -> Any:
    if setting.kind == "bool":
        if not isinstance(value, bool):
            raise ConfigError(400, f"{setting.key} must be a boolean")
        return value
    if setting.kind == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ConfigError(400, f"{setting.key} must be an integer")
        if setting.minimum is not None and value < setting.minimum:
            raise ConfigError(400, f"{setting.key} must be >= {setting.minimum}")
        if setting.maximum is not None and value > setting.maximum:
            raise ConfigError(400, f"{setting.key} must be <= {setting.maximum}")
        return value
    if setting.kind == "str":
        if not isinstance(value, str) or not value:
            raise ConfigError(400, f"{setting.key} must be a non-empty string")
        if "\x00" in value:
            raise ConfigError(400, f"{setting.key} must not contain NUL")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ConfigError(400, f"{setting.key} must be UTF-8 encodable") from None
        return value
    raise ConfigError(400, f"{setting.key} has an unsupported type")


def apply_settings(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ConfigError(400, "config body must be an object")
    values = payload.get("settings")
    if not isinstance(values, dict) or not values:
        raise ConfigError(400, "settings must be a non-empty object")

    updates: Dict[str, Any] = {}
    for key, value in values.items():
        setting = SETTINGS.get(key)
        if setting is None:
            raise ConfigError(400, f"unknown setting {key!r}")
        if setting.restart_required:
            raise ConfigError(
                409,
                f"{key} is read at import time and requires a restart",
            )
        updates[key] = _coerce(setting, value)

    previous = {key: os.environ.get(key) for key in updates}
    try:
        for key, value in updates.items():
            if SETTINGS[key].kind == "bool":
                os.environ[key] = "1" if value else "0"
            else:
                os.environ[key] = str(value)
    except (ValueError, UnicodeEncodeError) as e:
        for key, old in previous.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        raise ConfigError(400, f"cannot apply settings: {e}") from e
    result = config_payload()
    result["updated"] = sorted(updates)
    return result


_lock = asyncio.Lock()


@routes.get("/v2/extension/config")
async def get_config(request: web.Request) -> web.Response:
    return web.json_response(config_payload())


@routes.post("/v2/extension/config")
async def set_config(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    try:
        async with _lock:
            return web.json_response(apply_settings(payload))
    except ConfigError as e:
        return web.json_response({"error": e.message}, status=e.status)
