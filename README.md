# Comfy-REST-Ext

ComfyUI REST API 扩展，通过 Custom Node 机制向 ComfyUI 补充缺失的 REST API 端点。

## 功能

- **模型下载** — 从 CivitAI、HuggingFace、直链下载模型到本地
- **模型管理** — 递归列出模型、获取元数据、删除模型
- **工作流依赖安装** — 从 workflow JSON 解析依赖并自动安装
- **依赖检查** — 检查节点依赖是否满足
- **快照管理** — 快照导入/导出/对比
- **节点打包** — 将节点打包为 zip
- **节点验证** — ruff 安全检查
- **前端 PR 缓存** — 管理前端 PR 构建缓存

## 安装

将本目录软链接到 ComfyUI 的 `custom_nodes` 目录：

```bash
ln -s /path/to/comfy-rest-ext $COMFYUI/custom_nodes/comfy-rest-ext
```

## 快速验证

启动 ComfyUI 后，访问健康检查端点：

```bash
curl http://127.0.0.1:8188/v2/extension/health
# {"status": "ok", "extension": "comfy-rest-ext"}
```

## API 文档

### 模型下载 `/v2/extension/model/download`

#### 创建下载任务

```bash
POST /v2/extension/model/download
Content-Type: application/json

{
  "url": "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors",
  "folder": "checkpoints",
  "filename": "sd_xl.safetensors"  // 可选
}
```

**支持的 URL 类型：**
- **HuggingFace**: `https://huggingface.co/{org}/{repo}/resolve/{branch}/{filename}`
- **CivitAI**: `https://civitai.com/models/{id}` 或 `https://civitai.com/models/{id}?modelVersion={version_id}`
- **直链**: 任何 HTTP/HTTPS 下载链接

**响应：**
```json
{
  "task_id": "4353b883-88d0-401c-a66a-0fab1437ca28",
  "status": "queued",
  "url": "https://...",
  "folder": "checkpoints",
  "filename": "sd_xl.safetensors"
}
```

#### 查询下载状态

```bash
GET /v2/extension/model/download/{task_id}
```

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

**状态值：** `queued` | `downloading` | `completed` | `failed` | `cancelled`

#### 取消下载任务

```bash
DELETE /v2/extension/model/download/{task_id}
```

#### 列出所有下载任务

```bash
GET /v2/extension/model/download
```

---

### 模型管理 `/v2/extension/models`

#### 列出所有模型

```bash
GET /v2/extension/models/all?include_hash=false&folder=checkpoints
```

**参数：**
- `include_hash` (bool): 是否计算文件 hash（默认 false）
- `folder` (string): 筛选特定文件夹类型

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
      "metadata": {"__metadata__": {"model_version": "2.3.0", ...}}
    }
  ],
  "total": 2,
  "folders": ["checkpoints", "vae", "loras"]
}
```

#### 获取模型元数据

```bash
GET /v2/extension/models/info?path=LTX-Video/ltx-2.3-22b-dev.safetensors
```

**响应：**
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

#### 删除模型

```bash
DELETE /v2/extension/models/{path}?force=false
```

**参数：**
- `force` (bool): 强制删除（跳过保护检查）

---

### 自定义节点 `/v2/extension/nodes`

#### 列出所有节点

```bash
GET /v2/extension/nodes/list
```

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

#### 打包节点

```bash
POST /v2/extension/nodes/pack
Content-Type: application/json

{
  "node_name": "comfyui-manager",
  "respect_comfyignore": true
}
```

#### 验证节点

```bash
POST /v2/extension/nodes/validate
Content-Type: application/json

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
  "warnings": [],
  "files_checked": 42
}
```

#### 初始化节点项目

```bash
POST /v2/extension/nodes/init
Content-Type: application/json

{
  "path": "/basedir/custom_nodes/my-custom-node"
}
```

---

### 工作流依赖 `/v2/extension/workflow/dependencies`

#### 检查依赖

```bash
POST /v2/extension/workflow/dependencies/check
Content-Type: application/json

