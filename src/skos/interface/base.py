"""Surface — the runtime-adapter port for the Infinite Brain.

A *Surface* is a runtime adapter that lets a host tool (claude-code, codex,
obsidian, n8n, ...) read and write brain :class:`~skos.brain.entity.EntityNode`
objects through one common contract.  Surfaces sit above the brain
(``skos.brain``): obsidian reads/writes a markdown vault, claude-code is the
agent-facing canon surface, codex/n8n are planned scaffolds.

The contract::

    read(node_id)   -> EntityNode      # raises SurfaceError if missing
    write(node)     -> None            # create or overwrite
    list()          -> list[str]       # entity ids, sorted
    capabilities()  -> SurfaceCapabilities

Surfaces are :class:`~skos.adapter.Adapter` subclasses with
``capability = "surface"`` so they register through the shared
:class:`~skos.adapter.AdapterRegistry` like every other adapter family.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass

from skos.adapter import Adapter
from skos.brain.entity import EntityNode


class SurfaceError(RuntimeError):
    """Raised on surface I/O failures (missing node, unreadable host, ...)."""


class SurfaceNotImplementedError(SurfaceError):
    """Raised by *planned* scaffold surfaces that do not yet back a host.

    The message always contains the word ``planned`` so callers (and the CLI)
    can distinguish "this adapter exists but isn't wired yet" from a real error.
    """


@dataclass
class SurfaceCapabilities:
    """What a surface can do over its host.

    ``planned`` is True for scaffold surfaces (codex, n8n) that conform to the
    port but are not yet implemented; their read/write/list raise
    :class:`SurfaceNotImplementedError`.
    """
    name: str
    readable: bool = True
    writable: bool = True
    listable: bool = True
    planned: bool = False


class Surface(Adapter, abc.ABC):
    """The runtime-adapter port. Subclass, set ``name``, implement the contract."""

    capability = "surface"

    @abc.abstractmethod
    def read(self, node_id: str) -> EntityNode:
        """Read the entity node identified by *node_id*.

        Raises:
            SurfaceError: if no such node exists on this surface.
        """

    @abc.abstractmethod
    def write(self, node: EntityNode) -> None:
        """Create or overwrite *node* on this surface."""

    @abc.abstractmethod
    def list(self) -> list[str]:
        """Return the sorted list of entity ids visible on this surface."""

    @abc.abstractmethod
    def capabilities(self) -> SurfaceCapabilities:
        """Describe what this surface supports."""
