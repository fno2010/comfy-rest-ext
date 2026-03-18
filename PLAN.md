# Comfy-REST-Ext 实现计划

> 本文档为 comfy-rest-ext 项目的详细实现计划，按优先级排序。

---

## 概述

本项目通过 ComfyUI Custom Node 机制，向 ComfyUI REST API 补充以下功能：

1. **模型下载**（CivitAI / HuggingFace / 直链）
2. **模型管理增强**（递归列表、元数据、删除）
3. **工作流依赖安装**（异步 + WebSocket 进度）
4. **依赖检查**
5. **快照导入/导出**
6. **节点打包**
7. **前端 PR 缓存管理**

---

## 实现阶段

### 阶段 0：项目骨架

**目标**: 建立项目结构和基础代码

**文件**:
```
comfy-rest-ext/
├── __init__.py              # 导出 comfy_entrypoint
├── api/
│   ├── __init__.py
│   ├── routes.py            # 路由注册入口
│   ├── tasks/
│   │   ├── __init__.py
│   │   ├── task_queue.py    # 异步任务队列基础
│   │   └── registry.py       # 任务注册表
│   └── schemas/
│       ├── __init__.py
│       └── requests.py       # Pydantic 模型
├── docs/
│   └── references/
└── pyproject.toml
```

**验收标准**:
- ComfyUI 启动时加载本扩展无报错
- `curl http://127.0.0.1:8188/v2/extension/health` 返回 `{"status": "ok"}`

---

### 阶段 1：模型下载 (`/v2/extension/model/download`)

**优先级**: P0

**实现文件**:
- `api/models/download.py` — 下载相关端点
- `api/tasks/download_worker.py` — 下载任务处理器

#### 端点

| Method | Path | Handler | 说明 |
|--------|------|---------|------|
| POST | `/v2/extension/model/download` | `create_download_task` | 提交下载任务 |
| GET | `/v2/extension/model/download/{task_id}` | `get_download_status` | 查询任务状态 |
| DELETE | `/v2/extension/model/download/{task_id}` | `cancel_download_task` | 取消任务 |
| GET | `/v2/extension/model/download` | `list_download_tasks` | 列出所有任务 |

#### 实现要点

1. **URL 解析**（参考 `comfy_cli/command/models/models.py:41-133`）
   - `check_civitai_url()`: 检测 CivitAI URL，返回 `(is_model_url, is_api_url, model_id, version_id)`
   - `check_huggingface_url()`: 检测 HuggingFace URL，返回 `(is_hf, repo_id, filename, folder, branch)`

2. **CivitAI API**
   - `GET https://civitai.com/api/v1/models/{model_id}` — 获取模型信息，查找 `primary=true` 的文件
   - `GET https://civitai.com/api/v1/model-versions/{version_id}` — 获取版本详情
   - `Authorization: Bearer {token}` — 带 token 访问

3. **HuggingFace**
   - 使用 `huggingface_hub.hf_hub_download()` — 授权文件下载
   - 无 token 时回退直接 HTTP 下载

4. **HTTP 下载**（参考 `comfy_cli/file_utils.py:66-92`）
   - 使用 `httpx.stream()` 流式下载
   - 支持 `Content-Length` 进度计算
   - 中断时清理文件

5. **任务队列**
   - 内存中 `dict[task_id, TaskInfo]` 管理任务状态
   - 后台 `asyncio.create_task()` 执行下载
   - WebSocket 推送进度: `extension-model-download-progress`

#### 任务状态

```python
@dataclass
class DownloadTask:
    task_id: str
    status: Literal["queued", "downloading", "completed", "failed", "cancelled"]
    url: str
    local_path: str | None
    progress: float  # 0.0 - 1.0
    downloaded_bytes: int
    total_bytes: int | None
    error: str | None
    created_at: float
```

#### WebSocket 事件

```python
# 进度
{"event": "extension-model-download-progress", "data": {"task_id": "...", "progress": 0.45, "speed_bps": 10485760}}

# 完成
{"event": "extension-model-download-complete", "data": {"task_id": "...", "path": "checkpoints/model.safetensors"}}

# 失败
{"event": "extension-model-download-failed", "data": {"task_id": "...", "error": "File not found"}}
```

---

### 阶段 2：模型管理增强

**优先级**: P1

**实现文件**:
- `api/models/management.py`

#### 端点

