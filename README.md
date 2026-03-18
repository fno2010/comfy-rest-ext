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

## 项目结构

```
comfy-rest-ext/
├── __init__.py          # comfy_entrypoint 入口
├── api/                  # API 路由
│   ├── routes.py         # 路由注册
│   ├── extension.py      # ComfyExtension 实现
│   ├── models/           # 按功能域分组
│   ├── tasks/            # 异步任务处理
│   └── schemas/          # Pydantic 请求模型
├── docs/                 # 文档
│   └── references/       # 参考文档
├── tests/                # 测试
├── pyproject.toml
├── AGENTS.md             # 开发规范
└── PLAN.md               # 实现计划
```

## 文档

- [AGENTS.md](AGENTS.md) — 开发规范和编码指南
- [PLAN.md](PLAN.md) — 详细实现计划

## 参考文档

- [docs/references/API.md](docs/references/API.md) — ComfyUI REST API 完整文档
- [docs/references/API-supplement-proposal.md](docs/references/API-supplement-proposal.md) — 新增 API 建议
- [docs/references/API-implementation-reference.md](docs/references/API-implementation-reference.md) — comfy_cli 实现参考
- [docs/references/openapi.yaml](docs/references/openapi.yaml) — OpenAPI 规范
