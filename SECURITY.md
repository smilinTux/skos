# Security Policy - skos

`skos` is the sovereign agent OS: a library plus CLI that owns the data-root filesystem,
the capability resolver, the unified GTD capture sink, the scheduler wrapping, and one
optional read-only web surface. It is **not** an identity root and **not** a crypto
library. It does hold one small piece of key material, so it carries a crypto posture
statement, scoped honestly below.

> ⚠️ **Experimental, pre-1.0, and NOT independently security-audited.** No third-party
> security audit, fuzzing, or formal review has been performed on this repository. The
> one cryptographic surface (`skos.secrets.vault_file`) is a thin binding over the
> `cryptography` library's Fernet recipe; the original code is the key-file handling,
> the store layout, and the CLI plumbing. A passing test suite proves behaviour, **not**
> the absence of side channels or handling flaws. **Review it yourself before
> production use.**

---

## Honest claims (what skos does and does NOT promise)

Per the sk-standards
[CRYPTOGRAPHY_STANDARD](https://github.com/smilinTux/sk-standards/blob/main/standards/CRYPTOGRAPHY_STANDARD.md),
every claim is scoped to a surface and cites its primitive.

- ✅ **Encryption at rest for one local secret blob.** `src/skos/secrets/vault_file.py`
  encrypts `$SK_DATA_ROOT/secrets/vault-file.enc` with **Fernet**, that is
  **AES-128-CBC with HMAC-SHA256** authentication, from the `cryptography` package. The
  key is generated once at `$SK_DATA_ROOT/secrets/master.key` and written `chmod 0600`,
  or supplied through `$SKOS_VAULT_KEY`.
- ✅ **Symmetric primitives are quantum-acceptable.** AES and HMAC-SHA256 face only
  Grover speedup. Do **not** "fix" them, and do not describe them as quantum-broken.
- ✅ **Secrets stay out of the tree.** No real secret or PII value is committed.
  `skos.secret_env` resolves a name from the process environment, then a gitignored
  mode-600 operator file (`~/.skcapstone/skos-schedule.env`, override
  `$SKOS_SCHEDULE_ENV`), then a caller-supplied placeholder. `skos schedule render`
  prints variable **names**, never values. `.github/workflows/secret-scan.yml` runs the
  gitleaks **binary** (not the licence-gated action) with `--exit-code 1`, so a finding
  fails the build.
- ✅ **The web surface is read-only and loopback.** Only `GET` routes are registered and
  the default bind is `127.0.0.1:7781`. Enforced by
  `tests/test_webui.py::test_no_write_methods` and
  `::test_only_get_routes_are_registered`.
- ❌ **No asymmetric crypto, no key exchange, no signatures, no network crypto.** The
  crypto maturity tier is **T0 (classical)**, and narrowly so: there is no negotiated
  surface, so T1 (agility), T2 (hybrid KEM) and T3 (hybrid signature) are **not
  applicable** rather than "not yet done".
- ❌ **The `capauth` secret backend is a stub.** `src/skos/secrets/capauth.py` raises on
  every operation. skos does **not** implement sovereign PGP secret storage; that is
  [capauth](https://github.com/smilinTux/capauth).
- ❌ **Fernet is AES-128, not AES-256.** If you need a 256-bit symmetric margin for a
  given secret, do not put that secret in the `vault-file` backend.
- ❌ **The vault-file key is not passphrase-wrapped.** `master.key` is protected by
  filesystem permissions (mode 0600) and nothing else. Anyone who can read the file, or
  read the process environment when `$SKOS_VAULT_KEY` is set, can decrypt the blob.
- ❌ **Never** "quantum-proof", "quantum-safe", "unbreakable", "CNSA 2.0 compliant",
  "FIPS 206", or "Falcon". None of those apply to anything in this repo.
- ❌ **skos does not own `skoperator`.** `~/.skenv/bin/skoperator` imports
  `skcapstone.operator_seat.cli:main`. Report operator-seat issues against
  [skcapstone](https://github.com/smilinTux/skcapstone), even though skos wraps the unit.

---

## Threat model

### In scope

- **Secret leakage through the repo or a rendered artifact.** A committed credential, or
  `skos schedule render` / `skos schedule install` emitting a secret **value** into the
  crontab, a log, or a diff.
- **Weak custody of the vault-file key.** `master.key` created world-readable, or the
  encrypted blob written without `0600`.
- **Path traversal or data-root escape.** `skos.paths` is the single joiner of data-root
  literals; a caller-controlled subdir escaping `$SK_DATA_ROOT`, or `skos path` resolving
  outside the tree.
- **Privilege gain through the scheduler wrap.** `scripts/sk-cron-run.sh` and the
  `timer_wrap` drop-ins run arbitrary commands with the operator's user rights. A defect
  that lets an untrusted input choose or alter the wrapped command is in scope.
- **Silent alteration of a scheduled command.** `timer_wrap` used to read `ExecStart`
  from the **base unit file only**, never the effective post-drop-in value, so wrapping a
  unit silently dropped flags that other drop-ins contributed. That reverted an executing
  operator seat to report-only with no error. **Fixed by card `47e32514`**: the wrap now
  reads the effective command from systemd and falls back to the file only when systemd
  cannot answer. ⚠️ Merging the fix does not repair drop-ins a previous version already
  wrote; see the retirement sequence in [SOP.md section 8](SOP.md). Related defects of
  this class, any path by which a wrap changes the command a unit actually runs, remain
  in scope.
- **Write access through the read-only web surface.** Any route on `skos serve` that
  mutates state, or a default bind on a non-loopback interface.
- **GTD sink integrity.** A capture that corrupts the shared JSON store, or a dedup-key
  collision that lets one source overwrite another's items.

### Out of scope (handle these elsewhere)

- **Confidentiality of secrets held anywhere but the `vault-file` blob.** Passphrases,
  PGP keys and token custody belong to capauth, gpg-agent and skvault.
- **Whatever the scheduler runs.** skos wraps commands for observability; the security of
  each wrapped job is that job's own.
- **Network transport.** skos publishes no ingress. Reaching `127.0.0.1:7781` from
  elsewhere is a tailnet or firewall question, not a skos one.
- **Side channels in bound libraries.** Constant-time behaviour and correctness come from
  `cryptography`; skos does not re-audit them.
- **skcapstone's operator seat**, including `skoperator run --execute --honor` semantics.
- **Optional siblings.** Defects inside `skharness` (the autocode engine behind the
  `autopilot` extra) belong to that repo.

### Trust roots / dependencies

| Surface | Library | Assurance basis |
|---|---|---|
| Secret blob at rest | `cryptography` (Fernet) | AES-128-CBC + HMAC-SHA256, RFC 3602 / RFC 2104 |
| Key file custody | POSIX file mode `0600` | operating system permissions only |
| Config parsing | `pyyaml`, `ruamel.yaml`, `pydantic` | upstream maintenance |
| CLI | `typer` (pinned `<0.13`), `click` (pinned `<8.2`) | upstream maintenance |
| Optional web surface | `fastapi`, `uvicorn` | upstream maintenance; optional extra `skos[web]` |
| Secret scanning | `gitleaks` binary, pinned version | `.github/workflows/secret-scan.yml`, `--exit-code 1` |

skos **binds** these libraries. It does not hand-roll ciphers, KDFs, or curve
primitives.

---

## Supported versions

| Version | Supported |
|---|---|
| latest published `0.x` | ✅ current |
| any earlier `0.x` | ❌ best-effort, upgrade first |

Pre-1.0, only the latest published `0.x` line receives security fixes, per
[VERSION_LIFECYCLE](https://github.com/smilinTux/sk-standards/blob/main/standards/VERSION_LIFECYCLE.md).
The distribution is **`skos-sovereign`** on PyPI (the import package is still `skos`).
An environment may still carry a pre-rename editable install registered as `skos`, so
check the ref you are running rather than trusting `pip show`.

---

## Reporting a vulnerability

**Do not open a public GitHub issue for a security vulnerability.**

- **Primary:** GitHub **private vulnerability reporting**, the "Report a vulnerability"
  button on the Security tab of
  [`smilinTux/skos`](https://github.com/smilinTux/skos/security).
- **Secondary (out of band):** contact the maintainers (smilinTux / SKWorld) through the
  address on the GitHub org profile. Encrypt sensitive reports to the maintainer's
  sovereign capauth / `sk_pgp` PGP key, fingerprint published on the org profile.

Please include: the skos version or git ref, your Python version, which extras are
installed (`web`, `autopilot`), your `$SK_DATA_ROOT` layout if relevant, and a minimal
reproduction. We aim to **acknowledge within 72 hours** and to ship a fix or mitigation
within 90 days, coordinating a disclosure date with you.

**Safe harbour:** good-faith research conducted under coordinated disclosure will not be
pursued. Stay within systems you own or are authorised to test, do not access or exfil
other people's data, and give us reasonable time before publishing. Credit is given
unless you ask otherwise.

### What we especially want to hear about

- A secret **value** appearing in rendered crontab output, a log line, the run ledger, or
  a committed file.
- `master.key` or `vault-file.enc` created with permissions looser than `0600`.
- A `skos path` / data-root input that escapes `$SK_DATA_ROOT`.
- A wrapped scheduled command that can be altered or chosen by untrusted input.
- Another instance of the drop-in flag-strip class described above: a unit whose
  effective `ExecStart` silently loses flags its drop-ins contributed.
- Any mutating route, or any non-loopback default bind, on the `skos serve` surface.
- A GTD capture that corrupts the shared store or overwrites another source's items.

---

**License:** GPL-3.0-or-later. **Standards:**
[CRYPTOGRAPHY_STANDARD](https://github.com/smilinTux/sk-standards/blob/main/standards/CRYPTOGRAPHY_STANDARD.md),
[SECURITY_DISCLOSURE_STANDARD](https://github.com/smilinTux/sk-standards/blob/main/standards/SECURITY_DISCLOSURE_STANDARD.md);
ISO/IEC 29147 and 30111 (disclosure); CVSS v4.0.
