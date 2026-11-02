"""
Task dashboard UI.

A dependency-free single-page view over the extension's own endpoints:
  GET /v1/videos
  GET /v2/extension/image

It never calls ComfyUI's native API or /view routes.
"""

from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web
from server import PromptServer

logger = logging.getLogger("comfy-rest-ext.ui")

routes = PromptServer.instance.routes

_INDEX = Path(__file__).resolve().parents[1] / "web" / "index.html"


@routes.get("/v2/extension/ui")
async def task_dashboard(request: web.Request) -> web.Response:
    try:
        html = _INDEX.read_text(encoding="utf-8")
    except OSError as e:
        logger.error("Dashboard asset missing: %s", e)
        return web.json_response({"error": "UI asset missing"}, status=500)
    return web.Response(
        text=html,
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@routes.get("/ui")
async def task_dashboard_redirect(request: web.Request) -> web.Response:
    raise web.HTTPFound("/v2/extension/ui")
