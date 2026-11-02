# Model adapter contract

`api.catalog.ModelSpec` is the server-side adapter contract. The generic task
runner must not contain model-specific frame grids, task labels, node names, or
canonical fallbacks.

## ModelSpec fields

| Field | Responsibility |
|---|---|
| `id`, `aliases`, `alias_tasks` | Registry identity and request task aliases |
| `modality`, `tasks` | Capabilities exposed by `/v1/models` |
| `build(task)` | Build a ComfyUI API-format workflow |
| `defaults`, `assets`, `variant` | Model defaults and checkpoint/LoRA selection |
| `capabilities` | Feature flags such as `reference_video` |
| `accel_methods` | Acceleration allowlist (`"*"` or method ids) |
| `length_for_seconds(seconds)` | Model-specific duration → frame count |
| `infer_task_type(fields)` | Optional request-content → task mapping |
| `default_task` | Fallback task when no explicit task/alias/inference applies |
| `size`, `max_n`, `output_mime`, `timeout_s` | Request policy and output metadata |

If `length_for_seconds` is omitted, the core uses a plain 24fps estimate. If
`infer_task_type` is omitted, the model must provide a `default_task` or be
addressed with an explicit task.

## Registration

Models are registered in `api.catalog` or supplied through `models.json`.
`builder` may name a built-in builder or a `module:function` descriptor; the
descriptor receives a generic task and returns a `BuildResult`. New models do
not require changing `api/openai/v1.py` or `api/tasks/video_task.py`.

`COMFY_REST_DEFAULT_MODEL` selects the default model; otherwise the first
registered video model, then the first registered model, is used. An empty
registry has no default and the API returns a clear error instead of choosing
H3.

## Capability discovery

Acceleration methods declare required ComfyUI nodes and Python package groups.
The schema reports `available`, `missing_nodes`, and `missing_packages`;
requests fail closed with a structured error. Provisioning is tracked separately
in #49.
