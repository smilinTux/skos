"""Renderer — the port every platform renderer conforms to.

A Renderer transforms an AppDescriptor into a platform-specific deployment
manifest (a string).  The ``platform`` class attribute is the canonical key
used by the RENDERERS registry in ``skos.render``.
"""
from __future__ import annotations

import abc

from skos.descriptor import AppDescriptor


class RenderError(ValueError):
    """Raised when a renderer cannot produce a valid manifest."""


class Renderer(abc.ABC):
    """Abstract base class for platform renderers.

    Subclasses must set ``platform`` and implement ``render``.
    """

    platform: str = ""

    @abc.abstractmethod
    def render(self, descriptor: AppDescriptor, profile: str = "local") -> str:
        """Render *descriptor* for *profile* and return the manifest string."""
