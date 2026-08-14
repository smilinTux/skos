# Contributing to skos

Thanks for helping. skos is the substrate the rest of the SKWorld stack stands on, so a
small change here has a wide blast radius. This document is the short version; the
operational detail lives in [SOP.md](SOP.md).

Start with [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Security issues do **not** go in a
public issue; follow [SECURITY.md](SECURITY.md).

## Set up

```bash
git clone https://github.com/smilinTux/skos && cd skos
pip install -e ".[dev]"       # Python >= 3.12
pytest -q
```

Optional extras, only if you are touching those surfaces:
`pip install -e ".[dev,web]"` for `skos serve`, `pip install -e ".[dev,autopilot]"` for
the autocode engine (pulls the `skharness` sibling).

⚠️ **Do not unpin `typer` or `click`.** `typer>=0.12,<0.13` and `click>=8.1,<8.2` are
both required, and unpinning either breaks the whole CLI at import. The reasons are in
the `pyproject.toml` comments. A PR that relaxes those pins needs a working CLI
demonstrated from a clean venv.

## Branch and commit

- Branch off `main`. Never commit to `main` directly.
- Name branches by intent: `feat/...`, `fix/...`, `docs/...`, `ci/...`, `chore/...`.
- Conventional-commit subjects: `feat(gtd): ...`, `fix(timer-wrap): ...`,
  `docs(sop): ...`.
- Every commit ends with the co-author trailer:

  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  ```

  (Use the identity that actually did the work.)

- **No em dashes or en dashes** anywhere: code, comments, docs, commit messages, PR
  bodies. Use commas, parentheses, a colon, or a new sentence. Regular hyphens are fine.
- **Never push a tag from a branch.** A `v*` tag triggers the PyPI publish workflow.
  Tags are cut from `main` only, after the change is merged.

## The gate

`.github/workflows/ci.yml` must be green before review:

| Job | What it runs | Blocking |
|---|---|---|
| `test (py3.12)` and `test (py3.13)` | `pytest -q --cov=skos --cov-report=term-missing --cov-report=xml` | ✅ |
| `lint (ruff)` | `ruff check --select=E9,F63,F7,F82,F401 src tests` | ✅ |
| `lint (ruff)` | full `ruff check` and `ruff format --check` | ❌ advisory, `continue-on-error` |
| `build` | `python -m build` then `twine check dist/*` | ✅ |
| `secret-scan` | the gitleaks binary, `--exit-code 1` | ✅ |
| `docs-check` | the sk-standards docs freshness gate (see below) | ✅ |

⚠️ **`test (autopilot extra)` is green even when it runs nothing.** Without the
`SKHARNESS_TOKEN` secret its gate step sets `enabled=false` and every subsequent step is
skipped, so the job reports success having executed zero tests. If your change touches
`src/skos/autopilot/**`, run those tests yourself with the extra installed and say so in
the PR. Do not read that green check as coverage.

Run the blocking parts locally before pushing:

```bash
pytest -q
ruff check --select=E9,F63,F7,F82,F401 src tests
python -m build && twine check dist/*
```

## Tests are the contract

- New behaviour needs a test. Bug fixes need a test that fails before the fix.
- Name tests as sentences describing the guarantee, matching the existing style:
  `test_a_continued_execstart_is_joined_not_truncated`,
  `test_only_get_routes_are_registered`.
- Two suites self-skip by design and that is expected: `needs_skcapstone`-marked tests
  when skcapstone is absent, and every `skos.autopilot` module when `skharness` is
  absent (`tests/conftest.py`).
- Keep tests hermetic. No network, no live systemd, no writes outside `tmp_path`.

## Documentation is part of the change

- **Any change under `src/**` or to `pyproject.toml` requires a `CHANGELOG.md` entry.**
  The `docs-check` gate enforces this. Keep-a-Changelog format, SemVer headers, dated.
- If your change alters a fact stated in [SOP.md](SOP.md), update the SOP **and** the
  `docs-evidence` block at the bottom of it. Those checks execute on every push, so a
  stale documented value turns the gate red. That is the point.
- New evidence checks must be **hermetic** (repo-local, no network, no live host, no
  `systemctl`, no `ssh`, no `curl`), **cheap** (seconds), and must exit non-zero when the
  documented fact drifts. Prove yours can fail: break the fact, confirm the check goes
  non-zero, restore.
- Subsystem detail belongs in the subsystem SOP
  ([gtd-ingest](docs/gtd-ingest-SOP.md), [autopilot](docs/skos-autopilot-SOP.md)), not
  duplicated into the root SOP. State each fact once, in one place, and link to it.

## Things that are easy to get wrong here

- **`src/skos/paths.py` is the only place that joins data-root literals.** If you need a
  path, add it to `TREE` or go through `subdir()`. Do not build one by hand.
- **`skos.gtd_ingest` is the only write path into the GTD store.** `capture()` is
  create-or-skip, `upsert()` is create-or-update and performs no write on `unchanged`.
  Adding a source means adding an adapter, never a parallel store.
- **`src/skos/timer_wrap.py` writes systemd drop-ins.** It currently reads `ExecStart`
  from the base unit file only, which silently drops flags contributed by other
  drop-ins. That is a known live defect owned by card `47e32514` and documented in
  [SOP.md section 8](SOP.md). Read that section before touching this module, and do not
  "helpfully" delete a `zz-*.conf` workaround drop-in on a live box.
- **`deploy/` artifacts must stay machine-independent.** Write `$HOME` and `$SKOS_REPO`,
  never a literal `/home/<user>/...`. Never commit a secret **value**; add the variable
  **name** to `secret_env` in `deploy/schedule/jobs.yaml` and document it in
  `deploy/schedule/skos-schedule.env.example`.
- **Nothing may be applied by import.** Deploy artifacts stay inert until an explicit
  `skos schedule install` or `systemctl --user enable`.
- **The `skos serve` surface is read-only by construction.** Do not add a `POST`, `PUT`,
  `PATCH` or `DELETE` route. Two tests and one docs-evidence check hold that line.

## Review path

1. Open a PR against `main` with a description of the change, the reasoning, and how you
   verified it.
2. State explicitly what you could **not** verify. An honest gap is worth more than a
   confident guess; a wrong doc gets trusted.
3. Wait for green CI and a maintainer review. Maintainers merge; do not self-merge.
4. Releases are cut from `main` by a maintainer: bump the version in `pyproject.toml`,
   add the CHANGELOG entry, then tag `vX.Y.Z`. See [SOP.md section 5](SOP.md).

## License

By contributing you agree your contribution is licensed under **GPL-3.0-or-later**, the
same terms as the project. See [LICENSE](LICENSE).
