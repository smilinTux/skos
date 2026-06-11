"""skos.render — platform renderer registry.

Usage::

    from skos.render import get_renderer
    renderer = get_renderer("swarm")
    manifest = renderer.render(descriptor)

Supported platform keys
-----------------------
``"compose"``    — Docker Compose v3 YAML (generic).
``"swarm"``      — Docker Swarm stack (same schema as compose).
``"kubernetes"`` — Kubernetes Deployment + Service multi-doc YAML.
                   Compatible with rke2 and k3d — see KubernetesRenderer.
``"nomad"``      — HashiCorp Nomad job spec (HCL, docker driver).
"""
from __future__ import annotations

from skos.render.base import Renderer, RenderError
from skos.render.compose import ComposeRenderer, SwarmRenderer
from skos.render.kubernetes import KubernetesRenderer
from skos.render.nomad import NomadRenderer

#: Registry mapping platform key -> Renderer instance.
RENDERERS: dict[str, Renderer] = {
    "compose": ComposeRenderer(),
    "swarm": SwarmRenderer(),
    "kubernetes": KubernetesRenderer(),
    "nomad": NomadRenderer(),
}


def get_renderer(platform: str) -> Renderer:
    """Return the :class:`Renderer` for *platform*.

    Raises:
        KeyError: if *platform* is not registered.
    """
    try:
        return RENDERERS[platform]
    except KeyError:
        supported = ", ".join(sorted(RENDERERS))
        raise KeyError(
            f"Unknown platform {platform!r}. Supported: {supported}."
        ) from None


__all__ = [
    "Renderer",
    "RenderError",
    "ComposeRenderer",
    "SwarmRenderer",
    "KubernetesRenderer",
    "NomadRenderer",
    "RENDERERS",
    "get_renderer",
]
