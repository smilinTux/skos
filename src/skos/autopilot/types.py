"""Shim: re-exports skharness.autocode.types (Wave 2, Phase B of the autocode extraction).

skos.autopilot.types now delegates to the shared engine in skharness.autocode;
this module is kept so every existing import path (skos.cli, skos tests,
out-of-tree callers) keeps resolving unchanged. See
docs/superpowers/specs/2026-07-25-autocode-engine-extraction-architecture.md
section 5 (Phase B) for the migration plan. Do not add new code here; add it
to skharness.autocode.types instead.
"""
from skharness.autocode.types import *  # noqa: F401,F403
