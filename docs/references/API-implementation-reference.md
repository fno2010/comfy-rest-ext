# comfy_cli 实现参考

> 本文档整理 comfy_cli 中各功能模块的代码实现细节，作为实现补充 API 端点的参考指南。

---

## 目录

- [1. 模型下载](#1-模型下载)
- [2. 模型管理增强](#2-模型管理增强)
- [3. 工作流依赖安装](#3-工作流依赖安装)
- [4. 依赖检查](#4-依赖检查)
- [5. 节点打包](#5-节点打包)
- [6. 节点验证](#6-节点验证)
- [7. 快照导入/导出](#7-快照导入导出)
- [8. PR 缓存](#8-pr-缓存)

---

## 1. 模型下载

### 核心文件

| 文件 | 说明 |
|------|------|
| `comfy_cli/command/models/models.py` | CLI 入口和下载逻辑 |
| `comfy_cli/file_utils.py` | `download_file()` HTTP 下载工具 |

### URL 解析

**`check_huggingface_url()`** (`models.py:41-75`)
```python
def check_huggingface_url(url: str) -> tuple[bool, str | None, str | None, str | None, str | None]:
    parsed_url = urlparse(url)
    # netloc 必须是 huggingface.co 或 huggingface.com
    # path 格式: <org>/<repo>/resolve|blob/<branch>/<path>
    # 返回: (是否HF, repo_id, filename, folder_name, branch_name)
```
支持路径格式：
- `/resolve/<branch>/<path>` — 直接下载
- `/blob/<branch>/<path>` — HTML 页面，需提取

**`check_civitai_url()`** (`models.py:78-133`)
```python
def check_civitai_url(url: str) -> tuple[bool, bool, int | None, int | None]:
    # 返回: (是否Civitai模型页, 是否Civitai API URL, model_id, version_id)
```
支持三种 URL 格式：
1. `/api/download/models/<version_id>` — API 下载链接
2. `/api/v1/model-versions/<version_id>` — API 版本信息
3. `/models/<model_id>[?modelVersionId=<id>]` — 网页模型页

### API 调用

**CivitAI Model API** (`models.py:138-181`)
```python
def request_civitai_model_api(model_id, version_id, headers):
    # GET https://civitai.com/api/v1/models/{model_id}
    # 查找 primary=true 的文件
    # 返回: (filename, download_url, model_type, basemodel)
```

**CivitAI Model Version API** (`models.py:138-181`)
```python
def request_civitai_model_version_api(version_id, headers):
    # GET https://civitai.com/api/v1/model-versions/{version_id}
```

### 模型路径映射

```python
model_path_map = {
    "lora": "loras",
    "hypernetwork": "hypernetworks",
    "checkpoint": "checkpoints",
    "textualinversion": "embeddings",
    "controlnet": "controlnet",
}
# 默认路径: models/<type>/<basemodel>/
```

### 文件下载

**`download_file()`** (`file_utils.py:66-92`)
```python
def download_file(url: str, local_filepath: pathlib.Path, headers: dict | None = None):
    local_filepath.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, follow_redirects=True, headers=headers) as response:
        if response.status_code == 200:
            with open(local_filepath, "wb") as f:
                for data in response.iter_bytes():
                    f.write(data)
        else:
            raise DownloadException(...)
```
关键点：使用 `httpx.stream()` 流式下载，支持大文件。

### HuggingFace 下载

```python
# 使用 huggingface_hub 库
from huggingface_hub import hf_hub_download
hf_hub_download(
    repo_id=repo_id,
    filename=filename,
    subfolder=folder_name,
    revision=branch_name,
    token=api_token,
    local_dir=local_path,
    cache_dir=cache_path,
)
```
无 token 时回退到直接 HTTP 下载（`download_file()`）。

### Token 管理

- CivitAI: `CIVITAI_API_TOKEN` 环境变量或 config
- HuggingFace: `HF_API_TOKEN` 环境变量或 config
- 401 检测：`check_unauthorized()` (`file_utils.py:47-63`)

---

## 2. 模型管理增强

### 递归列出模型

当前 `GET /models/{folder}` 仅返回顶层文件名。增强实现需：

```python
import os
from pathlib import Path

def list_models_recursive(folder_path: Path, include_size=False, include_hash=False):
    results = []
    for root, dirs, files in os.walk(folder_path):
        for filename in files:
            filepath = Path(root) / filename
            entry = {"name": filename, "path": filepath.relative_to(folder_path)}
            if include_size:
                entry["size"] = filepath.stat().st_size
            if include_hash:
                # 使用 blake3 计算 hash
                import binascii
                import hashlib
                with open(filepath, "rb") as f:
                    entry["hash"] = "blake3:" + binascii.hexlify(
                        hashlib.blake3(f.read()).digest()
                    ).decode()
            results.append(entry)
    return results
```

blake3 计算参考 ComfyUI 现有实现（`comfy/utils.py` 中有 `blake3` 用法）。

### 删除模型

```python
def delete_model(model_path: Path) -> int:
    """删除模型文件，返回释放字节数"""
    size = model_path.stat().st_size
    model_path.unlink()
    return size
```
保护逻辑：检查模型是否被锁定（如 Manager 的 protected models 列表）。

---

## 3. 工作流依赖安装

### 核心文件

| 文件 | 说明 |
|------|------|
| `comfy_cli/command/custom_nodes/command.py` | `install-deps` 命令入口 |
| `comfy_cli/command/custom_nodes/cm_cli_util.py` | `execute_cm_cli()` 执行封装 |

### 工作流依赖解析流程

```
1. 接收 workflow JSON 或 .png
2. 调用 cm-cli.py deps-in-workflow --workflow <file> --output <tmp.json>
3. 读取 tmp.json 得到依赖列表
4. 调用 cm-cli.py install-deps <tmp.json>
5. 清理临时文件
```

**`execute_cm_cli()`** (`cm_cli_util.py:24-89`)
```python
def execute_cm_cli(cmd: list[str], channel=None, mode=None, ...):
    cm_cli_path = os.path.join(workspace_path, "custom_nodes", "ComfyUI-Manager", "cm-cli.py")
    full_cmd = [sys.executable, cm_cli_path] + cmd
    new_env = os.environ.copy()
    new_env["COMFYUI_PATH"] = workspace_path
    subprocess.run(full_cmd, env=new_env, check=True)
```

关键点：设置 `COMFYUI_PATH` 环境变量，指向 ComfyUI 工作区根目录。

### cm-cli.py 命令参考

cm-cli.py 是 ComfyUI-Manager 的 CLI 封装，支持以下命令：

| 命令 | 功能 |
|------|------|
| `deps-in-workflow --workflow <file> --output <out.json>` | 从 workflow 提取依赖到 JSON |
| `install-deps <deps.json>` | 安装 deps.json 中的依赖 |
| `restore-dependencies` | 从已安装节点恢复依赖 |
| `save-snapshot [--output <path>]` | 保存快照 |
| `restore-snapshot <path>` | 从快照恢复 |
| `simple-show` | 简易节点列表 |
| `node install/uninstall/update/disable/enable/fix` | 节点管理 |

> cm-cli.py 不在本地安装的 venv 中，位于 `custom_nodes/ComfyUI-Manager/cm-cli.py`，由 Manager 自身提供。

### 异步任务设计

ComfyUI Manager 的 `/v2/manager/queue/task` 已是异步队列。实现时：

1. `POST /v2/workflow/dependencies` 接收 workflow JSON
2. 解析出节点类型 → 查询 `object_info` 获取依赖
3. 生成 `deps.json` → 调用 `install_deps` 异步任务
4. 通过 WebSocket 推送进度

### 依赖安装实现

依赖安装通过 `uv pip install` 执行，参考 `uv.py:DependencyCompiler`。

---

## 4. 依赖检查

### `uv.py` — DependencyCompiler

**`__init__()`** (`uv.py:358-405`)
```python
class DependencyCompiler:
    def __init__(self, cwd, executable, gpu, ...):
        self.cwd = Path(cwd)
        self.reqFilesCore = self.find_core_reqs()  # ComfyUI 根目录的 requirements
        self.reqFilesExt = self.find_ext_reqs()    # custom_nodes/*/ 下所有 requirements

    def find_core_reqs(self):
        return DependencyCompiler.Find_Req_Files(self.cwd)

    def find_ext_reqs(self):
        # 遍历 custom_nodes/ 下所有目录
        extDirs = [d for d in (self.cwd / "custom_nodes").iterdir() if d.is_dir()]
        return DependencyCompiler.Find_Req_Files(*extDirs)
```

**`Find_Req_Files()`** (`uv.py:66-106`)
搜索顺序：
1. `requirements.txt`
2. `pyproject.toml`
3. `setup.py`

### 检查依赖状态

```python
def check_dependencies(node_name: str) -> dict:
    """检查节点依赖是否满足"""
    node_path = Path("custom_nodes") / node_name
    req_files = DependencyCompiler.Find_Req_Files(node_path)

    missing = []
    satisfied = []
    for req_file in req_files:
        # 解析 requirements
        with open(req_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                pkg_name = line.split("==")[0].split(">=")[0].split("<=")[0].strip()
                if is_installed(pkg_name):
                    satisfied.append(line)
                else:
                    missing.append(line)
    return {"missing": missing, "satisfied": satisfied, "can_run": len(missing) == 0}
```

### GPU Torch 覆盖

```python
overrideGpu = """
# ensure usage of {gpu} version of pytorch
torch
torchaudio
torchsde
torchvision
"""
# cu126 (NVIDIA), rocm6.1 (AMD), cpu
```

---

## 5. 节点打包

### `zip_files()` — (`file_utils.py:133-227`)

核心逻辑：
```python
def zip_files(zip_filename, includes=None):
    # 1. 加载 .comfyignore
    ignore_spec = _load_comfyignore_spec()  # PathSpec.from_lines("gitwildmatch", ...)

    # 2. 获取 git 追踪文件列表
    git_files = list_git_tracked_files(".")  # git ls-files

    # 3. 遍历并过滤
    for rel_path in git_files:
        if ignore_spec and not _is_force_included(rel_path, include_prefixes):
            if ignore_spec.match_file(rel_path):
                continue  # 跳过 .comfyignore 匹配的文件

        zipf.write(rel_path)
```

**`.comfyignore`** 支持 gitwildmatch 模式（与 `.gitignore` 相同语法）。

**`_load_comfyignore_spec()`** (`file_utils.py:95-107`)
```python
def _load_comfyignore_spec(ignore_filename: str = ".comfyignore") -> PathSpec | None:
    if not os.path.exists(ignore_filename):
        return None
    patterns = [line.strip() for line in ignore_file if line.strip() and not line.lstrip().startswith("#")]
    return PathSpec.from_lines("gitwildmatch", patterns)
```

**`list_git_tracked_files()`** (`file_utils.py:110-119`)
```python
def list_git_tracked_files(base_path: str | os.PathLike = ".") -> list[str]:
    result = subprocess.check_output(
        ["git", "-C", os.fspath(base_path), "ls-files"],
        text=True,
    )
    return [line for line in result.splitlines() if line.strip()]
```

### pyproject.toml 配置读取

```python
from registry.config_parser import extract_node_configuration
config = extract_node_configuration()  # 读取并解析节点的 pyproject.toml
includes = config.tool_comfy.includes if config and config.tool_comfy else []
```

---

## 6. 节点验证

### `ruff` 安全检查 (`command.py:695-727`)

```python
def validate_node_for_publishing():
    cmd = [
        sys.executable, "-m", "ruff", "check", ".",
        "-q",
        "--select", "S102,S307,E702",  # exec, eval, multiline statement
        "--exit-zero"  # 只警告，不失败
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout  # 返回警告列表
```

### 验证结果处理

```python
# 解析 ruff 输出
warnings = []
for line in result.stdout.splitlines():
    # 格式: "nodes/test.py:10:5: S102 Use of exec detected"
    parts = line.split(":")
    warnings.append({
        "file": parts[0],
        "line": int(parts[1]),
        "column": int(parts[2]),
        "code": parts[3].strip(),
        "message": ":".join(parts[4:]).strip(),
    })
```

---

## 7. 快照导入/导出

### comfy_cli 委托模式

```python
# 保存快照
execute_cm_cli(["save-snapshot", "--output", output_path])

# 恢复快照
execute_cm_cli(["restore-snapshot", snapshot_path, extras])
# extras 可选: --pip-non-url, --pip-non-local-url, --pip-local-url

# 恢复依赖
execute_cm_cli(["restore-dependencies"])
```

### 快照文件格式

快照通常为 `.json` 文件，包含：
- 已安装节点列表及版本
- pip 包列表
- 可选：模型文件清单

### ComfyUI Manager 快照存储

Manager 的快照系统位于 `comfyui_manager/glob/snapshot.py`（glob 版本）。

---

## 8. PR 缓存

### `PRCache` 类 (`pr_cache.py:19-235`)

```python
class PRCache:
    def __init__(self, config_path: str):
        self.cache_dir = Path(config_path) / "pr-cache"
        self.max_cache_age = 7 * 24 * 3600  # 7 天
        self.max_items = 10

    def cache_info_path(self, pr_info) -> Path:
        cache_key = f"{pr_info.user}-{pr_info.number}-{pr_info.head_branch}"
        return self.cache_dir / "frontend" / cache_key / ".cache-info.json"

    def is_valid(self, cache_info: dict) -> bool:
        """检查缓存是否有效（未过期）"""
        cached_at = datetime.fromisoformat(cache_info["cached_at"])
        age = (datetime.now() - cached_at).total_seconds()
        return age < self.max_cache_age

    def cleanup(self):
        """清理过期和超量缓存"""
        # 按 cached_at 排序，删除最旧的直到 <= max_items
        # 删除 age > max_cache_age 的
```

### 缓存信息结构

```json
{
    "pr_number": 1234,
    "pr_title": "Add new feature",
    "user": "username",
    "head_branch": "feature-branch",
    "head_repo_url": "https://github.com/...",
    "cached_at": "2024-01-01T00:00:00.000000"
}
```

---

## 附录：关键依赖库

| 库 | 用途 |
|----|------|
| `httpx` | 异步 HTTP 客户端，支持流式下载 |
| `requests` | 简单 HTTP GET（401 检测） |
| `huggingface_hub` | HuggingFace 文件下载 |
| `gitpython` (`git.Repo`) | Git 仓库操作 |
| `pathspec` | `.comfyignore` / `.gitignore` 模式匹配 |
| `uv` | Python 依赖编译和安装（`uv pip compile/install/sync`） |
| `ruff` | Python 安全检查 |
| `zipfile` | 节点打包 |

---

## 附录：关键文件路径索引

| 功能 | comfy_cli 文件 | 关键函数/类 |
|------|--------------|------------|
| 模型下载 URL 解析 | `command/models/models.py` | `check_civitai_url()`, `check_huggingface_url()` |
| 模型下载执行 | `command/models/models.py` | `download()` |
| 文件下载工具 | `file_utils.py` | `download_file()`, `check_unauthorized()` |
| 模型路径映射 | `command/models/models.py` | `model_path_map` |
| 节点管理命令 | `command/custom_nodes/command.py` | `execute_cm_cli()` |
| 工作流依赖安装 | `command/custom_nodes/command.py` | `install_deps()` |
| 依赖编译 | `uv.py` | `DependencyCompiler` |
| uv pip compile | `uv.py` | `DependencyCompiler.Compile()` |
| uv pip install | `uv.py` | `DependencyCompiler.Install()` |
| 节点打包 | `file_utils.py` | `zip_files()`, `list_git_tracked_files()`, `_load_comfyignore_spec()` |
| 节点验证 | `command/custom_nodes/command.py` | `validate_node_for_publishing()` |
| PR 缓存 | `pr_cache.py` | `PRCache` |
| 快照 | `command/custom_nodes/command.py` | `execute_cm_cli(["save-snapshot"])` 等 |
