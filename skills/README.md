# Agent skills

Installable agent skills for comfy-rest-ext live here.

## comfy-rest-ext

A skill for remote ComfyUI image/video generation, task recovery, capability
provisioning, and multi-step media plans through the HTTP-only CLI.

```bash
npx skills add fno2010/comfy-rest-ext --skill comfy-rest-ext
```

The skill resolves the client through `scripts/bootstrap-cli.sh`. The bundled
`comfy-rest-ext/cli-version` file pins the exact release; bootstrap validates
`PATH`, `COMFY_REST_EXT_CLI`, and the versioned cache, then downloads the
matching static binary if necessary. It does not require Python, uv, pipx, a
repository checkout, or a local ComfyUI install.

See [comfy-rest-ext/SKILL.md](comfy-rest-ext/SKILL.md) and its
`references/` and `scripts/` directories.

## Upgrading an installed skill

CLI and skill versions are coupled by `cli-version`. Updating the skill updates
the pin; the next bootstrap resolution downloads the matching CLI once.

```bash
# Project install (run from the project root)
npx skills update comfy-rest-ext

# Global install
npx skills update -g comfy-rest-ext

npx skills list          # or: npx skills list -g
```

Diagnose the resolved client without downloading:

```bash
./scripts/bootstrap-cli.sh --check
```

Version-mismatched candidates are ignored. The agent must use the path returned
by bootstrap; a bare `comfy-rest-ext` from `PATH` is not assumed compatible.
`--refresh` only repairs the pinned version; it is not the upgrade path.

If a Python CLI is intentionally used, upgrade it with `uv tool upgrade
comfy-rest-ext-cli` or `pipx upgrade comfy-rest-ext-cli`; otherwise bootstrap
falls back to the pinned static binary.
