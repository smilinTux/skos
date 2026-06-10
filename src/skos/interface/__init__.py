"""skos.interface — runtime-adapter / surfaces layer over the Infinite Brain.

A *Surface* lets a host tool read/write brain EntityNodes through one common
port (see :mod:`skos.interface.base`).  Surfaces register through the shared
:class:`~skos.adapter.AdapterRegistry` (capability ``"surface"``).

Usage::

    from skos.interface import get_surface
    vault = get_surface("obsidian", vault_root="~/clawd/vault")
    node = vault.read("agent-lumina")

Surface targets
---------------
``obsidian``     — markdown-vault read/write (full).
``claude-code``  — agent-facing canon surface (full).
``codex``        — PLANNED scaffold (raises SurfaceNotImplementedError).
``n8n``          — PLANNED scaffold (raises SurfaceNotImplementedError).
"""
from __future__ import annotations

from skos.adapter import AdapterRegistry
from skos.interface.base import (
    Surface,
    SurfaceCapabilities,
    SurfaceError,
    SurfaceNotImplementedError,
)
from skos.interface.obsidian import ObsidianSurface
from skos.interface.claude_code import ClaudeCodeSurface
from skos.interface.codex import CodexSurface
from skos.interface.n8n import N8nSurface

REGISTRY = AdapterRegistry()
REGISTRY.register(ObsidianSurface)
REGISTRY.register(ClaudeCodeSurface)
REGISTRY.register(CodexSurface)
REGISTRY.register(N8nSurface)

#: name -> Surface subclass, for ergonomic iteration / introspection.
SURFACES: dict[str, type[Surface]] = {
    ObsidianSurface.name: ObsidianSurface,
    ClaudeCodeSurface.name: ClaudeCodeSurface,
    CodexSurface.name: CodexSurface,
    N8nSurface.name: N8nSurface,
}


def get_surface(name: str, **kwargs) -> Surface:
    """Return an instantiated :class:`Surface` for *name*.

    Extra keyword args are forwarded to the surface constructor
    (e.g. ``vault_root=`` for obsidian, ``wiki_root=`` for claude-code).

    Raises:
        AdapterError: if *name* is not a registered surface.
    """
    cls = REGISTRY.lookup("surface", name)  # raises AdapterError if unknown
    return cls(**kwargs)  # type: ignore[abstract]


__all__ = [
    "Surface",
    "SurfaceCapabilities",
    "SurfaceError",
    "SurfaceNotImplementedError",
    "ObsidianSurface",
    "ClaudeCodeSurface",
    "CodexSurface",
    "N8nSurface",
    "REGISTRY",
    "SURFACES",
    "get_surface",
]
