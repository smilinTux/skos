"""Topology profile resolution. Profile decides the data-root default and (later) adapters."""
from __future__ import annotations

import os
from enum import Enum
from pathlib import Path


class ProfileError(RuntimeError):
    pass


class Profile(str, Enum):
    LOCAL = "local"
    CLUSTER = "cluster"
    CLOUD = "cloud"


_DATA_ROOT_DEFAULTS = {
    Profile.LOCAL: Path.home() / "var" / "data" / "sk",
    Profile.CLUSTER: Path("/var/data/sk"),
    Profile.CLOUD: None,  # cloud supplies a PVC mount via explicit SK_DATA_ROOT
}


def active() -> Profile:
    raw = os.environ.get("SKOS_PROFILE", "local").strip().lower()
    try:
        return Profile(raw)
    except ValueError as exc:
        raise ProfileError(
            f"Invalid SKOS_PROFILE {raw!r}; expected one of {[p.value for p in Profile]}."
        ) from exc


def default_data_root() -> Path | None:
    return _DATA_ROOT_DEFAULTS[active()]
