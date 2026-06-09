"""Provisioner — apply an InstallPlan to the local data-root.

apply(plan, data_root=None) -> ProvisionResult

  For each step in the plan:
  1. Ensure the data-root directory tree exists (skos.paths.ensure_tree).
  2. Record the resolved intent into the registry (skos.registry.record).
  3. For adapters that are *implemented packaging targets* (i.e. OCI),
     the full materialize path can be triggered; currently guarded behind
     SKOS_MATERIALIZE=1 so tests never need a running container daemon.
  4. All other adapters are marked "planned / coming-soon" with a note.

The provisioner deliberately does NOT raise on "planned" steps — a partial
install that records intent is the correct initial state.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, model_validator

from skos.install.planner import InstallPlan
from skos import paths as _paths
from skos import registry as _registry

# Adapters that have a real packaging materialize implementation in #1.
# Others are "planned" (intent recorded, daemon not invoked).
_IMPLEMENTED_PACKAGING: frozenset[str] = frozenset({"oci"})


StatusLiteral = Literal["recorded", "planned", "error"]


class StepOutcome(BaseModel):
    """Result of provisioning one step."""
    capability: str
    adapter: str
    status: StatusLiteral
    note: str = ""


class ProvisionResult(BaseModel):
    """Aggregate result of applying an InstallPlan."""
    outcomes: list[StepOutcome] = []

    @property
    def success(self) -> bool:
        """True unless any step has status='error'."""
        return all(o.status != "error" for o in self.outcomes)

    @property
    def recorded_count(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "recorded")

    @property
    def planned_count(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "planned")


def apply(
    install_plan: InstallPlan,
    *,
    data_root: Path | None = None,
) -> ProvisionResult:
    """Apply an InstallPlan: ensure tree, then record each step's intent.

    Parameters
    ----------
    install_plan:
        The resolved plan from skos.install.planner.plan().
    data_root:
        Override $SK_DATA_ROOT for testing.  Normally left as None so the
        active environment variable controls the path.
    """
    if data_root is not None:
        # Temporarily push data_root into env so paths.ensure_tree() uses it
        _old = os.environ.get("SK_DATA_ROOT")
        os.environ["SK_DATA_ROOT"] = str(data_root)

    try:
        _paths.ensure_tree()
        outcomes = _provision_steps(install_plan)
    finally:
        if data_root is not None:
            if _old is None:
                os.environ.pop("SK_DATA_ROOT", None)
            else:
                os.environ["SK_DATA_ROOT"] = _old

    return ProvisionResult(outcomes=outcomes)


def _provision_steps(install_plan: InstallPlan) -> list[StepOutcome]:
    outcomes: list[StepOutcome] = []

    for step in install_plan.steps:
        outcome = _provision_one(step.capability, step.adapter)
        outcomes.append(outcome)

    return outcomes


def _provision_one(capability: str, adapter: str) -> StepOutcome:
    """Record intent for a single (capability, adapter) step.

    If the adapter is an implemented packaging target *and* SKOS_MATERIALIZE=1
    is set, attempt full OCI materialization.  Otherwise record as planned.
    """
    materialize = (
        adapter in _IMPLEMENTED_PACKAGING
        and os.environ.get("SKOS_MATERIALIZE", "0") == "1"
    )

    if materialize:
        # Full OCI path — only reached when SKOS_MATERIALIZE=1 (never in tests)
        try:
            from skos.packaging.oci import OciAdapter
            from skos.descriptor import AppDescriptor, Packaging, OciSpec
            # Build a minimal descriptor for the capability
            oci_spec = OciSpec(image=f"ghcr.io/smilinTux/{capability}:latest", ports=[])
            pkg_spec = Packaging(oci=oci_spec)
            desc = AppDescriptor(name=capability, capability=capability, packaging=pkg_spec)
            result = OciAdapter().materialize(desc)
            _registry.record(capability, adapter=adapter, ref=result.ref)
            return StepOutcome(
                capability=capability,
                adapter=adapter,
                status="recorded",
                note=f"materialized {result.ref}",
            )
        except Exception as exc:  # noqa: BLE001
            return StepOutcome(
                capability=capability,
                adapter=adapter,
                status="error",
                note=str(exc),
            )
    else:
        # Record intent — adapter may not be an OCI image or SKOS_MATERIALIZE is off
        ref = f"intent:{adapter}"
        _registry.record(capability, adapter=adapter, ref=ref)
        if adapter in _IMPLEMENTED_PACKAGING:
            note = f"adapter={adapter} ready; set SKOS_MATERIALIZE=1 to materialize"
        else:
            note = f"adapter={adapter!r} is coming-soon; intent recorded"
        return StepOutcome(
            capability=capability,
            adapter=adapter,
            status="planned" if adapter not in _IMPLEMENTED_PACKAGING else "recorded",
            note=note,
        )
