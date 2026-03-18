"""
Workflow dependency endpoints (Phase 3, 4).

Provides workflow dependency checking and installation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from typing import List, Optional

from aiohttp import web
from server import PromptServer

from ..schemas.requests import WorkflowDepsRequest, WorkflowDepsCheckRequest
from ..tasks import get_task_queue
from ..tasks.deps_task import (
    DepsTask,
    check_workflow_deps,
    install_packages,
    parse_workflow_dependencies,
)

routes = PromptServer.instance.routes

logger = logging.getLogger("comfy-rest-ext.deps")

# In-memory task storage
_deps_tasks: dict[str, DepsTask] = {}


async def _emit_websocket_event(event: str, data: dict) -> None:
    """Emit a WebSocket event to all connected clients."""
    ws = PromptServer.instance
    if hasattr(ws, "broadcast"):
        await ws.broadcast({"event": event, "data": data})


@routes.post("/v2/extension/workflow/dependencies")
async def install_workflow_deps(request: web.Request) -> web.Response:
    """
    Install dependencies for a workflow.

    Takes a workflow dict (same format as /prompt endpoint) and
    installs all required Python packages.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"error": "Invalid JSON body"},
            status=400,
        )

    try:
        req = WorkflowDepsRequest(**body)
    except Exception as e:
        return web.json_response(
            {"error": f"Invalid request: {e}"},
            status=400,
        )

    workflow = req.workflow
    async_install = req.async_install

    # Parse dependencies
    packages = parse_workflow_dependencies(workflow)

    if not packages:
        return web.json_response({
            "task_id": None,
            "status": "completed",
            "message": "No dependencies to install",
            "installed": [],
            "failed": [],
        })

    # Check which are already installed
    check_result = await check_workflow_deps(workflow)
    missing = check_result["missing"]

    if not missing:
        return web.json_response({
            "task_id": None,
            "status": "completed",
            "message": "All dependencies already satisfied",
            "installed": packages,
            "failed": [],
        })

    # Create task
    task = DepsTask(
        task_id="",
        status="queued",
        installed=[],
        failed=[],
    )

    async def run_install():
        task.status = "installing"
        try:
            installed, failed, restart = await install_packages(
                missing,
                task.task_id,
                get_task_queue().get_cancellation_event(task.task_id),
            )
            task.status = "completed"
            task.installed = installed
            task.failed = failed
            task.restart_required = restart

            await _emit_websocket_event(
                "extension-deps-install-complete",
                {
                    "task_id": task.task_id,
                    "installed": installed,
                    "failed": failed,
                    "restart_required": restart,
                },
            )

        except asyncio.CancelledError:
            task.status = "cancelled"
            await _emit_websocket_event(
                "extension-deps-install-cancelled",
                {"task_id": task.task_id},
            )
        except Exception as e:
            task.status = "failed"
            task.pip_output = str(e)
            await _emit_websocket_event(
                "extension-deps-install-failed",
                {"task_id": task.task_id, "error": str(e)},
            )

    task_id = get_task_queue().create_task(
        coro=run_install(),
        name="deps:install",
    )
    task.task_id = task_id
    _deps_tasks[task_id] = task

    return web.json_response({
        "task_id": task_id,
        "status": "queued",
        "packages_to_install": missing,
        "async": async_install,
    })


@routes.get("/v2/extension/workflow/dependencies/{task_id}")
async def get_deps_status(request: web.Request) -> web.Response:
    """Get the status of a dependency installation task."""
    task_id = request.match_info["task_id"]

    queue = get_task_queue()
    info = queue.get_info(task_id)
    task = _deps_tasks.get(task_id)

    if not info and not task:
        return web.json_response(
            {"error": "Task not found"},
            status=404,
        )

    response = {
        "task_id": task_id,
        "status": task.status if task else info.status.value,
        "progress": info.progress if info else 0.0,
    }

    if task:
        response["package"] = task.package
        response["installed"] = task.installed
        response["failed"] = task.failed
        response["restart_required"] = task.restart_required
        response["pip_output"] = task.pip_output

    return web.json_response(response)


