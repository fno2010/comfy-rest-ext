"""
Snapshot management endpoints (Phase 5).

Provides snapshot export, import, and diff functionality
via delegation to ComfyUI Manager's cm-cli.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

from aiohttp import web
from server import PromptServer

from ..schemas.requests import SnapshotExportRequest, SnapshotImportRequest

routes = PromptServer.instance.routes

logger = logging.getLogger("comfy-rest-ext.snapshot")

# Find cm-cli path
def find_cm_cli() -> Optional[str]:
    """Find the ComfyUI Manager cm-cli script."""
    search_paths = [
        os.path.join(os.path.expanduser("~"), ".comfyui", "cm-cli.py"),
        "/usr/local/bin/cm-cli.py",
        "/opt/ComfyUI/manager/cm-cli.py",
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "manager", "cm-cli.py"),
    ]

    for path in search_paths:
        if os.path.exists(path):
            return path
    return None


@routes.post("/v2/extension/snapshot/export")
async def export_snapshot(request: web.Request) -> web.Response:
    """
    Export a snapshot to a file.

    Body:
    - snapshot_id: str - identifier for this snapshot
    - format: str - export format (default: tarball)
    - include_models: bool - include models in snapshot (default: false)
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"error": "Invalid JSON body"},
            status=400,
        )

    try:
        req = SnapshotExportRequest(**body)
    except Exception as e:
        return web.json_response(
            {"error": f"Invalid request: {e}"},
            status=400,
        )

    cm_cli = find_cm_cli()
    if cm_cli is None:
        return web.json_response(
            {"error": "ComfyUI Manager cm-cli not found"},
            status=503,
        )

    # Build output path
    snapshot_dir = os.path.join(os.path.expanduser("~"), ".comfyui", "snapshots")
    os.makedirs(snapshot_dir, exist_ok=True)
    output_path = os.path.join(snapshot_dir, f"{req.snapshot_id}.tar.gz")

    # Build command
    cmd = [
        sys.executable, cm_cli, "save-snapshot",
        "--output", output_path,
    ]
    if req.format == "tarball":
        cmd.append("--format")
        cmd.append("tarball")
    if req.include_models:
        cmd.append("--include-models")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode == 0:
            return web.json_response({
                "success": True,
                "snapshot_id": req.snapshot_id,
                "path": output_path,
            })
        else:
            return web.json_response(
                {
                    "error": "Export failed",
                    "stderr": result.stderr,
                },
                status=500,
            )

    except Exception as e:
        logger.exception("Error exporting snapshot")
        return web.json_response(
            {"error": str(e)},
            status=500,
        )


@routes.post("/v2/extension/snapshot/import")
async def import_snapshot(request: web.Request) -> web.Response:
    """
    Import a snapshot from a file.

    Body (multipart or JSON with path):
    - path: str - path to the snapshot file
    - restore_models: bool - restore models (default: true)
    - restore_nodes: bool - restore custom nodes (default: true)
    """
    content_type = request.content_type

    if "multipart" in content_type:
        # Handle file upload
        reader = await request.multipart()
        field = await reader.next()
        if field.name == "snapshot":
            snapshot_data = await field.read()
            # Save to temp file
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as f:
                f.write(snapshot_data)
                temp_path = f.name
    else:
        try:
            body = await request.json()
        except Exception:
            return web.json_response(
                {"error": "Invalid JSON body"},
                status=400,
            )

        path = body.get("path")
        if not path:
            return web.json_response(
                {"error": "Missing 'path' in request body"},
                status=400,
            )

        restore_models = body.get("restore_models", True)
        restore_nodes = body.get("restore_nodes", True)
        temp_path = None

    cm_cli = find_cm_cli()
    if cm_cli is None:
        return web.json_response(
            {"error": "ComfyUI Manager cm-cli not found"},
            status=503,
        )

    cmd = [sys.executable, cm_cli, "restore-snapshot", path or temp_path]
    if not restore_models:
        cmd.append("--skip-models")
    if not restore_nodes:
        cmd.append("--skip-nodes")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode == 0:
            return web.json_response({
                "success": True,
                "stdout": result.stdout,
            })
        else:
            return web.json_response(
                {
                    "error": "Import failed",
                    "stderr": result.stderr,
                },
                status=500,
            )

    except Exception as e:
        logger.exception("Error importing snapshot")
        return web.json_response(
            {"error": str(e)},
            status=500,
        )
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