| Method | Path | Handler | 说明 |
|--------|------|---------|------|
| GET | `/v2/extension/models/all` | `list_all_models` | 递归列出所有模型 |
| GET | `/v2/extension/models/info` | `get_model_info` | 获取模型元数据 |
| DELETE | `/v2/extension/models/{path}` | `delete_model` | 删除模型文件 |

#### 实现要点

1. **递归列出**（参考 `API-implementation-reference.md` 第2节）
   - `os.walk(folder_paths.get_folder_paths()["checkpoints"])` 递归
   - 可选包含 `size`（`os.path.getsize`）、`hash`（blake3）

2. **模型元数据**
   - safetensors: 读取 header 解析元数据
   - ckpt: 读取 pickle 解析
   - 其他格式: 仅返回文件 stat

3. **删除保护**
   - 检查 Manager 的 protected models 列表
   - 不可删除正在被使用的模型

---

### 阶段 3：工作流依赖安装

**优先级**: P0

**实现文件**:
- `api/models/dependencies.py`

#### 端点

| Method | Path | Handler | 说明 |
|--------|------|---------|------|
| POST | `/v2/extension/workflow/dependencies` | `install_workflow_deps` | 提交依赖安装任务 |
| GET | `/v2/extension/workflow/dependencies/{task_id}` | `get_deps_status` | 查询安装状态 |
| POST | `/v2/extension/workflow/dependencies/check` | `check_workflow_deps` | 仅检查依赖 |

#### 实现要点

1. **Workflow 解析**
   - 接收 workflow JSON（与 `/prompt` 的 `prompt` 字段相同格式）
   - 从节点 `class_type` 查找 `nodes.NODE_CLASS_MAPPINGS` 得到模块
   - 从模块 `__init__.py` 或 `requirements.txt` 提取依赖

2. **依赖安装**（参考 `comfy_cli/uv.py`）
   - 使用 `subprocess.run([sys.executable, "-m", "uv", "pip", "install", ...])`
   - GPU 版本选择: 检测 NVIDIA/AMD/CPU
   - 进度通过 WebSocket 推送

3. **检查模式**
   - 仅解析依赖，不执行安装
   - 返回 `missing` / `already_satisfied` / `can_run`

#### 任务状态

```python
@dataclass
class DepsTask:
    task_id: str
    status: Literal["queued", "installing", "completed", "failed"]
    package: str | None  # 当前安装的包
    progress: float
    pip_output: str | None
    installed: list[str]
    failed: list[str]
    restart_required: bool
```

---

### 阶段 4：依赖检查

**优先级**: P2

**实现文件**:
- `api/models/dependencies.py`（与阶段3合并）

#### 端点

| Method | Path | Handler | 说明 |
|--------|------|---------|------|
| GET | `/v2/extension/dependencies/check` | `check_node_deps` | 检查节点依赖状态 |
| POST | `/v2/extension/dependencies/restore` | `restore_node_deps` | 恢复节点依赖（异步） |

#### 实现要点

1. **依赖检查**
   - 读取节点的 `requirements.txt` / `pyproject.toml`
   - 用 `subprocess.run([sys.executable, "-m", "pip", "list"])` 获取已安装包
   - 对比版本是否满足

2. **恢复依赖**
   - 调用 Manager cm-cli: `subprocess.run(["python", cm_cli_path, "restore-dependencies"])`

---

### 阶段 5：快照管理增强

**优先级**: P2

**实现文件**:
- `api/models/snapshot.py`

#### 端点

| Method | Path | Handler | 说明 |
|--------|------|---------|------|
| POST | `/v2/extension/snapshot/export` | `export_snapshot` | 导出快照到文件 |
| POST | `/v2/extension/snapshot/import` | `import_snapshot` | 从文件导入快照 |
| GET | `/v2/extension/snapshot/diff` | `diff_snapshots` | 对比快照差异 |

#### 实现要点

1. **导出** — 委托 Manager cm-cli: `save-snapshot --output <path>`
2. **导入** — 委托 Manager cm-cli: `restore-snapshot <path>`
3. **对比** — 读取两个快照 JSON，对比 `custom_nodes` 和 `pip_packages` 差异

---

### 阶段 6：节点打包

**优先级**: P2

**实现文件**:
- `api/models/nodes.py`

#### 端点

| Method | Path | Handler | 说明 |
|--------|------|---------|------|
| POST | `/v2/extension/nodes/pack` | `pack_node` | 打包节点为 zip |
| POST | `/v2/extension/nodes/validate` | `validate_node` | 验证节点 |
| POST | `/v2/extension/nodes/init` | `init_node` | 初始化节点项目 |

