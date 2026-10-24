# Comfy-REST-Ext

ComfyUI REST API 扩展，通过 Custom Node 机制向 ComfyUI 补充缺失的 REST API 端点。

## 功能

- **OpenAI 兼容视频生成 API** — T2V / I2V / R2V（图像或视频参考）生成、任务持久化、历史查询（MiniMax-H3）
- **OpenAI 兼容图片生成 API** — 同步 `/v1/images/generations`、Qwen-Image 2.1 编辑、Anima/Qwen-Image 2.1 模型目录
- **任务面板** — 单页 Web UI，显示视频/图片任务列表、进度、错误和生成结果，并可直接通过 OpenAI 兼容端点提交图片/视频任务
- **模型下载** — 从 CivitAI、HuggingFace、直链下载模型到本地
- **模型管理** — 递归列出模型、获取元数据、删除模型
- **工作流依赖安装** — 从 workflow JSON 解析依赖并自动安装
- **依赖检查** — 检查节点依赖是否满足
- **快照管理** — 快照导入/导出/对比
- **节点打包** — 将节点打包为 zip
- **节点验证** — ruff 安全检查
- **前端 PR 缓存** — 管理前端 PR 构建缓存
- **CLI 工具** — `comfy-rest-ext-cli` 命令行客户端

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

## 任务面板

启动 ComfyUI 后打开：

```text
http://<host>:8188/v2/extension/ui
```

`/ui` 会 302 到同一页面。面板按需刷新（有活跃任务时 2s，否则 5s；页面隐藏时暂停），展示：

- 视频任务（`GET /v1/videos`）和图片任务（`GET /v2/extension/image`）
- 状态、进度条、当前节点、ETA、耗时、错误
- 视频播放和图片缩略图/大图预览
- 分页、每页数量、按状态/类型/模型筛选、搜索、排序
- Models 弹窗直接读取 `/v1/models`，以卡片展示并支持一键筛选，不跳转 JSON
- `+ Image` / `+ Video` 弹窗直接提交生成任务：图片走 `POST /v1/images/generations` + `Prefer: respond-async`，视频走 `POST /v1/videos`；提交后任务立即进入列表并可跟踪进度
- 参考输入：Qwen-Image 2.1 的 image edit 支持 1–16 张参考图；视频支持 `First frame (I2V)` 单图、`Reference images (R2V)` 多图，以及 H3 的参考视频（`reference_video` capability，最多 3 个，prompt 用 `<Video N>`）。

面板只请求扩展自身和 OpenAI 兼容端点：

```text
GET /v1/videos
GET /v1/videos/{id}/content
GET /v2/extension/image
GET /v2/extension/image/{id}/content?index=N
```

不调用 ComfyUI 的 `/view`、`/prompt`、`/object_info`，也不依赖 ComfyUI 前端 SDK。

## 模型配置（models.json）

内置模型定义在 `api/catalog.py`，可在 ComfyUI 的 system user 目录下放置 `models.json` 覆盖或新增模型，无需改代码：

```text
<system_user_directory("comfy_rest_ext")>/models.json
```

也可用 `COMFY_REST_MODEL_CONFIG=/path/to/models.json` 指定其他路径。配置在扩展加载时读取一次，修改后重启 ComfyUI。默认路径解析为 ComfyUI 用户目录下的 `__comfy_rest_ext/models.json`（例如 `ComfyUI/user/__comfy_rest_ext/models.json`）。

```json
{
  "models": {
    "anima-base": {
      "assets": {
        "unet": "anima-base-v1.0.safetensors",
        "text_encoder": "qwen_3_06b_base.safetensors",
        "vae": "qwen_image_vae.safetensors"
      },
      "defaults": {"steps": 36, "cfg": 4.5}
    },
    "my-anima": {
      "builder": "anima",
      "modality": "image",
      "tasks": ["t2i"],
      "aliases": ["my-anima-alt"],
      "alias_tasks": {"my-anima-alt": "t2i"},
      "defaults": {"steps": 30, "cfg": 4.0},
      "capabilities": ["negative_prompt", "n", "seed"],
      "assets": {
        "unet": "anima-base-v1.0.safetensors",
        "text_encoder": "qwen_3_06b_base.safetensors",
        "vae": "qwen_image_vae.safetensors"
      },
      "size": {
        "default_width": 1024,
        "default_height": 1024,
        "min_width": 512,
        "min_height": 512,
        "max_width": 1536,
        "max_height": 1536,
        "step": 16,
        "max_pixels": 2359296,
        "default_megapixels": 1.0
      },
      "max_n": 8,
      "output_mime": "image/png",
      "timeout_s": 900
    }
  }
}
```

