"""Structured capability discovery for acceleration methods.

The schema endpoint describes what can be requested. This module answers the
operational question: what is available now, what is missing, and what action
would provision it.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from aiohttp import web
from server import PromptServer

from .accel import (
    _available_nodes,
    _missing_package_groups,
    methods_for_dimension,
    ACCEL_DIMENSIONS,
)
from .catalog import available_models, get_model
from . import provisioners

routes = PromptServer.instance.routes


def _method_capability(layer: str, method) -> Dict[str, Any]:
    missing_packages = _missing_package_groups(method)
    known_nodes = _available_nodes()
    missing_nodes = [
        node for node in method.required_nodes
        if known_nodes and node not in known_nodes
    ]
    if not method.implemented:
        status = "unsupported"
    elif not missing_packages and not missing_nodes:
        status = "available"
    elif method.install_spec:
        status = "installable"
    else:
        status = "unsupported"

    actions = []
    if status == "installable":
        spec = dict(method.install_spec or {})
        if spec.get("kind") == "python_package":
            actions.append({
                "kind": "python_package",
                "package": spec.get("package"),
                "source": spec.get("source"),
                "pinned": bool(spec.get("pinned")),
                "requires_restart": bool(spec.get("requires_restart")),
            })
        elif spec:
            actions.append(dict(spec))

    return {
        "id": f"{layer}.{method.id}",
        "layer": layer,
        "method": method.id,
        "status": status,
        "implemented": method.implemented,
        "available": status == "available",
        "required_nodes": list(method.required_nodes),
        "required_packages": [list(group) for group in method.required_packages],
        "missing_nodes": missing_nodes,
        "missing_packages": missing_packages,
        "install_spec": dict(method.install_spec or {}),
        "actions": actions,
        "requires_restart": bool((method.install_spec or {}).get("requires_restart")),
    }


def capability_report(
    model_id: Optional[str] = None,
    capability_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Return a structured report for acceleration capabilities.

    With ``model_id``, methods outside that model's acceleration allowlist are
    omitted. With ``capability_ids``, only those ids are returned.
    """
    allowed = None
    if model_id is not None:
        allowed = frozenset(get_model(model_id).accel_methods)
    wanted = set(capability_ids) if capability_ids is not None else None

    items: List[Dict[str, Any]] = []
    for layer in ACCEL_DIMENSIONS:
        for method in methods_for_dimension(layer):
            cid = f"{layer}.{method.id}"
            if wanted is not None and cid not in wanted:
                continue
            if allowed is not None and "*" not in allowed and method.id not in allowed:
                continue
            items.append(_method_capability(layer, method))

    counts: Dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        "model": model_id,
        "capabilities": items,
        "summary": counts,
    }


@routes.get("/v2/extension/capabilities")
async def get_capabilities(request: web.Request) -> web.Response:
    """Return operational capability status and provisioning actions."""
    model_id = request.query.get("model") or None
    requested = request.query.get("capability") or None
    capability_ids = (
        [item for item in requested.split(",") if item] if requested else None
    )
    try:
        payload = capability_report(model_id, capability_ids)
    except KeyError:
        return web.json_response(
            {"error": f"unknown model {model_id!r}"}, status=404
        )
    if model_id is None and not available_models():
        payload = {"model": None, "capabilities": [], "summary": {}}
    payload["provisioners"] = await provisioners.provisioner_report()
    return web.json_response(payload, headers={"Cache-Control": "no-store"})
