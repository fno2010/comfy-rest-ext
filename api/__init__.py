"""
API routes package.

All REST API routes are registered here at module load time.
"""

from aiohttp import web
from server import PromptServer

routes = PromptServer.instance.routes

# Health check endpoint (Phase 0)
@routes.get("/v2/extension/health")
async def health_check(request):
    """Health check endpoint for the extension."""
    return web.json_response({"status": "ok", "extension": "comfy-rest-ext"})


# Import all route modules to trigger registration
# Each module registers its own routes via the @routes decorator
from api.models import download  # noqa: F401
from api.models import management  # noqa: F401
from api.models import dependencies  # noqa: F401
from api.models import snapshot  # noqa: F401
from api.models import nodes  # noqa: F401
from api.models import pr_cache  # noqa: F401
