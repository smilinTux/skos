### Changed
- **skbrain is a core dependency** (`skbrain>=1.0.1`, on PyPI). The `brain` extra stays as a no-op alias.
- **The fresh-node gate clones the public example canon** `smilinTux/skbrain-ops-example` through
  `SKBRAIN_CANON_REMOTE`, so the pack's `content_repo` step runs for real in CI with no credentials.

### Fixed
- **`content_repo` with no remote fails closed** with "set SKBRAIN_CANON_REMOTE in node config"
  instead of running `git clone skbrain-ops`.
