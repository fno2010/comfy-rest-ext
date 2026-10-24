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

| POST | `/v2/extension/video/generate` | 提交视频生成任务 |
| GET | `/v2/extension/video/{task_id}` | 查询视频生成状态 |
| GET | `/v2/extension/video` | 列出视频生成任务 |
| POST | `/v2/extension/image/generate` | 提交图片生成任务（异步） |
| GET | `/v2/extension/image/{task_id}` | 查询图片生成状态 |
| GET | `/v2/extension/image/{task_id}/content` | 读取图片输出（`?index=N`） |
| GET | `/v2/extension/image` | 列出图片生成任务 |
| GET | `/v2/extension/ui` | 任务面板（HTML） |
| GET | `/ui` | 302 到任务面板 |
| GET | `/v1/models` | 列出模型（OpenAI 兼容） |
| POST | `/v1/images/generations` | 生成图片（OpenAI 兼容，同步） |
| POST | `/v1/images/edits` | 编辑图片（OpenAI 兼容，multipart） |
| POST | `/v1/videos` | 创建视频任务（OpenAI 兼容） |
| GET | `/v1/videos/{video_id}` | 查询视频任务（OpenAI 兼容） |
| GET | `/v1/videos/{video_id}/content` | 下载视频（OpenAI 兼容） |
| GET | `/v1/videos` | 列出视频任务（OpenAI 兼容） |
| DELETE | `/v1/videos/{video_id}` | 删除视频任务（OpenAI 兼容） |

**总计：45 个端点**

---

## 视频生成 (MiniMax-H3)

### POST `/v2/extension/video/generate` — 提交视频生成任务

提交 MiniMax-H3 T2V/I2V 生成任务。任务异步执行，通过 `/v2/extension/video/{task_id}` 查询状态。

**请求体：**
```json
{
  "prompt": "A red cube rolling across a white floor",
  "task": "t2va",
  "width": 1344,
  "height": 768,
  "duration": 5.0,
  "seed": 42,
  "first_frame": "data:image/png;base64,..."
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `prompt` | string | 是 | 文本提示词 |
| `task` | string | 否 | `t2va`（默认）或 `fl2va` |
| `width` | int | 否 | 输出宽度（默认 1344） |
| `height` | int | 否 | 输出高度（默认 768） |
| `duration` | float | 否 | 时长秒数（默认 5.0，自动对齐 H3 17k+5 帧网格） |
| `seed` | int | 否 | 随机种子（默认 0） |
| `first_frame` | string | 否 | I2V 首帧：ComfyUI input 文件名或 data URL |

**响应：**
```json
{
  "task_id": "ae3ac055-3396-4bd0-811e-cbaf0607aaf1",
  "status": "queued",
  "task": "t2va",
  "prompt": "...",
  "width": 1344,
  "height": 768,
  "duration": 5.0,
  "length": 125
}
```

### GET `/v2/extension/video/{task_id}` — 查询生成状态

**响应：**
```json
{
  "task_id": "...",
  "status": "completed",
  "task": "t2va",
  "progress": 1.0,
  "prompt_id": "...",
  "output_path": "/comfy/mnt/ComfyUI/output/video/comfy-rest-ext_00002_.mp4",
  "error": null,
  "created_at": 1785908457.0,
  "completed_at": 1785909085.0
}
```

**状态值：** `queued` | `running` | `completed` | `failed`

### GET `/v2/extension/video` — 列出活跃任务

---

## 图片生成

图片模型通过 `model` 字段路由：

| model id | 任务 | 默认尺寸 | 默认采样 |
|---|---|---|---|
| `anima-base` | `t2i` | 1024x1024 | 30 steps / cfg 4 / euler / simple |
| `anima-turbo` | `t2i` | 1024x1024 | 8 steps / cfg 1 / euler / simple |
| `qwen-image-2.1` | `t2i`, `edit` | 1024x1024 | 25 steps / cfg 1 / euler / simple |
| `qwen-image-2.1-turbo` | `t2i`, `edit` | 1024x1024 | 8-step ManualSigmas |

### POST `/v1/images/generations` — 同步生成

**请求体：**

```json
{
  "model": "anima-base",
  "prompt": "masterpiece, best quality, 1girl, ...",
  "negative_prompt": "worst quality, ...",
  "size": "1024x1024",
  "n": 1,
  "response_format": "b64_json",
  "seed": 42,
  "steps": 30,
  "cfg": 4.0,
  "sampler": "euler",
  "scheduler": "simple"
}
```

| 字段 | 说明 |
|---|---|
| `model` | 必填，图片模型 id |
| `prompt` | 必填 |
| `size` | `WIDTHxHEIGHT` 或 `auto`；也可用 `width`/`height` |
| `aspect_ratio` | Qwen-Image 2.1：如 `16:9`，配合 `megapixels` |
| `megapixels` | Qwen-Image 2.1：目标像素数（1.0 ≈ 1024²，4.0 ≈ 2048²） |
| `n` | 1..模型上限（Anima 8，Qwen-Image 2.1 4） |
| `response_format` | `b64_json`（默认）或 `url` |
| `negative_prompt` | Qwen-Image 2.1 在 cfg=1 时实际不生效 |
| `seed` / `steps` / `cfg` / `sampler` / `scheduler` | 扩展字段 |
| `quality` / `style` | 接受但不改变生成参数 |
| `output_format` | 仅支持 `png`（Qwen-Image 2.1 的 alpha 通道） |
| `Prefer` | 请求头；`respond-async` 时立即返回 202 + 任务记录，不等待生成完成 |
| `stream` / `partial_images` | 不支持，返回 400 |

**响应：**

```json
{
  "created": 1792590000,
  "model": "anima-base",
  "size": "1024x1024",
  "data": [{"b64_json": "...", "revised_prompt": "..."}]
}
```

`response_format=url` 时 `data[]` 为 `{"url": "http://.../view?..."}`；URL 基址优先取 `PUBLIC_BASE_URL`，否则取请求 origin。

### POST `/v1/images/edits` — 编辑（Qwen-Image 2.1）

`multipart/form-data`，字段：`model`、`prompt`、`image`（可重复，1..16 张）、`n`、`response_format`、`resolution`（0..4096，步长 32）。

```bash
curl -X POST http://host:8188/v1/images/edits \
  -F model=qwen-image-2.1 \
  -F prompt='Keep <image1> unchanged, put the shirt from <image2> on the character' \
  -F image=@portrait.png -F image=@shirt.png
