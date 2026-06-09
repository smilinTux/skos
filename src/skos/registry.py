"""Registry of installed apps, persisted under $SK_DATA_ROOT/registry/installed.json."""
from __future__ import annotations

import json

from skos import paths


def _file():
    paths.ensure_tree()
    return paths.subdir("registry") / "installed.json"


def list_installed() -> dict:
    f = _file()
    return json.loads(f.read_text()) if f.exists() else {}


def record(name: str, *, adapter: str, ref: str) -> None:
    items = list_installed()
    items[name] = {"adapter": adapter, "ref": ref}
    _file().write_text(json.dumps(items, indent=2))


def forget(name: str) -> None:
    items = list_installed()
    items.pop(name, None)
    _file().write_text(json.dumps(items, indent=2))