@routes.get("/v2/extension/snapshot/diff")
async def diff_snapshots(request: web.Request) -> web.Response:
    """
    Compare two snapshots and show differences.

    Query params:
    - snapshot_a: str - path to first snapshot
    - snapshot_b: str - path to second snapshot
    """
    snapshot_a = request.query.get("snapshot_a")
    snapshot_b = request.query.get("snapshot_b")

    if not snapshot_a or not snapshot_b:
        return web.json_response(
            {"error": "Both 'snapshot_a' and 'snapshot_b' are required"},
            status=400,
        )

    try:
        # Load both snapshot JSONs
        def load_snapshot(path: str) -> dict:
            if not os.path.exists(path):
                raise FileNotFoundError(f"Snapshot not found: {path}")

            # cm-cli saves snapshots as tar.gz, need to extract
            if path.endswith(".tar.gz"):
                import tarfile
                import io
                with tarfile.open(path, "r:gz") as tar:
                    # Look for snapshot.json
                    for member in tar.getmembers():
                        if member.name.endswith("snapshot.json"):
                            f = tar.extractfile(member)
                            return json.load(f)
                raise ValueError("No snapshot.json found in archive")

            # Or it might be a JSON file directly
            with open(path) as f:
                return json.load(f)

        snap_a = load_snapshot(snapshot_a)
        snap_b = load_snapshot(snapshot_b)

        # Compare custom_nodes
        nodes_a = set(snap_a.get("custom_nodes", []))
        nodes_b = set(snap_b.get("custom_nodes", []))
        nodes_added = sorted(nodes_b - nodes_a)
        nodes_removed = sorted(nodes_a - nodes_b)
        nodes_common = sorted(nodes_a & nodes_b)

        # Compare pip_packages
        pkgs_a = set(snap_a.get("pip_packages", []))
        pkgs_b = set(snap_b.get("pip_packages", []))
        pkgs_added = sorted(pkgs_b - pkgs_a)
        pkgs_removed = sorted(pkgs_a - pkgs_b)
        pkgs_common = sorted(pkgs_a & pkgs_b)

        return web.json_response({
            "snapshot_a": snapshot_a,
            "snapshot_b": snapshot_b,
            "custom_nodes": {
                "added": nodes_added,
                "removed": nodes_removed,
                "common": nodes_common,
            },
            "pip_packages": {
                "added": pkgs_added,
                "removed": pkgs_removed,
                "common": pkgs_common,
            },
        })

    except FileNotFoundError as e:
        return web.json_response(
            {"error": str(e)},
            status=404,
        )
    except Exception as e:
        logger.exception("Error comparing snapshots")
        return web.json_response(
            {"error": str(e)},
            status=500,
        )


@routes.get("/v2/extension/snapshot/list")
async def list_snapshots(request: web.Request) -> web.Response:
    """List available snapshots."""
    snapshot_dir = os.path.join(os.path.expanduser("~"), ".comfyui", "snapshots")

    if not os.path.exists(snapshot_dir):
        return web.json_response({"snapshots": []})

    snapshots = []
    for fname in os.listdir(snapshot_dir):
        fpath = os.path.join(snapshot_dir, fname)
        if os.path.isfile(fpath):
            stat = os.stat(fpath)
            snapshots.append({
                "name": fname,
                "path": fpath,
                "size": stat.st_size,
                "modified": stat.st_mtime,
            })

    return web.json_response({
        "snapshots": sorted(snapshots, key=lambda x: x["modified"], reverse=True),
    })