@routes.post("/v2/extension/workflow/dependencies/check")
async def check_workflow_deps_endpoint(request: web.Request) -> web.Response:
    """
    Check workflow dependencies without installing.

    Returns missing packages, already satisfied packages, and whether
    the workflow can run.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"error": "Invalid JSON body"},
            status=400,
        )

    try:
        req = WorkflowDepsCheckRequest(**body)
    except Exception as e:
        return web.json_response(
            {"error": f"Invalid request: {e}"},
            status=400,
        )

    try:
        result = await check_workflow_deps(req.workflow)
        return web.json_response(result)
    except Exception as e:
        logger.exception("Error checking workflow dependencies")
        return web.json_response(
            {"error": str(e)},
            status=500,
        )


@routes.get("/v2/extension/dependencies/check")
async def check_node_deps(request: web.Request) -> web.Response:
    """
    Check dependencies for a specific node.

    Query params:
    - node: str - node name/class_type
    """
    node_name = request.query.get("node")
    if not node_name:
        return web.json_response(
            {"error": "Missing 'node' query parameter"},
            status=400,
        )

    # Find the node class
    try:
        import nodes
        if hasattr(nodes, "NODE_CLASS_MAPPINGS") and node_name in nodes.NODE_CLASS_MAPPINGS:
            cls = nodes.NODE_CLASS_MAPPINGS[node_name]
            module = getattr(cls, "__module__", None)

            packages = []
            if module:
                pkg_dir = os.path.dirname(module.replace(".", os.sep))
                req_file = os.path.join(pkg_dir, "requirements.txt")
                if os.path.exists(req_file):
                    with open(req_file) as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith("#"):
                                packages.append(line)

            # Check which are installed
            check_result = await check_workflow_deps({"node": {"class_type": node_name}})
            # This is a bit hacky - parse_workflow_dependencies needs a proper workflow
            # For now, just return the packages list
            return web.json_response({
                "node": node_name,
                "required_packages": packages,
            })

        else:
            return web.json_response(
                {"error": f"Node not found: {node_name}"},
                status=404,
            )

    except Exception as e:
        logger.exception(f"Error checking node deps for {node_name}")
        return web.json_response(
            {"error": str(e)},
            status=500,
        )


@routes.post("/v2/extension/dependencies/restore")
async def restore_node_deps(request: web.Request) -> web.Response:
    """
    Restore node dependencies using ComfyUI Manager's cm-cli.

    This is an async operation.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"error": "Invalid JSON body"},
            status=400,
        )

    nodes_list = body.get("nodes", [])
    async_mode = body.get("async_mode", True)

    # Find cm-cli
    cm_cli_paths = [
        os.path.join(os.path.expanduser("~"), ".comfyui", "cm-cli.py"),
        "/usr/local/bin/cm-cli.py",
        os.path.join(os.path.dirname(__file__), "..", "..", "cm-cli.py"),
    ]

    cm_cli = None
    for path in cm_cli_paths:
        if os.path.exists(path):
            cm_cli = path
            break

    if cm_cli is None:
        return web.json_response(
            {"error": "ComfyUI Manager cm-cli not found"},
            status=503,
        )

    if not async_mode:
        # Synchronous execution
        try:
            result = subprocess.run(
                [sys.executable, cm_cli, "restore-dependencies"] + nodes_list,
                capture_output=True,
                text=True,
                timeout=300,
            )
            return web.json_response({
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            })
        except Exception as e:
            return web.json_response(
                {"error": str(e)},
                status=500,
            )

    # Async execution via task queue
    async def run_restore():
        proc = await asyncio.create_subprocess_exec(
            sys.executable, cm_cli, "restore-dependencies", *nodes_list,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output = []
        async for line in proc.stdout:
            output.append(line.decode("utf-8", errors="replace"))
        await proc.wait()
        return proc.returncode, "".join(output)

    async def restore_coro():
        returncode, output = await run_restore()
        # Could emit WebSocket event here
        return {"returncode": returncode, "output": output}

    task_id = get_task_queue().create_task(
        coro=restore_coro(),
        name="deps:restore",
    )

    return web.json_response({
        "task_id": task_id,
        "status": "queued",
    })