{
  "workflow": {
    "1": {"class_type": "KSampler", ...},
    "2": {"class_type": "CLIPTextEncode", ...}
  }
}
```

**响应：**
```json
{
  "missing": ["some-missing-package"],
  "already_satisfied": ["torch", "numpy"],
  "can_run": false,
  "gpu_type": "cuda"
}
```

#### 安装依赖

```bash
POST /v2/extension/workflow/dependencies
Content-Type: application/json

{
  "workflow": {...},
  "async_install": true
}
```

#### 查询安装状态

```bash
GET /v2/extension/workflow/dependencies/{task_id}
```

---

### 前端 PR 缓存 `/v2/extension/frontend/pr-cache`

#### 列出缓存

```bash
GET /v2/extension/frontend/pr-cache
```

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

#### 删除指定缓存

```bash
DELETE /v2/extension/frontend/pr-cache/{pr_name}
```

#### 清空所有缓存

```bash
DELETE /v2/extension/frontend/pr-cache?confirm=true
```

---

## 项目结构

```
comfy-rest-ext/
├── __init__.py              # comfy_entrypoint 入口
├── api/                     # API 路由实现
│   ├── __init__.py          # 路由注册 + 健康检查
│   ├── extension.py         # ComfyExtension 实现
│   ├── models/              # 按功能域分组的端点
│   │   ├── download.py      # 模型下载
│   │   ├── management.py    # 模型管理
│   │   ├── dependencies.py  # 工作流依赖
│   │   ├── snapshot.py      # 快照管理
│   │   ├── nodes.py         # 节点管理
│   │   └── pr_cache.py      # PR 缓存
│   ├── tasks/               # 异步任务处理
│   │   ├── task_queue.py    # 任务队列
│   │   ├── registry.py      # 任务注册表
│   │   ├── download_task.py # 下载任务实现
│   │   └── deps_task.py    # 依赖任务实现
│   └── schemas/             # Pydantic 请求模型
│       └── requests.py
├── docs/                    # 文档
│   └── references/          # 参考文档
├── tests/                   # 测试
├── pyproject.toml
├── AGENTS.md                # 开发规范
└── PLAN.md                  # 实现计划
```

## 依赖

| 依赖 | 用途 | 状态 |
|------|------|------|
| aiohttp | HTTP 客户端 | ComfyUI 内置 |
| httpx | 异步 HTTP（流式下载） | 已安装 |
| huggingface_hub | HuggingFace 下载 | 已安装 |
| blake3 | 文件 hash | ComfyUI 内置 |
| pathspec | .comfyignore 解析 | 已安装 |

## WebSocket 事件

下载进度通过 WebSocket 推送：

```javascript
// 进度更新
{"event": "extension-model-download-progress", "data": {"task_id": "...", "progress": 0.45}}

// 下载完成
{"event": "extension-model-download-complete", "data": {"task_id": "...", "path": "checkpoints/model.safetensors"}}

// 下载失败
{"event": "extension-model-download-failed", "data": {"task_id": "...", "error": "File not found"}}

// 依赖安装完成
{"event": "extension-deps-install-complete", "data": {"task_id": "...", "installed": [...], "failed": [...]}}
```

## 文档

- [AGENTS.md](AGENTS.md) — 开发规范和编码指南
- [PLAN.md](PLAN.md) — 详细实现计划

## API 文档

- [docs/comfy-rest-ext-api.md](docs/comfy-rest-ext-api.md) — **用户 API 文档**（本扩展提供的所有端点）

## 开发参考

- [docs/references/API.md](docs/references/API.md) — ComfyUI REST API 完整文档
- [docs/references/API-supplement-proposal.md](docs/references/API-supplement-proposal.md) — 新增 API 建议
- [docs/references/API-implementation-reference.md](docs/references/API-implementation-reference.md) — comfy_cli 实现参考
- [docs/references/openapi.yaml](docs/references/openapi.yaml) — OpenAPI 规范
