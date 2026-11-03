# comfy-rest-ext Go CLI

HTTP-only, statically linked CLI for `comfy-rest-ext`.

```bash
./build.sh
./dist/comfy-rest-ext --help
ldd ./dist/comfy-rest-ext   # "not a dynamic executable"
```

Implemented commands: `health`, `capabilities`, `doctor`, `models`,
`videos generate/list/status/wait/content/retry`,
`images generate/edit/list/status/content`,
`provision`, `restart`, `wait-reconnect`,
`downloads list/create/status/cancel`, and `config list/set/use/get/path`.

The Python CLI under `../cli` remains the reference client while command
parity is completed. Both use the same HTTP contract and exit-code mapping.
