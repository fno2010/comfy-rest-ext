# Planning and recovery

Use a small external manifest for multi-step or long-running work. The manifest
belongs to the caller; the server stores task state, not the user's plan.

```json
{
  "profile": "prod",
  "steps": [
    {
      "id": "hero-image",
      "kind": "image",
      "model": "from-runtime-discovery",
      "prompt": "...",
      "status": "planned",
      "task_id": null,
      "artifact": null
    },
    {
      "id": "hero-video",
      "kind": "video",
      "depends_on": ["hero-image"],
      "status": "planned",
      "task_id": null,
      "artifact": null
    }
  ]
}
```

Rules:

- Run `doctor`/`models`/`capabilities` before fixing a plan.
- Persist the task id immediately after submission.
- Mark a step `waiting` while a task is queued/running; do not busy-loop.
- On reconnect, compare `boot_id` and re-query the task before retrying.
- On `blocked_on_provisioning`, preserve the task id and run the provision flow;
  do not create a duplicate task.
- On failure, record the error and decide: prompt/seed change, reference change,
  provisioning, restart, or model switch.
- At completion, save the artifact path and MIME type. Never store large base64
  blobs in the manifest.

Suggested states: `planned`, `submitted`, `waiting`, `completed`, `failed`,
`blocked`, `cancelled`.
