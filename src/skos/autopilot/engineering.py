"""Shim: re-exports skharness.autocode.engineering (Wave 2, Phase B of the autocode extraction).

skos.autopilot.engineering now delegates to the shared engine in skharness.autocode;
this module is kept so every existing import path (skos.cli, skos tests,
out-of-tree callers) keeps resolving unchanged. See
docs/superpowers/specs/2026-07-25-autocode-engine-extraction-architecture.md
section 5 (Phase B) for the migration plan. Do not add new code here; add it
to skharness.autocode.engineering instead.
"""
from skharness.autocode.engineering import *  # noqa: F401,F403
from skharness.autocode.engineering import _revert_impl  # noqa: F401  private names used by cli/tests
