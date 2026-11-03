# comfy-rest-ext Roadmap

Current release: **v0.7.0**  
Next milestone: **[v0.8.0 — deployment-agnostic server + agent harness](https://github.com/fno2010/comfy-rest-ext/milestone/1)**

## Status

- [x] #47 package boundaries — merged in PR #55 (`5990844`)
- [x] #48 deployment-agnostic adapters/capability discovery
- [x] #49 capability provisioning
- [ ] #50 standalone CLI distribution
- [ ] #51 agent skill / harness
- [ ] #52 clean-environment acceptance


## Product shape

`comfy-rest-ext` is two explicitly separated products with one HTTP contract:

```text
ComfyUI host
  └── server extension (custom node)
        REST API / OpenAPI
          ├── CLI (standalone Python package)
          └── Agent skill (SKILL.md + references + harness scripts)
```

Dependency direction is one-way:

- server must not import CLI or skill code;
- CLI and skill must not import `server`, `nodes`, `folder_paths`, or `api.*`;
- shared contract lives in `contracts/` or is generated; neither side imports the other's implementation.

ComfyUI remains the host runtime. “Deployment-agnostic” means no assumptions about a particular ComfyUI deployment, image, model list, node list, or filesystem layout; it does not mean a ComfyUI-free inference backend.

## Workstreams

### 1. Package boundaries

Split the repository into independently installable server, CLI, and skill subprojects.

Tracked by **#47**: [refactor: split server, CLI, and skills into explicit subprojects](https://github.com/fno2010/comfy-rest-ext/issues/47)

Exit criterion: both Python packages install and test independently; no cross-imports.

### 2. Deployment-agnostic server

Remove the H3-specific core, fixed model list, canonical fallback, fixed frame-grid logic, and installed-node assumptions.

Tracked by **#48**: [feat: deployment-agnostic model and acceleration adapters](https://github.com/fno2010/comfy-rest-ext/issues/48)

Key contracts:

- model adapter: modality, tasks, capabilities, defaults, size policy, frame/duration mapping, workflow build, acceleration methods;
- acceleration adapter: dimensions, parameters, required nodes/imports, compatibility, install metadata;
- capability discovery: ComfyUI node registry/object_info plus Python package checks;
- startup works with an empty capability set.

### 3. Capability provisioning

Missing capabilities become an install/provision workflow, not a generic error or silent fallback.

Tracked by **#49**: [feat: capability discovery and consented auto-provisioning](https://github.com/fno2010/comfy-rest-ext/issues/49)

Flow:

```text
discover -> classify -> authorize -> install -> validate
         -> restart/reconnect if needed -> resume original task
```

Policy is explicit (`ask` / `auto` / `never`), sources are pinned, and install/restart/retry actions are recorded on the task. Fallback is only for optional optimizations and must be marked `fallback_used`.

### 4. Standalone CLI

Make the CLI installable from a clean machine, with persistent multi-server profiles and agent-friendly output.

Tracked by **#50**: [feat: standalone CLI distribution and persistent server config](https://github.com/fno2010/comfy-rest-ext/issues/50)

Exit criteria:

- static single-file CLI per supported OS/arch; no Python/uv/pipx runtime, no repo checkout;
- Python wheel remains an additional path (`uvx` / `pipx`);
- skill can bootstrap the pinned static binary by checksum;
- no ComfyUI imports;
- persistent `~/.config/comfy-rest-ext/config.toml` profiles;
- stable JSON + exit codes, robust wait/reconnect;
- `doctor` and provisioning commands.

### 5. Agent skill / harness

Turn the CLI into an installable skill that handles discovery, generation, task management, long-running plans, recovery, and prompt design.

Tracked by **#51**: [feat: installable agent skill and end-to-end harness](https://github.com/fno2010/comfy-rest-ext/issues/51)

The skill must:

- discover models/capabilities at runtime, never hardcode model or node lists;
- cover image generation/edit, T2V/I2V/R2V, wait/status/cancel, artifacts;
- handle provisioning consent and retry;
- support multi-step plans and resume;
- include prompt/planning references based on H3 and Qwen-Image/Anima harnesses;
- install with `npx skills add`.

### 6. Acceptance and release

Prove the milestone from clean environments, not from the development checkout.

Tracked by **#52**: [test: clean-environment release acceptance](https://github.com/fno2010/comfy-rest-ext/issues/52)

Includes empty deployment startup, authorized provisioning/resume, CLI wheel install, `npx skills add`, image/video end-to-end, and independent release artifacts.

## Order

1. #47 package boundaries
2. #48 adapter/capability discovery
3. #49 provisioning
4. #50 CLI distribution (starts after #47; provisioning commands after #49)
5. #51 skill/harness (prompt references can start early; end-to-end after #50)
6. #52 clean-environment acceptance

Tracking issue: **[#53](https://github.com/fno2010/comfy-rest-ext/issues/53)**

## Deferred / non-goals

- **#16 acceleration profile benchmarking/defaults**: explicitly deferred; not a v0.8.0 blocker.
- New acceleration methods without adapter/support metadata.
- A ComfyUI-free inference backend.
- Silent fallback when a requested capability is missing.

## Baseline already shipped

- #15 config-driven workflow builder loading: closed.
- #43 acceleration framework parity: closed.
- #44 acceleration method support matrix: closed.
- Current tests: 282 server + 74 CLI passing.
