"""
Node pack/validate endpoints (Phase 6).

Provides node packing, validation, and initialization.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import zipfile
from typing import Any, Dict, List, Optional

from aiohttp import web
from server import PromptServer

from ..schemas.requests import NodePackRequest, NodeValidateRequest, NodeInitRequest

routes = PromptServer.instance.routes

logger = logging.getLogger("comfy-rest-ext.nodes")


def get_custom_nodes_dir() -> str:
    """Get the ComfyUI custom_nodes directory."""
    try:
        import sys
        sys.path.insert(0, '/comfy/mnt/ComfyUI')
        from folder_paths import get_folder_paths
        paths = get_folder_paths("custom_nodes")
        if paths:
            return paths[0]
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "ComfyUI", "custom_nodes")


def load_comfyignore(node_path: str) -> set:
    """Load .comfyignore patterns from node directory."""
    ignore_file = os.path.join(node_path, ".comfyignore")
    if not os.path.exists(ignore_file):
        return set()

    patterns = set()
    try:
        import pathspec
        with open(ignore_file) as f:
            patterns = pathspec.parse_gitignore(f)
    except ImportError:
        # Fallback: simple line-by-line parsing
        with open(ignore_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.add(line)
    except Exception:
        pass

    return patterns


def get_git_tracked_files(node_path: str) -> List[str]:
    """Get list of files tracked by git in the node directory."""
    try:
        result = subprocess.run(
            ["git", "-C", node_path, "ls-files"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return [f.strip() for f in result.stdout.splitlines() if f.strip()]
    except Exception as e:
        logger.debug(f"Git not available or not a git repo: {e}")
    return []


def should_ignore_path(path: str, ignore_patterns: set) -> bool:
    """Check if a path should be ignored based on ignore patterns."""
    import fnmatch
    for pattern in ignore_patterns:
        if fnmatch.fnmatch(path, pattern):
            return True
        # Also check directory parts
        parts = path.split(os.sep)
        for part in parts:
            if fnmatch.fnmatch(part, pattern):
                return True
    return False


@routes.post("/v2/extension/nodes/pack")
async def pack_node(request: web.Request) -> web.Response:
    """
    Pack a node directory into a zip archive.

    Body:
    - node_name: str - name of the node directory
    - respect_comfyignore: bool - apply .comfyignore rules (default: true)
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"error": "Invalid JSON body"},
            status=400,
        )

    try:
        req = NodePackRequest(**body)
    except Exception as e:
        return web.json_response(
            {"error": f"Invalid request: {e}"},
            status=400,
        )

    node_name = req.node_name
    respect_comfyignore = req.respect_comfyignore

    # Find node directory
    nodes_dir = get_custom_nodes_dir()
    node_path = os.path.join(nodes_dir, node_name)

    if not os.path.exists(node_path):
        return web.json_response(
            {"error": f"Node not found: {node_name}"},
            status=404,
        )

    if not os.path.isdir(node_path):
        return web.json_response(
            {"error": f"Node is not a directory: {node_name}"},
            status=400,
        )

    # Get list of files to include
    files = get_git_tracked_files(node_path)

    if not files:
        # Fall back to walking the directory
        for root, dirs, filenames in os.walk(node_path):
            for fname in filenames:
                fpath = os.path.relpath(os.path.join(root, fname), node_path)
                if not fpath.startswith("."):
                    files.append(fpath)

    # Apply .comfyignore if requested
    ignore_patterns = set()
    if respect_comfyignore:
        ignore_patterns = load_comfyignore(node_path)
        files = [f for f in files if not should_ignore_path(f, ignore_patterns)]

    # Create zip
    output_path = os.path.join(nodes_dir, f"{node_name}.zip")

    try:
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fpath in files:
                full_path = os.path.join(node_path, fpath)
                if os.path.exists(full_path) and os.path.isfile(full_path):
                    zf.write(full_path, fpath)

        stat = os.stat(output_path)

        return web.json_response({
            "success": True,
            "node_name": node_name,
            "path": output_path,
            "size": stat.st_size,
            "files_included": len(files),
        })

    except Exception as e:
        logger.exception(f"Error packing node {node_name}")
        return web.json_response(
            {"error": str(e)},
            status=500,
        )


