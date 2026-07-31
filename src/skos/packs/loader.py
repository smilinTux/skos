"""Resolve a pack NAME (e.g. ``skbrain``) to its parsed manifest + assets dir.

Built-in packs ship as declarative assets under ``skos/packs/<id>/`` (the spec's
"the pack is a DECLARATIVE unit ... living in skos/src/skos/packs/skbrain/").
Each pack dir holds its signed ``skworld.module.json`` and any pack-relative
assets the install steps reference (fleet object templates, etc.).
"""

from __future__ import annotations

import json
from pathlib import Path

from skos.packs.model import PackError, PackManifest

#: The directory that holds built-in pack asset dirs (this module's own dir).
PACKS_ROOT = Path(__file__).resolve().parent

#: The manifest filename inside a pack dir.
MANIFEST_NAME = "skworld.module.json"


class PackNotFound(LookupError):
    """No built-in pack asset dir exists for the requested name."""


def pack_dir(pack_id: str) -> Path:
    """Return the asset dir for a built-in pack, or raise :class:`PackNotFound`."""
    d = PACKS_ROOT / pack_id
    if not (d / MANIFEST_NAME).is_file():
        raise PackNotFound(f"no built-in pack {pack_id!r} (looked in {d})")
    return d


def available() -> list[str]:
    """List the built-in pack ids (dirs that contain a manifest)."""
    return sorted(
        child.name
        for child in PACKS_ROOT.iterdir()
        if child.is_dir() and (child / MANIFEST_NAME).is_file()
    )


def is_pack(name: str) -> bool:
    """True when ``name`` resolves to a built-in pack asset dir."""
    return (PACKS_ROOT / name / MANIFEST_NAME).is_file()


def load_manifest_dict(pack_id: str) -> dict:
    """Read and JSON-parse a built-in pack's manifest (unvalidated)."""
    d = pack_dir(pack_id)
    return json.loads((d / MANIFEST_NAME).read_text(encoding="utf-8"))


def load_pack(pack_id: str) -> tuple[PackManifest, Path]:
    """Resolve a pack name to its validated :class:`PackManifest` and asset dir.

    Args:
        pack_id: The built-in pack id (e.g. ``skbrain``).

    Returns:
        A ``(manifest, pack_dir)`` tuple.

    Raises:
        PackNotFound: no built-in pack dir for that name.
        PackError: the manifest is malformed.
    """
    d = pack_dir(pack_id)
    raw = json.loads((d / MANIFEST_NAME).read_text(encoding="utf-8"))
    manifest = PackManifest.from_dict(raw)
    if manifest.id != pack_id:
        raise PackError(
            f"pack dir {pack_id!r} holds a manifest whose id is {manifest.id!r} (mismatch)"
        )
    return manifest, d


__all__ = [
    "PACKS_ROOT",
    "MANIFEST_NAME",
    "PackNotFound",
    "pack_dir",
    "available",
    "is_pack",
    "load_manifest_dict",
    "load_pack",
]
