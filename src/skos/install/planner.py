"""Installation planner — resolves adapters for a set of capabilities and an install profile.

plan(profile, capabilities) -> InstallPlan

  * capabilities=None  — use the profile's PERSONAL-FIRST recommended set
  * capabilities=[]    — explicit empty list raises PlanError
  * capabilities=[...] — explicit set (deduplicated, order-preserved)

Each capability is validated against the catalog before resolving.  The
resolver (skos.resolver.resolve) applies the standard precedence chain:
  profile-override > profile-default > catalog-default.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from skos.install.profiles import InstallProfile, recommended
from skos import resolver as _resolver
from skos.capability import Catalog, CapabilityError


class PlanError(RuntimeError):
    pass


class InstallStep(BaseModel):
    """One resolved (capability, adapter) step in the install plan."""
    capability: str
    adapter: str


class InstallPlan(BaseModel):
    """Ordered, resolved install plan for a topology profile."""
    profile: str
    steps: list[InstallStep] = field(default_factory=list)

    def capability_names(self) -> list[str]:
        return [s.capability for s in self.steps]


def plan(
    profile: InstallProfile,
    capabilities: list[str] | None,
) -> InstallPlan:
    """Resolve an ordered install plan.

    Parameters
    ----------
    profile:
        Target topology profile (LOCAL / CLUSTER / CLOUD).
    capabilities:
        Explicit list of capability names to include.  Pass *None* to use the
        profile's PERSONAL-FIRST recommended set.  An explicit empty list raises
        PlanError so callers don't silently get a no-op install.
    """
    if capabilities is not None and len(capabilities) == 0:
        raise PlanError("capabilities list is empty — nothing to plan.")

    # Determine the working capability list (dedup, preserve order)
    raw: list[str] = capabilities if capabilities is not None else recommended(profile)
    seen: set[str] = set()
    caps: list[str] = []
    for c in raw:
        if c not in seen:
            seen.add(c)
            caps.append(c)

    # Validate all requested capabilities exist in the catalog
    cat = Catalog.load()
    for cap in caps:
        try:
            cat.get(cap)
        except CapabilityError as exc:
            raise PlanError(f"Unknown capability {cap!r}: {exc}") from exc

    # Resolve an adapter for each capability
    steps: list[InstallStep] = []
    for cap in caps:
        adapter = _resolver.resolve(cap, profile=profile.value)
        steps.append(InstallStep(capability=cap, adapter=adapter))

    return InstallPlan(profile=profile.value, steps=steps)