@routes.post("/v2/extension/nodes/validate")
async def validate_node(request: web.Request) -> web.Response:
    """
    Validate a node for common issues.

    Body:
    - node_name: str - name of the node directory

    Checks:
    - Security: uses of eval, exec, shell commands
    - Syntax errors
    - Import issues
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"error": "Invalid JSON body"},
            status=400,
        )

    try:
        req = NodeValidateRequest(**body)
    except Exception as e:
        return web.json_response(
            {"error": f"Invalid request: {e}"},
            status=400,
        )

    node_name = req.node_name

    # Find node directory
    nodes_dir = get_custom_nodes_dir()
    node_path = os.path.join(nodes_dir, node_name)

    if not os.path.exists(node_path):
        return web.json_response(
            {"error": f"Node not found: {node_name}"},
            status=404,
        )

    warnings = []
    errors = []

    # Run ruff if available
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", node_path,
             "--select", "S102,S307,E702,E701",
             "--ignore", "S101"],
            capture_output=True,
            text=True,
            timeout=60,
        )

        for line in result.stdout.splitlines():
            if "S102" in line or "S307" in line or "E702" in line:
                warnings.append(line)
            elif "E" in line:
                errors.append(line)

    except FileNotFoundError:
        # ruff not installed, skip
        pass
    except Exception as e:
        logger.debug(f"Ruff validation skipped: {e}")

    # Basic Python syntax check
    py_files = []
    for root, _, files in os.walk(node_path):
        for fname in files:
            if fname.endswith(".py"):
                py_files.append(os.path.join(root, fname))

    for pyfile in py_files:
        try:
            with open(pyfile) as f:
                compile(f.read(), pyfile, "exec")
        except SyntaxError as e:
            errors.append(f"{os.path.relpath(pyfile, node_path)}: {e}")

    # Check for __init__.py
    has_init = any(
        os.path.exists(os.path.join(node_path, d, "__init__.py"))
        for d in os.listdir(node_path)
        if os.path.isdir(os.path.join(node_path, d))
    )

    if not has_init:
        init_py = os.path.join(node_path, "__init__.py")
        if not os.path.exists(init_py):
            warnings.append("Missing __init__.py - node may not be properly importable")

    return web.json_response({
        "node_name": node_name,
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "files_checked": len(py_files),
    })


@routes.post("/v2/extension/nodes/init")
async def init_node(request: web.Request) -> web.Response:
    """
    Initialize a new node project structure.

    Body:
    - path: str - path where to create the node
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"error": "Invalid JSON body"},
            status=400,
        )

    try:
        req = NodeInitRequest(**body)
    except Exception as e:
        return web.json_response(
            {"error": f"Invalid request: {e}"},
            status=400,
        )

    node_path = req.path

    if os.path.exists(node_path):
        return web.json_response(
            {"error": f"Path already exists: {node_path}"},
            status=409,
        )

    # Get git remote URL if available
    git_url = None
    try:
        result = subprocess.run(
            ["git", "-C", node_path, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            git_url = result.stdout.strip()
    except Exception:
        pass

    # Try parent directory if not a git repo
    if not git_url and os.path.dirname(node_path):
        try:
            result = subprocess.run(
                ["git", "-C", os.path.dirname(node_path), "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                git_url = result.stdout.strip()
        except Exception:
            pass

    try:
        os.makedirs(node_path, exist_ok=True)

        # Determine node name from path
        node_name = os.path.basename(node_path)

        # Create pyproject.toml
        pyproject = f"""[project]
name = "{node_name}"
version = "0.1.0"
description = "ComfyUI custom node"
requires-python = ">=3.10"

[project.optional-dependencies]
dev = [
    "ruff>=0.1.0",
]

[tool.ruff]
line-length = 120
"""

        with open(os.path.join(node_path, "pyproject.toml"), "w") as f:
            f.write(pyproject)

        # Create __init__.py
        init_content = f'''"""ComfyUI custom node: {node_name}"""

from typing import List, Type
from comfy_api.latest import io


class {node_name.replace("-", "_").replace(" ", "_")}Node:
    """Node description."""

    @classmethod
    def INPUT_TYPES(cls):
        return {{
            "required": {{}},
            "optional": {{}},
        }}

    RETURN_TYPES = ()
    FUNCTION = "execute"
    CATEGORY = "custom"

    def execute(self, **kwargs):
        return ()


NODE_CLASS_MAPPINGS = {{
    "{node_name}": {node_name.replace("-", "_").replace(" ", "_")}Node,
}}

__all__ = ["NODE_CLASS_MAPPINGS"]
'''

        with open(os.path.join(node_path, "__init__.py"), "w") as f:
            f.write(init_content)

        # Create README.md
        readme_content = f"""# {node_name}

ComfyUI custom node.

## Installation

Copy this directory to `custom_nodes/` in your ComfyUI installation.

## Usage

Describe how to use this node.
"""

        with open(os.path.join(node_path, "README.md"), "w") as f:
            f.write(readme_content)

        return web.json_response({
            "success": True,
            "path": node_path,
            "node_name": node_name,
            "git_url": git_url,
            "files_created": ["pyproject.toml", "__init__.py", "README.md"],
        })

    except Exception as e:
        logger.exception(f"Error initializing node at {node_path}")
        return web.json_response(
            {"error": str(e)},
            status=500,
        )


@routes.get("/v2/extension/nodes/list")
async def list_nodes(request: web.Request) -> web.Response:
    """List all installed custom nodes."""
    nodes_dir = get_custom_nodes_dir()

    if not os.path.exists(nodes_dir):
        return web.json_response({"nodes": []})

    nodes = []
    for item in os.listdir(nodes_dir):
        item_path = os.path.join(nodes_dir, item)
        if os.path.isdir(item_path):
            # Check if it's a valid node (has __init__.py or is a git repo)
            has_init = os.path.exists(os.path.join(item_path, "__init__.py"))
            is_git = os.path.exists(os.path.join(item_path, ".git"))

            nodes.append({
                "name": item,
                "path": item_path,
                "has_init": has_init,
                "is_git": is_git,
            })

    return web.json_response({
        "nodes": nodes,
        "count": len(nodes),
    })
