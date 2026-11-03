"""Controlled ComfyUI restart adapters.

comfy-rest-ext never calls os.execv or accepts a command from a request.
It can only use one of the adapters below, in order:

1. ComfyUI-Restart-Control's guarded /safe-restart API;
2. ComfyUI-Manager's /manager/reboot;
3. no adapter: return an explicit external-restart plan.

The CLI must compare the boot_id from /v2/extension/health before and after
the restart.  A reachable port is not proof that the process restarted.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional

from aiohttp import web
from server import PromptServer

from .boot import BOOT_ID
from .provisioners import (
    BackendError,
    BackendUnavailable,
    _base_url,
    _request,
    manager_reboot,
    manager_status,
)

logger = logging.getLogger("comfy-rest-ext.restart")
routes = PromptServer.instance.routes

SAFE_RESTART_PREFIX = "/safe-restart"
SAFE_RESTART_TOKEN_HEADER = "X-ComfyUI-Safe-Restart-Token"


class RestartError(Exception):
    def __init__(self, status: int, message: str, plan: Optional[dict] = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.plan = plan or {}


def restart_mode() -> str:
    mode = os.environ.get("COMFY_REST_RESTART_MODE", "ask").lower()
    return mode if mode in ("off", "ask") else "off"


async def _safe_restart_status() -> Dict[str, Any]:
    try:
        status, data, _text = await _request(
            "GET", f"{SAFE_RESTART_PREFIX}/capabilities"
        )
    except Exception as e:
        return {
            "id": "safe-restart",
            "available": False,
            "reason": type(e).__name__,
        }
    if status != 200 or not isinstance(data, dict):
        return {
            "id": "safe-restart",
            "available": False,
            "reason": f"http_{status}",
        }
    token = data.get("restart_token")
    if not isinstance(token, str) or not token:
        return {
            "id": "safe-restart",
            "available": False,
            "reason": "missing_restart_token",
        }
    return {
        "id": "safe-restart",
        "available": True,
        "boot_id": data.get("boot_id"),
        "restart_in_progress": bool(data.get("restart_in_progress")),
        "token": token,
    }


def _active_task_count() -> int:
    """Count work that would be lost or interrupted by a restart."""
    count = 0
    instance = getattr(PromptServer, "instance", None)
    prompt_queue = getattr(instance, "prompt_queue", None)
    if prompt_queue is not None:
        try:
            current = prompt_queue.get_current_queue()
            if isinstance(current, (tuple, list)) and len(current) >= 2:
                count += len(current[0]) + len(current[1])
        except Exception:
            logger.warning("Unable to inspect the ComfyUI queue", exc_info=True)

    try:
        from .tasks.video_task import get_video_manager

        count += len(get_video_manager().list_active())
    except Exception:
        logger.warning("Unable to inspect video task state", exc_info=True)
    return count


def _public_backend(status: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in status.items()
        if key not in ("token",)
    }


async def restart_capabilities() -> Dict[str, Any]:
    mode = restart_mode()
    safe = await _safe_restart_status()
    manager = await manager_status()
    backends = [
        _public_backend(safe),
        _public_backend(manager),
        {
            "id": "external",
            "available": True,
            "action": "restart ComfyUI through the process supervisor",
        },
    ]
    active = _active_task_count()
    return {
        "mode": mode,
        "allowed": mode != "off",
        "active_tasks": active,
        "restart_safe": active == 0,
        "backends": backends,
    }


def _safe_origin() -> Optional[str]:
    return _base_url()


async def trigger_restart(
    *,
    authorized: bool,
    confirm: str,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Trigger one of the allow-listed adapters, or return a plan."""
    mode = restart_mode()
    if mode == "off":
        raise RestartError(
            409,
            "restart is disabled",
            {"backend": "external", "mode": mode},
        )
    if not authorized or confirm != "restart":
        raise RestartError(
            409,
            "restart authorization required",
            {"backend": "external", "mode": mode},
        )
    active = _active_task_count()
    if active:
        raise RestartError(
            409,
            "active ComfyUI tasks; restart refused",
            {"backend": "external", "active_tasks": active},
        )

    safe = await _safe_restart_status()
    if safe.get("available"):
        if safe.get("restart_in_progress"):
            raise RestartError(
                409,
                "a restart is already in progress",
                {"backend": "safe-restart"},
            )
        origin = _safe_origin()
        if not origin:
            raise RestartError(
                503,
                "cannot determine the local ComfyUI origin",
                {"backend": "safe-restart"},
            )
        status, _data, text = await _request(
            "POST",
            f"{SAFE_RESTART_PREFIX}/restart",
            json_body={"safe_mode": False},
            headers={
                SAFE_RESTART_TOKEN_HEADER: safe["token"],
                "Origin": origin,
            },
        )
        if status not in (200, 202):
            raise RestartError(
                502,
                "safe-restart refused the restart",
                {
                    "backend": "safe-restart",
                    "status": status,
                    "response": text[-2000:],
                },
            )
        return {
            "accepted": True,
            "backend": "safe-restart",
            "boot_id": BOOT_ID,
            "reason": reason,
        }

    manager = await manager_status()
    if manager.get("available"):
        async def reboot_later() -> None:
            try:
                await manager_reboot()
            except Exception:
                logger.exception("Manager reboot failed after acceptance")

        # Manager may replace the process before writing its HTTP response.
        # Return the acceptance first; the CLI verifies a new boot_id.
        asyncio.create_task(reboot_later())
        return {
            "accepted": True,
            "backend": "manager",
            "boot_id": BOOT_ID,
            "reason": reason,
        }

    raise RestartError(
        501,
        "no automatic restart adapter is available",
        {
            "backend": "external",
            "action": "restart ComfyUI through the process supervisor, then run wait-reconnect",
        },
    )


@routes.get("/v2/extension/restart/capabilities")
async def get_restart_capabilities(request: web.Request) -> web.Response:
    return web.json_response(
        await restart_capabilities(),
        headers={"Cache-Control": "no-store"},
    )


@routes.post("/v2/extension/restart")
async def restart(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    confirm = body.get("confirm")
    if not isinstance(confirm, str):
        confirm = ""
    reason = body.get("reason")
    if reason is not None and not isinstance(reason, str):
        return web.json_response({"error": "reason must be a string"}, status=400)
    try:
        result = await trigger_restart(
            authorized=body.get("authorized") is True,
            confirm=confirm,
            reason=reason,
        )
    except RestartError as e:
        return web.json_response(
            {"error": e.message, "plan": e.plan},
            status=e.status,
        )
    except (BackendUnavailable, BackendError) as e:
        return web.json_response(
            {"error": e.message, "plan": e.plan},
            status=503,
        )
    return web.json_response(result, status=202)
