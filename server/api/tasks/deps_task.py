"""
Dependency task implementation for workflow dependency installation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Literal, Optional

logger = logging.getLogger("comfy-rest-ext.deps")


@dataclass
class DepsTask:
    """Dependency installation task state."""
    task_id: str
    status: Literal["queued", "installing", "completed", "failed"]
    package: Optional[str] = None
    progress: float = 0.0
    pip_output: Optional[str] = None
    installed: List[str] = None
    failed: List[str] = None
    restart_required: bool = False

    def __post_init__(self):
        if self.installed is None:
            self.installed = []
        if self.failed is None:
            self.failed = []


def detect_gpu() -> str:
    """Detect GPU type for appropriate package installation."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass

    # Check for AMD GPUs
    try:
        result = subprocess.run(
            ["rocm-smi", "--id"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return "rocm"
    except Exception:
        pass

    return "cpu"


def parse_workflow_dependencies(workflow: dict) -> List[str]:
    """
    Parse a workflow dict and extract required Python packages.

    Returns a list of package specifications.
    """
    packages = set()

    if not isinstance(workflow, dict):
        return []

    # Look for nodes in the workflow
    # ComfyUI workflows have nodes indexed by ID
    for node_id, node_data in workflow.items():
        if not isinstance(node_data, dict):
            continue

        class_type = node_data.get("class_type")
        if not class_type:
            continue

        # Try to find the node class
        try:
            import nodes
            if hasattr(nodes, "NODE_CLASS_MAPPINGS") and class_type in nodes.NODE_CLASS_MAPPINGS:
                cls = nodes.NODE_CLASS_MAPPINGS[class_type]
                module = getattr(cls, "__module__", None)

                if module:
                    # Look for requirements.txt or pyproject.toml in the module's package
                    pkg_dir = os.path.dirname(module.replace(".", os.sep))
                    req_file = os.path.join(pkg_dir, "requirements.txt")
                    if os.path.exists(req_file):
                        with open(req_file) as f:
                            for line in f:
                                line = line.strip()
                                if line and not line.startswith("#"):
                                    packages.add(line)

                    pyproject = os.path.join(pkg_dir, "pyproject.toml")
                    if os.path.exists(pyproject):
                        # Try to parse dependencies from pyproject.toml
                        try:
                            import tomllib
                            with open(pyproject, "rb") as f:
                                data = tomllib.load(f)
                            deps = data.get("project", {}).get("dependencies", [])
                            for dep in deps:
                                packages.add(dep)
                        except Exception:
                            pass
        except Exception as e:
            logger.debug(f"Could not analyze class_type {class_type}: {e}")

    return list(packages)


async def install_packages(
    packages: List[str],
    task_id: str,
    cancellation_event: Optional[asyncio.Event] = None,
) -> tuple[List[str], List[str], bool]:
    """
    Install packages via pip.

    Returns: (installed, failed, restart_required)
    """
    from . import get_task_queue

    queue = get_task_queue()
    installed = []
    failed = []
    restart_required = False

    gpu = detect_gpu()
    total = len(packages)
    completed = 0

    for pkg in packages:
        if cancellation_event and cancellation_event.is_set():
            raise asyncio.CancelledError("Installation cancelled")

        # Check if already installed (simple check)
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "show", pkg.split("==")[0].split(">=")[0].split("<=")[0]],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                logger.info(f"Package already installed: {pkg}")
                completed += 1
                queue.update_progress(task_id, completed / total)
                installed.append(pkg)
                continue
        except Exception:
            pass

        # Build pip install command
        cmd = [sys.executable, "-m", "pip", "install", pkg]

        # Add GPU-specific extras if applicable
        if gpu == "cuda":
            cmd.extend(["--extra-index-url", "https://download.pytorch.org/whl/cu121"])
        elif gpu == "rocm":
            cmd.extend(["--extra-index-url", "https://download.pytorch.org/whl/rocm5.7"])

        try:
            logger.info(f"Installing package: {' '.join(cmd)}")
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            output = []
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break

                decoded = line.decode("utf-8", errors="replace")
                output.append(decoded)
                logger.debug(f"pip: {decoded.rstrip()}")

            await proc.wait()

            if proc.returncode == 0:
                installed.append(pkg)
                logger.info(f"Successfully installed: {pkg}")
            else:
                failed.append(pkg)
                logger.warning(f"Failed to install: {pkg}")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"Error installing {pkg}")
            failed.append(pkg)

        completed += 1
        queue.update_progress(task_id, completed / total)

    # Check if restart might be required (installed new packages that might need restart)
    restart_required = any(
        pkg.startswith(("torch", "numpy", "scipy", "transformers", "diffusers"))
        for pkg in installed
    )

    return installed, failed, restart_required


async def check_workflow_deps(workflow: dict) -> dict:
    """
    Check if workflow dependencies are satisfied.

    Returns dict with: missing, already_satisfied, can_run
    """
    packages = parse_workflow_dependencies(workflow)

    missing = []
    already_satisfied = []

    for pkg in packages:
        # Parse package name
        name = pkg.split("==")[0].split(">=")[0].split("<=")[0]

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "show", name],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                already_satisfied.append(pkg)
            else:
                missing.append(pkg)
        except Exception:
            missing.append(pkg)

    return {
        "missing": missing,
        "already_satisfied": already_satisfied,
        "can_run": len(missing) == 0,
        "gpu_type": detect_gpu(),
    }
