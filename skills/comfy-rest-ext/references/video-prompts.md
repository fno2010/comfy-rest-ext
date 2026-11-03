# Video prompt guide

These are prompting principles, not a model catalog. Select the model and
task from `models`/`capabilities` at runtime.

## Structure

For short clips, a useful order is:

1. shot/subject: who or what is on screen;
2. action: a single continuous motion, not a list of disconnected events;
3. camera: shot size, angle, dolly/pan/orbit, lens feel;
4. environment and light: location, time of day, practical sources;
5. style/grade: photographic, animation, texture, color palette;
6. constraints: avoid cuts, text, morphing, extra limbs, unstable identity.

Keep one primary action per few seconds. Long prompts become more reliable
when split into timeline beats instead of many simultaneous verbs.

## Timelines

For multi-beat clips:

```text
0-2s: wide shot establishes the empty street.
2-5s: the subject walks in from the right; camera tracks laterally.
5-8s: close-up as the subject turns toward the light.
```

Match the requested duration to the model's supported duration/frame policy
reported by discovery. Do not assume a fixed frame grid.

## References

- First-frame input: describe how motion continues from the still; avoid
  re-describing the frame as a new scene.
- Multiple reference images: name them `<Picture 1>`, `<Picture 2>`, etc. and
  state the role of each (identity, costume, environment, style).
- Reference video: name it `<Video 1>` and describe which motion, camera move,
  or timing to transfer.
- Preserve identity by repeating concrete invariants: face shape, hair, clothing,
  accessories, color, body type.

## Audio

When the model supports audio, describe ambience and diegetic sound separately
from dialogue. Keep spoken lines short and assign them to a visible speaker.
Avoid promising lip-sync accuracy unless the selected model advertises it.

## Review loop

After a completed artifact, compare the result to the prompt and report:

- what changed between beats;
- identity/frame stability issues;
- whether the requested camera motion actually occurred;
- whether a retry should change the prompt, seed, references, or model.

Use a new seed for a genuinely different attempt. Reusing a seed while changing
the prompt helps isolate prompt effects.
