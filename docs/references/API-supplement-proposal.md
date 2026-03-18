# ComfyUI REST API 补充建议

> 本文档整理 ComfyUI 当前 REST API 中缺失的功能缺口，并给出推荐的补充端点设计。
> 不包含 Comfy Registry 相关功能（外部服务，非 ComfyUI 自身职责）。

---

## 目录

- [背景](#背景)
- [补充端点清单](#补充端点清单)
  - [模型下载](#1-模型下载)
  - [模型管理增强](#2-模型管理增强)
  - [工作流依赖安装](#3-工作流依赖安装)
  - [依赖管理](#4-依赖管理)
  - [自定义节点查询](#5-自定义节点查询)
  - [快照管理增强](#6-快照管理增强)
  - [节点打包与验证](#7-节点打包与验证)
  - [前端 PR 缓存](#8-前端-pr-缓存)
- [不推荐通过 REST API 暴露的功能](#不推荐通过-rest-api-暴露的功能)

---

## 背景

当前 ComfyUI REST API（约 115 端点）覆盖了核心的执行、队列、历史、节点信息等能力。

对比 `comfy_cli` 的完整功能集，存在以下主要缺口：

| 功能域 | REST API 现状 | 差距 |
|--------|-------------|------|
| 模型下载 | 完全缺失 | 无从 CivitAI/HuggingFace 等平台下载模型的能力 |
| 模型管理 | 仅列出顶层文件 | 不递归、无元数据 |
| 工作流依赖安装 | 完全缺失 | 无从 workflow JSON 解析并安装依赖的能力 |
| 依赖管理 | 完全缺失 | pip/uv 检查/安装无 API |
| 自定义节点查询 | 仅有 Manager 队列版本 | 缺少轻量级同步查询端点 |
| 快照 | 仅基础 CRUD | 无导入/导出/对比 |
| 节点打包 | 完全缺失 | git-tracked zip 操作 |
| PR 缓存 | 完全缺失 | 本地文件系统操作 |

### 设计原则

- **长任务异步化**：下载、依赖安装等耗时长、易超时的操作，必须通过异步队列 + WebSocket 推送进度
- **短查询同步化**：节点列表、元数据查询等轻量操作，提供同步 REST 端点

---

## 补充端点清单

### 1. 模型下载

#### 现状问题

完全缺失。无法通过 REST API 从 CivitAI、HuggingFace 等平台下载模型到本地模型目录。

`comfy_cli` 的 `comfy model download` 支持 CivitAI URL 解析、HuggingFace 文件下载、直接 URL 下载，REST API 无对应能力。

#### 设计思路

模型下载为**异步操作**：提交下载任务后立即返回 `task_id`，客户端通过 WebSocket 订阅任务进度，完成后获得文件路径。

#### 建议端点

##### `POST /v2/extension/model/download` — 提交模型下载任务

```
POST /v2/extension/model/download
```

**Request Body:**
```json
{
  "url": "https://civitai.com/api/v1/model-versions/12345",
  "folder": "checkpoints",
  "filename": "model.safetensors"
}
```

支持三种 URL 格式：
- **CivitAI**: `https://civitai.com/api/v1/model-versions/{id}` — 自动解析下载链接
- **HuggingFace**: `https://huggingface.co/{user}/{repo}/blob/{path}` — 自动解析下载链接
- **Direct URL**: 任意直链 — 直接下载

**Response:**
```json
{
  "task_id": "download-task-uuid",
  "status": "queued",
  "estimated_size": 5368709120
}
```

---

##### `GET /v2/extension/model/download/{task_id}` — 查询下载任务状态

```
GET /v2/extension/model/download/download-task-uuid
```

**Response (downloading):**
```json
{
  "task_id": "download-task-uuid",
  "status": "downloading",
  "progress": 0.45,
  "downloaded_bytes": 2415919104,
  "total_bytes": 5368709120,
  "speed_bps": 10485760
}
```

**Response (completed):**
```json
{
  "task_id": "download-task-uuid",
  "status": "completed",
  "path": "checkpoints/model.safetensors",
  "size": 5368709120,
  "hash": "blake3:abc123..."
}
```

**Response (failed):**
```json
{
  "task_id": "download-task-uuid",
  "status": "failed",
  "error": "File not found on CivitAI",
  "traceback": "..."
}
```

---

##### `DELETE /v2/extension/model/download/{task_id}` — 取消下载任务

```
DELETE /v2/extension/model/download/download-task-uuid
```

---

##### `GET /v2/extension/model/download` — 列出所有下载任务

```
GET /v2/extension/model/download
```

**Response:**
```json
{
  "tasks": [
    {
      "task_id": "download-task-uuid",
      "status": "downloading",
      "url": "https://civitai.com/...",
      "folder": "checkpoints"
    }
  ]
}
```

---

### 2. 模型管理增强

#### 现状问题

- `GET /models/{folder}` 只列出顶层文件名，不递归
- 无文件大小、hash、修改时间等元数据
- 无删除模型的 REST 端点

#### 建议端点

##### `GET /models/all` — 递归列出所有模型

返回所有模型目录下的完整文件列表。

```
GET /models/all
```

**Query Parameters:**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `folder` | string | - | 可选，限制特定文件夹（如 `checkpoints`） |
| `recursive` | boolean | `true` | 是否递归子目录 |
| `include_size` | boolean | `false` | 包含文件大小 |
| `include_hash` | boolean | `false` | 包含 blake3 hash |
| `name_contains` | string | - | 文件名包含的字符串过滤 |

**Response:**
```json
{
  "folders": {
    "checkpoints": [
      {
        "name": "model_a.safetensors",
        "path": "checkpoints/model_a.safetensors",
        "size": 5368709120,
        "hash": "blake3:abc123...",
        "modified_at": 1710000000.0
      }
    ]
  }
}
```

---

##### `DELETE /models/{path}` — 删除模型文件

```
DELETE /models/checkpoints/model_a.safetensors
```

**Response:**
```json
{
  "status": "ok",
  "deleted": "checkpoints/model_a.safetensors",
  "freed_bytes": 5368709120
}
```

**Error Responses:**
- `404`: 文件不存在
- `403`: 文件为保护模型，不可删除

---

##### `GET /models/info` — 获取模型详细信息

```
GET /models/info?path=checkpoints/model_a.safetensors
```

返回特定模型的完整元数据（从 safetensors header 解析）。

---

### 3. 工作流依赖安装

#### 现状问题

`comfy_cli` 的 `node install-deps` 支持从 workflow JSON 或 dependencies.json 解析并安装 Python 依赖，REST API 完全缺失。

这是**高频刚需**：用户导入了新 workflow 后，节点缺失依赖是最常见的报错场景。

#### 设计思路

工作流依赖安装为**异步操作**：解析依赖 → 提交安装任务 → WebSocket 推送进度 → 完成/失败通知。

#### 建议端点

##### `POST /v2/extension/workflow/dependencies` — 从工作流解析并安装依赖

提交 workflow JSON，自动解析节点所需依赖并安装。

```
POST /v2/extension/workflow/dependencies
```

**Request Body:**
```json
{
  "workflow": {
    "1": {
      "class_type": "KSampler",
      "inputs": {"model": ["2", 0]}
    },
    "2": {
      "class_type": "CheckpointLoaderSimple",
      "inputs": {"ckpt_name": "model.safetensors"}
    }
  }
}
```

或直接提交 workflow JSON 文件内容（和 `/prompt` 的 prompt 字段相同）。

**Response:**
```json
{
  "task_id": "deps-task-uuid",
  "status": "queued",
  "workflow_hash": "blake3:abc123",
  "dependencies": {
    "missing": ["torch>=2.0"],
    "already_satisfied": ["numpy"]
  },
  "installing": ["torch>=2.0"]
}
```

---

##### `GET /v2/extension/workflow/dependencies/{task_id}` — 查询依赖安装状态

```
GET /v2/extension/workflow/dependencies/deps-task-uuid
```

**Response:**
```json
{
  "task_id": "deps-task-uuid",
  "status": "installing",
  "package": "torch>=2.0",
  "progress": 0.7,
  "pip_output": "Downloading torch..."
}
```

**Response (completed):**
```json
{
  "task_id": "deps-task-uuid",
  "status": "completed",
  "installed": ["torch>=2.0"],
  "failed": [],
  "restart_required": true
}
```

---

##### `POST /v2/extension/workflow/dependencies/check` — 仅检查依赖（不安装）

```
POST /v2/extension/workflow/dependencies/check
```

**Request Body:** 同上

**Response:**
```json
{
  "workflow_hash": "blake3:abc123",
  "dependencies": {
    "missing": ["torch>=2.0"],
    "already_satisfied": ["numpy", "pillow"]
  },
  "can_run": false,
  "missing_nodes": ["CustomNodeX"]
}
```

---

### 4. 依赖管理

#### 现状问题

完全缺失。无法通过 REST API 检查节点依赖状态或从 `requirements.txt` 恢复依赖。

#### 建议端点

##### `GET /v2/extension/dependencies/check` — 检查节点依赖状态

```
GET /v2/extension/dependencies/check?node=custom-node-id
```

**Response:**
```json
{
  "node": "custom-node-id",
  "missing": ["torch>=2.0"],
  "satisfied": ["numpy", "pillow"],
  "can_run": false
}
```

---

##### `POST /v2/extension/dependencies/restore` — 恢复节点依赖

从已安装节点目录的 `requirements.txt` 恢复所有依赖。

```
POST /v2/extension/dependencies/restore
```

**Request Body:**
```json
{
  "nodes": ["node-a", "node-b"],
  "async": true
}
```

- `async=true`：异步执行，通过 WebSocket 推送进度
- `async=false`：同步执行，等待完成（可能超时）

**Response (async):**
```json
{
  "task_id": "deps-task-uuid",
  "status": "queued",
  "nodes": ["node-a", "node-b"]
}
```

---

### 5. 自定义节点查询

#### 现状问题

Manager 的 `GET /v2/customnode/installed` 返回完整的节点信息，过于冗长。缺少轻量级的快速查询端点。

#### 建议端点

##### `POST /v2/customnode/simple-show` — 轻量级节点列表

返回简洁的已安装节点列表。

```
POST /v2/customnode/simple-show
```

**Request Body:**
```json
{
  "channel": "default",
  "mode": "local"
}
```

**Response:**
```json
{
  "nodes": [
    {
      "name": "custom-node-id",
      "title": "Display Name",
      "author": "author",
      "installed_version": "abc1234"
    }
  ]
}
```

---

### 6. 快照管理增强

#### 现状问题

现有 `/v2/extension/snapshot/*` 仅支持基础 CRUD，无导入/导出/对比能力。

#### 建议端点

##### `POST /v2/extension/snapshot/export` — 导出快照

将快照导出为可分发的文件。

```
POST /v2/extension/snapshot/export
```

**Request Body:**
```json
{
  "snapshot_id": "uuid-or-name",
  "format": "tarball",
  "include_models": false
}
```

**Response:**
```json
{
  "status": "ok",
  "path": "/path/to/snapshot.tar.gz",
  "size": 1024000,
  "checksum": "blake3:..."
}
```

---

##### `POST /v2/extension/snapshot/import` — 导入快照

从文件恢复快照。

```
POST /v2/extension/snapshot/import
```

**Request Body:** `multipart/form-data`
- `file`: snapshot.tar.gz
- `restore_models`: `"true"` / `"false"`
- `restore_nodes`: `"true"` / `"false"`

**Response:**
```json
{
  "status": "ok",
  "restored_nodes": ["node-a", "node-b"],
  "restored_models": [],
  "errors": []
}
```

---

##### `GET /v2/extension/snapshot/diff` — 对比快照差异

对比两个快照或快照与当前环境的差异。

```
GET /v2/extension/snapshot/diff?id1=snapshot-a&id2=snapshot-b
```

**Response:**
```json
{
  "left": "snapshot-a",
  "right": "snapshot-b",
  "custom_nodes": {
    "only_in_a": ["deprecated-node"],
    "only_in_b": ["new-node"],
    "different": []
  },
  "dependencies": {
    "only_in_a": ["old-package"],
    "only_in_b": ["new-package"]
  }
}
```

---

### 7. 节点打包与验证

#### 现状问题

`comfy node pack` 打包节点为 zip（收集 git-tracked 文件），`comfy node validate` 运行 ruff 安全检查，均无 REST API。

#### 建议端点

##### `POST /v2/extension/nodes/pack` — 打包节点

```json
POST /v2/extension/nodes/pack
```

**Request Body:**
```json
{
  "node_name": "custom-node-id",
  "respect_comfyignore": true
}
```

**Response:**
```json
{
  "status": "ok",
  "path": "/path/to/custom-node-id.zip",
  "size": 102400,
  "file_count": 23
}
```

---

##### `POST /v2/extension/nodes/validate` — 验证节点

运行 ruff 安全检查，验证节点配置是否合规。

```
POST /v2/extension/nodes/validate
```

**Request Body:**
```json
{
  "node_name": "custom-node-id"
}
```

**Response (passed):**
```json
{
  "status": "ok",
  "node_name": "custom-node-id",
  "passed": true,
  "warnings": []
}
```

**Response (with warnings):**
```json
{
  "status": "ok",
  "node_name": "custom-node-id",
  "passed": true,
  "warnings": [
    {"code": "S102", "message": "exec used", "file": "nodes/test.py", "line": 10}
  ]
}
```

---

##### `POST /v2/extension/nodes/init` — 初始化节点项目

从当前目录创建节点 `pyproject.toml` 脚手架。

```
POST /v2/extension/nodes/init
```

**Request Body:**
```json
{
  "path": "/path/to/custom-node"
}
```

**Response:**
```json
{
  "status": "ok",
  "created": ["pyproject.toml"],
  "git_remote": "https://github.com/author/repo"
}
```

---

### 8. 前端 PR 缓存

#### 现状问题

`comfy_cli pr-cache` 管理前端 PR 构建缓存（存储在 `~/.config/comfy-cli/pr-cache/`），REST API 完全缺失。

#### 建议端点

##### `GET /v2/extension/frontend/pr-cache` — 列出 PR 缓存

```
GET /v2/extension/frontend/pr-cache
```

**Response:**
```json
{
  "items": [
    {
      "pr": 1234,
      "cached_at": 1710000000.0,
      "size": 52428800,
      "url": "https://github.com/..."
    }
  ]
}
```

---

##### `DELETE /v2/extension/frontend/pr-cache/{pr}` — 清理指定 PR 缓存

```
DELETE /v2/extension/frontend/pr-cache/1234
```

**Response:**
```json
{
  "status": "ok",
  "pr": 1234,
  "freed_bytes": 52428800
}
```

---

##### `DELETE /v2/extension/frontend/pr-cache` — 清理所有 PR 缓存

```
DELETE /v2/extension/frontend/pr-cache
```

---

## 不推荐通过 REST API 暴露的功能

以下功能建议保持为 CLI 工具，不适合 REST API：

| 命令 | 原因 |
|------|------|
| `comfy install` | 完整 Git clone + pip install，涉及网络、文件系统初始化 |
| `comfy launch` | subprocess 启动 `main.py`，需控制进程生命周期 |
| `comfy stop` | 终止进程树，需系统级权限 |
| `comfy update` | git pull + pip install，本地文件系统操作 |
| `comfy standalone` | 打包独立 Python 环境，涉及下载、解压、PATH 修改 |
| `comfy env` | 本地环境检查，直接读取系统信息 |
| `comfy set-default` | 写入用户配置文件 |
| `comfy feedback` | 交互式用户输入，不适合 API |

### 为什么不推荐同步节点安装操作

`POST /v2/customnode/{install, uninstall, update, disable, enable, fix}` 这类同步阻塞操作**不推荐加入 REST API**，原因：

1. **超时问题**：节点 `git clone` 可能需要数分钟，HTTP 请求超时无法避免
2. **ComfyUI Manager 已覆盖**：异步队列 `/v2/manager/queue/{install,update,uninstall,fix,disable}` 已支持这些操作，配合 WebSocket 推送进度，客户端可通过轮询或订阅获得结果
3. **进程管理复杂性**：同步 subprocess 调用增加了服务器进程管理的复杂度

如需同步查询节点状态，使用轻量级的 `POST /v2/customnode/simple-show` 即可。

---

## 端点优先级

| 优先级 | 端点 | 理由 |
|--------|------|------|
| **P0** | `POST /v2/extension/model/download` + `GET /v2/extension/model/download/{task_id}` | 模型下载是高频需求，必须通过异步队列 + WebSocket 实现 |
| **P0** | `POST /v2/extension/workflow/dependencies` + `GET /v2/extension/workflow/dependencies/{task_id}` | workflow 导入后自动安装依赖是最高频痛点，异步队列避免超时 |
| **P0** | `POST /v2/extension/workflow/dependencies/check` | 仅检查依赖，不安装，适合快速诊断 |
| **P1** | `GET /models/all` + `DELETE /models/{path}` | 模型管理增强，覆盖真实使用场景 |
| **P1** | `POST /v2/extension/dependencies/restore` | 从已安装节点恢复依赖，异步执行 |
| **P2** | `GET /v2/extension/dependencies/check` | 检查节点依赖状态，轻量查询 |
| **P2** | `POST /v2/extension/nodes/pack` | 节点打包是发布流程的核心环节 |
| **P2** | `POST /v2/extension/snapshot/{export,import}` | 快照导入导出提升可移植性 |
| **P3** | `POST /v2/extension/nodes/validate` + `POST /v2/extension/nodes/init` | 辅助节点开发工作流 |
| **P3** | `GET /v2/extension/snapshot/diff` | 快照对比，较低频 |
| **P3** | `GET/DELETE /v2/extension/frontend/pr-cache` | PR 缓存管理，低频 |

---

## WebSocket 事件扩展

异步任务（模型下载、依赖安装）完成后，通过 `/ws` WebSocket 推送事件，需扩展以下事件类型：

| 事件名 | 触发时机 | Payload 示例 |
|--------|---------|-------------|
| `extension-model-download-progress` | 下载进度更新 | `{"task_id": "...", "progress": 0.45, "speed_bps": 10485760}` |
| `extension-model-download-complete` | 下载完成 | `{"task_id": "...", "path": "checkpoints/model.safetensors"}` |
| `extension-model-download-failed` | 下载失败 | `{"task_id": "...", "error": "File not found"}` |
| `extension-deps-install-progress` | 依赖安装进度 | `{"task_id": "...", "package": "torch", "progress": 0.7}` |
| `extension-deps-install-complete` | 依赖安装完成 | `{"task_id": "...", "installed": [...], "restart_required": true}` |
| `extension-deps-install-failed` | 依赖安装失败 | `{"task_id": "...", "package": "torch", "error": "..."}` |
