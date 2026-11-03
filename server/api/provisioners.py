"""Provisioning backends for custom nodes and Python packages.

The extension never accepts arbitrary install commands or URLs from a
request.  A capability adapter declares either a Manager registry spec or a
native pinned GitHub spec; this module detects which backends are available
and executes the declared spec.

Manager is optional.  When it is absent the native pinned backend remains
available, but manager-only specs fail with an explicit plan instead of
falling back to an unpinned source.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any, Dict, Optional

import aiohttp
from server import PromptServer

logger = logging.getLogger("comfy-rest-ext.provisioners")

MANAGER_PREFIXES = ("/manager", "/v2/manager")
MANAGER_TIMEOUT = 2.0
MANAGER_INSTALL_TIMEOUT = float(
    os.environ.get("COMFY_REST_MANAGER_INSTALL_TIMEOUT", "300")
)
MANAGER_INSTALL_POLL = 1.0
_MANAGER_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,200}")
_MANAGER_VERSION_RE = re.compile(r"[A-Za-z0-9_.+!~-]{1,100}")


class BackendUnavailable(Exception):
    """No configured backend can satisfy an install spec."""

    def __init__(self, message: str, plan: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.plan = plan or {}


class BackendError(Exception):
    """A selected backend failed while executing an install."""

    def __init__(self, message: str, plan: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.plan = plan or {}


def _base_url() -> Optional[str]:
    instance = getattr(PromptServer, "instance", None)
    port = getattr(instance, "port", None)
    if port is None:
        return None
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None
    if not 0 < port < 65536:
        return None
    return f"http://127.0.0.1:{port}"


async def _request(
    method: str,
    path: str,
    *,
    json_body: Optional[dict] = None,
    headers: Optional[dict] = None,
) -> tuple[int, Any, str]:
    base = _base_url()
    if base is None:
        raise BackendUnavailable(
            "ComfyUI server port is unavailable",
            {"backend": "manager", "reason": "server_port_unavailable"},
        )
    timeout = aiohttp.ClientTimeout(total=MANAGER_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(
            method,
            base + path,
            json=json_body,
            headers=headers,
        ) as response:
            text = await response.text()
            data: Any = None
            if text:
                try:
                    data = await response.json()
                except (ValueError, aiohttp.ContentTypeError):
                    data = None
            return response.status, data, text


async def _manager_request(
    method: str,
    path: str,
    *,
    json_body: Optional[dict] = None,
) -> tuple[int, Any, str]:
    try:
        return await _request(method, path, json_body=json_body)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise BackendError(
            "Manager request failed",
            {"backend": "manager", "reason": type(e).__name__},
        ) from e


async def manager_status() -> Dict[str, Any]:
    """Probe ComfyUI-Manager without assuming a route prefix."""
    base = _base_url()
    if base is None:
        return {
            "id": "manager",
            "available": False,
            "reason": "server_port_unavailable",
        }

    last_error = "manager_routes_not_found"
    for prefix in MANAGER_PREFIXES:
        try:
            status, _data, text = await _request("GET", f"{prefix}/version")
        except (aiohttp.ClientError, asyncio.TimeoutError, BackendUnavailable) as e:
            last_error = type(e).__name__
            continue
        if status == 200:
            return {
                "id": "manager",
                "available": True,
                "prefix": prefix,
                "version": text.strip(),
            }
        last_error = f"http_{status}"
    return {
        "id": "manager",
        "available": False,
        "reason": last_error,
    }


async def provisioner_report() -> Dict[str, Any]:
    """Return backend status for doctor/capability consumers."""
    manager = await manager_status()
    return {
        "backends": [
            dict(manager),
            {
                "id": "native",
                "available": True,
                "pinned_only": True,
            },
        ]
    }


def _manager_install_spec(spec: Dict[str, Any]) -> Dict[str, str]:
    manager_id = spec.get("manager_id")
    version = spec.get("manager_version", spec.get("version"))
    if not isinstance(manager_id, str) or not _MANAGER_ID_RE.fullmatch(manager_id):
        raise BackendError(
            "manager custom-node spec requires a valid manager_id",
            {"backend": "manager", "spec": dict(spec)},
        )
    if not isinstance(version, str) or not _MANAGER_VERSION_RE.fullmatch(version):
        raise BackendError(
            "manager custom-node spec requires an exact manager_version",
            {"backend": "manager", "spec": dict(spec)},
        )
    if version.lower() in ("latest", "nightly"):
        raise BackendError(
            "manager custom-node spec must pin an exact version or commit",
            {"backend": "manager", "spec": dict(spec)},
        )
    channel = spec.get("channel", "default")
    mode = spec.get("mode", "cache")
    if not isinstance(channel, str) or not channel:
        raise BackendError("invalid Manager channel", {"backend": "manager"})
    if not isinstance(mode, str) or not mode:
        raise BackendError("invalid Manager mode", {"backend": "manager"})
    return {
        "manager_id": manager_id,
        "version": version,
        "channel": channel,
        "mode": mode,
    }


def _custom_node_prefix(manager_prefix: str) -> str:
    suffix = "/manager"
    return manager_prefix[:-len(suffix)] if manager_prefix.endswith(suffix) else ""


def _installed_matches(data: Any, manager_id: str, target: Optional[str]) -> bool:
    if isinstance(data, dict):
        candidates = []
        for key, value in data.items():
            candidates.append(str(key))
            if isinstance(value, dict):
                candidates.extend(
                    str(value.get(field))
                    for field in ("id", "name", "title", "repository", "cnr_id", "aux_id")
                    if value.get(field)
                )
        wanted = manager_id.casefold()
        return any(
            wanted == candidate.casefold()
            or wanted in candidate.casefold()
            or (target and target.casefold() == candidate.casefold())
            for candidate in candidates
        )
    if isinstance(data, list):
        for value in data:
            if isinstance(value, str) and (
                manager_id == value or manager_id in value
                or (target and target == value)
            ):
                return True
            if isinstance(value, dict) and _installed_matches(
                {str(value.get("id") or value.get("name") or ""): value},
                manager_id,
                target,
            ):
                return True
    return False


async def _wait_for_install(
    prefix: str,
    manager_id: str,
    target: Optional[str],
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    started = False
    while True:
        status, data, _text = await _manager_request(
            "GET", f"{prefix}/queue/status"
        )
        if status != 200 or not isinstance(data, dict):
            raise BackendError(
                "Manager queue status is unavailable",
                {"backend": "manager", "status": status},
            )
        processing = bool(data.get("is_processing"))
        if processing:
            started = True
        if not processing:
            installed_status, installed, _installed_text = await _manager_request(
                "GET", f"{_custom_node_prefix(prefix)}/customnode/installed"
            )
            if installed_status == 200 and _installed_matches(
                installed, manager_id, target
            ):
                return
            if started:
                raise BackendError(
                    "Manager finished without installing the requested node",
                    {
                        "backend": "manager",
                        "manager_id": manager_id,
                        "queue": data,
                    },
                )
        if time.monotonic() >= deadline:
            raise BackendError(
                "Manager install timed out",
                {
                    "backend": "manager",
                    "manager_id": manager_id,
                    "queue": data,
                },
            )
        await asyncio.sleep(MANAGER_INSTALL_POLL)


async def install_custom_node(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Install a declared Manager registry node at an exact version."""
    status = await manager_status()
    if not status.get("available"):
        raise BackendUnavailable(
            "ComfyUI-Manager is not available for this install spec",
            {
                "backend": "manager",
                "manager": status,
                "spec": dict(spec),
            },
        )
    parsed = _manager_install_spec(spec)
    prefix = status["prefix"]
    target = spec.get("target")
    ui_id = f"comfy-rest-ext:{parsed['manager_id']}"
    payload = {
        "id": parsed["manager_id"],
        "version": parsed["version"],
        "selected_version": parsed["version"],
        "channel": parsed["channel"],
        "mode": parsed["mode"],
        "ui_id": ui_id,
        "skip_post_install": False,
    }
    logger.info(
        "Provisioning via ComfyUI-Manager: %s @ %s",
        parsed["manager_id"],
        parsed["version"],
    )
    task_payload = {
        "ui_id": ui_id,
        "client_id": "comfy-rest-ext",
        "kind": "install",
        "params": {
            "id": parsed["manager_id"],
            "version": parsed["version"],
            "selected_version": parsed["version"],
            "channel": parsed["channel"],
            "mode": parsed["mode"],
            "skip_post_install": False,
        },
    }
    response_status, _data, text = await _manager_request(
        "POST", f"{prefix}/queue/task", json_body=task_payload
    )
    if response_status in (404, 405):
        # ComfyUI-Manager < 4.2 used /queue/install instead of /queue/task.
        response_status, _data, text = await _manager_request(
            "POST", f"{prefix}/queue/install", json_body=payload
        )
    if response_status != 200:
        raise BackendError(
            "Manager rejected the custom-node install",
            {
                "backend": "manager",
                "status": response_status,
                "response": text[-2000:],
                "spec": dict(spec),
            },
        )
    start_status, _start_data, _start_text = await _manager_request(
        "POST", f"{prefix}/queue/start"
    )
    if start_status not in (200, 201):
        raise BackendError(
            "Manager could not start its install queue",
            {"backend": "manager", "status": start_status},
        )
    await _wait_for_install(
        prefix,
        parsed["manager_id"],
        target if isinstance(target, str) else None,
        MANAGER_INSTALL_TIMEOUT,
    )
    return {
        "kind": "custom_node",
        "backend": "manager",
        "manager_id": parsed["manager_id"],
        "version": parsed["version"],
        "target": target,
        "requires_restart": True,
    }


async def manager_reboot() -> Dict[str, Any]:
    """Ask Manager to reboot ComfyUI. Caller owns consent/security policy."""
    status = await manager_status()
    if not status.get("available"):
        raise BackendUnavailable(
            "ComfyUI-Manager is not available",
            {"backend": "manager", "manager": status},
        )
    response_status, _data, text = await _manager_request(
        "POST", f"{status['prefix']}/reboot", json_body={}
    )
    if response_status not in (200, 201, 202):
        raise BackendError(
            "Manager refused the reboot",
            {
                "backend": "manager",
                "status": response_status,
                "response": text[-2000:],
            },
        )
    return {"backend": "manager", "accepted": True}
