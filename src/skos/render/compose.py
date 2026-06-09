"""ComposeRenderer — renders an AppDescriptor to a Docker Compose YAML manifest.

Supports platform values ``"compose"`` (generic Compose v3) and
``"swarm"`` (Docker Swarm mode; identical schema, deploy key reserved for
future use).  Output is deterministic: keys are sorted, volumes are
generated in descriptor order.

Service naming follows the ``skos-<name>`` convention so containers can be
identified on the host without namespace collisions.
"""
from __future__ import annotations

from typing import Any

import yaml

from skos.descriptor import AppDescriptor
from skos.render.base import RenderError, Renderer


class ComposeRenderer(Renderer):
    """Render an AppDescriptor to a docker-compose YAML string.

    Both the ``"compose"`` and ``"swarm"`` platform aliases resolve to this
    renderer — swarm stacks use the same Compose schema.
    """

    platform: str = "compose"  # primary; "swarm" registered as alias in __init__

    def render(self, descriptor: AppDescriptor, profile: str = "local") -> str:
        """Return a docker-compose v3 YAML string for *descriptor*.

        Raises:
            RenderError: if the descriptor has no OCI packaging spec.
        """
        oci = descriptor.packaging.oci
        if oci is None:
            raise RenderError(
                f"ComposeRenderer requires an OCI spec; {descriptor.name!r} has none."
            )

        service_name = f"skos-{descriptor.name}"
        image = oci.image

        # Port mappings: host_port:container_port (identity mapping)
        ports: list[str] = [f"{p}:{p}" for p in oci.ports]

        # Environment variables from the OCI spec
        environment: dict[str, str] = dict(oci.env)

        # Named volumes: one per data entry, driver=local
        volumes_top: dict[str, Any] = {
            f"skos-{descriptor.name}-{vol}": {"driver": "local"}
            for vol in descriptor.data
        }

        # Volume mounts inside the service: /data/<vol_name>
        volume_mounts: list[str] = [
            f"skos-{descriptor.name}-{vol}:/data/{vol}"
            for vol in descriptor.data
        ]

        service: dict[str, Any] = {"image": image}
        if ports:
            service["ports"] = ports
        if environment:
            service["environment"] = environment
        if volume_mounts:
            service["volumes"] = volume_mounts

        compose: dict[str, Any] = {
            "version": "3.9",
            "services": {service_name: service},
        }
        if volumes_top:
            compose["volumes"] = volumes_top

        return yaml.dump(compose, default_flow_style=False, sort_keys=True)


class SwarmRenderer(ComposeRenderer):
    """Alias renderer for ``platform="swarm"``.

    Docker Swarm stacks use the same Compose schema; rke2/k3d users should
    prefer the KubernetesRenderer.  This renderer is intentionally thin —
    swarm-specific ``deploy:`` keys can be added in a future subsystem.
    """

    platform: str = "swarm"
