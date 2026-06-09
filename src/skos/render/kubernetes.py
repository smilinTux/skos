"""KubernetesRenderer — renders an AppDescriptor to a minimal but valid
Kubernetes Deployment + Service YAML multi-doc manifest.

Platform key: ``"kubernetes"``.

rke2 and k3d both consume standard Kubernetes manifests; point either
distribution's kubeconfig at the output of this renderer and apply with
``kubectl apply -f -``.  No distribution-specific annotations are added
here — they belong in an overlay layer (e.g. Kustomize/Helm).

Naming conventions:
- All resource names use the ``skos-<name>`` prefix.
- The Deployment selects Pods via ``app: skos-<name>``.
- The Service exposes every port from the OCI spec as a ClusterIP (default).
- A PersistentVolumeClaim is generated for each ``data:`` entry.
"""
from __future__ import annotations

from typing import Any

import yaml

from skos.descriptor import AppDescriptor
from skos.render.base import RenderError, Renderer

_API_APPS = "apps/v1"
_API_CORE = "v1"


def _labels(name: str) -> dict[str, str]:
    return {"app": name}


class KubernetesRenderer(Renderer):
    """Render an AppDescriptor to a multi-doc Kubernetes YAML string.

    The output contains:
    - A ``Deployment`` (one replica by default).
    - A ``Service`` (ClusterIP) exposing every OCI port.
    - A ``PersistentVolumeClaim`` for each ``data:`` entry (1Gi, ReadWriteOnce).

    rke2 and k3d both consume standard Kubernetes manifests — no
    distribution-specific annotations are emitted.

    Raises:
        RenderError: if the descriptor has no OCI packaging spec.
    """

    platform: str = "kubernetes"

    def render(self, descriptor: AppDescriptor, profile: str = "local") -> str:
        """Return a multi-doc YAML string (``---`` separated) for *descriptor*."""
        oci = descriptor.packaging.oci
        if oci is None:
            raise RenderError(
                f"KubernetesRenderer requires an OCI spec; {descriptor.name!r} has none."
            )

        k8s_name = f"skos-{descriptor.name}"
        labels = _labels(k8s_name)
        image = oci.image
        ports = oci.ports

        docs: list[Any] = []

        # --- PersistentVolumeClaims (one per data entry) ---
        pvc_names: list[str] = []
        for vol in descriptor.data:
            pvc_name = f"{k8s_name}-{vol}"
            pvc_names.append((vol, pvc_name))
            docs.append({
                "apiVersion": _API_CORE,
                "kind": "PersistentVolumeClaim",
                "metadata": {"name": pvc_name, "labels": labels},
                "spec": {
                    "accessModes": ["ReadWriteOnce"],
                    "resources": {"requests": {"storage": "1Gi"}},
                },
            })

        # --- Deployment ---
        container: dict[str, Any] = {
            "name": k8s_name,
            "image": image,
        }
        if ports:
            container["ports"] = [{"containerPort": p} for p in ports]
        if oci.env:
            container["env"] = [
                {"name": k, "value": v} for k, v in sorted(oci.env.items())
            ]
        if pvc_names:
            container["volumeMounts"] = [
                {"mountPath": f"/data/{vol}", "name": vol}
                for vol, _ in pvc_names
            ]

        pod_spec: dict[str, Any] = {"containers": [container]}
        if pvc_names:
            pod_spec["volumes"] = [
                {
                    "name": vol,
                    "persistentVolumeClaim": {"claimName": pvc_name},
                }
                for vol, pvc_name in pvc_names
            ]

        deployment: dict[str, Any] = {
            "apiVersion": _API_APPS,
            "kind": "Deployment",
            "metadata": {"name": k8s_name, "labels": labels},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": labels},
                "template": {
                    "metadata": {"labels": labels},
                    "spec": pod_spec,
                },
            },
        }
        docs.append(deployment)

        # --- Service (ClusterIP) ---
        if ports:
            service: dict[str, Any] = {
                "apiVersion": _API_CORE,
                "kind": "Service",
                "metadata": {"name": k8s_name, "labels": labels},
                "spec": {
                    "selector": labels,
                    "ports": [
                        {"port": p, "targetPort": p, "protocol": "TCP"}
                        for p in ports
                    ],
                    "type": "ClusterIP",
                },
            }
            docs.append(service)

        return yaml.dump_all(docs, default_flow_style=False, sort_keys=True)
