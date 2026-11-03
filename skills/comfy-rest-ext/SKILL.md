---
name: comfy-rest-ext
description: Operate a remote ComfyUI deployment through the comfy-rest-ext CLI for image generation/editing, text/image/reference video generation, task tracking, recovery, and capability provisioning. Use this skill whenever the user asks to make an image or video with ComfyUI, inspect or retry a generation task, install a missing acceleration/plugin capability, or build a multi-step media plan against a ComfyUI endpoint—even if they do not name this skill or know the server's model and node inventory.
---

# Comfy-REST-Ext

Use the installed `comfy-rest-ext` client as the only transport to the server. Do not reimplement HTTP calls in ad-hoc scripts, and do not import ComfyUI or this repository's server code.

## Resolve the client

1. Prefer an executable already on `PATH`:
   ```bash
   command -v comfy-rest-ext
   comfy-rest-ext --version
   ```
2. If it is absent, run the bundled bootstrap:
   ```bash
   ./scripts/bootstrap-cli.sh
   ```
   It downloads a pinned static binary plus checksum into the user cache. Never assume Python, uv, or pipx is installed on the agent host.
3. If bootstrap cannot reach the release (for example, offline or blocked GitHub access), stop and give the user the exact failing command and URL. Do not silently switch to a different transport.

Configure the server once; never hardcode localhost:

```bash
comfy-rest-ext config set prod base_url https://comfy.example
comfy-rest-ext config set prod api_key -   # token from stdin, not echoed
comfy-rest-ext config use prod
comfy-rest-ext --json doctor
```

Environment overrides remain available: `COMFY_REST_EXT_PROFILE`, `COMFY_REST_EXT_BASE_URL`, `COMFY_REST_EXT_API_KEY`, `COMFY_REST_EXT_TIMEOUT`.

## Discover before planning

Always run discovery before choosing a model or acceleration method:

```bash
comfy-rest-ext --json doctor
comfy-rest-ext --json models
comfy-rest-ext --json capabilities
```

This inventory is the source of truth. Do not assume a model id, task type, required node, or acceleration method. Read [references/cli.md](references/cli.md) for command and exit-code details.

Use discovery to decide:

- modality (`image` or `video`) and supported task;
- whether reference images/videos are accepted;
- available acceleration methods and current provisioning state;
- whether the requested capability is available, installable, or unsupported.

## Generate

### Images

```bash
comfy-rest-ext --json images generate "a red fox in fresh snow" \
  --model "$IMAGE_MODEL" --size 1024x1024 --out fox.png
comfy-rest-ext --json images edit "replace <image1> background with snow" \
  --model "$IMAGE_MODEL" --image input.png --out edited.png
```

Use a model discovered from `models`, not a remembered default. For edit requests, mention `<image1>` explicitly when the model follows that convention.

### Videos

Text-only, first-frame, and reference workflows use the same endpoint:

```bash
comfy-rest-ext --json videos generate "a slow dolly shot through a neon alley" \
  --model "$VIDEO_MODEL" --seconds 5
comfy-rest-ext --json videos generate "animate this still" \
  --model "$VIDEO_MODEL" --image first.png --seconds 3
comfy-rest-ext --json videos generate "use <Picture 1> as style; <Video 1> as motion" \
  --model "$VIDEO_MODEL" --image style.png --video motion.mp4
```

Discovery tells you whether the selected model supports I2V or reference-image/reference-video input. See [references/video-prompts.md](references/video-prompts.md) for prompt structure and [references/image-prompts.md](references/image-prompts.md) for image/edit guidance.

## Track, poll, and recover

Generation is asynchronous for videos. Preserve the task id returned by the CLI:

```bash
comfy-rest-ext --json videos status "$TASK_ID"
comfy-rest-ext --json videos wait "$TASK_ID" --timeout 600
comfy-rest-ext --json videos content "$TASK_ID" --out result.mp4
```

For a failed task, inspect `status`, `error`, and `provisioning_actions`. Do not retry blindly:

- transient/network failure: retry with backoff;
- `blocked_on_provisioning`: follow the provisioning flow below;
- incompatible model/node: report the exact missing capability;
- completed task: fetch the artifact and preserve its task id.

Image tasks are synchronous through `images generate`; use `images list/status/content` for tasks created through the asynchronous endpoint.

## Provision missing capabilities

`capabilities` returns `available`, `installable`, `authorization_required`, or `unsupported`. Never install silently.

```bash
comfy-rest-ext --json provision "$CAPABILITY_ID" --retry-task "$TASK_ID"
# ask for explicit consent before adding --yes
comfy-rest-ext --json restart --yes --retry-task "$TASK_ID"
comfy-rest-ext --json wait-reconnect --since-boot "$OLD_BOOT_ID" --retry-task "$TASK_ID"
```

The install response may say `restart_required`. If so:

1. capture the current `boot_id` from `doctor`/health output;
2. restart with explicit authorization;
3. wait for a different `boot_id`;
4. retry the blocked task only after reconnect.

If the server reports no automatic restart adapter, give the user the exact manual restart action; do not claim the task resumed.

## Long-horizon work

For multi-step work, maintain a small manifest outside the server: requested artifact, model/profile, prompt, references, task id, status, and next action. Read [references/planning.md](references/planning.md) for the manifest and recovery rules.

Do not hold an interactive agent session open for a long render. Return the task id and a resumable command; poll only when the user asks or when the current step requires the artifact.

## Output rules

- Return task ids, status, model id, and artifact paths. Do not dump large base64 payloads into the conversation.
- Save generated media to files and report paths.
- Preserve the server profile name and endpoint in multi-step plans.
- Never hide a fallback: if an acceleration method or model was substituted, say so and include why.
- Use `--json` for machine decisions; use human-readable output only for the user-facing summary.
- Respect exit codes: `0` success, `1` operation failure, `2` usage/config, `3` auth, `4` not found, `5` network/transient, `6` conflict/provisioning.

## References

- [CLI contract](references/cli.md) — commands, profiles, exit codes, task lifecycle.
- [Video prompt guide](references/video-prompts.md) — H3-style timeline/reference/audio prompting.
- [Image prompt guide](references/image-prompts.md) — Qwen-Image/Anima generation and edit patterns.
- [Planning and recovery](references/planning.md) — manifest, pause/resume, retries.

Keep these references generic. Resolve models, nodes, and methods from the live server, not from this skill.
