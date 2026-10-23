# Release 与版本规则

本仓库使用 **pre-1.0 SemVer**。版本号以 `pyproject.toml` 为唯一来源；Git tag 和 GitHub Release 必须与它一致。

## 版本号

格式：`MAJOR.MINOR.PATCH`（当前 `MAJOR=0`）。

| 变更类型 | 版本动作 | 说明 |
|---|---|---|
| 新功能、行为变化、0.x 阶段的 breaking change | `0.MINOR.0` | 一个完整的 feature 批次 |
| 已发布版本上的向后兼容 bugfix | `0.MINOR.PATCH` | 不改变公共 API/CLI 语义 |
| 纯文档、测试、内部重构，不准备发版 | 不 bump | 跟随下一个 feature release |
| 公共 API/CLI 契约稳定、可以承诺兼容性 | `1.0.0` | 之后按常规 SemVer |
| 预发布 | `0.MINOR.0-rc.N` | 可选 |

补充规则：

- **多个 PR 可以共用一个 MINOR release**，按完整 feature 边界发版，不要求每个 PR 单独发版。
- 同一个 release 内的小修复直接并入该 feature bump，不单独打 PATCH。
- PATCH 只用于已经发布后的兼容性修复，不用于尚未发布的工作区改动。
- `MAJOR=0` 期间允许 breaking change，但必须在 release notes 中明确标注。
- 不因为“提交数量”或“时间间隔”自动 bump；以用户可见能力是否变化为准。

## Release 流程

1. 确认目标 PR 已合并，`main` clean 且已同步：
   ```bash
   git switch main
   git pull --ff-only
   git status -sb
   ```
2. 跑完整测试：
   ```bash
   /path/to/comfyui/venv/bin/python -m pytest -q
   ```
3. 修改 `pyproject.toml` 的 `version`。
4. 只提交版本文件：
   ```bash
   git add pyproject.toml
   git commit -m "chore: release vX.Y.Z"
   ```
5. 创建 annotated tag：
   ```bash
   git tag -a vX.Y.Z -m "vX.Y.Z: <one-line summary>"
   ```
6. 推送 `main` 和 tag（网络不稳时持续重试）：
   ```bash
   git push origin main vX.Y.Z
   ```
7. 创建 GitHub Release，release notes 至少包含：
   - Highlights / 新能力
   - 关键修复
   - Verification（测试数、真实设备验证）
   - Known issues
   - 关联 issue / PR
8. 确认 Release 页面、tag 和 `pyproject.toml` 三者一致。

## Release notes

- 仓库不维护单独的 `CHANGELOG.md`；GitHub Release notes 就是 changelog。
- 每个 release 必须写清楚 **验证环境** 和 **已知问题**，不能只列文件改动。
- 涉及性能/质量结论时，标明测量环境、样本数和是否为单次测量。

## 历史

| Tag | 内容 |
|---|---|
| `v0.2.0` | 多模态核心 + Dashboard |
| `v0.3.0` | Dashboard 参考图 |
| `v0.4.0` | models.json registry + CLI 全覆盖 |
| `v0.5.0` | reference-video 端到端 |
| `v0.6.0` | acceleration method registry |
