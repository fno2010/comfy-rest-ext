"""
Comfy-REST-Ext: ComfyUI REST API Extension

通过 Custom Node 机制向 ComfyUI REST API 补充新端点。
"""

import logging

logger = logging.getLogger("comfy-rest-ext")

# Import route registration at module load time
from api import routes  # noqa: F401
from api.schemas import requests  # noqa: F401


async def comfy_entrypoint():
    """
    ComfyUI Custom Node 入口点。
    ComfyUI 在加载自定义节点时会调用此函数。
    """
    from api.extension import ComfyRestExtExtension

    logger.info("[Comfy-REST-Ext] Loading extension...")
    extension = ComfyRestExtExtension()
    await extension.on_load()
    logger.info("[Comfy-REST-Ext] Extension loaded successfully.")
    return extension
