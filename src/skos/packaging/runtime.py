"""Container runtime seam: podman (sovereign default) -> docker (interop). OCI image is the contract."""
from __future__ import annotations

import os
import shutil
import subprocess

PREFERENCE = ("podman", "docker")


class RuntimeError_(RuntimeError):
    pass


def detect() -> str:
    forced = os.environ.get("SKOS_RUNTIME", "").strip().lower()
    if forced:
        if shutil.which(forced):
            return forced
        raise RuntimeError_(f"SKOS_RUNTIME={forced!r} requested but not found on PATH.")
    for binary in PREFERENCE:
        if shutil.which(binary):
            return binary
    raise RuntimeError_("No container runtime found (looked for podman, docker).")


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([detect(), *args], capture_output=True, text=True, check=check)
