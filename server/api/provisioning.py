"""Consented capability provisioning.

This module deliberately installs only capabilities declared by the server
itself. It never accepts an arbitrary package name, URL, or command from a
request. The first supported installer is a Python package from PyPI; custom
node installation remains an explicit manual plan until a pinned source is
declared by the method adapter.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any, Dict, Iterable, List, Optional

from aiohttp import web
from server import PromptServer

from .accel import _missing_package_groups, get_method
from .capabilities import capability_report

routes = PromptServer.instance.routes
logger = logging.getLogger("comfy-rest-ext.provisioning")


class ProvisionError(Exception):
    def __init__(self, status: int, message: str, plan: Optional[dict] = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.plan = plan


def provision_mode() -> str:
    return os.environ.get("COMFY_REST_PROVISION_MODE", "ask").lower()


def provision_allowlist() -> set:
    raw = os.environ.get("COMFY_REST_PROVISION_ALLOW", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _authorized(ids: Iterable[str], explicit: bool) -> bool:
    mode = provision_mode()
    if mode == "never":
        return False
    if mode == "auto":
        allow = provision_allowlist()
        return "*" in allow and bool(ids) or all(item in allow for item in ids)
    if mode == "ask":
        return explicit
    return False


async def _install_python_package(spec: Dict[str, Any]) -> Dict[str, Any]:
    package = spec.get("package")
    if not isinstance(package, str) or not package:
        raise ProvisionError(400, "invalid Python package install spec")
    cmd = [sys.executable, "-m", "pip", "install", package]
    logger.info("Provisioning package: %s", package)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output = []
    if proc.stdout is not None:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            output.append(line.decode("utf-8", errors="replace"))
    await proc.wait()
    if proc.returncode != 0:
        raise ProvisionError(
            502,
            f"pip install failed for {package}",
            {"package": package, "output_tail": "".join(output[-20:])},
        )
    return {
        "kind": "python_package",
        "package": package,
        "returncode": proc.returncode,
    }


def _select_capabilities(ids: Optional[List[str]]) -> List[dict]:
    report = capability_report(capability_ids=ids)
    return report["capabilities"]


async def provision_capabilities(
    ids: Optional[List[str]] = None,
    authorized: bool = False,
) -> Dict[str, Any]:
    """Provision selected capabilities or return a structured plan.

    Raises ProvisionError when the caller is not authorized or a capability
    cannot be installed automatically.
    """
    selected = _select_capabilities(ids)
    if not selected:
        raise ProvisionError(404, "no matching capability found")

    blocked = [
        item for item in selected
        if item["status"] in ("unsupported",) and not item["available"]
    ]
    if blocked:
        raise ProvisionError(
            409,
            "some capabilities cannot be provisioned automatically",
            {"capabilities": selected},
        )

    installable = [item for item in selected if item["status"] == "installable"]
    if not installable:
        return {"status": "completed", "capabilities": selected, "actions": []}

    install_ids = [item["id"] for item in installable]
    if not _authorized(install_ids, authorized):
        raise ProvisionError(
            409,
            "provisioning authorization required",
            {
                "status": "authorization_required",
                "capabilities": installable,
                "mode": provision_mode(),
            },
        )

    actions = []
    for item in installable:
        spec = item.get("install_spec") or {}
        if spec.get("kind") != "python_package":
            raise ProvisionError(
                409,
                f"no automatic installer for {item['id']}",
                {"capability": item},
            )
        actions.append(await _install_python_package(spec))

    after = _select_capabilities(ids)
    return {
        "status": "completed",
        "actions": actions,
        "capabilities": after,
        "restart_required": any(
            item.get("requires_restart") for item in after
        ),
    }


@routes.post("/v2/extension/provision")
async def provision(request: web.Request) -> web.Response:
    """Provision explicit capabilities, subject to policy/authorization."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    ids = body.get("capabilities")
    if ids is not None and (
        not isinstance(ids, list)
        or not all(isinstance(item, str) and item for item in ids)
    ):
        return web.json_response(
            {"error": "capabilities must be a list of ids"}, status=400
        )
    try:
        result = await provision_capabilities(
            ids, authorized=bool(body.get("authorized", False))
        )
    except ProvisionError as e:
        return web.json_response(
            {"error": e.message, "plan": e.plan}, status=e.status
        )
    return web.json_response(result)
