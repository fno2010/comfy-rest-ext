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

硬约束：**新 skill 的 `cli-version` pin 出现在 `main` 之前，对应 CLI assets 必须已经发布并可下载。** 下面两条路径都满足这个约束；不能先 merge 到 `main` 再补 tag。

### A. PR release（默认，推荐）

1. PR 基于最新 `main`，包含本次 release 的全部改动、`VERSION` bump 和 skill `cli-version`。完成 review，之后不再改代码。
2. 跑完整测试、bootstrap 合约测试和版本检查：
   ```bash
   /path/to/comfyui/venv/bin/python -m pytest -q
   (cd cli-go && go test ./...)
   ./scripts/test-bootstrap-cli.sh
   ./scripts/check-version.sh
   ```
   并在干净环境执行 `npx skills add` + skill smoke。
3. 在 **PR head（尚未 merge）** 创建 annotated tag，并只推送 tag：
   ```bash
   git switch <pr-branch>
   git pull --ff-only
   git tag -a vX.Y.Z -m "vX.Y.Z: <one-line summary>"
   git push origin vX.Y.Z
   ```
4. 等 `cli-static.yml` 完成，确认 GitHub Release 中 CLI assets、`.sha256`、server/skill 包都存在，且 `comfy-rest-ext-<os>-<arch> --version` 为 `X.Y.Z`。
5. 资产确认后，使用 **merge commit** 合并 PR（禁止 squash/rebase；tag 指向的 commit 必须成为 `main` 的祖先）：
   ```bash
   gh pr merge <number> --merge
   # 或 GitHub UI: Create a merge commit
   ```
6. 验证 tag 已进入 main 历史：
   ```bash
   git fetch origin
   git merge-base --is-ancestor vX.Y.Z origin/main
   git log --oneline --decorate -3 origin/main
   ```
7. 编辑/确认 Release notes，至少包含 Highlights、关键修复、Verification、Known issues、关联 issue/PR。

### B. Direct main release（不使用 PR 时）

1. `main` clean 且与 `origin/main` 一致；完成 review 和测试。
2. 执行 `./scripts/bump-version.sh X.Y.Z`，提交全部 release 改动，但**先不要 push `main`**。
3. 创建 annotated tag，只推送 tag：
   ```bash
   git tag -a vX.Y.Z -m "vX.Y.Z: <one-line summary>"
   git push origin vX.Y.Z
   ```
4. 等 CLI assets 发布并验证。
5. 最后再推送 `main`：
   ```bash
   git push origin main
   ```

无论哪条路径，tag 一旦推送就不移动、不复用；需要修复时发布新的 PATCH。若仓库强制 squash-only merge，则不能把 `cli-version` 放在待 squash 的 PR 中，应先走 direct-main 等价流程，或在 release 后单独提交 pin；不要让 tag 指向不会被 merge 保留的 commit。

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
