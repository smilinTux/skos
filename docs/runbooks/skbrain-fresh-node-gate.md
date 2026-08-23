# skbrain fresh-node E2E gate

Card d17892ae (epic fb3cc09d, blocks f3cb6231). The 2026-08-23 swarm audit
found that card f3cb6231's completion criterion -- "complete only after a
fresh-node observation passes" -- had no gate behind it anywhere in
skcapstone, skcoord, skos, skharness, or skdashboard. The only drill-like
coverage was `tests/test_pack_provisioner.py`, which drives the pack
provisioner through a mocked `Effects` protocol (dependency-injected fakes).
That is real coverage of dispatch/ordering/coupling logic, but it never runs
a real install, a real Postgres, or a real `skmemory` subprocess. This runbook
documents the gate built to close that gap.

## Single command

```
scripts/fresh-node-gate.sh
```

Env overrides:

- `SKMEMORY_REPO` -- path to a skmemory checkout (default
  `~/clawd/skcapstone-repos/skmemory`, matching the fleet's editable-install
  convention and `resolve_package_path()`'s own lookup).
- `SKMEM_PG_IMAGE` -- the skmem-pg image tag to boot a fresh, ephemeral
  instance from (default `smilintux/skmem-pg:pg17-bm25-age`, the tag this
  host already has built locally; see `docker image ls`). Must exist locally;
  the script prints the `docker build` command to produce it if missing
  (compiles Apache AGE from source -- can take several minutes).
- `EVIDENCE_DIR` -- where the evidence JSON lands (default
  `artifacts/fresh-node-gate/<run-id>/report.json` under the repo root).

Exit codes: `0` harness ran AND the gate passed. `1` harness ran but the gate
did not pass (see "Expected result" below -- this is the normal outcome
today). `2` the harness itself broke (docker missing, Postgres never came
up, image build failed) -- not a gate result either way.

CI: `tests/test_fresh_node_gate_it.py`, gated behind `RUN_FRESH_NODE_GATE=1`
plus docker + the skmem-pg image, mirroring the existing
`RUN_SANDBOX_IT=1` / `tests/test_sandbox_confinement_it.py` precedent. It is
heavy (builds an image, boots a full BM25+AGE+pgvector Postgres) and is
never part of the default `pytest` run.

## What the isolation is, and what it proves

Two containers, both destroyed at the end of the run:

1. **The node under test.** Built fresh each run from a staged copy of this
   skos worktree plus a staged copy of the skmemory checkout
   (`docker/fresh-node/Dockerfile`). `$HOME` is `/root` inside a container
   that has never had `~/.skcapstone`, `~/.skenv`, `~/.config/skcapstone`, or
   any systemd unit written to it -- because none of those paths exist
   anywhere in the image's build history. skos and skmemory are installed
   the same way the real fleet installs sibling SK\* packages: `pip install
   -e` against a repo checkout, not a bare PyPI wheel (confirmed during this
   harness's development: the PyPI `skmemory` wheel does **not** ship
   `deploy/`, so `skmemory pg migrate` cannot locate its own SQL against a
   plain `pip install skmemory` -- see Follow-ups).

2. **A throwaway skmem-pg.** The exact image tag this host's real
   production `skmem-pg` container runs (`smilintux/skmem-pg:pg17-bm25-age`
   -- pgvector + ParadeDB BM25 + Apache AGE), but a brand-new container with
   a brand-new, empty data volume. Its own first-boot init
   (`deploy/skmem-pg/initdb/00-run-init.sh`, bind-mounted read-only from the
   skmemory checkout, mirroring `skmemory/docker-compose.yml` exactly)
   applies `schema.sql` and every forward migration for real. This is a
   different container, different Docker network, and different data volume
   from the host's real `skmem-pg` -- `--rm`'d when the run ends, never
   published to a host port.

The node-under-test container runs sharing the fresh Postgres container's
network namespace (`--network container:<pg>`), so `localhost:5432` resolves
correctly for both `SKMEMORY_PG_DSN` and the hardcoded `localhost:5432` in
`DefaultEffects.db_roles()` -- no code change needed to make that assumption
true.

Inside that container, in order, all real (none mocked):

```
skos install skbrain --force
skbrain doctor
skbrain operator explain --json
skbrain operator observe --json
```

**What this proves:** the real `skos.packs.provisioner.install()` dispatch
through real `DefaultEffects` -- real subprocess calls to the `skmemory` CLI
(`skmemory pg migrate`, `skmemory pg roles`), real `skos.secrets.VaultFileBackend`
Fernet-encrypted credential storage, a real `psycopg` connection to a real,
freshly-migrated Postgres for `skbrain doctor` / `operator observe` -- on a
node with genuinely no prior skbrain state.

**What this does NOT prove**, versus a truly bare machine:

- **Kernel/init identity.** The container shares this host's kernel and is
  not a fresh OS install from ISO. Nothing here exercises systemd, cron, or
  any OS-level bootstrap.
- **`--force` bypasses the requires-gate NodeFacts synthesis**, not step
  execution. A genuinely fresh node has no way to discover "a skmem-pg
  capable Postgres is reachable" without the full skcapstone topology
  registry, which is out of scope for this harness. Every step's *side
  effect* still runs for real; only the pre-flight capability assertion is
  asserted rather than discovered.
- **Network egress / private repo credentials.** The `content_repo` install
  step (cloning the private `skbrain-ops` content repo) has no credentials
  available in this sandbox and is expected to fail here regardless of
  isolation quality -- see Follow-ups, this also surfaced a real gap in the
  pack manifest itself.
- **Fleet activation.** `fleet_objects` and `emit_manifest` steps, if
  reached, write files into the container's own fake `~/.skcapstone/fleet/`
  and `~/.skcapstone/shell/modules/` -- never the host's real ATLAS
  registry, and nothing here starts a cron job or systemd timer from them.

## Expected result (as of 2026-08-23)

The gate is expected to **fail** (`gate_pass: false`, harness exit `1`) even
on a clean, successful run, for two independent reasons visible in the
evidence JSON:

1. `src/skos/packs/skbrain/skworld.module.json`'s `content_repo` step has no
   `remotes` entry, so `DefaultEffects.content_repo()` falls back to
   `git clone skbrain-ops <dest>` -- not a valid URL. On a fresh node with no
   pre-existing Syncthing-synced checkout, this step fails and the
   all-or-nothing pack install stops there (after `sql_migration` and
   `db_roles`, both of which are real and can genuinely succeed against the
   fresh Postgres).
2. Independent of (1): `skbrain operator observe`'s `CmdbDriftBounded` and
   `KedbCanonCovered` conditions are hardcoded `"Unknown"` literals in
   `src/skos/brain/ops/cli.py` (`operator_observe()`), never derived from any
   check. Even a fully successful install cannot turn these `"True"`, so the
   4-condition gate (`operator observe` reporting all `status == "True"`)
   cannot pass until those are wired to real evidence.

This is the correct, useful result the audit was looking for: it demonstrates
the gate exists, runs for real, and correctly reports the current state as
not-yet-provable rather than fabricating a green.

## Follow-ups identified, deliberately not done here

- `content_repo` step in `skworld.module.json` has no `remotes`; either add
  one (if `skbrain-ops` has a real remote) or change the step to rely purely
  on the documented Syncthing sync path and skip cloning when no remote is
  configured. Not done here: touching the pack manifest's install steps is
  scope creep for a test-harness card, and the manifest's `content_repo`
  step is data, not code -- but the right owner should decide the intended
  behavior.
- `CmdbDriftBounded` / `KedbCanonCovered` hardcoded `"Unknown"` in
  `src/skos/brain/ops/cli.py`. Explicitly out of scope: another agent is
  concurrently editing that exact file on `feat/skbrain-kedb-and-contract`,
  and separate cards already own wiring those two conditions to real
  evidence.
- The PyPI `skmemory` wheel does not ship `deploy/`, so `skmemory pg
  migrate`/`pg roles` cannot resolve their own SQL/manifest against a bare
  `pip install skmemory` (only against an editable install from a repo
  checkout). Whether that's intentional (skmemory is meant to always be
  fleet-installed from a checkout) or a packaging gap is worth a decision,
  but is unrelated to this card.
- This harness asserts `--cap`/`--force` rather than teaching the requires
  gate to discover a reachable skmem-pg on its own; a real topology-registry
  probe would be a more faithful (if `--force`-free) NodeFacts source for a
  future iteration.
