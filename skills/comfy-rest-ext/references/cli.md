# CLI contract

The CLI is a remote HTTP client. Never require the agent to run on the ComfyUI machine.

## Profiles

Config path: `${XDG_CONFIG_HOME:-$HOME/.config}/comfy-rest-ext/config.toml`.

```bash
comfy-rest-ext config list
comfy-rest-ext config set prod base_url https://comfy.example
printf '%s\n' "$TOKEN" | comfy-rest-ext config set prod api_key -
comfy-rest-ext config use prod
comfy-rest-ext config get prod base_url
```

Precedence: explicit flags, then environment, then selected profile, then defaults.

Environment: `COMFY_REST_EXT_PROFILE`, `COMFY_REST_EXT_BASE_URL`,
`COMFY_REST_EXT_API_KEY`, `COMFY_REST_EXT_TIMEOUT`,
`COMFY_REST_EXT_DEFAULT_MODEL`, `COMFY_REST_EXT_CONFIG`.

## Discovery

```bash
comfy-rest-ext --json doctor
comfy-rest-ext --json models
comfy-rest-ext --json models get "$MODEL_ID"
comfy-rest-ext --json capabilities
comfy-rest-ext --json capabilities --model "$MODEL_ID"
comfy-rest-ext --json capabilities --include-unavailable
```

If `models` is empty, use the explicit provisioning inventory before giving up:
the server can still report installable methods with
`include_unavailable=1` (the Go CLI exposes this as `--include-unavailable`).

Model, task, node, and acceleration inventories are runtime state. Do not use a memorized list.

## Images

```bash
comfy-rest-ext --json images generate PROMPT --model MODEL --size WxH --out FILE
comfy-rest-ext --json images edit PROMPT --model MODEL --image FILE --out FILE
comfy-rest-ext --json images list
comfy-rest-ext --json images status TASK_ID
comfy-rest-ext --json images content TASK_ID --index 0 --out FILE
```

## Videos

```bash
comfy-rest-ext --json videos generate PROMPT --model MODEL --seconds N
comfy-rest-ext --json videos generate PROMPT --model MODEL --image FILE
comfy-rest-ext --json videos generate PROMPT --model MODEL \
  --image REF1.png --image REF2.png --video MOTION.mp4
comfy-rest-ext --json videos status TASK_ID
comfy-rest-ext --json videos wait TASK_ID --timeout 600 --interval 5
comfy-rest-ext --json videos content TASK_ID --out FILE
comfy-rest-ext --json videos retry TASK_ID
comfy-rest-ext --json videos delete TASK_ID
```

A single image is sent as a first-frame input. Multiple images or any video
are sent as reference inputs; discovery decides whether the model accepts them.

## Provisioning

```bash
comfy-rest-ext --json capabilities
comfy-rest-ext --json provision CAPABILITY
comfy-rest-ext --json provision CAPABILITY --yes --retry-task TASK_ID
comfy-rest-ext --json restart --yes --retry-task TASK_ID
comfy-rest-ext --json wait-reconnect --since-boot BOOT_ID --retry-task TASK_ID
```

Without `--yes`, a 409 authorization plan is expected. Never bypass it.

## Downloads

```bash
comfy-rest-ext --json downloads list --status all
comfy-rest-ext --json downloads create URL --folder FOLDER --filename NAME
comfy-rest-ext --json downloads status ID
comfy-rest-ext --json downloads cancel ID
```

## Exit codes

| Code | Meaning |
|---:|---|
| 0 | success |
| 1 | operation/task failure |
| 2 | usage or config error |
| 3 | authentication/authorization |
| 4 | not found |
| 5 | network/transient failure |
| 6 | conflict, blocked task, provisioning required |

With `--json`, failures are written to stderr as:

```json
{"ok":false,"error":{"code":"api_error","status":409,"message":"...","exit_code":6}}
```

Do not parse human-readable text when JSON is available.