字段说明：

| 字段 | 说明 |
|---|---|
| `builder` | `h3` / `anima` / `qwen_image_21`；新模型必填，覆盖时可省略 |
| `modality` | `video` 或 `image` |
| `tasks` | 如 `["t2va","fl2va","r2v"]` 或 `["t2i","edit"]` |
| `aliases` / `alias_tasks` | 兼容别名及别名对应 task；提供 `aliases` 时会替换旧列表，未显式给出的 `alias_tasks` 会按新 aliases 过滤 |
| `defaults` | 覆盖模型默认参数（图片：steps/cfg/sampler/scheduler/negative_prompt/n/seed/lora_strength，qwen_image_21 另支持 resolution；视频：width/height/seconds/steps/sampler/scheduler/seed/ref_image_size） |
| `assets` | 模型文件名；键随 builder 不同（见下） |
| `size` | 图片模型必需；字段对应 `SizePolicy`，覆盖时与现有策略合并 |
| `capabilities` | 如 `negative_prompt` / `n` / `seed` / `alpha` / `edit` / `reference_video` |
| `variant` | `base` 或 `turbo`；仅图片 builder 使用（`h3` 只接受 `base`），显式设置优先于内置 turbo id 回退，否则 `turbo_unet` / `turbo_lora` 不会被使用 |
| `max_n` / `timeout_s` / `output_mime` / `description` | 请求上限、任务超时、输出 MIME、模型说明 |

校验规则：`builder` 与 `modality` 必须匹配（`h3`→video，`anima`/`qwen_image_21`→image）；`tasks` 必须是该 builder 支持的集合；新图片模型必须提供正数 `defaults.steps` 与 `size`；未知字段、类型错误、别名冲突、空 model id 都会让整份配置被拒绝并回滚。

`assets` 键：

- `h3`: `unet`, `ref2va`, `text_encoder`, `video_vae`, `audio_vae`, `turbo_lora`
- `anima`: `unet`, `turbo_unet`, `text_encoder`, `vae`, `turbo_lora`
- `qwen_image_21`: `unet`, `turbo_unet`, `text_encoder`, `vae`

优先级：**环境变量 > models.json `assets` > 内置默认值**；已有部署的 `H3_*` / `ANIMA_*` / `QWEN_IMAGE_21_*` 环境变量保持不变。`turbo_unet` / `turbo_lora` 只有在模型 `variant: "turbo"`（或内置 turbo id）时才会被使用。视频 `defaults` 同时作用于 `/v1/videos` 与扩展自定义端点 `/v2/extension/video/generate`，请求中显式给出的字段优先。配置错误会回滚到内置注册表并在日志中报告，不会留下半应用的模型列表。

## CLI 工具

`comfy-rest-ext-cli` 同时覆盖 OpenAI 兼容 API 和扩展自定义 API。

**零外部依赖**——仅使用 Python 标准库（`urllib.request`），任何 Python 3.10+ 环境无需安装任何第三方包即可运行：

```bash
# 直接运行（无需安装）
python3 -m cli --help
```

### 命令结构

| Group | Commands |
|---|---|
| `models` | `list` / `get` / `all` / `info` / `delete` |
| `videos` | `generate` / `status` / `list` / `wait` / `content` / `delete` |
| `images` | `generate` / `edit` / `list` / `status` / `content` |
| `downloads` | `create` / `status` / `list` / `cancel` |
| `deps` | `install` / `status` / `check-workflow` / `check-node` / `restore` |
| `snapshot` | `export` / `import` / `diff` / `list` |
| `nodes` | `list` / `pack` / `validate` / `init` |
| `pr-cache` | `list` / `size` / `delete` / `clear --confirm` |
| `health` | 无子命令 |

旧的扁平命令（`generate` / `status` / `list` / `wait` / `image` / `image-edit` / `images` / `image-status` / `download` / `download-status`）保留为别名。`models` 和 `images` 不带子命令时仍然执行列表。

### 示例

