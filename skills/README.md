# Agent skills

Installable agent skills for comfy-rest-ext live here.

## comfy-rest-ext

A skill for remote ComfyUI image/video generation, task recovery, capability
provisioning, and multi-step media plans through the HTTP-only CLI.

```bash
npx skills add fno2010/comfy-rest-ext --skill comfy-rest-ext
```

The skill prefers `comfy-rest-ext` from `PATH`; otherwise its bootstrap script
downloads a pinned static binary and verifies the release checksum. It does not
require Python, uv, pipx, a repository checkout, or a local ComfyUI install.

See [comfy-rest-ext/SKILL.md](comfy-rest-ext/SKILL.md) and its
`references/` and `scripts/` directories.
