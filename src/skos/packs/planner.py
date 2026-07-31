"""The pack planner (OPS1.1): a PURE, hands-off function that turns a parsed
capability-pack manifest into an ordered, validated, gated execution plan.

``plan_pack(manifest, facts)`` produces a :class:`PackPlan`: the steps in
canonical execution order, a :class:`GateResult` evaluating the manifest's
``requires`` against injected :class:`NodeFacts` (node capabilities + installed
package versions), and a human-readable dry-run description. It performs NO side
effects: the node facts are INJECTED, never probed, so the planner is total,
deterministic, and trivially testable. The provisioner (OPS1.2) is the layer
that actually touches the world.

Ordering resolves step dependencies: steps are stable-sorted by
:data:`skos.packs.model.KIND_ORDER` (schema -> roles -> content -> seeds ->
fleet objects -> doctor), breaking ties by declaration index. A structurally
impossible pack (db_roles with no sql_migration to grant against) is a
:class:`skos.packs.model.PackError` at plan time, not a runtime surprise.

Gating is advisory-to-the-provisioner: a blocked plan (unsatisfied ``requires``)
is still returned so callers can render exactly what is missing; the provisioner
refuses to execute a blocked plan (fail-safe), and the CLI prints the gate
reasons. Version constraints use PEP 440 (``packaging.specifiers``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from skos.packs.model import KIND_ORDER, PackError, PackManifest, PackStep, Requires


@dataclass(frozen=True)
class NodeFacts:
    """The injected facts the planner gates against (never probed here).

    Attributes:
        capabilities: The capability-catalog ids present on this node.
        packages: Map of installed package name to its version string.
    """

    capabilities: frozenset[str] = frozenset()
    packages: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class UnmetPackage:
    """A package whose installed version does not satisfy the required floor.

    Attributes:
        package: The package name.
        constraint: The required PEP 440 constraint (e.g. ``>=0.16``).
        found: The installed version, or None when the package is absent.
        reason: A human-readable explanation.
    """

    package: str
    constraint: str
    found: str | None
    reason: str


@dataclass(frozen=True)
class GateResult:
    """The evaluation of a manifest's ``requires`` against node facts.

    Attributes:
        satisfied: True when every required capability is present and every
            package constraint is met.
        missing_capabilities: Required capabilities absent on the node.
        unmet_packages: Package constraints the node does not satisfy.
    """

    satisfied: bool
    missing_capabilities: tuple[str, ...] = ()
    unmet_packages: tuple[UnmetPackage, ...] = ()

    def reasons(self) -> tuple[str, ...]:
        """Human-readable one-liners for every unmet requirement (empty when satisfied)."""
        out: list[str] = []
        for cap in self.missing_capabilities:
            out.append(f"missing capability: {cap}")
        for pkg in self.unmet_packages:
            out.append(pkg.reason)
        return tuple(out)


@dataclass(frozen=True)
class PlannedStep:
    """One step in the ordered execution plan.

    Attributes:
        order: The 0-based position in the resolved execution order.
        kind: The step kind (one of :data:`skos.packs.model.STEP_KINDS`).
        params: The step's declared parameters, verbatim.
        description: A one-line dry-run description of what execution would do.
    """

    order: int
    kind: str
    params: Mapping[str, object]
    description: str


@dataclass(frozen=True)
class PackPlan:
    """An ordered, gated, validated pack execution plan (no side effects).

    Attributes:
        pack_id: The pack id (e.g. ``skbrain``).
        gate: The requires-gate result.
        steps: The steps in canonical execution order.
    """

    pack_id: str
    gate: GateResult
    steps: tuple[PlannedStep, ...]

    @property
    def blocked(self) -> bool:
        """True when the requires gate is unsatisfied (the provisioner must refuse)."""
        return not self.gate.satisfied

    def dry_run(self) -> str:
        """Render a multi-line, human-readable dry-run description of the plan."""
        lines = [f"install plan: {self.pack_id}  ({len(self.steps)} steps)"]
        if self.blocked:
            lines.append("  BLOCKED: requires not satisfied:")
            for reason in self.gate.reasons():
                lines.append(f"    - {reason}")
        for step in self.steps:
            lines.append(f"  {step.order + 1}. [{step.kind}] {step.description}")
        return "\n".join(lines)


def plan_pack(manifest: PackManifest, facts: NodeFacts | None = None) -> PackPlan:
    """Resolve a capability-pack manifest into an ordered, gated execution plan.

    Args:
        manifest: The parsed pack manifest.
        facts: The node facts to gate ``requires`` against. When None, the gate
            is evaluated against an empty node (nothing present), which blocks any
            pack that declares requirements.

    Returns:
        The ordered, gated :class:`PackPlan`.

    Raises:
        PackError: the pack is structurally impossible (e.g. a db_roles step with
            no sql_migration to grant against).
    """
    facts = facts or NodeFacts()
    _validate_dependencies(manifest.steps)
    gate = _evaluate_requires(manifest.requires, facts)

    ordered = sorted(manifest.steps, key=lambda s: (KIND_ORDER[s.kind], s.index))
    steps = tuple(
        PlannedStep(
            order=position,
            kind=step.kind,
            params=step.params,
            description=_describe(step),
        )
        for position, step in enumerate(ordered)
    )
    return PackPlan(pack_id=manifest.id, gate=gate, steps=steps)


def _validate_dependencies(steps: tuple[PackStep, ...]) -> None:
    """Reject structurally impossible packs (db_roles with no sql_migration)."""
    kinds = {s.kind for s in steps}
    if "db_roles" in kinds and "sql_migration" not in kinds:
        raise PackError(
            "install plan has a db_roles step but no sql_migration step: the NOLOGIN "
            "group roles the logins bind to are created by the migration"
        )


def _evaluate_requires(requires: Requires, facts: NodeFacts) -> GateResult:
    """Evaluate a manifest's requires against the injected node facts."""
    missing_caps = tuple(c for c in requires.capabilities if c not in facts.capabilities)

    unmet: list[UnmetPackage] = []
    for package, constraint in requires.packages.items():
        found = facts.packages.get(package)
        if found is None:
            unmet.append(
                UnmetPackage(
                    package=package,
                    constraint=constraint,
                    found=None,
                    reason=f"package {package} is not installed (need {constraint})",
                )
            )
            continue
        try:
            spec = SpecifierSet(constraint)
        except InvalidSpecifier:
            unmet.append(
                UnmetPackage(
                    package=package,
                    constraint=constraint,
                    found=found,
                    reason=f"package {package} has an invalid version constraint {constraint!r}",
                )
            )
            continue
        try:
            installed = Version(found)
        except InvalidVersion:
            unmet.append(
                UnmetPackage(
                    package=package,
                    constraint=constraint,
                    found=found,
                    reason=f"package {package} has an unparseable installed version {found!r}",
                )
            )
            continue
        if installed not in spec:
            unmet.append(
                UnmetPackage(
                    package=package,
                    constraint=constraint,
                    found=found,
                    reason=f"package {package} {found} does not satisfy {constraint}",
                )
            )

    satisfied = not missing_caps and not unmet
    return GateResult(
        satisfied=satisfied,
        missing_capabilities=missing_caps,
        unmet_packages=tuple(unmet),
    )


