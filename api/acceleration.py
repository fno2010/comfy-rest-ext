"""Read-only acceleration schema endpoint for dashboard/CLI consumers."""

from aiohttp import web
from server import PromptServer

from .accel import acceleration_schema

routes = PromptServer.instance.routes


@routes.get("/v2/extension/acceleration/schema")
async def get_acceleration_schema(request: web.Request) -> web.Response:
    """Return dimensions, methods, params, availability, and compatibility."""
    return web.json_response(
        acceleration_schema(),
        headers={"Cache-Control": "no-store"},
    )
