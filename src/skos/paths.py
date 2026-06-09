"""Single source of path truth for skos. Nothing else joins data-root literals."""
from __future__ import annotations

import os
from pathlib import Path

TREE = ("apps", "src", "data", "secrets", "config", "state", "cache", "registry")


class DataRootError(RuntimeError):
    pass


def data_root() -> Path:
    """Resolve $SK_DATA_ROOT (explicit env wins, else the active profile's default)."""
    env = os.environ.get("SK_DATA_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    from skos.profile import default_data_root  # local import avoids cycle
    root = default_data_root()
    if root is None:
        raise DataRootError(
            "SK_DATA_ROOT is unset and no profile default applies. "
            "Set SK_DATA_ROOT or SKOS_PROFILE (local|cluster|cloud)."
        )
    return root.expanduser().resolve()


def subdir(name: str) -> Path:
    if name not in TREE:
        raise DataRootError(f"Unknown skos subdir {name!r}; expected one of {TREE}.")
    return data_root() / name


def ensure_tree() -> Path:
    root = data_root()
    for name in TREE:
        (root / name).mkdir(parents=True, exist_ok=True)
    return root