```bash
# 健康检查 / 模型
comfy-rest-ext-cli health
comfy-rest-ext-cli models
comfy-rest-ext-cli models get anima-base
comfy-rest-ext-cli models all --folder diffusion_models
comfy-rest-ext-cli models info anima-base-v1.0.safetensors

# 视频（canonical 路径）
comfy-rest-ext-cli videos generate "a cat walking on the moon"
comfy-rest-ext-cli videos generate "make it cinematic" --image ref.png
comfy-rest-ext-cli videos generate "Use <Picture 1> as style" \
  --image ref1.png --image ref2.png
comfy-rest-ext-cli videos generate "Use <Video 1> as motion reference" \
  --video ref.mp4 --multipart
comfy-rest-ext-cli videos status video_xxxx
comfy-rest-ext-cli videos list
comfy-rest-ext-cli videos wait video_xxxx --timeout 300
comfy-rest-ext-cli videos content video_xxxx --out out.mp4
comfy-rest-ext-cli videos delete video_xxxx

# 图片
comfy-rest-ext-cli images generate "a red fox" --model anima-turbo --size 1024x1024
comfy-rest-ext-cli images edit "replace <image1> background with snow" \
  --image input.png --model qwen-image-2.1
comfy-rest-ext-cli images list
comfy-rest-ext-cli images status image_xxxx
comfy-rest-ext-cli images content image_xxxx --index 0 --out out.png

# 模型下载
comfy-rest-ext-cli downloads create https://civitai.com/models/123
comfy-rest-ext-cli downloads list --status all
comfy-rest-ext-cli downloads cancel dl_xxxx

# 依赖 / 快照 / 节点 / PR 缓存
comfy-rest-ext-cli deps check-workflow --workflow workflow.json
comfy-rest-ext-cli deps install --workflow workflow.json
comfy-rest-ext-cli deps check-node --node KSampler
comfy-rest-ext-cli snapshot export my-snapshot --include-models
comfy-rest-ext-cli snapshot list
comfy-rest-ext-cli nodes list
comfy-rest-ext-cli nodes validate comfy-rest-ext
comfy-rest-ext-cli pr-cache size

# 所有命令支持 --json；全局 --timeout 控制单次 HTTP 请求超时
comfy-rest-ext-cli --json videos list
comfy-rest-ext-cli --timeout 120 models all

# 指向非默认 ComfyUI 地址
comfy-rest-ext-cli --base-url http://192.168.1.10:8188 models
```

`videos generate --multipart` 会用 multipart 上传参考图（接线 `create_video_multipart`）；该模式目前只支持 legacy preset/`--speed` 字符串，不支持 composable 对象。`--timeout` 放在子命令前是 HTTP 超时；`videos wait --timeout` 是等待任务完成的时间。

CLI 加速输入有三类：

```bash
# 1. 方法选择 + dot-path 参数
comfy-rest-ext-cli videos generate "a red cube" \
  --block-cache fbc --block-cache.threshold 0.12 --block-cache.start_step 2

# 2. 完整 JSON / 文件
comfy-rest-ext-cli videos generate "a cube" \
  --speed-json '{"turbo":{"method":"lightx2v-4step-v1.3","params":{"strength":1.0}}}'
comfy-rest-ext-cli videos generate "a cube" --speed-file speed.json
comfy-rest-ext-cli videos generate "a cube" --speed-json @speed.json

# 3. schema preset（当前 auto/none/te-speed/sol-stack）
comfy-rest-ext-cli videos generate "a cube" --preset sol-stack
```

dot-path 参数会先按 `/v2/extension/acceleration/schema` 校验维度、方法、类型、范围和 choices；flat flag 不再硬编码 method choices，同样由 schema 校验。新增 **method** 不需要改 CLI；新增 **维度** 仍应新增一个正式 CLI flag（以及后端维度登记）。

`--preset` 也从同一 schema 读取，当前值等价于 legacy speed 字符串，并保留 `auto` 的服务端 env 语义。`--speed-json`/`--speed-file`/`--preset` 不能与其它加速输入混用；`--multipart` 模式下 composable 对象仍不支持。

```bash
# 4-step LightX2V Turbo LoRA（默认 4 步、shift 6/3、euler/simple）
# 面向 FL2V/I2V 效果最好；复杂 T2V 4-step 建议先 A/B
comfy-rest-ext-cli videos generate "a red cube" --turbo lightx2v-4step-v1.3
```

