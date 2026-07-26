# skos secret provisioning + recovery on a blank machine

Card d65ff0ca. This runbook defines how to re-establish the secrets PLANE (the
credentials skos and its guarded services need) on a wiped or freshly
provisioned node, in order, and where each secret comes from.

## Why this is separate from cold-start

The sibling card f15d086d added the cold-start empty-STORE guard
(`docs/runbooks/skos-coldstart.md`): it protects the GTD *data* from being
clobbered by an un-restored node. That guard assumes the process can already
resolve its secrets. On a truly blank machine it cannot: the vault master key,
the operator env file, the gog keyring password, and the capauth identity are
all gone. The store can be restored from Syncthing / skbackup, but a secret is
never "restored" that way. It comes back from escrow, from skvault, or from a
re-auth. This runbook is that layer, underneath the store guard.

Rule of order on a cold node: **provision the secrets plane FIRST, then restore
the store (cold-start runbook), then start services.** A service that starts
without its secrets fails loudly; a service that emits onto an un-restored store
replicates emptiness. Do secrets, then store, then run.

## What lives where (no real values ever in this repo)

| Secret | On this machine | Recovered from |
|---|---|---|
| `master.key` (vault-file Fernet key) | `$SKOS_VAULT_KEY` env, else `$SK_DATA_ROOT/secrets/master.key` (mode 600) | offline escrow / skvault (seals the vault-file backend) |
| operator env file | `$SKOS_SCHEDULE_ENV`, else `~/.skcapstone/skos-schedule.env` (mode 600) | `skos secrets bootstrap` scaffolds it; fill from skvault/escrow |
| `GOG_KEYRING_PASSWORD` | inside the operator env file | skvault (do NOT paste the old value back into git) |
| `SKMEM_PG_PASSWORD` | inside the operator env file | skvault |
| gog OAuth tokens | the gog file keyring (`~/.config/gog`) | re-auth via the `gmail-oauth` skill (never duplicated into skos) |
| capauth identity | the capauth agent home | capauth agent / skvault (vault-file is the working default backend today) |

No secret VALUE is stored in code or docs. `skos secrets check` only ever
reports present/absent, never a value, so it is safe to run and paste anywhere.

## Preflight: what is missing on this machine

```
skos secrets check          # human-readable
skos secrets check --json   # machine-readable
```

Exit 0 = every REQUIRED plane credential (`master.key`, operator env file,
`GOG_KEYRING_PASSWORD`) is present. Exit 1 = at least one is missing; the report
names which and where each comes from. Optional/external secrets
(`SKMEM_PG_PASSWORD`, gog tokens, capauth) are reported but do not gate the exit
code, because skos recovers those by re-auth/delegation rather than from its own
store.

## The recovery order (do this on a blank node)

1. **Restore the vault master key.** Retrieve `master.key` from offline escrow
   or from skvault and place it at `$SK_DATA_ROOT/secrets/master.key`, mode 600.
   Alternatively export `SKOS_VAULT_KEY` in the environment. Without it the
   vault-file backend cannot decrypt anything, so this is step one. If the key
   is lost entirely, the encrypted vault blob is unrecoverable and every secret
   in it must be re-issued at source (rotate), not "recovered".

2. **Scaffold the operator env file.**

   ```
   skos secrets bootstrap
   ```

   This writes `~/.skcapstone/skos-schedule.env` (or `$SKOS_SCHEDULE_ENV`) with
   placeholder KEYS only, mode 600, and never clobbers an existing file unless
   you pass `--force`. It prints this same recovery checklist.

3. **Fill the env file from skvault/escrow.** Replace each `replace-me`
   placeholder with the real value pulled from skvault. At minimum set
   `GOG_KEYRING_PASSWORD` (unlocks the gog Gmail token keyring) and, if the
   status corpus tile is used, `SKMEM_PG_PASSWORD`. Keep the file mode 600 and
   OUT of git. Do not paste rotated-out values back in.

4. **Recover the gog tokens by re-auth.** The gog OAuth tokens are NOT stored by
   skos and are NOT duplicated here. Re-establish them with the `gmail-oauth`
   skill (`gog` re-auth), unlocked by the `GOG_KEYRING_PASSWORD` you just set. If
   you restored a gog keyring from backup instead, just confirm it decrypts.

5. **Provision the capauth identity (optional today).** The capauth secret
   backend is a stub; the working default is vault-file. When capauth is the
   chosen backend, provision its PGP identity via the capauth agent / skvault.
   Until then this step is informational.

6. **Verify.**

   ```
   skos secrets check
   ```

   Exit 0 means the secrets plane is operable. NOW proceed to the cold-start
   store runbook (`docs/runbooks/skos-coldstart.md`): restore the GTD store,
   `skos coldstart check`, `skos coldstart init`, then start the scheduler.

## Quick reference

```
skos secrets check              # read-only present/missing report (exit 1 if a required secret is missing)
skos secrets check --json       # machine-readable
skos secrets bootstrap          # scaffold the operator env file (placeholders, mode 600) + print this order
skos secrets bootstrap --force  # overwrite an existing env file (deliberate)
```

## Notes

- This runbook and tooling never emit a secret value. `bootstrap` writes
  `replace-me` placeholders only; `check` computes booleans and discards values.
- `GOG_KEYRING_PASSWORD` / `SKMEM_PG_PASSWORD` presence is judged as "set AND not
  still the template placeholder", so a half-scaffolded file reads as missing.
- Consistent with `skos.secret_env` (env, then the gitignored operator env file,
  then a safe placeholder) and `deploy/schedule/skos-schedule.env.example`.
