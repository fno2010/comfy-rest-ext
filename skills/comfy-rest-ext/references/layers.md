# See-through layers

Use this only when the live model inventory reports a `layers` model (normally
`see-through`). The API is not OpenAI Images compatible: it consumes exactly one
image and returns layered artifacts.

Before generating, check the model dependency inventory:

```bash
comfy-rest-ext --json capabilities --model see-through
```

If `model.see-through.node_pack` is `installable`, provision that id, follow
`restart_required`, and re-run discovery. Do not substitute a local filesystem
path in a public workflow.

```bash
comfy-rest-ext --json layers decompose INPUT.png \
  --model see-through --resolution 1024 --steps 20 \
  --resolution-depth -1 --tblr-split --use-lama
```

To skip optional body tags before diffusion (not delete after generation):

```bash
comfy-rest-ext --json layers decompose INPUT.png \
  --model see-through --exclude-tags wings,tail
```

Parameter notes:

- `--resolution`: square processing size; 1280 is the model default, lower values
  are useful for smoke tests.
- `--steps`: LayerDiff diffusion steps; 30 is the quality default, 5 is enough
  to verify the pipeline.
- `--resolution-depth`: depth-map size; `-1` uses the layer resolution.
- `--tblr-split`: split symmetric left/right parts.
- `--use-lama`: LaMa inpainting for hair splitting.
- `--quant-mode`: `none` or `nf4`; defaults to `none`.
- `--group-offload`: lower peak GPU memory, slower.
- `--exclude-tags`: only use when the live model capabilities include
  `exclude_tags`; comma-separated `wings`, `tail`, or `objects`. These tags
  are dropped from the generated layers/manifest/ZIP/PSD. The v3 UNet still
  evaluates its fixed 13-frame body group, so this does not reduce runtime.
  Only use it when the character clearly lacks the part. Do not exclude
  `objects` for a character holding props, and never hide a real wing/tail.

Poll with `layers wait`; then download with `layers content` (ZIP: RGBA PNGs,
depth PNGs, `*_layers.json` manifest) or `layers psd` (server-side PSD). A
completed task also returns `preview_url`, `archive_url`, and `psd_url` for
direct clients.

`wings` is a known false-positive tag on wingless characters. The model always
predicts the fixed v3 body tags; the node only drops fully transparent masks.
Do not use seed/steps as a substitute for exclusion; neither removes the tag
reliably.

If the task already completed and the manifest names an unwanted optional tag,
do not assume it is absent from the input. Check the source/preview and ask the
user; to correct a confirmed false positive, rerun with `--exclude-tags` rather
than editing the ZIP manually. Do not download every layer just to inspect the
manifest; use `GET /v2/extension/layers/{task_id}/layers`.

Do not retry a failed decomposition blindly: inspect `error`; a missing
`SeeThrough_*` node means the external `ComfyUI-See-through` custom node is not
installed, not that the input image is invalid.
