# Clean acceptance

Use a downloaded static CLI artifact and a clean temporary project. The
acceptance script must not rely on the repository on `PYTHONPATH` or on a
preinstalled Python.

```bash
COMFY_REST_EXT_CLI=/path/to/comfy-rest-ext-linux-amd64 \
COMFY_REST_EXT_BASE_URL=https://comfy.example \
./scripts/acceptance-clean.sh
```

It verifies:

- `ldd` reports a static Linux binary (when run on Linux);
- the skill installs with `npx skills add` into an isolated project;
- the skill's smoke harness reaches `doctor`, `models`, and `capabilities`;
- a temporary profile is written outside the repository.

For an end-to-end generation test, run the skill task from that installed
project against a disposable deployment after the smoke test passes.