def _describe(step: PackStep) -> str:
    """Build a one-line dry-run description for a step."""
    p = step.params
    if step.kind == "sql_migration":
        dump = "pre-dump + " if p.get("pre_dump") else ""
        verify = f", verify {p['verify']}" if p.get("verify") else ""
        return f"{dump}apply {p.get('script')} to {p.get('db')}{verify}"
    if step.kind == "db_roles":
        logins = p.get("logins", {})
        bindings = ", ".join(f"{login}->{group}" for login, group in dict(logins).items())
        return f"create login roles ({bindings}) from {p.get('password_source')}"
    if step.kind == "content_repo":
        marker = f", verify {p['marker']}" if p.get("marker") else ""
        return f"clone {p.get('name')} to {p.get('dest')} if absent{marker}"
    if step.kind == "seed":
        cmd = " ".join(str(c) for c in p.get("cmd", []))
        defer = " (defer_ok)" if p.get("defer_ok") else ""
        return f"run seed `{cmd}`{defer}"
    if step.kind == "fleet_objects":
        objects = p.get("objects", [])
        return f"write {len(objects)} fleet object(s): {', '.join(str(o) for o in objects)}"
    if step.kind == "doctor":
        checks = p.get("checks", [])
        return f"register {len(checks)} doctor check(s): {', '.join(str(c) for c in checks)}"
    return step.kind


__all__ = [
    "NodeFacts",
    "UnmetPackage",
    "GateResult",
    "PlannedStep",
    "PackPlan",
    "plan_pack",
]
