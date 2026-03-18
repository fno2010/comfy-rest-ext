# ComfyUI REST API 完整文档

> 基于 ComfyUI 核心 + ComfyUI Manager (glob 版本)
> ComfyUI 版本: `main` 分支, 2026-03

---

## 目录

- [概述](#概述)
- [核心执行 API](#核心执行-api)
  - [Prompt/队列管理](#prompt队列管理)
  - [任务管理](#任务管理)
  - [历史记录](#历史记录)
- [模型与文件 API](#模型与文件-api)
  - [模型列表](#模型列表)
  - [图片上传/查看](#图片上传查看)
  - [元数据](#元数据)
- [节点信息 API](#节点信息-api)
- [系统状态 API](#系统状态-api)
- [内部 API](#内部-api)
- [用户管理 API](#用户管理-api)
- [设置 API](#设置-api)
- [工作流与子图 API](#工作流与子图-api)
- [资产 API](#资产-api)
- [ComfyUI Manager API](#comfyui-manager-api)
  - [管理器核心](#管理器核心)
  - [自定义节点](#自定义节点)
  - [快照管理](#快照管理)
  - [第三方分享](#第三方分享)
- [附录](#附录)
  - [Prompt JSON Schema](#prompt-json-schema)
  - [API 路径总表](#api-路径总表)

---

## 概述

- **框架**: aiohttp
- **服务器**: `server.py` 中的 `PromptServer`
- **路由注册**: `@routes.get()`, `@routes.post()` 装饰器
- **Base URL**: `http://{host}:{port}` (默认 `http://127.0.0.1:8188`)
- **认证**: ComfyUI 无内置认证，通过反向代理实现
- **Manager**: ComfyUI Manager 通过 `args.enable_manager` 启用，使用 `/v2/` 前缀

### 路径前缀

| 前缀 | 来源 | 说明 |
|------|------|------|
| `/` | ComfyUI 核心 | 页面、静态资源 |
| `/api/*` | ComfyUI 核心 | 核心 API（含别名无前缀版本） |
| `/internal/*` | ComfyUI 核心 | 内部 API（仅前端使用） |
| `/v2/manager/*` | ComfyUI Manager | 管理器核心功能 |
| `/v2/customnode/*` | ComfyUI Manager | 自定义节点管理 |
| `/v2/snapshot/*` | ComfyUI Manager | 快照管理 |
| `/v2/comfyui_manager/*` | ComfyUI Manager | ComfyUI 版本管理 |

---

## 核心执行 API

### Prompt/队列管理

#### POST `/prompt` — 提交工作流执行

**最重要的端点**，提交工作流到队列执行。

**Request Body:**
```json
{
  "number": 1.0,
  "front": false,
  "prompt": {
    "node_id": {
      "class_type": "KSampler",
      "inputs": {
        "model": ["node_id_2", 0],
        "seed": 1234567890,
        "steps": 20,
        "cfg": 8.0,
        "sampler_name": "euler",
        "scheduler": "normal",
        "positive": ["node_id_3", 0],
        "negative": ["node_id_4", 0]
      },
      "_meta": {
        "title": "KSampler",
        "tags": ["sampling"]
      }
    }
  },
  "prompt_id": "optional-custom-uuid",
  "extra_data": {
    "client_id": "websocket-client-id"
  },
  "partial_execution_targets": ["node_id"]
}
```

**字段说明:**
- `prompt` **(必填)**: 工作流节点图，key 为节点 ID
- `prompt_id` **(可选)**: 自定义 prompt UUID，不提供则自动生成
- `number` **(可选)**: 队列位置（浮点数）
- `front` **(可选)**: `true` 则插队到最前
- `extra_data` **(可选)**: 额外数据，通常含 `client_id` 用于 WebSocket 推送
- `partial_execution_targets` **(可选)**: 指定执行的节点子集
- `node_errors` **(可选)**: 节点错误映射

**节点输入值类型:**
- 原始值: `string`, `number`, `boolean`
- 链接: `["other_node_id", output_index]`
- 列表: `[val1, val2, ...]`

**Response:**
```json
{
  "prompt_id": "prompt-uuid-string",
  "number": 1.0,
  "node_errors": {}
}
```

---

#### GET `/prompt` — 获取队列信息

返回当前队列状态和已入队的 prompt。

**Response:**
```json
{
  "queue_running": [["prompt_id", "node_id"]],
  "queue_pending": [["prompt_id", "node_id"]]
}
```

---

#### POST `/queue` — 修改队列

**Request Body:**
```json
{
  "clear": true,
  "delete": ["prompt_id_1", "prompt_id_2"]
}
```

- `clear`: 清除所有队列项
- `delete`: 删除指定的 prompt_id 项
- 二者可同时使用

---

#### GET `/queue` — 获取队列状态

**Response:**
```json
{
  "queue_running": [...],
  "queue_pending": [...]
}
```

---

#### POST `/interrupt` — 中断执行

**Request Body (可选):**
```json
{
  "prompt_id": "specific-prompt-id-to-interrupt"
}
```

无 body 时中断所有执行，有 `prompt_id` 时仅中断指定的 prompt。

**Response:**
```json
{
  "status": "ok"
}
```

---

#### POST `/free` — 释放内存

**Request Body:**
```json
{
  "unload_models": true,
  "free_memory": true
}
```

**Response:**
```json
{
  "status": "ok",
  "vram_free": "2.00 GiB",
  "ram_free": "12.34 GiB"
}
```

---

### 任务管理

#### GET `/api/jobs` — 列出所有任务

**Query Parameters:**
| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `status` | string | - | 按状态过滤，逗号分隔: `pending`, `in_progress`, `completed`, `failed` |
| `workflow_id` | string | - | 按工作流 ID 过滤 |
| `sort_by` | string | `created_at` | 排序字段: `created_at`, `execution_duration` |
| `sort_order` | string | `desc` | `asc` 或 `desc` |
| `limit` | integer | - | 最大返回条数 |
| `offset` | integer | 0 | 跳过条数 |

**Response:**
```json
{
  "jobs": [
    {
      "id": "job-uuid",
      "prompt_id": "prompt-uuid",
      "workflow_id": "workflow-uuid",
      "status": "completed",
      "created_at": 1710000000.0,
      "started_at": 1710000001.0,
      "finished_at": 1710000010.0,
      "execution_duration": 9.0
    }
  ],
  "total": 100
}
```

---

#### GET `/api/jobs/{job_id}` — 获取特定任务

**Response:**
```json
{
  "id": "job-uuid",
  "prompt_id": "prompt-uuid",
  "workflow_id": "workflow-uuid",
  "status": "completed",
  "created_at": 1710000000.0,
  "started_at": 1710000001.0,
  "finished_at": 1710000010.0,
  "execution_duration": 9.0,
  "error": null,
  "outputs": {}
}
```

---

### 历史记录

#### GET `/history` — 获取执行历史

**Query Parameters:**
- `max_entries` (optional): 最大条目数

**Response:**
```json
{
  "prompt_id_1": {
    "status": {
      "exec_rt": 1.5,
      "ids": ["node_1", "node_2"]
    },
    "outputs": {
      "node_id": {
        "images": [{"filename": "img.png", "subfolder": "", "type": "output"}]
      }
    }
  }
}
```

---

#### GET `/history/{prompt_id}` — 获取特定 prompt 的历史

---

#### POST `/history` — 清除历史

**Request Body:**
```json
{
  "clear": true,
  "delete": ["prompt_id_1", "prompt_id_2"]
}
```

---

## 模型与文件 API

### 模型列表

#### GET `/models` — 获取模型类型列表

**Response:**
```json
{
  "folders": ["checkpoints", "loras", "embeddings", "upscale_models", ...]
}
```

---

#### GET `/models/{folder}` — 获取特定文件夹中的模型

**Response:**
```json
{
  "model_names": ["model_a.safetensors", "model_b.ckpt", ...]
}
```

---

#### GET `/experiment/models` — 实验性：获取模型文件夹（含路径）

---

#### GET `/experiment/models/{folder}` — 实验性：获取文件夹中所有模型及元数据

---

#### GET `/experiment/models/preview/{folder}/{path_index}/{filename}` — 实验性：获取模型预览图

返回 WEBP 格式预览图。

---

### 图片上传/查看

#### POST `/upload/image` — 上传图片

**Request:** `multipart/form-data`
- `image`: 图片文件 (必填)
- `overwrite`: `"true"` / `"false"` (默认 `"false"`)
- `type`: 目录类型 (默认 `"input"`)

**Response:**
```json
{
  "name": "uploaded_image.png",
  "path": "path/to/uploaded_image.png"
}
```

---

#### POST `/upload/mask` — 上传 Mask

**Request:** `multipart/form-data`
- `image`: mask 文件 (必填)
- `original_ref`: 参考图片路径 (必填，用于合成)
- `overwrite`: `"true"` / `"false"`

**Response:**
```json
{
  "name": "mask_image.png",
  "path": "path/to/mask_image.png"
}
```

---

#### GET `/view` — 查看/下载图片

**Query Parameters:**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `filename` | string | 是 | 图片文件名 |
| `type` | string | 否 | 目录类型: `input`, `output`, `temp` (默认: `output`) |
| `subfolder` | string | 否 | 子文件夹路径 |
| `preview` | string | 否 | 预览格式，如 `"webp;90"` (质量 90) |
| `channel` | string | 否 | 颜色通道: `rgba`, `rgb`, `a` |

**Response:** 返回图片二进制数据。

---

### 元数据

#### GET `/view_metadata/{folder_name}` — 获取 safetensors 文件元数据

**Query Parameters:**
- `filename` (required): 文件名
- `type`: 目录类型，默认 `output`

**Response:** 返回 safetensors 文件的元数据字典。

---

## 节点信息 API

#### GET `/object_info` — 获取所有节点类信息

返回所有注册节点的完整定义，包含输入/输出规范。

**Response:** JSON 对象，key 为节点类名，value 为节点定义。

---

#### GET `/object_info/{node_class}` — 获取特定节点类信息

**Response:** 单个节点的完整定义。

---

## 系统状态 API

#### GET `/system_stats` — 获取系统统计

**Response:**
```json
{
  "devices": [
    {
      "name": "NVIDIA GeForce RTX 4090",
      "type": "cuda",
      "index": 0,
      "vram_total": 24.0,
      "vram_free": 18.5,
      "torch_vram_total": 24.0,
      "torch_vram_free": 18.5
    }
  ],
  "ram_total": 64.0,
  "ram_free": 32.0,
  "version": "1.2.3"
}
```

---

#### GET `/features` — 获取服务器特性标志

**Response:**
```json
{
  "disableicanhaz": true,
  "disable_list": false,
  "feature_config": {}
}
```

---

#### GET `/embeddings` — 获取可用 embeddings 列表

**Response:**
```json
{
  "embeddings": ["embedding_file_1.pt", "embedding_file_2.safetensors"]
}
```

---

#### GET `/extensions` — 获取 JavaScript 扩展列表

**Response:**
```json
{
  "extensions": ["/extensions/core/my-extension.js"]
}
```

---

#### GET `/` — 获取首页 (HTML)

返回 ComfyUI Web UI 的 HTML 页面。

---

#### GET `/ws` — WebSocket 连接

用于实时事件推送（任务状态、执行进度等）。通过 `client_id` 关联订阅。

---

## 内部 API

> 以下 API 供 ComfyUI 前端内部使用，路径前缀 `/internal`

#### GET `/internal/logs` — 获取格式化日志

**Response:**
```json
{
  "data": "2026-03-18 10:00:00,123 - INFO - message\n2026-03-18 10:00:01,456 - WARNING - warning message"
}
```

---

#### GET `/internal/logs/raw` — 获取原始日志

**Response:**
```json
{
  "data": "原始日志字符串...",
  "width": 120,
  "height": 40
}
```

---

#### PATCH `/internal/logs/subscribe` — 订阅日志更新

**Request Body:**
```json
{
  "clientId": "client-id-string",
  "enabled": true
}
```

---

#### GET `/internal/folder_paths` — 获取配置的文件夹路径

**Response:**
```json
{
  "output": "/path/to/output",
  "input": "/path/to/input",
  "temp": "/path/to/temp",
  ...
}
```

---

#### GET `/internal/files/{directory_type}` — 获取目录中的文件列表

**Path Parameter:** `directory_type` 必须是 `output`, `input`, 或 `temp`

**Query Parameters:**
- `recursive`: `"true"` / `"false"` — 是否递归
- `name` (optional): 文件名过滤器

---

## 用户管理 API

> 路径前缀 `/userdata`，多用户模式下可用

#### GET `/userdata` — 列出用户数据文件

**Query Parameters:**
- `dir` (required): 目录路径
- `recurse`: `"true"` — 递归列出子目录
- `full_info`: `"true"` — 返回详细文件信息
- `split`: `"true"` — 将路径拆分为组件

---

#### GET `/v2/userdata` — 列出用户数据文件 (v2)

**Query Parameters:**
- `path` (optional): 用户数据目录内的相对路径

---

#### GET `/userdata/{file}` — 下载用户数据文件

---

#### POST `/userdata/{file}` — 上传/创建用户数据文件

**Query Parameters:**
- `overwrite`: `"true"` / `"false"` (默认 `true`)
- `full_info`: `"true"` / `"false"`

---

#### DELETE `/userdata/{file}` — 删除用户数据文件

---

#### POST `/userdata/{file}/move/{dest}` — 移动/重命名文件

**Query Parameters:**
- `overwrite`: `"true"` / `"false"`
- `full_info`: `"true"` / `"false"`

---

#### GET `/users` — 获取用户列表

**Response:**
```json
{
  "storage": "server",
  "users": {"user_id": "username"}
}
```

---

#### POST `/users` — 创建用户

**Request Body:**
```json
{"username": "string"}
```

---

## 设置 API

#### GET `/settings` — 获取所有设置

**Response:** JSON 对象，包含所有设置键值对。

---

#### GET `/settings/{id}` — 获取特定设置

**Response:** 设置值。

---

#### POST `/settings` — 批量更新设置

**Request Body:** JSON 对象，多个设置键值对。

---

#### POST `/settings/{id}` — 更新特定设置

**Request Body:** 设置值（任意 JSON 类型）。

---

## 工作流与子图 API

#### GET `/workflow_templates` — 获取工作流模板

来自自定义节点的工作流模板列表。

**Response:**
```json
{
  "module_name": {"name": "template_name", "url": "/api/workflow_templates/..."}
}
```

---

#### GET `/i18n` — 获取国际化翻译

来自自定义节点的翻译文件。

---

#### GET `/node_replacements` — 获取节点替换映射

**Response:**
```json
{
  "OldNode": "NewNode",
  ...
}
```

---

#### GET `/global_subgraphs` — 获取全局子图列表

**Response:**
```json
{
  "subgraph_id": {
    "source": "custom_node" | "templates",
    "name": "subgraph_name",
    "info": {"node_pack": "pack_name"}
  }
}
```

---

#### GET `/global_subgraphs/{id}` — 获取特定子图

返回子图的完整 JSON 数据。

---

## 资产 API

> 需要 `--enable-assets` 标志启用

### 资源发现

#### HEAD `/api/assets/hash/{hash}` — 检查资产是否存在

**Response Headers:**
- `HTTP 200`: 资产存在
- `HTTP 404`: 资产不存在

---

#### GET `/api/assets` — 列出资产

**Query Parameters:**
| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `include_tags` | string | - | 要包含的标签，逗号分隔 |
| `exclude_tags` | string | - | 要排除的标签，逗号分隔 |
| `name_contains` | string | - | 文件名包含的字符串 |
| `metadata_filter` | string | - | JSON 元数据过滤器 |
| `limit` | integer | 20 | 1-500 |
| `offset` | integer | 0 | 跳过条数 |
| `sort` | string | - | 排序字段: `name`, `created_at`, `updated_at`, `size`, `last_access_time` |
| `order` | string | - | `asc` 或 `desc` |

---

#### GET `/api/assets/{id}` — 获取资产详情

---

#### GET `/api/assets/{id}/content` — 下载资产内容

**Query Parameters:**
- `disposition`: `"inline"` 或 `"attachment"` (默认: `attachment`)

---

### 资产管理

#### POST `/api/assets/from-hash` — 从已有 hash 创建资产引用

**Request Body:**
```json
{
  "hash": "blake3:hex_string",
  "name": "display_name",
  "tags": ["models", "checkpoints"],
  "user_metadata": {}
}
```

---

#### POST `/api/assets` — 上传新资产

**Request:** `multipart/form-data`
- `file`: 文件二进制 (必填)
- `tags`: 标签列表，第一个必须是 `models`/`input`/`output` 之一
- `name`: 显示名
- `user_metadata`: JSON 字符串
- `hash`: 可选的 blake3 hash 用于校验

---

#### PUT `/api/assets/{id}` — 更新资产元数据

**Request Body:**
```json
{
  "name": "new_name",
  "user_metadata": {}
}
```

---

#### DELETE `/api/assets/{id}` — 删除资产引用

**Query Parameters:**
- `delete_content`: `"true"` / `"false"` (默认删除孤立内容)

---

### 标签管理

#### GET `/api/tags` — 列出所有标签

**Query Parameters:**
- `prefix`: 标签前缀过滤
- `limit`: 1-1000, 默认 100
- `offset`: 非负整数
- `order`: `count_desc` 或 `name_asc`
- `include_zero`: 包含计数为 0 的标签

---

#### POST `/api/assets/{id}/tags` — 添加标签

**Request Body:**
```json
{"tags": ["tag1", "tag2"]}
```

---

#### DELETE `/api/assets/{id}/tags` — 删除标签

**Request Body:**
```json
{"tags": ["tag1", "tag2"]}
```

---

### 资产扫描

#### POST `/api/assets/seed` — 触发资产扫描

**Request Body:**
```json
{"roots": ["models", "input", "output"]}
```

**Query Parameters:**
- `wait`: `"true"` 等待扫描完成

**Response:**
```json
{"status": "ok", "message": "Seeding started"}
```

---

#### GET `/api/assets/seed/status` — 获取扫描状态

---

#### POST `/api/assets/seed/cancel` — 取消扫描

---

#### POST `/api/assets/prune` — 标记缺失资产

---

## ComfyUI Manager API

> ComfyUI Manager 通过 `args.enable_manager` 启用，默认使用 glob 版本
> 路径前缀 `/v2/`

### 管理器核心

#### GET `/v2/manager/version` — 获取 Manager 版本

**Response:**
```json
{
  "version": "2.0.0"
}
```

---

#### GET `/v2/manager/is_legacy_manager_ui` — 检查是否使用 Legacy UI

---

#### GET `/v2/manager/db_mode` — 获取数据库模式

---

#### GET `/v2/manager/policy/update` — 更新管理器策略

---

#### GET `/v2/manager/channel_url_list` — 获取渠道 URL 列表

**Response:**
```json
{
  "urls": ["https://example.com/list.json"]
}
```

---

#### GET `/v2/manager/reboot` — 重启 Manager

---

#### GET `/v2/manager/queue/status` — 获取队列状态

**Response:**
```json
{
  "status": "idle" | "running" | "paused",
  "current": "task-id-or-null",
  "queue": 0
}
```

---

#### GET `/v2/manager/queue/reset` — 重置队列

---

#### GET `/v2/manager/queue/start` — 开始队列处理

---

#### GET `/v2/manager/queue/update_all` — 更新所有自定义节点

---

#### GET `/v2/manager/queue/update_comfyui` — 更新 ComfyUI

---

#### POST `/v2/manager/queue/task` — 添加单一任务到队列 (glob)

**Request Body:**
```json
{
  "type": "install" | "update" | "fix" | "uninstall",
  "data": {
    "name": "custom-node-name",
    "url": "optional-git-url"
  }
}
```

---

#### POST `/v2/manager/queue/install_model` — 安装模型

**Request Body:**
```json
{
  "model_name": "model.safetensors",
  "model_url": "https://example.com/model.safetensors",
  "folder": "checkpoints"
}
```

---

#### GET `/v2/manager/queue/history_list` — 获取历史列表

**Response:**
```json
{
  "items": [
    {
      "id": "task-id",
      "type": "install",
      "state": "done" | "failed" | "running",
      "name": "node-name",
      "timestamp": 1710000000.0
    }
  ]
}
```

---

#### GET `/v2/manager/queue/history` — 获取任务历史

**Query Parameters:**
- `id` (optional): 特定任务 ID

---

#### GET `/v2/comfyui_manager/comfyui_versions` — 获取可用 ComfyUI 版本

---

#### GET `/v2/comfyui_manager/comfyui_switch_version` — 切换 ComfyUI 版本

**Query Parameters:**
- `version` (required): 目标版本号

---

### 自定义节点

#### GET `/v2/customnode/getmappings` — 获取节点映射

返回 `extension-node-map.json` 的内容，映射自定义节点到其提供的节点类型。

**Response:**
```json
{
  "custom_node_id": {
    "node_class_1": "path/to/node.py",
    "node_class_2": "path/to/another.py"
  }
}
```

---

#### GET `/v2/customnode/fetch_updates` — 获取可用更新

---

#### GET `/v2/customnode/installed` — 获取已安装节点列表

**Response:**
```json
{
  "installed": [
    {
      "title": "Node Name",
      "author": "author",
      "name": "custom_node_repo_name",
      "url": "https://github.com/author/repo",
      "description": "description",
      "installed": true,
      "installed_version": "commit-hash",
      "git_cloned": true
    }
  ]
}
```

---

#### GET `/v2/customnode/getlist` — 获取可用节点列表 (legacy)

来自 `custom-node-list.json`

---

#### GET `/v2/customnode/versions/{node_name}` — 获取节点版本 (legacy)

---

#### GET `/v2/customnode/disabled_versions/{node_name}` — 获取禁用版本 (legacy)

---

#### GET `/customnode/alternatives` — 获取替代节点 (legacy)

---

#### GET `/v2/externalmodel/getlist` — 获取外部模型列表 (legacy)

---

#### POST `/v2/customnode/import_fail_info` — 上报导入失败

**Request Body:**
```json
{
  "name": "node-name",
  "module": "module-name",
  "error": "error-message"
}
```

---

#### POST `/v2/customnode/import_fail_info_bulk` — 批量上报导入失败

**Request Body:**
```json
{
  "items": [
    {"name": "node1", "module": "mod1", "error": "err1"},
    {"name": "node2", "module": "mod2", "error": "err2"}
  ]
}
```

---

#### POST `/v2/customnode/install/git_url` — 通过 git URL 安装 (legacy)

**Request Body:**
```json
{
  "url": "git@github.com:author/repo.git",
  "name": "optional-name"
}
```

---

#### POST `/v2/customnode/install/pip` — 通过 pip 安装 (legacy)

**Request Body:**
```json
{
  "package": "package-name",
  "name": "optional-name"
}
```

---

### 快照管理

#### GET `/v2/snapshot/getlist` — 获取快照列表

---

#### GET `/v2/snapshot/save` — 保存快照

**Query Parameters:**
- `name` (optional): 快照名称

---

#### GET `/v2/snapshot/get_current` — 获取当前快照

---

#### GET `/v2/snapshot/restore` — 从快照恢复

**Query Parameters:**
- `id` (required): 快照 ID

---

#### GET `/v2/snapshot/remove` — 删除快照

**Query Parameters:**
- `id` (required): 快照 ID

---

### 第三方分享

#### GET `/v2/manager/share_option` — 获取/设置分享选项

---

#### POST `/v2/manager/share` — 分享作品

**Request Body:**
```json
{
  "type": "matrix" | "comfyworkflows",
  "data": {
    "workflow": {...},
    "images": ["base64-image-strings"]
  }
}
```

---

#### GET `/v2/manager/get_matrix_auth` — 获取 Matrix 认证

---

#### GET `/v2/manager/get_matrix_dep_status` — 检查 Matrix 依赖状态

---

#### GET `/v2/manager/get_openart_auth` — 获取 OpenArt 认证

---

#### POST `/v2/manager/set_openart_auth` — 设置 OpenArt 认证

---

#### GET `/v2/manager/get_comfyworkflows_auth` — 获取 ComfyWorkflows 认证

---

#### GET `/v2/manager/youml/settings` — 获取/设置 YouML 设置

---

#### GET `/v2/manager/get_esheep_workflow_and_images` — 获取 eSheep 数据

---

#### POST `/v2/manager/set_esheep_workflow_and_images` — 设置 eSheep 数据

---

## 附录

### Prompt JSON Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "ComfyUI Prompt",
  "type": "object",
  "required": ["prompt"],
  "properties": {
    "prompt": {
      "type": "object",
      "description": "Workflow node graph, key is node_id (string)",
      "additionalProperties": {
        "type": "object",
        "required": ["class_type", "inputs"],
        "properties": {
          "class_type": {
            "type": "string",
            "description": "Node class name (e.g. KSampler, LoadImage)"
          },
          "inputs": {
            "type": "object",
            "description": "Node input key-value pairs"
          },
          "_meta": {
            "type": "object",
            "description": "Node metadata (title, tags, etc.)"
          }
        }
      }
    },
    "prompt_id": {
      "type": "string",
      "description": "Custom prompt UUID (auto-generated if not provided)"
    },
    "number": {
      "type": "number",
      "description": "Queue position"
    },
    "front": {
      "type": "boolean",
      "description": "Execute at front of queue if true"
    },
    "extra_data": {
      "type": "object",
      "description": "Extra data (e.g. {client_id: string})"
    },
    "partial_execution_targets": {
      "type": "array",
      "items": {"type": "string"},
      "description": "Subset of node IDs to execute"
    }
  }
}
```

### 节点输入值类型

| 类型 | 示例 | 说明 |
|------|------|------|
| 原始值 | `"hello"`, `42`, `3.14`, `true` | 直接字面值 |
| 链接 | `["node_id", 0]` | 引用另一节点的第 0 个输出 |
| 列表 | `[1, 2, 3]` | 多值列表输入 |

### API 路径总表

#### ComfyUI 核心

| Method | Path |
|--------|------|
| GET | `/` |
| GET | `/ws` |
| GET | `/embeddings` |
| GET | `/extensions` |
| GET | `/features` |
| GET | `/features` |
| GET | `/history` |
| GET | `/history/{prompt_id}` |
| POST | `/history` |
| GET | `/models` |
| GET | `/models/{folder}` |
| GET | `/object_info` |
| GET | `/object_info/{node_class}` |
| GET | `/prompt` |
| POST | `/prompt` |
| GET | `/queue` |
| POST | `/queue` |
| POST | `/interrupt` |
| POST | `/free` |
| GET | `/system_stats` |
| GET | `/upload/image` |
| POST | `/upload/image` |
| POST | `/upload/mask` |
| GET | `/view` |
| GET | `/view_metadata/{folder_name}` |
| GET | `/api/jobs` |
| GET | `/api/jobs/{job_id}` |
| GET | `/userdata` |
| GET | `/v2/userdata` |
| GET | `/userdata/{file}` |
| POST | `/userdata/{file}` |
| DELETE | `/userdata/{file}` |
| POST | `/userdata/{file}/move/{dest}` |
| GET | `/users` |
| POST | `/users` |
| GET | `/settings` |
| GET | `/settings/{id}` |
| POST | `/settings` |
| POST | `/settings/{id}` |
| GET | `/global_subgraphs` |
| GET | `/global_subgraphs/{id}` |
| GET | `/node_replacements` |
| GET | `/workflow_templates` |
| GET | `/i18n` |
| GET | `/experiment/models` |
| GET | `/experiment/models/{folder}` |
| GET | `/experiment/models/preview/{folder}/{path_index}/{filename}` |
| GET | `/internal/logs` |
| GET | `/internal/logs/raw` |
| PATCH | `/internal/logs/subscribe` |
| GET | `/internal/folder_paths` |
| GET | `/internal/files/{directory_type}` |
| HEAD | `/api/assets/hash/{hash}` |
| GET | `/api/assets` |
| GET | `/api/assets/{id}` |
| GET | `/api/assets/{id}/content` |
| POST | `/api/assets/from-hash` |
| POST | `/api/assets` |
| PUT | `/api/assets/{id}` |
| DELETE | `/api/assets/{id}` |
| GET | `/api/tags` |
| POST | `/api/assets/{id}/tags` |
| DELETE | `/api/assets/{id}/tags` |
| POST | `/api/assets/seed` |
| GET | `/api/assets/seed/status` |
| POST | `/api/assets/seed/cancel` |
| POST | `/api/assets/prune` |

#### ComfyUI Manager (glob)

| Method | Path |
|--------|------|
| GET | `/v2/manager/version` |
| GET | `/v2/manager/is_legacy_manager_ui` |
| GET | `/v2/manager/db_mode` |
| GET | `/v2/manager/policy/update` |
| GET | `/v2/manager/channel_url_list` |
| GET | `/v2/manager/reboot` |
| GET | `/v2/manager/queue/status` |
| GET | `/v2/manager/queue/reset` |
| GET | `/v2/manager/queue/start` |
| GET | `/v2/manager/queue/update_all` |
| GET | `/v2/manager/queue/update_comfyui` |
| POST | `/v2/manager/queue/task` |
| POST | `/v2/manager/queue/install_model` |
| GET | `/v2/manager/queue/history_list` |
| GET | `/v2/manager/queue/history` |
| GET | `/v2/comfyui_manager/comfyui_versions` |
| GET | `/v2/comfyui_manager/comfyui_switch_version` |
| GET | `/v2/customnode/getmappings` |
| GET | `/v2/customnode/fetch_updates` |
| GET | `/v2/customnode/installed` |
| POST | `/v2/customnode/import_fail_info` |
| POST | `/v2/customnode/import_fail_info_bulk` |
| GET | `/v2/snapshot/getlist` |
| GET | `/v2/snapshot/save` |
| GET | `/v2/snapshot/get_current` |
| GET | `/v2/snapshot/restore` |
| GET | `/v2/snapshot/remove` |
| GET | `/v2/manager/share_option` |
| POST | `/v2/manager/share` |
| GET | `/v2/manager/get_openart_auth` |
| POST | `/v2/manager/set_openart_auth` |
| GET | `/v2/manager/get_matrix_auth` |
| GET | `/v2/manager/get_matrix_dep_status` |
| GET | `/v2/manager/get_comfyworkflows_auth` |
| GET | `/v2/manager/youml/settings` |
| POST | `/v2/manager/youml/settings` |
| GET | `/v2/manager/get_esheep_workflow_and_images` |
| POST | `/v2/manager/set_esheep_workflow_and_images` |

#### ComfyUI Manager (legacy 额外)

| Method | Path |
|--------|------|
| POST | `/v2/manager/queue/batch` |
| POST | `/v2/manager/queue/install` |
| POST | `/v2/manager/queue/fix` |
| POST | `/v2/manager/queue/reinstall` |
| POST | `/v2/manager/queue/uninstall` |
| POST | `/v2/manager/queue/update` |
| POST | `/v2/manager/queue/disable` |
| POST | `/v2/customnode/install/git_url` |
| POST | `/v2/customnode/install/pip` |
| GET | `/v2/customnode/getlist` |
| GET | `/v2/customnode/versions/{node_name}` |
| GET | `/v2/customnode/disabled_versions/{node_name}` |
| GET | `/customnode/alternatives` |
| GET | `/v2/externalmodel/getlist` |
| GET | `/v2/manager/queue/abort_current` |
| GET | `/v2/manager/notice` |
| GET | `/manager/notice` |

---

## Comfy-REST-Ext API

> ComfyUI REST API 扩展，通过 Custom Node 机制补充的额外端点
> 路径前缀 `/v2/extension/`

### 健康检查

#### GET `/v2/extension/health` — 健康检查

返回扩展加载状态。

**Response:**
```json
{
  "status": "ok",
  "extension": "comfy-rest-ext"
}
```

---

### 模型下载

#### POST `/v2/extension/model/download` — 创建下载任务

创建异步模型下载任务。

**Request Body:**
```json
{
  "url": "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors",
  "folder": "checkpoints",
  "filename": "sd_xl.safetensors"
}
```

**支持的 URL 类型：**
| 类型 | 示例 URL |
|------|----------|
| HuggingFace 直链 | `https://huggingface.co/{org}/{repo}/resolve/{branch}/{filename}` |
| HuggingFace 仓库 | `https://huggingface.co/{org}/{repo}` (自动查找主文件) |
| CivitAI 模型页 | `https://civitai.com/models/{id}` |
| CivitAI 版本页 | `https://civitai.com/models/{id}?modelVersion={version_id}` |
| 直链 HTTP | 任何可访问的 HTTP/HTTPS 下载链接 |

**Response:**
```json
{
  "task_id": "4353b883-88d0-401c-a66a-0fab1437ca28",
  "status": "queued",
  "url": "https://...",
  "folder": "checkpoints",
  "filename": "sd_xl.safetensors"
}
```

---

#### GET `/v2/extension/model/download/{task_id}` — 查询下载状态

**Response:**
```json
{
  "task_id": "4353b883-88d0-401c-a66a-0fab1437ca28",
  "status": "completed",
  "progress": 1.0,
  "url": "https://...",
  "downloaded_bytes": 28839,
  "total_bytes": 28839,
  "local_path": "/basedir/models/checkpoints/README.md",
  "error": null
}
```

**状态值：** `queued` | `downloading` | `completed` | `failed` | `cancelled`

---

#### DELETE `/v2/extension/model/download/{task_id}` — 取消下载任务

**Response:**
```json
{
  "task_id": "4353b883-88d0-401c-a66a-0fab1437ca28",
  "status": "cancelled"
}
```

---

#### GET `/v2/extension/model/download` — 列出所有下载任务

**Response:**
```json
{
  "tasks": [
    {
      "task_id": "...",
      "name": "download:https://...",
      "status": "completed",
      "progress": 1.0,
      "url": "https://...",
      "local_path": "/basedir/models/checkpoints/file.safetensors"
    }
  ]
}
```

---

### 模型管理

#### GET `/v2/extension/models/all` — 递归列出所有模型

**Query Parameters:**
| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `include_hash` | bool | false | 是否计算 SHA256 hash（较慢） |
| `folder` | string | - | 筛选特定文件夹类型 |

**Response:**
```json
{
  "models": [
    {
      "name": "sd_xl_base_1.0.safetensors",
      "path": "sd_xl_base_1.0.safetensors",
      "full_path": "/basedir/models/checkpoints/sd_xl_base_1.0.safetensors",
      "folder": "checkpoints",
      "size": 46149344974,
      "hash": "a1e637f7c2b1b0a9305ae4f6d0904143f1f79fee3d182417d39e7cf9f3838124",
      "metadata": {"__metadata__": {"model_version": "2.3.0", ...}}
    }
  ],
  "total": 2,
  "folders": ["checkpoints", "vae", "loras", "controlnet"]
}
```

**metadata 字段说明：**
- `.safetensors` 文件：读取文件 header 解析元数据
- `.ckpt`/`.pt`/`.pth` 文件：尝试解析 pickle 元数据
- 其他格式：返回 null

---

#### GET `/v2/extension/models/info` — 获取模型元数据

**Query Parameters:**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `path` | string | 是 | 模型路径（相对或绝对） |

**Response:**
```json
{
  "name": "ltx-2.3-22b-dev.safetensors",
  "path": "LTX-Video/ltx-2.3-22b-dev.safetensors",
  "full_path": "/basedir/models/checkpoints/LTX-Video/ltx-2.3-22b-dev.safetensors",
  "size": 46149344974,
  "hash": "a1e637f7...",
  "metadata": {"__metadata__": {"model_version": "2.3.0", ...}},
  "created": 1742332800.0,
  "modified": 1742332800.0
}
```

---

#### DELETE `/v2/extension/models/{path}` — 删除模型

**Query Parameters:**
| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `force` | bool | false | 强制删除（跳过保护检查） |

**Response:**
```json
{
  "deleted": true,
  "path": "/basedir/models/checkpoints/model.safetensors"
}
```

**错误响应：**
- `404`: 模型不存在
- `403`: 模型受保护（Manager 中的 protected_models）
- `409`: 模型正在使用中

---

### 自定义节点管理

#### GET `/v2/extension/nodes/list` — 列出所有节点

**Response:**
```json
{
  "nodes": [
    {
      "name": "comfyui-manager",
      "path": "/basedir/custom_nodes/comfyui-manager",
      "has_init": true,
      "is_git": true
    }
  ],
  "count": 15
}
```

---

#### POST `/v2/extension/nodes/pack` — 打包节点

**Request Body:**
```json
{
  "node_name": "comfyui-manager",
  "respect_comfyignore": true
}
```

**Response:**
```json
{
  "success": true,
  "node_name": "comfyui-manager",
  "path": "/basedir/custom_nodes/comfyui-manager.zip",
  "size": 1048576,
  "files_included": 42
}
```

---

#### POST `/v2/extension/nodes/validate` — 验证节点

**Request Body:**
```json
{
  "node_name": "comfyui-manager"
}
```

**Response:**
```json
{
  "node_name": "comfyui-manager",
  "valid": true,
  "errors": [],
  "warnings": ["Missing __init__.py - node may not be properly importable"],
  "files_checked": 42
}
```

**检查项：**
- Ruff 安全检查（S102 eval, S307 pickling, E702 exec）
- Python 语法错误
- `__init__.py` 存在性

---

#### POST `/v2/extension/nodes/init` — 初始化节点项目

**Request Body:**
```json
{
  "path": "/basedir/custom_nodes/my-custom-node"
}
```

**Response:**
```json
{
  "success": true,
  "path": "/basedir/custom_nodes/my-custom-node",
  "node_name": "my-custom-node",
  "git_url": "https://github.com/user/repo.git",
  "files_created": ["pyproject.toml", "__init__.py", "README.md"]
}
```

---

### 工作流依赖管理

#### POST `/v2/extension/workflow/dependencies/check` — 检查依赖

检查工作流所需依赖是否已满足。

**Request Body:**
```json
{
  "workflow": {
    "1": {"class_type": "KSampler", "inputs": {...}},
    "2": {"class_type": "CLIPTextEncode", "inputs": {...}}
  }
}
```

**Response:**
```json
{
  "missing": ["some-missing-package>=1.0"],
  "already_satisfied": ["torch", "numpy"],
  "can_run": false,
  "gpu_type": "cuda"
}
```

---

#### POST `/v2/extension/workflow/dependencies` — 安装依赖

异步安装工作流所需依赖。

**Request Body:**
```json
{
  "workflow": {...},
  "async_install": true
}
```

**Response:**
```json
{
  "task_id": "task-uuid",
  "status": "queued",
  "packages_to_install": ["missing-package"],
  "async": true
}
```

---

#### GET `/v2/extension/workflow/dependencies/{task_id}` — 查询安装状态

**Response:**
```json
{
  "task_id": "task-uuid",
  "status": "completed",
  "progress": 1.0,
  "installed": ["package1", "package2"],
  "failed": [],
  "restart_required": true,
  "pip_output": null
}
```

---

### 节点依赖管理

#### GET `/v2/extension/dependencies/check` — 检查节点依赖

**Query Parameters:**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `node` | string | 是 | 节点类名 |

**Response:**
```json
{
  "node": "KSampler",
  "required_packages": ["numpy>=1.20"]
}
```

---

#### POST `/v2/extension/dependencies/restore` — 恢复节点依赖

使用 ComfyUI Manager 的 cm-cli 恢复节点依赖。

**Request Body:**
```json
{
  "nodes": ["comfyui-manager", "comfyui-model-manager"],
  "async_mode": true
}
```

**Response (async):**
```json
{
  "task_id": "task-uuid",
  "status": "queued"
}
```

---

### 前端 PR 缓存管理

#### GET `/v2/extension/frontend/pr-cache` — 列出 PR 缓存

**Response:**
```json
{
  "cache": [
    {
      "name": "username-123-main",
      "path": "/home/user/.config/comfy-cli/pr-cache/frontend/username-123-main",
      "size": 104857600,
      "modified": 1742332800.0,
      "user": "username",
      "pr_number": 123,
      "branch": "main"
    }
  ],
  "total_size": 209715200,
  "count": 2
}
```

---

#### DELETE `/v2/extension/frontend/pr-cache/{pr}` — 删除指定缓存

**Response:**
```json
{
  "deleted": true,
  "name": "username-123-main"
}
```

---

#### DELETE `/v2/extension/frontend/pr-cache` — 清空所有缓存

**Query Parameters:**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `confirm` | bool | 是 | 必须设为 `true` |

**Response:**
```json
{
  "cleared": true,
  "entries_removed": 5,
  "errors": null
}
```

---

#### GET `/v2/extension/frontend/pr-cache/size` — 获取缓存大小

**Response:**
```json
{
  "total_size": 524288000,
  "count": 10
}
```

---

### WebSocket 事件

模型下载和依赖安装进度通过 WebSocket 推送：

```javascript
// 连接 WebSocket
const ws = new WebSocket('ws://127.0.0.1:8188/ws?clientId=your-client-id');

// 监听事件
ws.addEventListener('message', (event) => {
  const data = JSON.parse(event.data);

  switch (data.event) {
    case 'extension-model-download-progress':
      // 进度更新
      console.log(`下载进度: ${data.data.progress * 100}%`);
      break;

    case 'extension-model-download-complete':
      // 下载完成
      console.log(`已下载到: ${data.data.path}`);
      break;

    case 'extension-model-download-failed':
      // 下载失败
      console.error(`下载失败: ${data.data.error}`);
      break;

    case 'extension-model-download-cancelled':
      // 下载取消
      console.log('下载已取消');
      break;

    case 'extension-deps-install-complete':
      // 依赖安装完成
      console.log(`已安装: ${data.data.installed.join(', ')}`);
      break;

    case 'extension-deps-install-failed':
      // 依赖安装失败
      console.error(`安装失败: ${data.data.error}`);
      break;
  }
});
```

---

### 快照管理

#### POST `/v2/extension/snapshot/export` — 导出快照

**Request Body:**
```json
{
  "snapshot_id": "my-snapshot-2024",
  "format": "tarball",
  "include_models": false
}
```

**Response:**
```json
{
  "success": true,
  "snapshot_id": "my-snapshot-2024",
  "path": "/home/user/.comfyui/snapshots/my-snapshot-2024.tar.gz"
}
```

---

#### POST `/v2/extension/snapshot/import` — 导入快照

**Request Body (JSON):**
```json
{
  "path": "/path/to/snapshot.tar.gz",
  "restore_models": true,
  "restore_nodes": true
}
```

**Response:**
```json
{
  "success": true,
  "stdout": "Import completed..."
}
```

---

#### GET `/v2/extension/snapshot/diff` — 对比快照差异

**Query Parameters:**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `snapshot_a` | string | 是 | 第一个快照路径 |
| `snapshot_b` | string | 是 | 第二个快照路径 |

**Response:**
```json
{
  "snapshot_a": "/path/to/snap1.tar.gz",
  "snapshot_b": "/path/to/snap2.tar.gz",
  "custom_nodes": {
    "added": ["new-node"],
    "removed": ["old-node"],
    "common": ["shared-node"]
  },
  "pip_packages": {
    "added": ["new-package"],
    "removed": ["removed-package"],
    "common": ["shared-package"]
  }
}
```

---

#### GET `/v2/extension/snapshot/list` — 列出快照

**Response:**
```json
{
  "snapshots": [
    {
      "name": "snapshot-2024-03-18.tar.gz",
      "path": "/home/user/.comfyui/snapshots/snapshot-2024-03-18.tar.gz",
      "size": 1048576,
      "modified": 1742332800.0
    }
  ]
}
```

---

## 统计

| 分类 | 端点数量 |
|------|---------|
| ComfyUI 核心 (server.py) | ~25 |
| ComfyUI 内部 API | ~5 |
| ComfyUI 用户/设置/资产 | ~30 |
| ComfyUI Manager (glob) | ~38 |
| ComfyUI Manager (legacy 额外) | ~17 |
| **Comfy-REST-Ext** | **~28** |
| **合计** | **~143** |
