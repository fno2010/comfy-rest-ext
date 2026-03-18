"""
ComfyExtension implementation for Comfy-REST-Ext.
"""

from typing import List, Type

from comfy_api.latest import ComfyExtension, io
from typing import override


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
