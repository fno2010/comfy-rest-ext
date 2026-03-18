# Comfy-REST-Ext Agent Guide

> 本文档为 comfy-rest-ext 项目的开发规范说明，供 AI Agent 和人类开发者参考。

---

## 项目概述

**comfy-rest-ext** 是 ComfyUI 的自定义节点扩展，通过自定义节点机制向 ComfyUI REST API 补充新的端点。

### 目标

在不修改 ComfyUI 核心代码的前提下，通过 custom node 的加载机制注册额外的 REST API 端点，实现：

1. 模型下载（CivitAI、HuggingFace、直链）
2. 模型管理增强（递归列表、元数据、删除）
3. 工作流依赖安装（从 workflow JSON 解析并安装依赖）
4. 依赖检查
5. 快照导入/导出
6. 节点打包
7. 前端 PR 缓存管理

---

## 项目结构

```
comfy-rest-ext/
├── __init__.py              # 包入口，导出 comfy_entrypoint
├── api/
│   ├── __init__.py
│   ├── routes.py            # 所有 REST API 路由注册
│   ├── models/
│   │   ├── __init__.py
│   │   ├── download.py      # 模型下载相关端点
│   │   ├── management.py    # 模型管理增强端点
│   │   ├── dependencies.py  # 依赖管理端点
│   │   ├── snapshot.py      # 快照管理端点
│   │   └── nodes.py         # 节点打包/验证端点
│   ├── tasks/
│   │   ├── __init__.py
│   │   ├── task_queue.py    # 异步任务队列
│   │   └── handlers.py      # 任务处理器
│   └── schemas/
│       ├── __init__.py
│       └── requests.py      # Pydantic 请求模型
├── web/                     # 前端资源（如有）
│   └── js/
├── docs/
│   └── references/          # 参考文档
│       ├── API.md
│       ├── API-supplement-proposal.md
│       ├── API-implementation-reference.md
│       └── openapi.yaml
├── pyproject.toml           # 项目配置
└── README.md
```

---

## 编码规范

### Python 版本与依赖

- Python: `>=3.10`
- 依赖尽量使用 ComfyUI 已有的包，避免额外依赖
- 额外依赖需在 `pyproject.toml` 中声明

### ComfyUI API 注册模式

所有路由通过 `PromptServer.instance.routes` 注册：

```python
from aiohttp import web
from server import PromptServer

routes = PromptServer.instance.routes

@routes.get("/v2/extension/model/download")
async def get_model_download(request):
    return web.json_response({"task_id": "..."})
```

### Custom Node 入口（V3 扩展）

```python
from comfy_api.latest import ComfyExtension
from typing import override

class ComfyRestExtExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return []  # 本扩展不注册任何计算节点，纯 API 扩展

async def comfy_entrypoint() -> ComfyExtension:
    return ComfyRestExtExtension()
```

> 本扩展不提供 ComfyUI 画布节点，仅扩展 REST API。

### 路由分组

所有新增端点统一前缀 **`/v2/extension/`**，避免与 ComfyUI 核心 API 和 Manager API 冲突：

| 前缀 | 模块 | 说明 |
|------|------|------|
| `/v2/extension/model/*` | `api/models/download.py` | 模型下载 |
| `/v2/extension/models/*` | `api/models/management.py` | 模型管理 |
| `/v2/extension/workflow/dependencies/*` | `api/models/dependencies.py` | 工作流依赖 |
| `/v2/extension/dependencies/*` | `api/models/dependencies.py` | 依赖管理 |
| `/v2/extension/snapshot/*` | `api/models/snapshot.py` | 快照 |
| `/v2/extension/nodes/*` | `api/models/nodes.py` | 节点打包/验证 |
| `/v2/extension/frontend/*` | `api/models/nodes.py` | 前端 PR 缓存 |

### 异步任务设计

长任务（如模型下载、依赖安装）必须异步化：

1. `POST /v2/model/download` 立即返回 `task_id`
2. 后台线程/进程处理实际工作
3. WebSocket 推送进度
4. `GET /v2/model/download/{task_id}` 查询状态

```python
from aiohttp import web

@routes.post("/v2/model/download")
async def post_model_download(request):
    data = await request.json()
    task_id = generate_uuid()

    # 提交后台任务
    asyncio.create_task(download_worker(task_id, data))

    return web.json_response({
        "task_id": task_id,
        "status": "queued"
    })
```

### WebSocket 进度推送

通过 `PromptServer.instance` 推送 WebSocket 事件。事件名统一带 `extension-` 前缀：

```python
from server import PromptServer

# 推送进度
PromptServer.instance.send_sync("extension-model-download-progress", {
    "task_id": task_id,
    "progress": 0.5,
    "speed_bps": 10485760
})
```

### 请求数据验证

使用 Pydantic 模型进行请求验证：

```python
from pydantic import BaseModel

class ModelDownloadRequest(BaseModel):
    url: str
    folder: str = "checkpoints"
    filename: str | None = None
```

### 错误处理

所有端点统一错误响应格式：

```python
@routes.post("/v2/model/download")
async def post_model_download(request):
    try:
        data = await request.json()
        req = ModelDownloadRequest(**data)
    except ValidationError as e:
        return web.json_response({"error": "Validation failed", "detail": e.errors()}, status=400)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
```

### 日志

使用标准 `logging` 模块：

```python
import logging
logger = logging.getLogger("comfy-rest-ext")

logger.info("Starting model download: %s", url)
logger.error("Download failed: %s", e)
```

---

## ComfyUI 关键 API 参考

### PromptServer

```python
from server import PromptServer

server = PromptServer.instance

# 路由
server.routes  # aiohttp routes 容器

# WebSocket 推送
server.send_sync("event-name", {"key": "value"})
server.sendwebsocket("event-name", {"key": "value"})  # 广播
```

### 文件路径

```python
import folder_paths
folder_paths.get_folder_paths()  # 获取所有模型文件夹路径
folder_paths.get_output_directory()
folder_paths.get_input_directory()
```

### 节点信息

```python
import nodes
nodes.NODE_CLASS_MAPPINGS  # 所有注册的节点类
```

---

## 开发调试

### 本地测试

1. 启动 ComfyUI（确保 custom_nodes 路径正确）
2. 扩展会在 ComfyUI 启动时自动加载
3. 检查日志确认加载成功：

```
[ComfyUI] Loaded 1 new custom node(s): comfy-rest-ext
```

### 验证路由注册

```bash
curl http://127.0.0.1:8188/v2/model/download
```

### 常见加载失败原因

1. `__init__.py` 缺失
2. `comfy_entrypoint()` 函数不存在或签名错误
3. 依赖包未安装
4. 语法错误

---

## 文件命名

- **模块**: 小写下划线 `api/routes.py`
- **类**: 大驼峰 `ModelDownloadHandler`
- **函数**: 小写下划线 `download_model()`
- **常量**: 大写下划线 `MAX_CONCURRENT_DOWNLOADS`
- **路由变量**: 小写下划线 `task_id`, `model_path`

---

## 提交规范

- 每条 commit 描述一个完整的改动
- commit message 格式: `feat: add model download endpoint`
- 文档与代码同步更新
