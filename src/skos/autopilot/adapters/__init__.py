"""Shim: re-exports skharness.autocode.adapters (Wave 2, Phase B of the
autocode extraction).

Importing skharness.autocode.adapters (below) runs ITS __init__ body, which
calls register_harness(...) for each adapter into the single shared HARNESSES
registry (skharness.autocode.harness.HARNESSES) exactly once, since Python
caches the module in sys.modules regardless of how many packages import it.
skos.autopilot.adapters therefore does not re-run registration itself; it
only re-exposes the same adapter classes under the historical import path.
See docs/superpowers/specs/2026-07-25-autocode-engine-extraction-architecture.md
section 5 (Phase B). Do not add new code here; add it to
skharness.autocode.adapters instead.
"""
from skharness.autocode.adapters import *  # noqa: F401,F403

__all__ = ["ClaudeCodeAdapter", "PiAdapter", "OpenCodeAdapter", "CodexStubAdapter"]