#### 实现要点

1. **打包**（参考 `comfy_cli/file_utils.py:133-227`）
   - `subprocess.run(["git", "-C", node_path, "ls-files"])` 获取追踪文件
   - 读取 `.comfyignore` 过滤
   - `zipfile.ZipFile()` 创建 zip

2. **验证**
   - `subprocess.run([sys.executable, "-m", "ruff", "check", ".", "--select", "S102,S307,E702"])`
   - 解析输出返回警告列表

3. **初始化**
   - 创建 `pyproject.toml` 骨架
   - 从 git remote 提取 URL

---

### 阶段 7：前端 PR 缓存

**优先级**: P3

**实现文件**:
- `api/models/pr_cache.py`

#### 端点

| Method | Path | Handler | 说明 |
|--------|------|---------|------|
| GET | `/v2/extension/frontend/pr-cache` | `list_pr_cache` | 列出 PR 缓存 |
| DELETE | `/v2/extension/frontend/pr-cache/{pr}` | `delete_pr_cache_item` | 删除指定 PR 缓存 |
| DELETE | `/v2/extension/frontend/pr-cache` | `clear_pr_cache` | 清空所有 PR 缓存 |

#### 实现要点

缓存目录: `~/.config/comfy-cli/pr-cache/frontend/{user}-{pr_number}-{branch}/`

参考 `comfy_cli/pr_cache.py:19-235` 的 `PRCache` 类实现。

---

## 文件清单

```
comfy-rest-ext/
├── __init__.py                          # comfy_entrypoint
├── api/
│   ├── __init__.py
│   ├── routes.py                        # 路由注册
│   ├── models/
│   │   ├── __init__.py
│   │   ├── download.py                  # 阶段1
│   │   ├── management.py                 # 阶段2
│   │   ├── dependencies.py              # 阶段3,4
│   │   ├── snapshot.py                   # 阶段5
│   │   └── nodes.py                     # 阶段6
│   ├── tasks/
│   │   ├── __init__.py
│   │   ├── task_queue.py                # 基础任务队列
│   │   ├── download_task.py             # 阶段1
│   │   └── deps_task.py                 # 阶段3
│   └── schemas/
│       ├── __init__.py
│       └── requests.py                  # Pydantic 模型
├── docs/
│   └── references/
│       ├── API.md
│       ├── API-supplement-proposal.md
│       ├── API-implementation-reference.md
│       └── openapi.yaml
├── pyproject.toml
├── README.md
└── AGENTS.md
```

---

## 依赖清单

| 依赖 | 用途 | 来源 |
|------|------|------|
| aiohttp | HTTP 客户端（已内置） | ComfyUI 内置 |
| httpx | 异步 HTTP（流式下载） | 新增 |
| huggingface_hub | HuggingFace 下载 | 新增（可选） |
| blake3 | 文件 hash | 已内置（ComfyUI） |
| pathspec | .comfyignore 解析 | 新增 |

---

## 测试计划

### 单元测试

每个模块独立的 `test_*.py`：

- `tests/test_url_parsing.py` — CivitAI/HuggingFace URL 解析
- `tests/test_task_queue.py` — 任务队列基本操作
- `tests/test_model_management.py` — 模型列表/删除

### 集成测试

- 启动本地 ComfyUI（测试模式），加载本扩展
- `curl` 测试各端点响应
- WebSocket 连接测试

### 测试文件结构

```
comfy-rest-ext/
└── tests/
    ├── __init__.py
    ├── test_url_parsing.py
    ├── test_download.py
    ├── test_model_management.py
    ├── test_dependencies.py
    └── conftest.py  # pytest fixtures
```

---

## 实现顺序总结

| 阶段 | 内容 | 优先级 | 状态 |
|------|------|--------|------|
| 0 | 项目骨架 + 健康检查 | — | 待实现 |
| 1 | 模型下载 | P0 | 待实现 |
| 2 | 模型管理增强 | P1 | 待实现 |
| 3 | 工作流依赖安装 | P0 | 待实现 |
| 4 | 依赖检查 | P2 | 待实现 |
| 5 | 快照导入/导出 | P2 | 待实现 |
| 6 | 节点打包/验证 | P2 | 待实现 |
| 7 | 前端 PR 缓存 | P3 | 待实现 |
