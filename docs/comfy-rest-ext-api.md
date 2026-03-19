# Comfy-REST-Ext API 参考

ComfyUI REST API 扩展，为 ComfyUI 补充的额外端点。

---

## 目录

- [健康检查](#健康检查)
- [模型下载](#模型下载)
- [模型管理](#模型管理)
- [自定义节点管理](#自定义节点管理)
- [工作流依赖管理](#工作流依赖管理)
- [节点依赖管理](#节点依赖管理)
- [前端 PR 缓存管理](#前端-pr-缓存管理)
- [快照管理](#快照管理)
- [WebSocket 事件](#websocket-事件)

---

## 健康检查

### GET `/v2/extension/health`

健康检查端点，验证扩展是否正常加载。

**响应示例：**
```json
{
  "status": "ok",
  "extension": "comfy-rest-ext"
}
```

---

## 模型下载

### POST `/v2/extension/model/download` — 创建下载任务

从 CivitAI、HuggingFace 或直链下载模型文件。

**请求体：**
```json
{
  "url": "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors",
  "folder": "checkpoints",
  "filename": "sd_xl.safetensors"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `url` | string | 是 | 下载链接 |
| `folder` | string | 否 | 目标文件夹类型（默认 checkpoints） |
| `filename` | string | 否 | 保存的文件名（默认自动解析） |

**支持的 URL 类型：**

| 类型 | 示例 |
|------|------|
| HuggingFace 直链 | `https://huggingface.co/{org}/{repo}/resolve/{branch}/{filename}` |
| HuggingFace 仓库 | `https://huggingface.co/{org}/{repo}` |
| CivitAI 模型页 | `https://civitai.com/models/{id}` |
| CivitAI 版本页 | `https://civitai.com/models/{id}?modelVersion={version_id}` |
| 直链 HTTP | 任何可访问的 HTTP/HTTPS 链接 |
| HuggingFace 镜像 | 如 `https://hf-mirror.com/...`（受 `HF_ENDPOINT` 环境变量控制） |

**断点续传：** 如果目标路径已存在同名文件，将自动从断点继续下载（通过 HTTP Range 头实现）。返回的 `existing_size` 表示已存在的文件大小。

**响应：**
```json
{
  "task_id": "4353b883-88d0-401c-a66a-0fab1437ca28",
  "status": "queued",
  "url": "https://...",
  "folder": "checkpoints",
  "filename": "sd_xl.safetensors",
  "existing_size": 45000000
}
```

| 额外字段 | 说明 |
|---------|------|
| `existing_size` | 如果目标路径已有同名文件，表示该文件大小（字节），可用于判断是否需要续传。为 `null` 表示无残留文件。 |

---

### GET `/v2/extension/model/download/{task_id}` — 查询下载状态

**响应：**
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

**状态值：**

| 状态 | 说明 |
|------|------|
| `queued` | 任务已创建，等待执行 |
| `downloading` | 下载中 |
| `completed` | 下载完成 |
| `failed` | 下载失败 |
| `cancelled` | 已取消 |

---

### DELETE `/v2/extension/model/download/{task_id}` — 取消下载任务

**响应：**
```json
{
  "task_id": "4353b883-88d0-401c-a66a-0fab1437ca28",
  "status": "cancelled"
}
```

---

### GET `/v2/extension/model/download` — 列出所有下载任务

**查询参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `status` | string | `queued,downloading` | 状态过滤，支持的值：`queued`, `downloading`, `completed`, `failed`, `cancelled`，或使用 `all` 包含所有状态。多个状态用逗号分隔 |

**示例：**
- `?status=queued,downloading` — 仅活跃任务（默认）
- `?status=all` — 所有任务（含历史）
- `?status=completed,failed` — 仅已完成和失败

**响应：**
```json
{
  "tasks": [
    {
      "task_id": "...",
      "name": "download:https://...",
      "status": "downloading",
      "progress": 0.45,
      "url": "https://...",
      "local_path": "/basedir/models/checkpoints/file.safetensors",
      "downloaded_bytes": 45000000,
      "total_bytes": 100000000
    }
  ]
}
```

---

## 模型管理

### GET `/v2/extension/models/all` — 列出所有模型

递归列出 ComfyUI 模型文件夹中的所有模型文件。

**查询参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `include_hash` | bool | false | 是否计算 SHA256 hash（会显著变慢） |
| `include_metadata` | bool | false | 是否包含模型元数据（默认关闭，元数据较大） |
| `folder` | string | - | 仅列出指定文件夹，如 `checkpoints`。支持自定义子目录（如下载任务创建的自定义文件夹） |

**响应：**
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
      "metadata": {
        "__metadata__": {
          "model_version": "2.3.0"
        }
      }
    }
  ],
  "total": 2,
  "folders": ["checkpoints", "vae", "loras", "controlnet"]
}
```

**metadata 字段：**
- `.safetensors` 文件：读取文件 header 解析元数据
- `.ckpt`/`.pt`/`.pth` 文件：尝试解析 pickle 元数据
- 其他格式：返回 `null`

---

### GET `/v2/extension/models/info` — 获取模型元数据

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `path` | string | 是 | 模型路径（相对于模型文件夹或绝对路径） |

**响应：**
```json
{
  "name": "ltx-2.3-22b-dev.safetensors",
  "path": "LTX-Video/ltx-2.3-22b-dev.safetensors",
  "full_path": "/basedir/models/checkpoints/LTX-Video/ltx-2.3-22b-dev.safetensors",
  "size": 46149344974,
  "hash": "a1e637f7...",
  "metadata": {"__metadata__": {"model_version": "2.3.0"}},
  "created": 1742332800.0,
  "modified": 1742332800.0
}
```

---

### DELETE `/v2/extension/models/{path}` — 删除模型

**查询参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `force` | bool | false | 强制删除（跳过保护检查） |

**响应：**
```json
{
  "deleted": true,
  "path": "/basedir/models/checkpoints/model.safetensors"
}
```

**错误响应：**

| 状态码 | 说明 |
|--------|------|
| 404 | 模型不存在 |
| 403 | 模型受保护（Manager 中的 protected_models） |
| 409 | 模型正在使用中 |

---

## 自定义节点管理

### GET `/v2/extension/nodes/list` — 列出所有节点

**响应：**
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

### POST `/v2/extension/nodes/pack` — 打包节点

将节点目录打包为 zip 文件。

**请求体：**
```json
{
  "node_name": "comfyui-manager",
  "respect_comfyignore": true
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `node_name` | string | 是 | 节点目录名 |
| `respect_comfyignore` | bool | 否 | 是否应用 .comfyignore 规则（默认 true） |

**响应：**
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

### POST `/v2/extension/nodes/validate` — 验证节点

检查节点代码安全问题。

**请求体：**
```json
{
  "node_name": "comfyui-manager"
}
```

**响应：**
```json
{
  "node_name": "comfyui-manager",
  "valid": true,
  "errors": [],
  "warnings": ["Missing __init__.py - node may not be properly importable"],
  "files_checked": 42
}
```

**检查项目：**
- Ruff 安全检查（`S102` eval, `S307` pickling, `E702` exec）
- Python 语法错误
- `__init__.py` 存在性

---

### POST `/v2/extension/nodes/init` — 初始化节点项目

创建新的 ComfyUI 节点项目结构。

**请求体：**
```json
{
  "path": "/basedir/custom_nodes/my-custom-node"
}
```

**响应：**
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

## 工作流依赖管理

### POST `/v2/extension/workflow/dependencies/check` — 检查依赖

检查工作流所需依赖是否已安装。

**请求体：**
```json
{
  "workflow": {
    "1": {"class_type": "KSampler", "inputs": {...}},
    "2": {"class_type": "CLIPTextEncode", "inputs": {...}}
  }
}
```

**响应：**
```json
{
  "missing": ["some-missing-package>=1.0"],
  "already_satisfied": ["torch", "numpy"],
  "can_run": false,
  "gpu_type": "cuda"
}
```

---

### POST `/v2/extension/workflow/dependencies` — 安装依赖

异步安装工作流所需依赖。

**请求体：**
```json
{
  "workflow": {...},
  "async_install": true
}
```

**响应：**
```json
{
  "task_id": "task-uuid",
  "status": "queued",
  "packages_to_install": ["missing-package"],
  "async": true
}
```

---

### GET `/v2/extension/workflow/dependencies/{task_id}` — 查询安装状态

**响应：**
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

## 节点依赖管理

### GET `/v2/extension/dependencies/check` — 检查节点依赖

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `node` | string | 是 | 节点类名 |

**响应：**
```json
{
  "node": "KSampler",
  "required_packages": ["numpy>=1.20"]
}
```

---

### POST `/v2/extension/dependencies/restore` — 恢复节点依赖

使用 ComfyUI Manager 的 cm-cli 恢复节点依赖。

**请求体：**
```json
{
  "nodes": ["comfyui-manager", "comfyui-model-manager"],
  "async_mode": true
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `nodes` | array | 是 | 节点名称列表 |
| `async_mode` | bool | 否 | 异步执行（默认 true） |

**响应（异步模式）：**
```json
{
  "task_id": "task-uuid",
  "status": "queued"
}
```

---

## 前端 PR 缓存管理

### GET `/v2/extension/frontend/pr-cache` — 列出 PR 缓存

**响应：**
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

### DELETE `/v2/extension/frontend/pr-cache/{pr}` — 删除指定缓存

**响应：**
```json
{
  "deleted": true,
  "name": "username-123-main"
}
```

---

### DELETE `/v2/extension/frontend/pr-cache` — 清空所有缓存

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `confirm` | bool | 是 | 必须设为 `true` |

**响应：**
```json
{
  "cleared": true,
  "entries_removed": 5,
  "errors": null
}
```

---

### GET `/v2/extension/frontend/pr-cache/size` — 获取缓存大小

**响应：**
```json
{
  "total_size": 524288000,
  "count": 10
}
```

---

## 快照管理

### POST `/v2/extension/snapshot/export` — 导出快照

**请求体：**
```json
{
  "snapshot_id": "my-snapshot-2024",
  "format": "tarball",
  "include_models": false
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `snapshot_id` | string | 是 | 快照标识符 |
| `format` | string | 否 | 格式（默认 tarball） |
| `include_models` | bool | 否 | 是否包含模型（默认 false） |

**响应：**
```json
{
  "success": true,
  "snapshot_id": "my-snapshot-2024",
  "path": "/home/user/.comfyui/snapshots/my-snapshot-2024.tar.gz"
}
```

---

### POST `/v2/extension/snapshot/import` — 导入快照

**请求体：**
```json
{
  "path": "/path/to/snapshot.tar.gz",
  "restore_models": true,
  "restore_nodes": true
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `path` | string | 是 | 快照文件路径 |
| `restore_models` | bool | 否 | 是否恢复模型（默认 true） |
| `restore_nodes` | bool | 否 | 是否恢复节点（默认 true） |

**响应：**
```json
{
  "success": true,
  "stdout": "Import completed..."
}
```

---

### GET `/v2/extension/snapshot/diff` — 对比快照差异

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `snapshot_a` | string | 是 | 第一个快照路径 |
| `snapshot_b` | string | 是 | 第二个快照路径 |

**响应：**
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

### GET `/v2/extension/snapshot/list` — 列出快照

**响应：**
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

## WebSocket 事件

模型下载和依赖安装进度通过 WebSocket 推送。连接方式：

```javascript
// 连接 WebSocket
const ws = new WebSocket('ws://127.0.0.1:8188/ws?clientId=your-client-id');

// 监听事件
ws.addEventListener('message', (event) => {
  const data = JSON.parse(event.data);
  console.log(data.event, data.data);
});
```

### 下载进度事件

| 事件名 | 说明 | data 字段 |
|--------|------|-----------|
| `extension-model-download-progress` | 下载进度更新 | `task_id`, `progress` |
| `extension-model-download-complete` | 下载完成 | `task_id`, `path` |
| `extension-model-download-failed` | 下载失败 | `task_id`, `error` |
| `extension-model-download-cancelled` | 下载取消 | `task_id` |

### 依赖安装事件

| 事件名 | 说明 | data 字段 |
|--------|------|-----------|
| `extension-deps-install-complete` | 安装完成 | `task_id`, `installed`, `failed`, `restart_required` |
| `extension-deps-install-failed` | 安装失败 | `task_id`, `error` |
| `extension-deps-install-cancelled` | 安装取消 | `task_id` |

---

## 端点总览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/v2/extension/health` | 健康检查 |
| POST | `/v2/extension/model/download` | 创建下载任务 |
| GET | `/v2/extension/model/download/{task_id}` | 查询下载状态 |
| DELETE | `/v2/extension/model/download/{task_id}` | 取消下载任务 |
| GET | `/v2/extension/model/download` | 列出所有下载任务 |
| GET | `/v2/extension/models/all` | 递归列出所有模型 |
| GET | `/v2/extension/models/info` | 获取模型元数据 |
| DELETE | `/v2/extension/models/{path}` | 删除模型 |
| GET | `/v2/extension/nodes/list` | 列出所有节点 |
| POST | `/v2/extension/nodes/pack` | 打包节点 |
| POST | `/v2/extension/nodes/validate` | 验证节点 |
| POST | `/v2/extension/nodes/init` | 初始化节点项目 |
| POST | `/v2/extension/workflow/dependencies/check` | 检查依赖 |
| POST | `/v2/extension/workflow/dependencies` | 安装依赖 |
| GET | `/v2/extension/workflow/dependencies/{task_id}` | 查询安装状态 |
| GET | `/v2/extension/dependencies/check` | 检查节点依赖 |
| POST | `/v2/extension/dependencies/restore` | 恢复节点依赖 |
| GET | `/v2/extension/frontend/pr-cache` | 列出 PR 缓存 |
| DELETE | `/v2/extension/frontend/pr-cache/{pr}` | 删除指定缓存 |
| DELETE | `/v2/extension/frontend/pr-cache` | 清空所有缓存 |
| GET | `/v2/extension/frontend/pr-cache/size` | 获取缓存大小 |
| POST | `/v2/extension/snapshot/export` | 导出快照 |
| POST | `/v2/extension/snapshot/import` | 导入快照 |
| GET | `/v2/extension/snapshot/diff` | 对比快照差异 |
| GET | `/v2/extension/snapshot/list` | 列出快照 |

**总计：28 个端点**
