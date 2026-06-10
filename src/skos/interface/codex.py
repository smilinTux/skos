"""codex surface — PLANNED scaffold.

Conforms to the :class:`~skos.interface.base.Surface` port so it registers and
constructs like any other surface, but its read/write/list raise
:class:`~skos.interface.base.SurfaceNotImplementedError` (message contains
``planned``).  ``capabilities()`` reports ``planned=True``.

Planned target: bridge brain EntityNodes into the OpenAI Codex / codex-cli agent
workspace (AGENTS.md + repo context).  Implement read/write/list when the codex
host integration lands.
"""
from __future__ import annotations

from skos.brain.entity import EntityNode
from skos.interface.base import (
    Surface,
    SurfaceCapabilities,
    SurfaceNotImplementedError,
)


class CodexSurface(Surface):
    name = "codex"

    def read(self, node_id: str) -> EntityNode:
        raise SurfaceNotImplementedError(
            "codex surface is planned, not yet implemented."
        )

    def write(self, node: EntityNode) -> None:
        raise SurfaceNotImplementedError(
            "codex surface is planned, not yet implemented."
        )

    def list(self) -> list[str]:
        raise SurfaceNotImplementedError(
            "codex surface is planned, not yet implemented."
        )

    def capabilities(self) -> SurfaceCapabilities:
        return SurfaceCapabilities(
            name=self.name,
            readable=False,
            writable=False,
            listable=False,
            planned=True,
        )
