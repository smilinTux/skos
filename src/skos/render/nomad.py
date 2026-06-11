"""NomadRenderer — renders an AppDescriptor to a HashiCorp Nomad job spec (HCL).

Closes the gap where ``nomad`` was advertised as a render target (README +
ARCHITECTURE) but not wired into the RENDERERS registry. Output is deterministic
(sorted env keys, volumes in descriptor order) and uses the ``skos-<name>`` job
naming convention, matching the Compose/Kubernetes renderers.
"""
from __future__ import annotations

from skos.descriptor import AppDescriptor
from skos.render.base import RenderError, Renderer


class NomadRenderer(Renderer):
    """Render an AppDescriptor to a Nomad job spec (HCL) for the docker driver."""

    platform: str = "nomad"

    def render(self, descriptor: AppDescriptor, profile: str = "local") -> str:
        oci = descriptor.packaging.oci
        if oci is None:
            raise RenderError(
                f"NomadRenderer requires an OCI spec; {descriptor.name!r} has none."
            )

        name = descriptor.name
        out: list[str] = []
        out.append(f'job "skos-{name}" {{')
        out.append('  datacenters = ["dc1"]')
        out.append('  type        = "service"')
        out.append(f'  group "{name}" {{')
        out.append("    count = 1")

        if oci.ports:
            out.append("    network {")
            for p in oci.ports:
                out.append(f'      port "p{p}" {{ to = {p} }}')
            out.append("    }")

        for vol in descriptor.data:
            out.append(f'    volume "{name}-{vol}" {{')
            out.append('      type   = "host"')
            out.append(f'      source = "{name}-{vol}"')
            out.append("    }")

        out.append(f'    task "{name}" {{')
        out.append('      driver = "docker"')
        out.append("      config {")
        out.append(f'        image = "{oci.image}"')
        if oci.ports:
            ports_list = ", ".join(f'"p{p}"' for p in oci.ports)
            out.append(f"        ports = [{ports_list}]")
        out.append("      }")

        if oci.env:
            out.append("      env {")
            for k in sorted(oci.env):
                out.append(f'        {k} = "{oci.env[k]}"')
            out.append("      }")

        for vol in descriptor.data:
            out.append("      volume_mount {")
            out.append(f'        volume      = "{name}-{vol}"')
            out.append(f'        destination = "/data/{vol}"')
            out.append("      }")

        out.append("    }")   # task
        out.append("  }")     # group
        out.append("}")       # job
        return "\n".join(out) + "\n"
