# comfy-rest-ext Go CLI

HTTP-only, statically linked CLI for `comfy-rest-ext`.

```bash
./build.sh                 # injects ../VERSION into main.version
./dist/comfy-rest-ext --version
./dist/comfy-rest-ext --help
ldd ./dist/comfy-rest-ext   # "not a dynamic executable"
```

Implemented commands:

- `health`, `capabilities`, `doctor`
- `models list/get/all/info/delete`
- `videos generate/list/status/wait/content/retry/delete`
- `images generate/edit/list/status/content`
- `provision`, `restart`, `wait-reconnect`
- `downloads list/create/status/cancel`
- `deps check-workflow/install/status/check-node/restore`
- `snapshot export/import/diff/list`
- `nodes list/pack/validate/init`
- `pr-cache list/size/delete/clear`
- `config list/set/use/get/path`


The Python CLI under `../cli` remains the reference client while command
parity is completed. Both use the same HTTP contract and exit-code mapping.
