"""Installation profile definitions with PERSONAL-FIRST default capability sets.

Each profile maps to the minimal-but-complete set a smilinTux operator needs
at that topology tier.  Higher profiles are strict supersets of lower ones.

  local   — single machine / home lab (sovereign baseline)
  cluster — multi-node / homelab cluster (adds mesh, data, bus, CI)
  cloud   — full enterprise / multi-cloud (adds everything)
"""
from __future__ import annotations

from enum import Enum


class InstallProfile(str, Enum):
    LOCAL = "local"
    CLUSTER = "cluster"
    CLOUD = "cloud"


# ---------------------------------------------------------------------------
# PERSONAL-FIRST capability sets
# The sets are **additive**: cluster = local ∪ cluster_extras, etc.
# ---------------------------------------------------------------------------

_LOCAL_CAPS: list[str] = [
    # core — sovereign identity, secrets, memory, fence (ingress), observability
    "capauth",
    "skmemory",
    "skvault",
    "skfence",
    "skmon",
    # comms — chat is the primary day-to-day surface
    "skchat",
    # compute — local model inference (required for memory subsystem)
    "skmodel",
    # memory / data — skmemory depends on a data backend
    "skdata",
    # files — synced notes, configs
    "skfiles",
    # uptime visibility
    "skpulse",
    # backup — sovereign data durability
    "skbackup",
]

_CLUSTER_EXTRAS: list[str] = [
    # mesh connectivity between nodes
    "skmesh",
    # DNS for internal resolution
    "skdns",
    # object storage for blobs / artefacts
    "skobject",
    # cache / KV for hot data
    "skcache",
    # automation / workflow
    "skflow",
    # machine A2A event bus
    "skbus",
    # SSO / identity federation
    "sksso",
    # PKI for internal mTLS
    "skca",
    # CI/CD
    "skcicd",
]

_CLOUD_EXTRAS: list[str] = [
    # threat defense (heavier in multi-tenant cloud)
    "sksec",
    # web application firewall
    "skwaf",
    # comms — voice/video
    "skvoice",
    # comms — multi-channel transport
    "skcomms",
    # infra provisioning / IaC
    "skinfra",
    # decentralized web
    "skdweb",
]

# Build the full cumulative lists (no duplicates, ordered deterministically)
_CLUSTER_CAPS: list[str] = _LOCAL_CAPS + _CLUSTER_EXTRAS
_CLOUD_CAPS: list[str] = _CLUSTER_CAPS + _CLOUD_EXTRAS

PROFILE_CAPS: dict[InstallProfile, list[str]] = {
    InstallProfile.LOCAL:   _LOCAL_CAPS,
    InstallProfile.CLUSTER: _CLUSTER_CAPS,
    InstallProfile.CLOUD:   _CLOUD_CAPS,
}


def recommended(profile: InstallProfile) -> list[str]:
    """Return the ordered, deduplicated list of capability names for *profile*."""
    return list(PROFILE_CAPS[profile])