```

参考图按顺序映射到 `<image1>`…`<imageN>`；`image1` 是被编辑图。`mask` 不支持（返回 400）。

### 异步端点

- `POST /v2/extension/image/generate` — JSON 请求，立即返回 `{"id":"image_...","status":"queued"}`。
- `GET /v2/extension/image/{task_id}` — 查询状态、进度、`view_urls`。
- `GET /v2/extension/image/{task_id}/content?index=N` — 读取第 N 张输出图（`Content-Type: image/png`）。
- `GET /v2/extension/image` — 列出图片任务。

`view_urls` 由扩展生成，指向 `/v2/extension/image/{id}/content?index=N`，不依赖 ComfyUI 的 `/view` 路由。视频的 `view_url` 同理指向 `/v1/videos/{id}/content`。

同步请求超时返回 504，任务不会自动取消；可继续轮询异步端点。

### GET `/v2/extension/ui` — 任务面板

返回零依赖单页 HTML（`/ui` 会 302 到此地址）。页面只请求：

```text
GET /v1/videos
GET /v1/videos/{id}/content
GET /v2/extension/image
GET /v2/extension/image/{id}/content?index=N
```

不调用 ComfyUI 原生 API、`/view` 或前端 SDK。支持自动刷新（有活跃任务时 2s，否则 5s）、分页、排序、类型/状态/模型筛选、搜索，以及 Models 弹窗（读取 `/v1/models`，不跳转 JSON）。卡片采用增量更新，视频播放不会被轮询刷新打断。

`+ Image` / `+ Video` 弹窗可直接提交任务：图片调用 `POST /v1/images/generations` 并带 `Prefer: respond-async`；视频调用 `POST /v1/videos`。提交成功后弹窗关闭、任务进入列表，由正常轮询跟踪进度。

参考图：选择 Qwen-Image 2.1 时图片表单显示参考图上传（走 `/v1/images/edits`，prompt 用 `<image1>`…）；视频表单提供 `Text only` / `First frame (I2V)` / `Reference images (R2V)`，后两者走 `/v1/videos` multipart，R2V prompt 用 `<Picture N>`。R2V 下若模型声明 `reference_video` capability，还会显示参考视频上传（最多 3 个，prompt 用 `<Video N>`）。

---

## OpenAI 兼容 API

通过 Custom Node 机制注册 OpenAI 风格的 `/v1/*` 端点，使 AI agent 可直接用官方 OpenAI SDK（`base_url="http://host:8188/v1"`）调用。

### GET `/v1/models` — 列出可用模型

模型列表来自 `api/catalog.py` 注册表，可在扩展加载时通过 `models.json` 覆盖或新增（见 README 的「模型配置」章节）。修改配置后需要重启 ComfyUI。

```json
{
  "object": "list",
  "data": [
    {"id": "minimax-h3-t2v", "object": "model", "created": 1785905031, "owned_by": "comfy-rest-ext"},
    {"id": "minimax-h3-i2v", "object": "model", "created": 1785905031, "owned_by": "comfy-rest-ext"}
  ]
}
```

### POST `/v1/videos` — 创建视频生成任务（异步）

接受 **multipart/form-data**（OpenAI SDK 格式）或 **application/json**。

**JSON 请求体（OpenAI 兼容字段 + 扩展）：**
```json
{
  "model": "minimax-h3",
  "prompt": "A red cube rolling across a white floor",
  "seconds": "4",
  "size": "1344x768",
  "seed": 42
}
```

**model 字段语义：**
- 默认/`minimax-h3`：任务类型**从请求内容自动推断**（任意视频参考或多图→R2V、单图→I2V、无图→T2V）
- 旧别名 `minimax-h3-t2v` / `minimax-h3-i2v` / `minimax-h3-r2v`：显式指定任务类型（向后兼容）
- 未知 model：返回 400 `Model mismatch`（对齐 vllm-omni）

**content[] / input[] 数组格式（对齐 OpenAI Responses / MiniMax V2）：**
```json
{
  "model": "minimax-h3",
  "input": [
    {"type": "input_text", "text": "Use <Picture 1> as character reference"},
    {"type": "input_image", "image_url": "https://.../ref1.png", "role": "reference_image"},
    {"type": "input_image", "image_url": "https://.../ref2.png", "role": "reference_image"},
    {"type": "input_video", "video_url": "https://.../ref1.mp4"}
  ],
  "seconds": 5
}
```
- `type: "input_text"`（或 `"text"`）：贡献 prompt 文本，多个会换行拼接
- `type: "input_image"`（或 `"image_url"`）：`image_url` 可为 URL / data URL / ComfyUI input 目录文件名
- `role`：`reference_image`（默认，最多 9 张 → R2V）、`first_frame` / `last_frame`（→ I2V 首尾帧）
- `type: "input_video"`（或 `"video_url"`）：`video_url` 可为 URL / `data:video/...` / ComfyUI input 文件名；最多 3 个 → R2V，prompt 用 `<Video 1>`… 引用。模型必须声明 `reference_video` capability，否则返回 400
- `input` 和 `content` 字段名等价

**multipart 字段（I2V）：**
```bash
curl -X POST http://host:8188/v1/videos \
  -F "model=minimax-h3-i2v" \
  -F "prompt=continue the scene" \
  -F "seconds=3" \
  -F "input_reference=@/path/to/first-frame.png;type=image/png"
```

**multipart 字段（R2V，多参考图）：**
```bash
curl -X POST http://host:8188/v1/videos \
  -F "model=minimax-h3-r2v" \
  -F "prompt=Use <Picture 1> as style reference" \
  -F "seconds=5" \
  -F "input_reference=@/path/to/ref1.png;type=image/png" \
  -F "input_reference=@/path/to/ref2.png;type=image/png"
```
R2V 使用 `ref2va` checkpoint，prompt 中通过 `<Picture 1>`、`<Picture 2>` 标签按上传顺序引用参考图（最多 9 张）。

**multipart 字段（R2V，参考视频）：**
```bash
curl -X POST http://host:8188/v1/videos \
  -F "model=minimax-h3" \
  -F "task=r2v" \
  -F "prompt=Use <Video 1> as motion reference" \
  -F "seconds=2" \
  -F "video=@/path/to/ref.mp4;type=video/mp4"
```
H3 的参考视频上限为 3 个；音频轨会自动作为对应 `<Audio N>` 参考。视频参考（参考图同理）会让任务走 `ref2va` checkpoint。参考视频帧会参与每个采样步，耗时显著高于纯文本/首帧；建议先用 `512x512`、短时长验证。

**响应（OpenAI VideoResource 形状）：**
```json
{
  "id": "video_9dba6e30637a421ba4af779bec3ed86f",
  "object": "video",
  "model": "minimax-h3-t2v",
  "prompt": "...",
  "status": "queued",
  "progress": 0,
  "size": "1344x768",
  "seconds": "4.458333333333333",
  "created_at": 1785908457,
  "completed_at": null,
  "media_type": "video/mp4",
  "file_name": null,
  "speed": {"turbo": "lightx2v-4step-v1.3"},
  "params": {"seconds": 4.458333333333333},
  "acceleration": {
    "steps": 4,
    "sampler": "euler",
    "scheduler": "simple",
    "shift_video": 6.0,
    "shift_audio": 3.0,
    "turbo": {
      "method": "lightx2v-4step-v1.3",
      "params": {"lora_name": "minimax_h3_fl2v_turbo_4step_v1.3_768p_comfyui_bf16.safetensors", "...": "..."}
    },
    "attention": {"method": "none", "params": {}},
    "block_cache": {"method": "none", "params": {}},
    "step_cache": {"method": "none", "params": {}},
    "vae_decode": {"method": "default", "applied": false},
    "precision": {"values": {}, "applied": false}
  },
  "inference_time_s": null,
  "error": null
}
```
`inference_time_s`：任务完成后为 `completed_at - created_at`（秒），否则为 `null`。

`speed` 是原始请求值；`params` 是任务级参数；`acceleration` 是**解析后实际生效的配置**（方法默认值 + 请求覆盖 + 解析出的 LoRA/steps/shift/sampler）。`vae_decode` / `precision` 目前标记为 `applied: false`。图片任务摘要同样返回 `speed` / `params` / `acceleration`。

**加速模式（speed 字段，可选）：**

`speed` 支持 legacy 字符串或结构化对象。legacy 值保持兼容：

- `auto`（默认）：跟随服务器环境变量（`H3_TE_SPEED` / `H3_SOL_STACK`）
- `none`：关闭加速
- `te-speed`：TE-Speed block cache
- `sol-stack`：Sol-Attn + FirstBlockCache

结构化对象按维度选择方法；每个维度可写成字符串，或 `{"method": ..., "params": {...}}`：

```json
{
  "model": "minimax-h3",
  "prompt": "a cube rolling",
  "seconds": 3,
  "speed": {
    "turbo": {"method": "lightx2v-4step-v1.3", "params": {"strength": 1.0}},
    "attention": "sol",
    "step_cache": "none"
  }
}
```

跨层可叠加；同层只能选一个方法。参数会在方法默认值上覆盖；未知参数、错误类型、越界值，以及节点不支持的 combo 值会在排队前返回 400。参数范围与当前安装节点的 `INPUT_TYPES` 对齐。部署级 `H3_TURBO_LORA` / models.json `assets.turbo_lora` 优先于方法默认的 `lora_name`；请求中显式给出的 `lora_name` 也按方法默认值处理，因此同样会被部署级覆盖。

当前已注册方法（默认参数）：

| 维度 | 方法 | 参数 |
|---|---|---|
| `turbo` | `turbo-v4` | `strength=1.0`, `low_vram=false`, `shift_video=12`, `shift_audio=3` |
| | `lightx2v-4step-v1.3` | 4-step FL2V LoRA；默认 `lora_name=minimax_h3_fl2v_turbo_4step_v1.3_768p_comfyui_bf16.safetensors`、`shift_video=6`、`shift_audio=3`、`sampler=euler`、`scheduler=simple`；未显式传 `steps` 时默认 4。该 LoRA 面向 FL2V，I2V/FL2V 效果最好；复杂 T2V 在 4 steps 下可能出现伪影，建议先 A/B |
| `attention` | `sol` | `enabled=true`, `tau=1.0`, `thresh_type="diag"` |
| | `sage` | `sage_attention="auto"` |
| | `flash` | — |
| `block_cache` | `te-speed` | `processing_control_value=0.12`, `processing_percent_1=0.1`, `processing_percent_2=0.9`, `mcs=2`, `device="auto"`, `cache_depth=0.75` |
| | `fbc` | `threshold=0.08`, `start_step=2`, `end_dense_steps=2`, `max_consecutive_skips=2` |
| | `t8` | `residual_diff_threshold=0.12`, `start_percent=0.08`, `end_percent=0.95`, `max_consecutive_hits=2`, `cache_device="cpu"`, `metric_stride=8`, `verbose=false` |
| | `dasiwa` | `reuse_threshold=0.05`, `start_percent=0.15`, `end_percent=0.90`, `max_steps=2`, `device="auto"`, `verbose=false` |
| `step_cache` | `easycache` / `lazycache` | `reuse_threshold=0.2`, `start_percent=0.15`, `end_percent=1.0`, `verbose=false` |
| | `spectrum` | `enabled=true`, `blend_weight=0.50`, `degree=1`, `ridge_lambda=0.10`, `window_size=2.0`, `flex_window=0.75`, `warmup_steps=1`, `tail_actual_steps=1`, `max_history=4`, `debug=false` |

`cachedit` / `teacache` 已注册但当前 H3 builder 未实现，运行时返回 400；`vae_decode` / `precision` 仍只做解析，尚未接入 H3 workflow（P1.5b/P2）。`lightx2v-4step-v1.3` 需要把对应 ComfyUI LoRA 放进 `models/loras/`，否则任务会在资产检查阶段失败。

P1.5b 目前接入的是 LightX2V v1.3。TAEH3 是预览解码器，不作为最终 VAE 替换。`t8` 需要安装 `T8mars/comfyui-minimax-h3-blockcache-T8`（节点 `MiniMaxH3BlockCacheT8`）；其 1.0.4 在 ComfyUI 0.37 上还需要 prefetch-cleanup 签名补丁（部署仓库 `patch/comfyui/`），否则命中步会报 `cleanup_prefetched_modules() missing 1 required positional argument`。`fbc` 在 ComfyUI 0.37 下必须使用带 malloc-graph 修复的 Sol-Attn 节点；未打补丁的节点会在第一步报 `aimdo memory compile error`。FBC 在 20-step、512×512、1s 的实测中，threshold 0.08 不跳步，0.12 约 1.33×/SSIM 0.976，0.15 约 1.65×/SSIM 0.925；4-step LightX2V 基本不命中。T8 同尺寸 seed 21 的 20-step 实测：0.15/gpu 22.0s、SSIM 0.910，0.18/gpu 21.0s、SSIM 0.912，0.20/gpu 20.0s、SSIM 0.880（baseline 28.0s）；默认 0.12 + CPU 反而 37.1s。`dasiwa` 需要 `MiniMaxH3Cache` 节点（完整 DaSiWa 节点包）；同尺寸 seed 31 的 20-step 实测：0.05/auto 23.1s、SSIM 0.898，0.08/auto 16.0s、SSIM 0.896（10/20 skipped），baseline 28.1s。该短 clip 上 FBC 的速度/质量仍最好；DaSiWa 0.08 的速度接近 FBC，但 fidelity 更低。三个 cache 的阈值/设备都必须按模型和分辨率重调。 `dasiwa` 在 H3 >39 帧（1.625s）时直接返回 400：当前节点一旦 cache hit 会把长片段音频压到近静音（1s/参考图正常），这是避免坏视频落盘的保护，待上游修复后再移除。

```bash
curl -X POST http://host:8188/v1/videos \
  -H "Content-Type: application/json" \
  -d '{
    "model": "minimax-h3",
    "prompt": "a cube rolling",
    "seconds": 3,
    "speed": {
      "steps": 8,
      "turbo": {"method": "lightx2v-4step-v1.3", "params": {"strength": 1.0}}
    }
  }'
```

### GET `/v1/videos/{video_id}` — 轮询任务状态

`status`: `queued` | `in_progress` | `completed` | `failed`；`progress`: 0-100

### GET `/v1/videos/{video_id}/content` — 下载生成的 MP4

返回 `video/mp4` 二进制流。

### GET `/v1/videos` — 列出视频任务

只返回 `modality=video` 的任务；图片任务通过 `GET /v2/extension/image` 查询。

### DELETE `/v1/videos/{video_id}` — 删除任务记录

---

## WebSocket 事件（视频生成）

| 事件 | 数据 |
|------|------|
| `extension-video-generation-queued` | `{task_id, task}` |
| `extension-video-generation-complete` | `{task_id, path}` |
| `extension-video-generation-failed` | `{task_id, error}` |
