"""The autocode shims delegate; skos does not re-test skharness's behaviour.

`skos.autopilot.{config,ci,claude_code}` are SHIMS. Each one is a
``from skharness.autocode.X import *`` re-export, added by Wave 2 Phase B of the
autocode extraction. The implementation, and the right to change it, moved to
skharness.

WHAT THIS REPLACES, and why deleting those tests was the fix rather than
repairing them:

    tests/test_autopilot_ci.py            16 tests, all 16 names also in skharness
    tests/test_autopilot_claude_code.py    6 tests, all  6 names also in skharness
    tests/test_autopilot_config.py         2 tests, both  2 names also in skharness

Every one was a duplicate of a test skharness already owns, and skharness has
supersets (19 / 13 / 8) that pass. So the copies added no coverage while
guaranteeing drift, and by 2026-08-17 three of them had drifted far enough to
turn skos CI red on main:

  * `test_missing_file_returns_disabled_default` asserted the literal
    "claude-code" while the engine's DEFAULT_HARNESS had become "pi".
  * `test_argv_carries_skip_permissions_json_and_allowlist` asserted an argv the
    harness no longer builds.
  * `test_diff_coverage_ratio_over_changed_lines_only` is the instructive one. It
    planted a `coverage.xml` and expected it to be read back. Card 53b8c8be/S21
    then hardened `diff_coverage` to DELETE any pre-existing report before
    running, precisely so a planted file cannot be mistaken for a measurement.
    The test was asserting the exact behaviour that was deliberately removed as
    an integrity fix, so it failed for being right about the old world.
    skharness's own copy of that test writes the report from the mocked
    subprocess instead, which is what a real coverage run does.

The lesson is structural, not incidental: a shim tested by re-running the
engine's behaviour breaks every time the engine legitimately changes, and the
breakage looks like a skos bug. A shim has exactly one responsibility, which is
to delegate, so that is what is asserted here. Behaviour is skharness's to test,
in the repo that can actually change it.
"""

from __future__ import annotations

import pytest

MODULES = [
    ("skos.autopilot.config", "skharness.autocode.config"),
    ("skos.autopilot.ci", "skharness.autocode.ci"),
    ("skos.autopilot.claude_code", "skharness.autocode.claude_code"),
]


@pytest.mark.parametrize(("shim_name", "engine_name"), MODULES)
def test_shim_reexports_the_engine(shim_name: str, engine_name: str) -> None:
    """Public names on the shim are the ENGINE's objects, not copies of them."""
    import importlib

    shim = importlib.import_module(shim_name)
    engine = importlib.import_module(engine_name)

    public = [n for n in dir(engine) if not n.startswith("_")]
    assert public, f"{engine_name} exports nothing public; the contract is vacuous"

    missing = [n for n in public if not hasattr(shim, n)]
    assert not missing, f"{shim_name} does not re-export: {missing}"

    # EVERY public name is checked, not just callables.
    #
    # This originally asserted identity only for callables and types, and a
    # negative control caught that as useless: shadowing the shim's
    # DEFAULT_HARNESS with the stale literal "claude-code" left the test GREEN.
    # A bare constant is exactly what drifted and turned CI red, so restricting
    # the check to functions exempted the one case that had already happened.
    #
    # Identity where it is meaningful (functions, classes, modules), equality
    # otherwise, because small ints and short strings may be interned and
    # identity would then pass for the wrong reason.
    for name in public:
        engine_obj = getattr(engine, name)
        shim_obj = getattr(shim, name)
        if callable(engine_obj) or isinstance(engine_obj, type):
            assert (
                shim_obj is engine_obj
            ), f"{shim_name}.{name} is not {engine_name}.{name}; the shim has a copy, not a delegation"
        else:
            assert shim_obj == engine_obj, (
                f"{shim_name}.{name} shadows {engine_name}.{name} "
                f"({shim_obj!r} vs {engine_obj!r}); a shim must not hold its own value"
            )


def test_the_engine_owns_the_default_harness() -> None:
    """skos reads the default, it does not declare one.

    Regression guard for the literal that turned CI red: skos asserted
    "claude-code" while the engine had moved to "pi".
    """
    from pathlib import Path

    from skharness.autocode.config import DEFAULT_HARNESS
    from skos.autopilot.config import Config

    cfg = Config.load(Path("/nonexistent/skos-autocode-shim-test.yaml"))
    assert cfg.harness == DEFAULT_HARNESS
