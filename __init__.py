"""
Comfy-REST-Ext: ComfyUI REST API Extension

通过 Custom Node 机制向 ComfyUI REST API 补充新端点。
"""

import logging

logger = logging.getLogger("comfy-rest-ext")

# Import route registration at module load time
try:
    from .api import routes  # noqa: F401
    from .api.schemas import requests  # noqa: F401
except ImportError:
    # Outside a package context (e.g. pytest with rootdir at the project
    # root, which triggers this file as an anonymous package init). Route
    # registration is irrelevant in that case.
    pass


async def comfy_entrypoint():
    """
    ComfyUI Custom Node 入口点。
    ComfyUI 在加载自定义节点时会调用此函数。
    """
    from .api.extension import ComfyRestExtExtension

    logger.info("[Comfy-REST-Ext] Loading extension...")
    extension = ComfyRestExtExtension()
    await extension.on_load()
    logger.info("[Comfy-REST-Ext] Extension loaded successfully.")
    return extension
