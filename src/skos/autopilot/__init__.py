"""SKOS Autopilot: harness-agnostic assess/plan/swarm/grade/report over the GTD spine.

Shim package (Wave 2, Phase B of the autocode extraction): the engine itself
now lives in skharness.autocode; every submodule under skos.autopilot is a
thin re-export shim so existing import paths (skos.cli, skos tests,
out-of-tree callers) keep resolving unchanged. See
docs/superpowers/specs/2026-07-25-autocode-engine-extraction-architecture.md
section 5 (Phase B).
"""
