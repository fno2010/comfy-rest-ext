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
import re
import subprocess
import sys
from pathlib import Path
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


def _custom_nodes_dir() -> Path:
    override = os.environ.get("COMFYUI_CUSTOM_NODES")
    if override:
        return Path(override).expanduser()
    try:
        import folder_paths
        paths = folder_paths.get_folder_paths("custom_nodes")
        if paths:
            return Path(paths[0])
    except Exception:
        pass
    raise ProvisionError(500, "cannot resolve ComfyUI custom_nodes directory")


async def _install_custom_node(spec: Dict[str, Any]) -> Dict[str, Any]:
    repo = spec.get("repo")
    commit = spec.get("commit")
    target_name = spec.get("target")
    if not isinstance(repo, str) or not re.fullmatch(
        r"https://github\.com/[^/]+/[^/]+(?:\.git)?", repo
    ):
        raise ProvisionError(400, "custom node repo must be a pinned GitHub URL")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ProvisionError(400, "custom node commit must be a 40-character SHA")
    if not isinstance(target_name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", target_name):
        raise ProvisionError(400, "custom node target is invalid")

    root = _custom_nodes_dir()
    target = root / target_name
    if target.exists():
        try:
            current = subprocess.check_output(
                ["git", "-C", str(target), "rev-parse", "HEAD"],
                text=True,
            ).strip()
        except Exception:
            raise ProvisionError(
                409, f"custom node target exists but is not a git checkout: {target}"
            )
        if current != commit:
            raise ProvisionError(
                409,
                f"custom node target is at {current}, expected {commit}",
            )
        return {"kind": "custom_node", "target": str(target), "skipped": True}

    root.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone", "--no-checkout", repo, str(target)]
    logger.info("Provisioning custom node: %s @ %s", repo, commit)
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
            502, f"git clone failed for {repo}",
            {"output_tail": "".join(output[-20:])},
        )
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(target), "checkout", commit,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    await proc.wait()
    if proc.returncode != 0:
        raise ProvisionError(502, f"git checkout failed for {commit}")
    return {"kind": "custom_node", "target": str(target), "commit": commit}


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
    retry_task_id: Optional[str] = None,
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
        result = {"status": "completed", "capabilities": selected, "actions": []}
        if retry_task_id:
            from .tasks.resume import ResumeError, resume_blocked_video_task
            try:
                task = await resume_blocked_video_task(retry_task_id)
            except ResumeError as e:
                raise ProvisionError(
                    e.status, e.message,
                    {"resume_plan": e.plan, "task_id": retry_task_id},
                ) from e
            result.update({
                "retry_status": "queued",
                "retry_task": {"id": task.task_id, "status": task.status},
            })
        return result

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
        if spec.get("kind") == "python_package":
            actions.append(await _install_python_package(spec))
        elif spec.get("kind") == "custom_node":
            actions.append(await _install_custom_node(spec))
        else:
            raise ProvisionError(
                409,
                f"no automatic installer for {item['id']}",
                {"capability": item},
            )

    after = _select_capabilities(ids)
    restart_required = any(
        spec.get("requires_restart")
        for item in installable
        for spec in [item.get("install_spec") or {}]
    )
    result = {
        "status": "completed",
        "actions": actions,
        "capabilities": after,
        "restart_required": bool(restart_required),
        "retry_task_id": retry_task_id,
    }
    if retry_task_id:
        if restart_required:
            result["retry_status"] = "waiting_for_restart"
        else:
            from .tasks.resume import ResumeError, resume_blocked_video_task
            try:
                task = await resume_blocked_video_task(retry_task_id)
            except ResumeError as e:
                raise ProvisionError(
                    e.status, e.message,
                    {"resume_plan": e.plan, "task_id": retry_task_id},
                ) from e
            result["retry_status"] = "queued"
            result["retry_task"] = {
                "id": task.task_id,
                "status": task.status,
            }
    return result


@routes.post("/v2/extension/provision")
async def provision(request: web.Request) -> web.Response:
    """Provision explicit capabilities, subject to policy/authorization."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    ids = body.get("capabilities")
    retry_task_id = body.get("retry_task_id")
    if retry_task_id is not None and not isinstance(retry_task_id, str):
        return web.json_response(
            {"error": "retry_task_id must be a string"}, status=400
        )
    if ids is not None and (
        not isinstance(ids, list)
        or not all(isinstance(item, str) and item for item in ids)
    ):
        return web.json_response(
            {"error": "capabilities must be a list of ids"}, status=400
        )
    try:
        result = await provision_capabilities(
            ids,
            authorized=bool(body.get("authorized", False)),
            retry_task_id=retry_task_id,
        )
    except ProvisionError as e:
        return web.json_response(
            {"error": e.message, "plan": e.plan}, status=e.status
        )
    return web.json_response(result)
