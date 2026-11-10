"""Declarative model and capability dependencies.

The server only provisions dependencies it declares.  A dependency may be a
custom-node pack or a Python package; model-level dependencies are mandatory
for serving a model, while capability-level dependencies only gate an optional
feature such as an acceleration method or ``exclude_tags``.

Install specs are intentionally separate from probes.  The probe answers
"is it available now?", while the install spec answers "what pinned source
can be installed and verified by the provisioner?".
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple


@dataclass(frozen=True)
class DependencySpec:
    """A declared dependency of a model or one of its capabilities."""

    id: str
    kind: str
    required_nodes: Tuple[str, ...] = ()
    required_packages: Tuple[Tuple[str, ...], ...] = ()
    install_spec: Dict[str, Any] = field(default_factory=dict)
    requires_restart: bool = False
    check_commit: bool = False
    description: str = ""


def _custom_nodes_dir() -> Optional[Path]:
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
    return None


def _installed_commit(target: str) -> Optional[str]:
    root = _custom_nodes_dir()
    if root is None:
        return None
    path = root / target
    if not path.exists():
        return None
    try:
        return subprocess.check_output(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def available_node_names() -> set:
    """Return currently registered ComfyUI node class names.

    An empty set is returned when the extension is imported outside ComfyUI;
    callers that need to distinguish "not probing" from "no nodes" can pass an
    explicit set to :func:`evaluate_dependency`.
    """
    try:
        import nodes as comfy_nodes

        return set(getattr(comfy_nodes, "NODE_CLASS_MAPPINGS", {}))
    except Exception:
        return set()


def _missing_packages(groups: Iterable[Tuple[str, ...]]) -> list:
    missing = []
    for group in groups:
        if not any(importlib.util.find_spec(name) is not None for name in group):
            missing.append(list(group))
    return missing


def evaluate_dependency(
    dependency: DependencySpec,
    nodes: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Probe one dependency and return its report fields."""
    known_nodes = set(nodes) if nodes is not None else available_node_names()
    missing_nodes = [
        name for name in dependency.required_nodes if name not in known_nodes
    ]
    missing_packages = _missing_packages(dependency.required_packages)

    install_spec = dict(dependency.install_spec or {})
    expected_commit = install_spec.get("commit")
    target = install_spec.get("target")
    current_commit = None
    version_mismatch = None
    if (
        dependency.kind == "custom_node"
        and dependency.check_commit
        and target
        and expected_commit
    ):
        current_commit = _installed_commit(str(target))
        if current_commit is not None and current_commit != expected_commit:
            version_mismatch = {
                "current": current_commit,
                "expected": expected_commit,
            }
        elif current_commit is None:
            root = _custom_nodes_dir()
            if root is not None and (root / str(target)).exists():
                version_mismatch = {
                    "current": "not-a-git-checkout",
                    "expected": expected_commit,
                }

    blocked = bool(missing_nodes or missing_packages or version_mismatch)
    if not blocked:
        status = "available"
    elif install_spec:
        status = "installable"
    else:
        status = "unsupported"

    actions = []
    if status == "installable":
        action = dict(install_spec)
        if action.get("kind") == "python_package":
            action.update({
                "package": action.get("package"),
                "source": action.get("source"),
                "pinned": bool(action.get("pinned")),
                "requires_restart": bool(action.get("requires_restart")),
            })
        actions.append(action)

    return {
        "id": dependency.id,
        "kind": dependency.kind,
        "status": status,
        "available": status == "available",
        "required_nodes": list(dependency.required_nodes),
        "missing_nodes": missing_nodes,
        "required_packages": [list(group) for group in dependency.required_packages],
        "missing_packages": missing_packages,
        "missing_version": version_mismatch,
        "current_commit": current_commit,
        "install_spec": install_spec,
        "actions": actions,
        "requires_restart": bool(dependency.requires_restart or install_spec.get("requires_restart")),
        "description": dependency.description,
    }
