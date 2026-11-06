# Image prompt guide

Select the model and supported task from runtime discovery. These are prompt
patterns, not fixed model recommendations.

## Generation

A stable prompt order:

```text
subject + action; composition/shot; environment; lighting; materials/texture;
style/medium; color and contrast; quality constraints
```

Prefer concrete nouns and visual relationships over stacked adjectives. State
the intended use (poster, product shot, character sheet, editorial photo) because
it changes framing and lighting.

## Editing

For instruction-style edits, identify the image explicitly and limit the scope:

```text
Using <image1>, replace only the background with a snowy street at dusk.
Preserve the subject's face, hair, clothing, pose, and camera framing.
```

For reference-driven edits, name each image and its role. Avoid ambiguous
instructions such as "make it better"; specify what must remain unchanged.

Qwen-Image 2.1 supports multiple reference images (up to the model capability,
currently 16 slots). `image_1` is the image being edited; later slots supply
content or style. Use exact tags: `<image1>`, `<image2>`, ... (not `<iimage1>`
or `<image 1>`).

```text
Extract the character from <image1> onto a transparent background.
Keep her face, hair, costume, colors, and accessories unchanged.
Change only the pose to the kneeling pose from <image2>:
both knees on the floor, legs folded to one side, hands raised near the chest,
head slightly lowered. Do not copy <image2>'s face, hair, clothing, wings, or background.
```

Reference images are guidance, not a pose-control signal. If the exact pose
must match, describe the joints/limbs explicitly; for production work use a
pose-control workflow or a manually prepared pose guide instead of relying on
the model to infer the pose from a second reference.

## Consistency

When generating a set, keep a shared block of invariants (character, palette,
lens, lighting) and vary only the requested dimension. Reuse seeds only when
testing a controlled change.

## Review

Check anatomy, text rendering, object count, perspective, and whether the
requested edit leaked into unrelated regions. If the model supports image
editing, prefer editing a successful base image over regenerating from scratch.
