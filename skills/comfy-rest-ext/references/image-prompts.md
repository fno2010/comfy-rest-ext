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

## Consistency

When generating a set, keep a shared block of invariants (character, palette,
lens, lighting) and vary only the requested dimension. Reuse seeds only when
testing a controlled change.

## Review

Check anatomy, text rendering, object count, perspective, and whether the
requested edit leaked into unrelated regions. If the model supports image
editing, prefer editing a successful base image over regenerating from scratch.
