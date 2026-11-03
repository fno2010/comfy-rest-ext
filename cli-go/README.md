# comfy-rest-ext Go CLI

HTTP-only, statically linked CLI for `comfy-rest-ext`.

```bash
./build.sh
./dist/comfy-rest-ext --help
ldd ./dist/comfy-rest-ext   # "not a dynamic executable"
```

Implemented core commands: `health`, `capabilities`, `doctor`, `models`,
`videos list/status/wait/content/retry`, `images list/status/content`, and
`config list/set/use/get/path`.

The Python CLI under `../cli` remains the reference client while command
parity is completed. Both use the same HTTP contract and exit-code mapping.
