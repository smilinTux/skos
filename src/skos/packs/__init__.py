"""skos.packs: the pluggable capability-pack installer.

A capability pack is a signed ``skworld.module.json`` (sk-standards schema v1.2)
whose ``install`` facet declares an ordered, typed, all-or-nothing install. The
first pack is ``skbrain`` (ITIL + CMDB + runbooks as one indivisible unit).

Layers:
  * :mod:`skos.packs.model`: parse the manifest install facet into typed steps.
  * :mod:`skos.packs.planner`: (OPS1.1, PURE) order + gate steps into a plan.
  * :mod:`skos.packs.provisioner`: (OPS1.2) execute a plan idempotently through
    an injected :mod:`skos.packs.effects` side-effect boundary; install / remove /
    status orchestration.
  * :mod:`skos.packs.loader`: resolve a built-in pack name to its manifest.
  * :mod:`skos.packs.state`: per-pack, per-step install state (packs.json).
"""

from skos.packs.loader import available, is_pack, load_pack, PackNotFound
from skos.packs.model import PackError, PackManifest, PackStep, Requires
from skos.packs.planner import GateResult, NodeFacts, PackPlan, PlannedStep, plan_pack
from skos.packs.provisioner import (
    ProvisionReport,
    install,
    install_pack,
    remove,
    remove_pack,
    status,
)

__all__ = [
    "available",
    "is_pack",
    "load_pack",
    "PackNotFound",
    "PackError",
    "PackManifest",
    "PackStep",
    "Requires",
    "GateResult",
    "NodeFacts",
    "PackPlan",
    "PlannedStep",
    "plan_pack",
    "ProvisionReport",
    "install",
    "install_pack",
    "remove",
    "remove_pack",
    "status",
]
