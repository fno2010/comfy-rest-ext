"""Read-only acceleration schema endpoint for dashboard/CLI consumers."""

from aiohttp import web
from server import PromptServer

from .accel import acceleration_schema
from .catalog import get_model

routes = PromptServer.instance.routes


@routes.get("/v2/extension/acceleration/schema")
async def get_acceleration_schema(request: web.Request) -> web.Response:
    """Return dimensions, methods, params, availability, and compatibility."""
    model_id = request.query.get("model") or None
    if model_id is None:
        payload = acceleration_schema()
    else:
        try:
            spec = get_model(model_id)
        except KeyError:
            return web.json_response(
                {"error": f"unknown model {model_id!r}"}, status=404
            )
        payload = acceleration_schema(model_id, spec.accel_methods)
    return web.json_response(
        payload,
        headers={"Cache-Control": "no-store"},
    )
