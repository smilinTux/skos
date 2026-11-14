"""skos keeps ``skos.brain.ops`` as a shim over the ``skbrain`` framework.

The engine tests moved to the skbrain repo with the engine. What stays here is
what only skos can check: the shim resolves to the framework modules, and the
signed pack manifest still matches what the framework's doctor implements.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

pytest.importorskip("skbrain", reason="skbrain (the brain extra) is not installed")

from skos.packs.loader import load_manifest_dict

MODULES = [
    "chunker",
    "cli",
    "cmdb_drift",
    "doctor",
    "embed",
    "kedb_coverage",
    "models",
    "parser",
    "postgres",
    "read_api",
    "secrets",
    "writer",
]


@pytest.mark.parametrize("name", MODULES)
def test_shim_module_is_the_framework_module(name: str):
    shim = importlib.import_module(f"skos.brain.ops.{name}")
    real = importlib.import_module(f"skbrain.{name}")
    assert shim is real


def test_shim_package_reexports_framework_names():
    import skbrain
    import skos.brain.ops as shim

    for name in skbrain.__all__:
        assert getattr(shim, name) is getattr(skbrain, name)


def test_manifest_retriever_ref_resolves_through_the_shim():
    ref = load_manifest_dict("skbrain")["knowledge"]["retriever"]
    mod_name, _, attr = ref.partition(":")
    assert hasattr(importlib.import_module(mod_name), attr)


def test_doctor_implements_every_manifest_declared_check(tmp_path: Path):
    from skos.brain.ops.doctor import run_checks

    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            self.current = self.rows.pop(0)

        def fetchone(self):
            return self.current[0] if self.current else None

    class Connection:
        def __init__(self, rows):
            self.cur = Cursor(rows)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def cursor(self):
            return self.cur

    declared = {
        check
        for step in load_manifest_dict("skbrain")["install"]["steps"]
        if step.get("kind") == "doctor"
        for check in step.get("checks", [])
    }
    assert declared
    (tmp_path / "pages").mkdir()
    conn = Connection(
        [[("ops.wiki_nodes", "ops.wiki_chunks", "ops.links")], [(True, True)], [(3, 60)]]
    )
    implemented = {
        c.name
        for c in run_checks(
            canon=tmp_path,
            reader_dsn="redacted",
            connect=lambda dsn: conn,
            kedb_loader=lambda home: [],
        )
    }
    assert not (declared - implemented)
