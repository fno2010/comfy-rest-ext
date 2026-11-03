# Acceleration method support matrix

This matrix tracks method adapters and runtime prerequisites. It is separate
from the framework contract (#43) and from profile benchmarking (#16).

## Smoke conditions

Unless noted otherwise, each method was exercised with one T2V task on the
reference GB10 / ComfyUI 0.37 stack:

- prompt: `A red cube rolling on a white table, soft studio light, simple clean scene`
- 512x512, 1 second, seed 1, 2 steps (4 steps for spectrum and LightX2V)
- no reference inputs; quality is not judged, only node-chain execution

## Status

| Method | Dimension | Status | Node / prerequisite | Smoke |
|---|---|---|---|---|
| `sage` | attention | supported | `PathchSageAttentionKJ` | completed, 18s |
| `sol` | attention | supported | `SolAttnMiniMaxH3Patcher` | completed, 13s |
| `flash` | attention | unavailable | `PatchFlashAttentionKJ` + one of `flash_attn`, `flash_attn_interface` | failed: ImportError; packages missing |
| `te-speed` | block_cache | supported | `TESpeedMiniMaxH3` | completed, 7s |
| `fbc` | block_cache | supported | `H3FirstBlockCache` + deployment malloc-graph patch | completed, 7s |
| `t8` | block_cache | supported | `MiniMaxH3BlockCacheT8` + deployment prefetch patch | completed, 7s |
| `dasiwa` | block_cache | supported with guard | `MiniMaxH3Cache`; max 39 frames | completed, 7s |
| `easycache` | step_cache | supported | `EasyCache` | completed, 7s |
| `lazycache` | step_cache | supported | `LazyCache` | completed, 7s |
| `spectrum` | step_cache | supported | `SpectrumApplyMiniMaxH3` | completed, 10s (4 steps) |
| `turbo-v4` | turbo | supported | `MiniMaxH3TurboLoRA`; LoRA asset | completed, 8s |
| `lightx2v-4step-v1.3` | turbo | supported | `MiniMaxH3TurboLoRA`; LightX2V LoRA asset | completed, 9.2s (4 steps) |
| `cachedit` | block_cache | not implemented | `Cachedith3` missing | schema `implemented=false` |
| `teacache` | step_cache | not implemented | `MiniMaxH3TeaCache` missing | schema `implemented=false` |
| TAEH3 | vae_decode | deferred | preview decoder, not final VAE | out of scope |
| precision | parse-only | unsupported | H3 weights pre-quantized | request returns 400 |
| `vae_decode` non-default | parse-only | process-scoped | `H3_VAE_BATCHED` before startup | request returns 400 |

## Notes

- `flash` remains registered for future environments, but is hidden as
  unavailable until the Python packages are installed. `check_option_available`
  reports the missing package group.
- FBC/T8 require the deployment-side patches documented in
  `patch/comfyui/`; node presence alone is insufficient.
- DaSiWa's 39-frame guard remains fail-closed because its audio residual
  collapses on longer clips.
- Smoke completion is not a quality or default recommendation. Profile
  selection remains in #16.