环境变量：`COMFY_REST_EXT_BASE_URL`（默认 `http://127.0.0.1:8188`）、`COMFY_REST_EXT_TIMEOUT`（默认 `60`）。

## 图片生成（OpenAI Images API）

服务端按模型 id 路由到对应的 ComfyUI workflow builder。图片模型：

| model id | 后端 | 任务 | 默认 |
|---|---|---|---|
| `anima-base` | Anima Base v1.0 (2B) | `t2i` | 1024², 30 steps, cfg 4 |
| `anima-turbo` | Anima Turbo v1.0 (2B) | `t2i` | 1024², 8 steps, cfg 1 |
| `qwen-image-2.1` | Qwen-Image 2.1 (7B) | `t2i`, `edit` | 1024², 25 steps, cfg 1 |
| `qwen-image-2.1-turbo` | Qwen-Image 2.1 Turbo | `t2i`, `edit` | 1024², 8-step ManualSigmas |

`qwen-image-2.1` 需要 ComfyUI >= 0.37.0；Anima 在当前 0.30.0 已有架构支持，但仍建议统一升级后验证。

### 同步生成

```bash
curl -X POST http://host:8188/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{
    "model": "anima-base",
    "prompt": "masterpiece, best quality, 1girl, ...",
    "size": "1024x1024",
    "response_format": "b64_json"
  }'
```

响应：

```json
{"created": 1792590000, "model": "anima-base", "size": "1024x1024",
 "data": [{"b64_json": "...", "revised_prompt": "..."}]}
```

扩展字段：`negative_prompt`、`seed`、`steps`、`cfg`、`sampler`、`scheduler`、`aspect_ratio`、`megapixels`、`resolution`（edit）、`n`。`size` 支持 `WIDTHxHEIGHT` 或 `auto`；`response_format` 支持 `b64_json`（默认）和 `url`。

异步提交（立即返回任务记录，不等待生成完成）：

```bash
curl -X POST http://host:8188/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Prefer: respond-async" \
  -d '{"model":"anima-base","prompt":"...","size":"1024x1024"}'
# HTTP 202
# {"id":"image_...","status":"queued",...}
# Location: /v2/extension/image/image_...
```

面板的 `+ Image` 表单使用的就是这条路径。

### 编辑（Qwen-Image 2.1）

```bash
curl -X POST http://host:8188/v1/images/edits \
  -F model=qwen-image-2.1 \
  -F prompt='Keep <image1> unchanged, put the shirt from <image2> on the character' \
  -F image=@portrait.png \
  -F image=@shirt.png
```

`mask` 暂不支持：Qwen-Image 2.1 的编辑通过 prompt + 参考图（`<image1>`…`<imageN>`）完成，和 OpenAI mask 语义不同。

### 异步任务

长任务（2K、`n>1`、编辑）可走异步端点：

```bash
curl -X POST http://host:8188/v2/extension/image/generate \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen-image-2.1","prompt":"...","size":"2048x2048"}'
# {"id":"image_...","status":"queued",...}

curl http://host:8188/v2/extension/image/image_...
```

### CLI

```bash
python3 -m cli image "masterpiece, 1girl, ..." --model anima-base \
  --size 1024x1024 --out cat.png

python3 -m cli image-edit "put <image2> on <image1>" \
  --image portrait.png --image shirt.png --out edited.png

python3 -m cli images                 # list image tasks
python3 -m cli image-status image_... # show one image task
```

`comfy-rest-ext-cli list` 只列出视频任务；图片任务用 `images` / `image-status` 查询（对应 `/v2/extension/image`）。

---

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
- [RELEASING.md](RELEASING.md) — 版本规则与 release 流程

## API 文档

- [docs/comfy-rest-ext-api.md](docs/comfy-rest-ext-api.md) — **用户 API 文档**（本扩展提供的所有端点）

## 开发参考

- [docs/references/API.md](docs/references/API.md) — ComfyUI REST API 完整文档
- [docs/references/API-supplement-proposal.md](docs/references/API-supplement-proposal.md) — 新增 API 建议
- [docs/references/API-implementation-reference.md](docs/references/API-implementation-reference.md) — comfy_cli 实现参考
- [docs/references/openapi.yaml](docs/references/openapi.yaml) — OpenAPI 规范
