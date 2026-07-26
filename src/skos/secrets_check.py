#!/usr/bin/env python3
"""skos.secrets_check: read-only provisioning + recovery status for the secrets
PLANE on a blank machine (card d65ff0ca).

A sibling card (f15d086d) added the cold-start empty-STORE guard (see
:mod:`skos.coldstart`). That guards the GTD data. THIS module is the layer
underneath it: the CREDENTIALS the guarded services need before skos can operate
at all. On a wiped node the store can be restored from Syncthing/skbackup, but
the secrets plane (the vault master key, the operator env file, the gog keyring
password, the capauth identity) has to be re-provisioned from escrow / skvault /
re-auth first. Nothing here restores replicates a secret: it only *reports* what
is present vs missing on THIS machine, and points at where each one comes from.

HARD RULE: this module never reads, returns, logs, or prints a secret VALUE. It
computes booleans (present / absent, real / still-placeholder) and discards the
value. `skos secrets check` is safe to run and paste anywhere.

See docs/runbooks/skos-secret-provisioning.md for the ordered recovery path.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from skos import secret_env

# Values that mean "the template placeholder is still here, this is NOT a real
# secret yet". Kept in sync with deploy/schedule/skos-schedule.env.example.
_PLACEHOLDERS = frozenset(
    {
        "",
        "replace-me",
        "you@example.com,you.other@example.com",
        "telegram:0",
        "0",
    }
)


@dataclass(frozen=True)
class SecretSpec:
    """A secret the secrets plane needs on a blank machine.

    ``required`` marks the skos-OWNED plane credentials (master key, operator env
    file, gog keyring password) whose absence should block provisioning. External
    credentials (gog OAuth tokens, capauth identity) are reported but not gating,
    because skos recovers them by re-auth/delegation, not from its own store.
    """

    name: str
    required: bool
    kind: str  # keyfile | env-file | env-value | external
    where: str  # human location on THIS machine
    source: str  # where recovery comes from (escrow / skvault / re-auth)


@dataclass
class SecretStatus:
    """The present/absent verdict for one :class:`SecretSpec`. No value ever."""

    name: str
    required: bool
    kind: str
    present: bool
    where: str
    source: str
    detail: str

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "required": self.required,
            "kind": self.kind,
            "present": self.present,
            "where": self.where,
            "source": self.source,
            "detail": self.detail,
        }


@dataclass
class SecretsReport:
    """Aggregate provisioning status for the machine. Carries no secret values."""

    statuses: list[SecretStatus] = field(default_factory=list)

    @property
    def missing_required(self) -> list[SecretStatus]:
        return [s for s in self.statuses if s.required and not s.present]

    @property
    def ok(self) -> bool:
        """True when every REQUIRED plane credential is present on this machine."""
        return not self.missing_required

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "missing_required": [s.name for s in self.missing_required],
            "secrets": [s.as_dict() for s in self.statuses],
        }


# ── probes: each returns (present: bool, detail: str); never a value ──────────


def _master_key_path() -> Path | None:
    """The vault-file master key path, or None if the data-root cannot resolve."""
    try:
        from skos import paths

        return paths.data_root() / "secrets" / "master.key"
    except Exception:  # noqa: BLE001 - unresolved profile/data-root is "unknown"
        return None


def _probe_master_key() -> tuple[bool, str]:
    if (os.environ.get("SKOS_VAULT_KEY") or "").strip():
        return True, "SKOS_VAULT_KEY is set in the environment"
    kp = _master_key_path()
    if kp is None:
        return False, "SK_DATA_ROOT/profile unresolved; cannot locate master.key"
    try:
        if kp.is_file() and kp.stat().st_size > 0:
            return True, f"present at {kp}"
    except OSError as exc:
        return False, f"cannot stat {kp}: {exc}"
    return False, f"absent at {kp}"


def _probe_env_file() -> tuple[bool, str]:
    path = secret_env._env_file_path()
    try:
        if path.is_file():
            return True, f"present at {path}"
    except OSError as exc:
        return False, f"cannot stat {path}: {exc}"
    return False, f"absent at {path} (scaffold with `skos secrets bootstrap`)"


def _probe_env_value(name: str) -> tuple[bool, str]:
    """Present iff resolvable AND not still a template placeholder. No value out."""
    secret_env._file_values.cache_clear()
    raw = secret_env.resolve(name)
    if raw is None:
        return False, "not set in env or the operator env file"
    if raw.strip() in _PLACEHOLDERS:
        return False, "still the template placeholder (fill from skvault/escrow)"
    return True, "set (value not shown)"


def _probe_gog_tokens() -> tuple[bool, str]:
    """Best-effort: does a gog config/keyring dir exist on this machine."""
    candidates = [
        os.environ.get("GOG_CONFIG_HOME", ""),
        str(Path.home() / ".config" / "gog"),
        str(Path.home() / ".gog"),
    ]
    for c in candidates:
        if c and Path(c).expanduser().exists():
            return True, f"gog config present at {c}"
    return False, "no gog config found; re-auth via the gmail-oauth skill"


def _probe_capauth() -> tuple[bool, str]:
    """capauth identity home (delegated to the capauth agent; vault-file is the
    working default backend today, so this is informational)."""
    candidates = [
        os.environ.get("CAPAUTH_HOME", ""),
        str(Path.home() / ".capauth"),
        str(Path.home() / ".skcapstone" / "capauth"),
    ]
    for c in candidates:
        if c and Path(c).expanduser().exists():
            return True, f"capauth home present at {c}"
    return False, "capauth identity absent; provision via capauth agent / skvault"


# ── the registry: what a blank machine needs, in recovery order ───────────────

SPECS: tuple[SecretSpec, ...] = (
    SecretSpec(
        name="master.key",
        required=True,
        kind="keyfile",
        where="$SKOS_VAULT_KEY env, else $SK_DATA_ROOT/secrets/master.key (mode 600)",
        source="offline escrow / skvault (seals the vault-file backend)",
    ),
    SecretSpec(
        name="schedule-env",
        required=True,
        kind="env-file",
        where="$SKOS_SCHEDULE_ENV, else ~/.skcapstone/skos-schedule.env (mode 600)",
        source="`skos secrets bootstrap` scaffolds it; fill from skvault/escrow",
    ),
    SecretSpec(
        name="GOG_KEYRING_PASSWORD",
        required=True,
        kind="env-value",
        where="the operator env file (unlocks the gog Gmail token keyring)",
        source="skvault (do not paste the old value back into git)",
    ),
    SecretSpec(
        name="SKMEM_PG_PASSWORD",
        required=False,
        kind="env-value",
        where="the operator env file (status corpus tile / skmem-pg)",
        source="skvault",
    ),
    SecretSpec(
        name="gog-tokens",
        required=False,
        kind="external",
        where="the gog file keyring (~/.config/gog), unlocked by GOG_KEYRING_PASSWORD",
        source="re-auth via the gmail-oauth skill (never duplicated into skos)",
    ),
    SecretSpec(
        name="capauth-identity",
        required=False,
        kind="external",
        where="the capauth agent home (PGP identity for the capauth backend)",
        source="capauth agent / skvault (vault-file is the working default today)",
    ),
)

_PROBES = {
    "master.key": _probe_master_key,
    "schedule-env": _probe_env_file,
    "gog-tokens": _probe_gog_tokens,
    "capauth-identity": _probe_capauth,
}


def check() -> SecretsReport:
    """Probe every required/optional secret on THIS machine (read-only, no values)."""
    statuses: list[SecretStatus] = []
    for spec in SPECS:
        probe = _PROBES.get(spec.name)
        if probe is not None:
            present, detail = probe()
        elif spec.kind == "env-value":
            present, detail = _probe_env_value(spec.name)
        else:  # defensive: unknown spec kind is reported as missing, never a value
            present, detail = False, "no probe registered"
        statuses.append(
            SecretStatus(
                name=spec.name,
                required=spec.required,
                kind=spec.kind,
                present=present,
                where=spec.where,
                source=spec.source,
                detail=detail,
            )
        )
    return SecretsReport(statuses=statuses)


# ── bootstrap: scaffold the operator env file (placeholders only, mode 600) ───

_SCAFFOLD_HEADER = (
    "# skos operator env file. Scaffolded by `skos secrets bootstrap`.\n"
    "# Fill each value from skvault / escrow, then chmod 600. NEVER commit this.\n"
    "# See docs/runbooks/skos-secret-provisioning.md for the recovery order.\n"
)


def env_scaffold() -> str:
    """Build the operator env-file scaffold from the registry. Placeholder values
    only (``replace-me``) - this function never emits a real secret."""
    lines = [_SCAFFOLD_HEADER]
    for spec in SPECS:
        if spec.kind != "env-value":
            continue
        lines.append(f"# {spec.name}: from {spec.source}")
        lines.append(f"{spec.name}=replace-me")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


@dataclass
class BootstrapResult:
    path: Path
    created: bool
    detail: str


def bootstrap(*, force: bool = False) -> BootstrapResult:
    """Create the operator env file with placeholder keys if it is missing.

    Writes mode-600, placeholder values only. Never clobbers an existing file
    unless ``force`` is set, and never writes a real secret. This is the safe
    "lay out the plane so the operator can fill it from escrow/skvault" seam.
    """
    path = secret_env._env_file_path()
    if path.exists() and not force:
        return BootstrapResult(path=path, created=False, detail="already exists (left untouched)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(env_scaffold(), encoding="utf-8")
    path.chmod(0o600)
    secret_env._file_values.cache_clear()
    return BootstrapResult(
        path=path,
        created=True,
        detail="scaffolded with placeholders; fill from skvault/escrow then chmod 600",
    )
