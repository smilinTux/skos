"""The skbrain capability-pack model: a PURE parse of a v1.2 module manifest's
``install`` facet into typed step descriptors (OPS1.1).

A capability pack is a signed ``skworld.module.json`` (sk-standards schema v1.2)
whose optional ``install`` facet turns a first-class subapp manifest into a
pluggable, all-or-nothing install unit. This module models that facet only; it
performs NO side effects (no filesystem, DB, git, subprocess, or crypto). The
planner (:mod:`skos.packs.planner`) orders + gates these models into an
executable plan, and the provisioner (:mod:`skos.packs.provisioner`) runs it.

The step-kind vocabulary and required fields mirror the normative schema at
``sk-standards/reference/skworld-module/skworld.module.schema.json`` and the
skcapstone ``operator_seat/manifest_adapter.py`` builder, kept in sync by hand
across the two repos (skos does not import skcapstone). The five step kinds:

  * sql_migration : apply an idempotent SQL script to a named DB, pre-dump +
    verify.
  * db_roles      : create LOGIN roles bound to the NOLOGIN group roles,
    passwords from skvault.
  * content_repo  : clone the pack's canon content repo, idempotently.
  * seed          : run an idempotent seed command (``defer_ok`` tolerates a
    not-yet-shipped command).
  * fleet_objects : write per-node CronJob/service object specs into the fleet
    store.
  * doctor        : register the pack's doctor check family.

Coupling is by construction: the manifest carries no step-selection or
facet-selection input, so a pack is one indivisible unit (Chef: "install one,
get the other").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

#: The install-facet step kinds this model understands. Unknown kinds are a
#: :class:`PackError` at parse time.
STEP_KINDS: frozenset[str] = frozenset(
    {
        "sql_migration",
        "db_roles",
        "content_repo",
        "seed",
        "fleet_objects",
        "doctor",
    }
)

#: Canonical execution order by step kind. The planner stable-sorts steps by
#: (kind priority, declaration index) so the plan is deterministic and correct
#: even if a manifest lists steps out of order: schema before its role grants,
#: content + seeds after the schema, fleet objects after seeds, doctor last.
KIND_ORDER: dict[str, int] = {
    "sql_migration": 0,
    "db_roles": 1,
    "content_repo": 2,
    "seed": 3,
    "fleet_objects": 4,
    "doctor": 5,
}

#: The fields each step kind MUST carry to be well formed. Optional fields
#: (pre_dump, verify, vault_entries, env_drop_in, defer_ok, private, marker,
#: remotes, syncthing, db) are preserved verbatim in :attr:`PackStep.params`.
_STEP_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "sql_migration": ("db", "script"),
    "db_roles": ("logins", "password_source"),
    "content_repo": ("name", "dest"),
    "seed": ("cmd",),
    "fleet_objects": ("objects",),
    "doctor": ("checks",),
}

#: Manifest keys that may carry the schema version (shipped manifests emit
#: ``schemaVersion``; the spec sketch writes ``schema_version``: accept both).
_SCHEMA_VERSION_KEYS = ("schemaVersion", "schema_version")

#: The schema version an install/knowledge-bearing manifest must declare.
REQUIRED_SCHEMA_VERSION = "1.2"


class PackError(ValueError):
    """A manifest was too malformed to model as a capability pack.

    Raised by :meth:`PackManifest.from_dict` when a required facet or field is
    missing or the wrong type. PURE: it reflects a contract violation, never an
    I/O failure.
    """


@dataclass(frozen=True)
class Requires:
    """The install gate: node capabilities + package version floors.

    Attributes:
        capabilities: skos capability-catalog ids that must be present on the
            node (e.g. ``skmem-pg``).
        packages: Map of package name to a PEP 440 version constraint the node
            must satisfy (e.g. ``{"skcapstone": ">=0.16"}``).
    """

    capabilities: tuple[str, ...] = ()
    packages: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PackStep:
    """One typed install step from the manifest ``install`` facet.

    A PURE description of an install action, never its execution. ``params``
    carries the kind-specific fields verbatim (minus ``kind``), so the
    provisioner has everything declared without this layer inventing or dropping
    fields.

    Attributes:
        kind: One of :data:`STEP_KINDS`.
        params: The remaining step fields, exactly as declared in the manifest.
        index: The step's 0-based declaration order in the manifest (a stable
            tie-breaker for the planner's canonical sort).
    """

    kind: str
    params: Mapping[str, Any] = field(default_factory=dict)
    index: int = 0


@dataclass(frozen=True)
class PackManifest:
    """A parsed, contract-valid capability-pack manifest.

    Attributes:
        id: The pack id (e.g. ``skbrain``).
        schema_version: The declared manifest schema version (must be
            :data:`REQUIRED_SCHEMA_VERSION` when an ``install`` facet is present).
        requires: The install gate (capabilities + package version floors).
        steps: The typed install steps, in DECLARATION order (the planner
            re-orders canonically; this preserves what the author wrote).
        knowledge: The raw ``knowledge`` facet mapping, or None.
        signed: Whether the manifest carries a signature envelope. This models
            the signed-manifest CONTRACT only; real cryptographic verification is
            the provisioner's job, not this pure layer's.
        raw: The full parsed manifest mapping (for downstream consumers that need
            a field this model does not surface).
    """

    id: str
    schema_version: str
    requires: Requires
    steps: tuple[PackStep, ...]
    knowledge: Mapping[str, Any] | None
    signed: bool
    raw: Mapping[str, Any]

    @classmethod
    def from_dict(cls, data: Any) -> "PackManifest":
        """Parse and validate a manifest mapping into a PackManifest.

        Args:
            data: The parsed ``skworld.module.json`` mapping.

        Returns:
            The contract-valid pack manifest.

        Raises:
            PackError: the manifest is not a mapping, has no ``install`` facet,
                declares the wrong schema version, or a step is malformed.
        """
        if not isinstance(data, Mapping):
            raise PackError(f"manifest must be a mapping, got {type(data).__name__}")

        ident = data.get("id")
        if not isinstance(ident, str) or not ident:
            raise PackError(f"manifest requires a non-empty str 'id', got {ident!r}")

        schema_version = _schema_version(data)
        if schema_version is None:
            raise PackError("manifest requires a schema version (schemaVersion / schema_version)")

        install = data.get("install")
        if install is None:
            raise PackError(
                f"manifest {ident!r} has no 'install' facet; it is a plain subapp, not a "
                "capability pack"
            )
        if not isinstance(install, Mapping):
            raise PackError(f"'install' facet must be a mapping, got {type(install).__name__}")

        if str(schema_version) != REQUIRED_SCHEMA_VERSION:
            raise PackError(
                f"a manifest with an 'install' facet must declare schemaVersion "
                f"{REQUIRED_SCHEMA_VERSION!r}, got {schema_version!r}"
            )

        requires = _parse_requires(install.get("requires"))
        steps = _parse_steps(install.get("steps"))

        knowledge = data.get("knowledge")
        if knowledge is not None and not isinstance(knowledge, Mapping):
            raise PackError(
                f"'knowledge' facet must be a mapping when present, got {type(knowledge).__name__}"
            )

        return cls(
            id=ident,
            schema_version=str(schema_version),
            requires=requires,
            steps=steps,
            knowledge=knowledge,
            signed=bool(data.get("signature") or data.get("signed")),
            raw=dict(data),
        )


def _schema_version(manifest: Mapping[str, Any]) -> Any:
    for key in _SCHEMA_VERSION_KEYS:
        if key in manifest:
            return manifest[key]
    return None


def _parse_requires(raw: Any) -> Requires:
    if raw is None:
        return Requires()
    if not isinstance(raw, Mapping):
        raise PackError(f"'install.requires' must be a mapping, got {type(raw).__name__}")
    caps_raw = raw.get("capabilities", [])
    if not isinstance(caps_raw, list) or not all(isinstance(c, str) and c for c in caps_raw):
        raise PackError("'install.requires.capabilities' must be a list of non-empty str")
    packages_raw = raw.get("packages", {})
    if not isinstance(packages_raw, Mapping) or not all(
        isinstance(k, str) and isinstance(v, str) and k and v for k, v in packages_raw.items()
    ):
        raise PackError("'install.requires.packages' must be a map of package name to constraint")
    return Requires(capabilities=tuple(caps_raw), packages=dict(packages_raw))


def _parse_steps(raw_steps: Any) -> tuple[PackStep, ...]:
    if not isinstance(raw_steps, list) or not raw_steps:
        raise PackError("'install.steps' must be a non-empty list")
    steps: list[PackStep] = []
    for index, raw in enumerate(raw_steps):
        if not isinstance(raw, Mapping):
            raise PackError(f"install.steps[{index}] must be a mapping, got {type(raw).__name__}")
        kind = raw.get("kind")
        if kind not in STEP_KINDS:
            raise PackError(
                f"install.steps[{index}] has unknown kind {kind!r} "
                f"(expected one of {sorted(STEP_KINDS)})"
            )
        for required in _STEP_REQUIRED_FIELDS[kind]:
            if required not in raw:
                raise PackError(
                    f"install.steps[{index}] (kind {kind!r}) missing required field {required!r}"
                )
        params = {k: v for k, v in raw.items() if k != "kind"}
        steps.append(PackStep(kind=kind, params=params, index=index))
    return tuple(steps)


__all__ = [
    "STEP_KINDS",
    "KIND_ORDER",
    "REQUIRED_SCHEMA_VERSION",
    "PackError",
    "Requires",
    "PackStep",
    "PackManifest",
]
