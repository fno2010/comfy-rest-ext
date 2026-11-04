# Release 与版本规则

本仓库使用 **pre-1.0 SemVer**。版本号以仓库根目录的 `VERSION` 为唯一来源；Git tag、GitHub Release、Python 包、Go CLI 和 skill 的 CLI pin 必须与它一致。

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
- `-rc.N` 是预发布：RC tag 和最终版 tag 不得指向同一个 commit；最终版必须在 RC 验证后重新提交/打 tag。
- tag 一旦发布不移动、不复用；需要修复时发布新的 PATCH。
- skill 的 `cli-version` 表示精确兼容版本，不写 `latest`；升级由 skill 更新驱动。

## Release 流程

1. 确认目标 PR 已合并，`main` clean 且已同步：
   ```bash
   git switch main
   git pull --ff-only
   git status -sb
   ```
2. 跑完整测试、bootstrap 合约测试和版本一致性检查：
   ```bash
   /path/to/comfyui/venv/bin/python -m pytest -q
   (cd cli-go && go test ./...)
   ./scripts/test-bootstrap-cli.sh
   ./scripts/check-version.sh
   ```
   并在干净环境执行 `npx skills add` + skill smoke；升级相关改动必须额外验证 skill pin/CLI asset 的匹配。
3. 用唯一入口同步所有版本字段（`VERSION`、两个 `pyproject.toml`、Go fallback、skill 的 `cli-version`）：
   ```bash
   ./scripts/bump-version.sh X.Y.Z
   ```
4. 提交本次 release 的全部改动（不要只提交版本文件）：
   ```bash
   git add -A
   git commit -m "chore: release vX.Y.Z"
   ```
5. 创建 annotated tag（此时**不要先 push `main`**）：
   ```bash
   git tag -a vX.Y.Z -m "vX.Y.Z: <one-line summary>"
   ```
6. **只推送 tag**，先让 `cli-static.yml` 构建并发布 CLI assets；此时不要推送 `main`：
   ```bash
   git push origin vX.Y.Z
   ```
7. 在 GitHub 创建/确认 Release，release notes 至少包含：
   - Highlights / 新能力
   - 关键修复
   - Verification（测试数、真实设备验证）
   - Known issues
   - 关联 issue / PR
8. 确认 Release 页面、tag、`VERSION` 和所有版本字段一致，并且 CLI asset/checksum 已经上传。`cli-static.yml` 在构建前运行 `scripts/check-version.sh`，并通过 `-ldflags -X main.version=...` 把 `VERSION` 注入 Go 二进制。
9. **资产确认后再推送 `main`**：
   ```bash
   git push origin main
   ```
   这一步才让使用新 `cli-version` pin 的 skill 出现在 `npx skills update` 的源分支上，避免用户先拿到新 skill、却下载不到对应 CLI。

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
| `v0.7.0` | LightX2V 4-step turbo + CLI speed-json |
| `v0.8.0` | deployment-agnostic server, static CLI artifacts, installable skill |
