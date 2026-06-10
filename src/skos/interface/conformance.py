"""Conformance for Surface adapters: write->read roundtrip, list, missing-raises.

Mirrors ``skos.secrets.conformance``.  Only call against *implemented* surfaces
(not the planned scaffolds, whose I/O raises by design).
"""
from __future__ import annotations

from skos.brain.entity import EntityNode, EntityType
from skos.interface.base import Surface, SurfaceError


def assert_surface_conforms(surface: Surface) -> None:
    node = EntityNode(
        id="conf-node",
        type=EntityType.knowledge,
        namespace="conf",
        summary="Conformance probe.",
        body="## Probe\n\nbody.\n",
    )
    surface.write(node)

    got = surface.read("conf-node")
    assert got.id == "conf-node", "write/read roundtrip failed (id)"
    assert got.summary == "Conformance probe.", "write/read roundtrip failed (summary)"

    assert "conf-node" in surface.list(), "list() missing the written node"

    caps = surface.capabilities()
    assert caps.name == surface.name, "capabilities().name must match surface.name"

    try:
        surface.read("definitely-not-present")
    except SurfaceError:
        return
    raise AssertionError("read() of a missing node should raise SurfaceError")
