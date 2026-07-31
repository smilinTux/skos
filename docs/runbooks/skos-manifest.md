# Runbook: skos SKWorld module manifest (emit / sign / register)

skos is a first-class SKWorld subapp and sits at the TOP of the subapp list
(umbrella spec 4.4, nav position 1). Like every subapp it declares ONE
capauth-signed `skworld.module.json` (two facets: UI + operator) that the umbrella
shell reads to mount its pane and let Atlas watch/steer it.

## Why a static file, not a `/.well-known/` HTTP endpoint

skchat and skcode serve their manifest from a live daemon at
`/.well-known/skworld-module.json` because they already run an HTTP server. **skos
does not**: it is a CLI plus a cron scheduler, with no web surface. The umbrella
shell's v1 registry (spec 5.3) references each module's manifest by **"local file
OR `/.well-known/` URL"**, so skos publishes the **local-file** form. This is the
same recipe skdashboard uses (spec 8.2, "Serve `skworld.module.json` ... (static
file)") and the one skos' web UI will inherit when it lands, unchanged, because the
manifest URLs are origin-relative.

## Artifacts

| File | Role |
|---|---|
| `src/skos/skworld_manifest.py` | Builds the manifest dict + the deterministic static-file emitter. |
| `skos manifest emit` | Operator CLI: write / print the manifest. |
| `~/.skcapstone/shell/modules/skos.skworld-module.json` | Emitted manifest (the well-known local-file location). |
| `~/.skcapstone/shell/modules.json` | The shell's signed registry that references the file above (spec 5.3). |

## Emit

```
skos manifest emit                       # write the well-known local file
skos manifest emit --print               # print JSON to stdout, write nothing
skos manifest emit --base-url http://<origin>:7780/   # once skos' web UI lands
skos manifest emit --out /path/to/file.json           # custom location
```

The bytes are deterministic (sorted keys, trailing newline), so re-emitting an
unchanged manifest is a no-op diff and its capauth signature is reproducible.
Because skos has no web server yet, `--base-url` defaults to a localhost
placeholder and the shell interim-routes this entry to the native skos screens
(spec 4.4); a Grade B -> A promotion is a re-emit with the real origin, never a
contract change.

## Sign + register (how the shell consumes it)

The shell **refuses any manifest whose detached capauth signature does not verify**
against an operator-approved key (spec 5.3). After emitting:

1. **Sign** the emitted file with the operator's capauth identity, producing a
   detached signature alongside it (`skos.skworld-module.json.sig`).
2. **Register** the file path in `~/.skcapstone/shell/modules.json`: add it to the
   ordered list of manifest locations (a local-file entry) and to the operator's
   enable set.
3. **Verify**: the shell loads the registry, checks each detached signature, and
   only then renders the module. An unsigned or tamper-mismatched manifest is
   never rendered.

Re-run `skos manifest emit` whenever the manifest contents change (schema bump,
operator-facet change, real web origin), then re-sign. The operator-facet block is
drift-guarded against Atlas's skos adapter by the skcapstone conformance test.
