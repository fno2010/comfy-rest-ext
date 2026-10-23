# H3 cache 质量矩阵（GB10）

日期：2026-10-23  
硬件：NVIDIA GB10 / DGX Spark，128 GB unified memory  
运行时：ComfyUI 0.37.0，MiniMax-H3 NVFP4，`res_multistep` / `beta`，20 steps，512×512  
参考：同 prompt、同 seed、同采样参数的 **无 cache** 输出

Cache 方法：

- FBC：Sol-Attn `H3FirstBlockCache`，threshold 0.15，需部署侧 malloc-graph 补丁。
- T8：`MiniMaxH3BlockCacheT8`，residual_diff_threshold 0.18，`cache_device=gpu`。
- DaSiWa：`MiniMaxH3Cache`，reuse_threshold 0.08，`device=auto`。

指标：

- `SSIM`：整段视频对 baseline，1.0 为完全一致。
- `SI-SDR`：整段音轨对 baseline，scale-invariant；越高越接近，负值表示误差大于信号。
- `RMS`：音轨整体电平，用来发现静音/增益异常。
- 计时包含 ComfyUI 任务执行，不含 HTTP 排队；同一 boot 内比较，boot 间有波动。

Prompt：`A glass marble rolls across a wooden table, camera tracks it, soft ambient room tone`

## 2 秒请求（实际 2.33s，seed 41）

| 配置 | 耗时 | 跳步 | 视频 SSIM | 音频 RMS | 音频 SI-SDR |
|---|---:|---:|---:|---:|---:|
| baseline | 43.1s | 0/20 | 1.000 | -38.1 dB | ref |
| FBC 0.15 | 24.2s | 10/20 | 0.902 | -32.2 dB | -2.20 dB |
| T8 0.18 / gpu | 32.1s | 6/20 | 0.926 | -29.3 dB | -4.18 dB |
| DaSiWa 0.08 / auto | 25.1s | n/a | 0.896 | **-95.2 dB** | **-45.72 dB** |

## 4 秒请求（实际 4.46s，seed 42）

| 配置 | 耗时 | 跳步 | 视频 SSIM | 音频 RMS | 音频 SI-SDR |
|---|---:|---:|---:|---:|---:|
| baseline | 93.1s | 0/20 | 1.000 | -27.8 dB | ref |
| FBC 0.15 | 57.1s | 11/9（computed/skipped） | 0.903 | -42.7 dB | -9.97 dB |
| T8 0.18 / gpu | 68.2s | 6/20 | 0.901 | -32.7 dB | -9.98 dB |

DaSiWa 4 秒未跑：2 秒已出现音轨近静音，长度继续增加没有意义，先按阻塞项处理。

## 结论

1. **FBC 是当前速度最优的可用 cache**：2 秒 1.78×，4 秒 1.63×；视频 SSIM 约 0.90。
2. **T8 的视频指标略好**（2 秒 SSIM 0.926），但更慢，且音频 SI-SDR 更差；不是“全面优于 FBC”。
3. **DaSiWa 在 2 秒片段上音轨接近静音（RMS -95 dB）**，1 秒片段则正常。命中/跳过逻辑在长片段上破坏了 audio 输出，不能作为 2 秒以上默认开启的 cache。需要上游修复或限制到 ≤1 秒。
4. 三个 cache 的音频都不是透明的：FBC/T8 的 SI-SDR 为负，且 4 秒比 2 秒更差。画面上看 4 秒帧仍接近 baseline，但音轨差异不能忽略。
5. 4-step LightX2V 的 first-block ratio 很高，FBC/T8 基本不命中；cache 只适合 20+ step 的普通采样。
6. 之前的 1 秒红方块测试不能代表这个结论；短片段上 FBC 0.15 的 SSIM 0.925、DaSiWa 音轨也正常，长片段/音频暴露了差异。

## 建议

- 默认推荐：FBC 0.15，用于 20+ step、需要速度且能接受近似音频的场景。
- T8 0.18 仅在视频 fidelity 优先、且能接受更慢和音频偏移时使用。
- DaSiWa 在 2 秒及以上先禁用；如果上游修复 audio residual 再加回。
- 任何 cache 上线前都按目标 prompt/时长/分辨率跑 baseline 对比；不要只看理论 skip 比例。
- 下一步需要人工听音和更复杂 prompt（对白、音乐、强运动）来确认 SI-SDR 的感知意义；SSIM/SI-SDR 只是筛选指标。

## 复现

```bash
# baseline
python3 -m cli --json videos generate "$PROMPT" \
  --width 512 --height 512 --seconds 2 --seed 41 --steps 20

# FBC
python3 -m cli --json videos generate "$PROMPT" \
  --width 512 --height 512 --seconds 2 --seed 41 \
  --speed-json '{"steps":20,"block_cache":{"method":"fbc","params":{"threshold":0.15}}}'

# T8
python3 -m cli --json videos generate "$PROMPT" \
  --width 512 --height 512 --seconds 2 --seed 41 \
  --speed-json '{"steps":20,"block_cache":{"method":"t8","params":{"residual_diff_threshold":0.18,"cache_device":"gpu"}}}'

# DaSiWa
python3 -m cli --json videos generate "$PROMPT" \
  --width 512 --height 512 --seconds 2 --seed 41 \
  --speed-json '{"steps":20,"block_cache":{"method":"dasiwa","params":{"reuse_threshold":0.08,"device":"auto"}}}'
```

SSIM / 音频指标：

```bash
ffmpeg -i base.mp4 -i cache.mp4 -lavfi '[0:v][1:v]ssim' -f null -
ffmpeg -i base.mp4 -i cache.mp4 -lavfi '[0:a][1:a]asisdr' -f null -
ffmpeg -i cache.mp4 -af astats=metadata=1:reset=0 -f null -
```
