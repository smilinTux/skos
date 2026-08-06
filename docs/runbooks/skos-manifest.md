# Runbook: skos SKWorld module manifest (emit / sign / register)

skos is a first-class SKWorld subapp and sits at the TOP of the subapp list
(umbrella spec 4.4, nav position 1). Like every subapp it declares ONE
capauth-signed `skworld.module.json` (two facets: UI + operator) that the umbrella
shell reads to mount its pane and let Atlas watch/steer it.

## Two discovery paths: live `/.well-known/` AND a static file

skos ships BOTH forms the umbrella shell registry accepts (spec 5.3, "local file
OR `/.well-known/` URL"):

- **Live**: skos' optional read-only web surface (`skos serve`, see `webui.py`)
  serves the manifest unauthenticated and origin-relative at
  `GET /.well-known/skworld-module.json` on `127.0.0.1:7781`, exactly like skchat
  and skcode serve theirs from their daemons.
- **Static**: `skos manifest emit` writes a deterministic, capauth-signed
  local-file copy for the OFFLINE discovery path the shell reads when skos' web
  surface is not running. This is the same recipe skdashboard uses (spec 8.2,
  "Serve `skworld.module.json` ... (static file)").

Both come from the one builder in `skworld_manifest.py`, so they never diverge;
the manifest URLs are origin-relative, so a re-point (e.g. to the tailnet host) is
a re-emit, never a contract change.

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
skos manifest emit --base-url http://<origin>:7781/   # point at a real origin
skos manifest emit --out /path/to/file.json           # custom location
```

The bytes are deterministic (sorted keys, trailing newline), so re-emitting an
unchanged manifest is a no-op diff and its capauth signature is reproducible.
`--base-url` defaults to skos' read-only web surface origin
(`http://127.0.0.1:7781/`). Served live the URLs are rebuilt origin-relative from
the request, so this default only fixes the static-registry copy; a Grade B -> A
promotion is a re-emit, never a contract change.

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
