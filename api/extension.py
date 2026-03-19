"""
ComfyExtension implementation for Comfy-REST-Ext.
"""

import logging
from typing import List, Type

from comfy_api.latest import ComfyExtension, io
from typing import override

from .tasks.persistence import init_persistence, stop_persistence

logger = logging.getLogger("comfy-rest-ext")


class ComfyRestExtExtension(ComfyExtension):
    """
    Comfy-REST-Ext is a REST API extension for ComfyUI.
    It does not register any compute nodes, only REST API endpoints.
    """

    @override
    async def get_node_list(self) -> List[Type[io.ComfyNode]]:
        """
        This extension does not provide any compute nodes.
        It only extends the REST API.
        """
        return []

    @override
    async def on_load(self) -> None:
        """Called when the extension is loaded."""
        logger.info("[Comfy-REST-Ext] Initializing task persistence...")
        await init_persistence()
        logger.info("[Comfy-REST-Ext] Task persistence initialized.")

    @override
    async def on_unload(self) -> None:
        """Called when the extension is unloaded or server is stopping."""
        logger.info("[Comfy-REST-Ext] Stopping task persistence...")
        await stop_persistence()
        logger.info("[Comfy-REST-Ext] Task persistence stopped.")
