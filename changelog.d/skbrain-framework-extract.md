### Changed
- **The skbrain engine is now the public `skbrain` framework.** `skos.brain.ops` is a
  re-export shim over `skbrain` (kept for one minor release), and the `skbrain` console
  script comes from the skbrain package. Install it with the optional `brain` extra
  (skbrain release tag from git until it is on PyPI). The skbrain pack's
  `content_repo` step uses `marker: skbrain.yaml`, `dest_env: SKBRAIN_CANON` and
  `remote_env: SKBRAIN_CANON_REMOTE`, and `skbrain-sync.service` pulls the estate canon
  first.
