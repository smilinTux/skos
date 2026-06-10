"""n8n surface — PLANNED scaffold.

Conforms to the :class:`~skos.interface.base.Surface` port so it registers and
constructs like any other surface, but its read/write/list raise
:class:`~skos.interface.base.SurfaceNotImplementedError` (message contains
``planned``).  ``capabilities()`` reports ``planned=True``.

Planned target: project ``workflow`` EntityNodes to/from the n8n automation
engine (REST API + workflow JSON).  Implement read/write/list when the n8n host
integration lands.
"""
from __future__ import annotations

from skos.brain.entity import EntityNode
from skos.interface.base import (
    Surface,
    SurfaceCapabilities,
    SurfaceNotImplementedError,
)


class N8nSurface(Surface):
    name = "n8n"

    def read(self, node_id: str) -> EntityNode:
        raise SurfaceNotImplementedError(
            "n8n surface is planned, not yet implemented."
        )

    def write(self, node: EntityNode) -> None:
        raise SurfaceNotImplementedError(
            "n8n surface is planned, not yet implemented."
        )

    def list(self) -> list[str]:
        raise SurfaceNotImplementedError(
            "n8n surface is planned, not yet implemented."
        )

    def capabilities(self) -> SurfaceCapabilities:
        return SurfaceCapabilities(
            name=self.name,
            readable=False,
            writable=False,
            listable=False,
            planned=True,
        )
