"""The side-effect boundary for pack provisioning (OPS1.2).

Every step the provisioner runs touches the world through an :class:`Effects`
object: DB migrations, role creation, git clones, seed subprocesses, fleet-store
writes, doctor registration. Isolating those behind one injected interface is
what makes the provisioner (dispatch + ordering + idempotency + coupling + state)
unit-testable WITHOUT a Postgres, a docker daemon, skvault, or the network: tests
inject a fake Effects and assert the dispatch. Production wires
:class:`DefaultEffects`, whose methods are real but GUARDED: they never run in
dry-run, and a missing tool or dependency returns a ``pending``/``failed``
:class:`StepResult` with a clear reason rather than raising.

Idempotency + fail-safety live in the effect methods: each is safe to re-run
(skip-if-present), and each returns a typed result rather than a bare exception,
so the provisioner can record per-step state and stop the (all-or-nothing) pack
install cleanly on the first failure.

sql_migration coordination (OPS1.3): the migration is applied THROUGH the
skmemory migrate runner (``skmemory pg migrate <script>``), which OPS1.3 is
building in parallel. This layer NEVER duplicates the ops DDL: it resolves the
package-qualified script path and hands it to the runner. Until the runner ships,
:meth:`DefaultEffects.migrate` falls back to a guarded raw ``docker exec ... psql
-v ON_ERROR_STOP=1`` invocation that is OFF by default (env
``SKBRAIN_ALLOW_PSQL_FALLBACK=1`` to arm it) and carries a TODO marker.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

#: A step ran to completion (or was already satisfied, idempotently).
DONE = "done"
#: A step is deferred (a not-yet-shipped seed command; a guarded, un-armed action).
PENDING = "pending"
#: A step failed; the provisioner stops the all-or-nothing install here.
FAILED = "failed"
#: A step was intentionally not run (e.g. every step of a blocked plan).
SKIPPED = "skipped"


@dataclass(frozen=True)
class StepResult:
    """The outcome of executing one step through an :class:`Effects` method.

    Attributes:
        status: One of :data:`DONE`, :data:`PENDING`, :data:`FAILED`, :data:`SKIPPED`.
        note: A human-readable explanation (what was done, skipped, or why it failed).
    """

    status: str
    note: str = ""


@runtime_checkable
class Effects(Protocol):
    """The injected side-effect boundary the provisioner dispatches to.

    Each method executes one install step kind idempotently and returns a
    :class:`StepResult`. ``remove_*`` methods reverse an installed step. All
    methods honor ``dry_run`` (describe, do not touch the world).
    """

    def migrate(self, params: Mapping[str, Any], pack_dir: Path, *, dry_run: bool) -> StepResult: ...

    def db_roles(self, params: Mapping[str, Any], *, dry_run: bool) -> StepResult: ...

    def content_repo(self, params: Mapping[str, Any], *, dry_run: bool) -> StepResult: ...

    def seed(self, params: Mapping[str, Any], *, dry_run: bool) -> StepResult: ...

    def fleet_objects(
        self, params: Mapping[str, Any], pack_dir: Path, *, dry_run: bool
    ) -> StepResult: ...

    def doctor(self, params: Mapping[str, Any], pack_id: str, *, dry_run: bool) -> StepResult: ...

    def emit_manifest(self, pack_id: str, pack_dir: Path, *, dry_run: bool) -> StepResult: ...

    # --- reverse (skos remove) -------------------------------------------------

    def remove_fleet_objects(self, params: Mapping[str, Any], *, dry_run: bool) -> StepResult: ...

    def remove_manifest(self, pack_id: str, *, dry_run: bool) -> StepResult: ...

    def purge_db(self, params: Mapping[str, Any], *, dry_run: bool) -> StepResult: ...


# ---------------------------------------------------------------------------
# The real, guarded implementation.
# ---------------------------------------------------------------------------


def _skcapstone_home() -> Path:
    env = os.environ.get("SKCAPSTONE_HOME", "").strip()
    return Path(env).expanduser() if env else Path.home() / ".skcapstone"


def _candidate_repo_dirs(package: str) -> list[Path]:
    """Where a sibling SK* package's repo checkout might live, best first."""
    env = os.environ.get(f"{package.upper()}_REPO", "").strip()
    roots = []
    if env:
        roots.append(Path(env).expanduser())
    home = Path.home()
    roots.append(home / "clawd" / "skcapstone-repos" / package)
    roots.append(home / "clawd" / package)
    return roots


def resolve_package_path(spec: str) -> Path | None:
    """Resolve a ``<package>:<repo-relative-path>`` script spec to a real file.

    Args:
        spec: e.g. ``skmemory:deploy/skmem-pg/03-ops-namespace.sql``.

    Returns:
        The first existing candidate path, or None when none resolve. A bare
        (non package-qualified) path is expanded and returned if it exists.
    """
    if ":" not in spec:
        p = Path(spec).expanduser()
        return p if p.exists() else None
    package, _, rel = spec.partition(":")
    for root in _candidate_repo_dirs(package):
        candidate = root / rel
        if candidate.exists():
            return candidate
    return None


@dataclass
class DefaultEffects:
    """Production side effects. Real, but guarded and never destructive by default.

    Attributes:
        pg_container: The skmem-pg container name for docker-exec fallbacks.
        pg_super_user: The superuser role migrations/role-grants run as.
        secret_scope: The skvault scope pack credentials live under.
        fleet_objects_dir: Where fleet object specs are written (defaults under
            the skcapstone home). Overridable for tests/nodes without sknoded.
        modules_dir: Where signed module manifests live (the shell/Atlas registry).
    """

    pg_container: str = "skmem-pg"
    pg_super_user: str = "postgres"
    secret_scope: str = "skbrain"
    fleet_objects_dir: Path | None = None
    modules_dir: Path | None = None

    def _fleet_dir(self) -> Path:
        return self.fleet_objects_dir or (_skcapstone_home() / "fleet" / "objects")

    def _modules_dir(self) -> Path:
        return self.modules_dir or (_skcapstone_home() / "shell" / "modules")

    # --- install steps ---------------------------------------------------------

    def migrate(self, params: Mapping[str, Any], pack_dir: Path, *, dry_run: bool) -> StepResult:
        script = str(params.get("script", ""))
        db = str(params.get("db", ""))
        pre_dump = bool(params.get("pre_dump"))
        script_path = resolve_package_path(script)
        script_name = script.partition(":")[2] or script  # basename form the runner accepts
        script_arg = str(script_path) if script_path else Path(script_name).name
        if dry_run:
            where = script_path or f"{Path(script_name).name} (resolved by runner)"
            return StepResult(DONE, f"would apply {where} to {db} (pre_dump={pre_dump})")

        # OPS1.3 coordination: apply THROUGH the shipped `skmemory pg migrate`
        # runner, which owns the guarded pre-dump + ON_ERROR_STOP apply + verify.
        # We never duplicate the ops DDL here: we hand it the script name/path.
        if shutil.which("skmemory"):
            cmd = ["skmemory", "pg", "migrate", script_arg]
            cmd.append("--pre-dump" if pre_dump else "--no-pre-dump")
            rc, out = _run(cmd)
            if rc != 0:
                return StepResult(FAILED, f"`skmemory pg migrate` failed: {out.strip()[:400]}")
            return StepResult(DONE, f"applied {Path(script_arg).name} via skmemory pg migrate")

        # Fallback while the runner is not on PATH: a guarded raw docker-exec psql,
        # OFF unless explicitly armed. TODO(OPS1.3): remove once the runner is a
        # declared dependency on every node.
        if script_path is None:
            return StepResult(FAILED, f"migration script not resolvable: {script}")
        if os.environ.get("SKBRAIN_ALLOW_PSQL_FALLBACK") != "1":
            return StepResult(
                PENDING,
                "skmemory migrate runner not on PATH; raw psql fallback disarmed "
                "(set SKBRAIN_ALLOW_PSQL_FALLBACK=1 to apply directly). "
                "TODO(OPS1.3): remove fallback once the runner is a declared dep.",
            )
        rc, out = _run(
            [
                "docker", "exec", "-i", self.pg_container, "psql",
                "-U", self.pg_super_user, "-v", "ON_ERROR_STOP=1", "-f", "-",
            ],
            stdin=script_path.read_text(encoding="utf-8"),
        )
        if rc != 0:
            return StepResult(FAILED, f"psql fallback failed: {out.strip()[:400]}")
        return StepResult(DONE, f"applied {script_path.name} via guarded psql fallback")

    def db_roles(self, params: Mapping[str, Any], *, dry_run: bool) -> StepResult:
        """Bind LOGIN roles + write the credential drop-in (closes the G2 gap).

        skos owns the credential side (pull/create passwords in skvault, write the
        ``~/.config/environment.d/skbrain.conf`` drop-in with the ``SKBRAIN_PG_*_PW``
        vars + projector/reader DSNs). The actual role SQL is delegated to the
        shipped OPS1.3 ``skmemory pg roles`` (which reads those env vars), so the
        CREATE/GRANT statements live in exactly one place.
        """
        logins: dict[str, str] = dict(params.get("logins", {}))
        vault_entries: dict[str, str] = dict(params.get("vault_entries", {}))
        env_drop_in = params.get("env_drop_in")
        if dry_run:
            binds = ", ".join(f"{login}->{group}" for login, group in logins.items())
            return StepResult(DONE, f"would create login roles ({binds}) from skvault")

        try:
            from skos import secrets as _secrets

            backend = _secrets.get_backend()
        except Exception as exc:  # noqa: BLE001
            return StepResult(FAILED, f"skvault backend unavailable: {exc}")

        pw_env: dict[str, str] = {}  # SKBRAIN_PG_*_PW env var name -> password
        dsns: dict[str, str] = {}
        for login, _group in logins.items():
            entry = vault_entries.get(login) or f"{login.upper()}_PW"
            try:
                password = backend.get(self.secret_scope, entry)
            except Exception:  # noqa: BLE001: create-on-first-install
                password = _gen_password()
                try:
                    backend.set(self.secret_scope, entry, password)
                except Exception as exc:  # noqa: BLE001
                    return StepResult(FAILED, f"could not persist skvault entry {entry}: {exc}")
            pw_env[entry] = password
            dsns[login] = f"postgresql://{login}:{password}@localhost:5432/skmemory"

        drop_note = ""
        if env_drop_in:
            drop_note = "; " + self._write_env_drop_in(str(env_drop_in), pw_env, dsns)

        # Delegate the CREATE ROLE / GRANT SQL to the shipped OPS1.3 runner.
        if shutil.which("skmemory"):
            env = {**os.environ, **pw_env}
            rc, out = _run(["skmemory", "pg", "roles"], env=env)
            if rc != 0:
                return StepResult(FAILED, f"`skmemory pg roles` failed: {out.strip()[:400]}")
            return StepResult(DONE, f"bound {len(logins)} login role(s) via skmemory pg roles{drop_note}")

        return StepResult(
            PENDING,
            f"credentials staged for {len(logins)} role(s){drop_note}; `skmemory pg roles` "
            "not on PATH to bind them yet",
        )

    def _write_env_drop_in(
        self, path_str: str, pw_env: Mapping[str, str], dsns: Mapping[str, str]
    ) -> str:
        path = Path(path_str).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# skbrain credentials + DSNs (written by `skos install skbrain`)"]
        # The SKBRAIN_PG_*_PW vars `skmemory pg roles` and the projector read.
        for var, pw in pw_env.items():
            lines.append(f"{var}={pw}")
        for login, dsn in dsns.items():
            lines.append(f"SKBRAIN_{login.upper()}_DSN={dsn}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
        return f"wrote credential drop-in {path}"

    def content_repo(self, params: Mapping[str, Any], *, dry_run: bool) -> StepResult:
        name = str(params.get("name", ""))
        dest = Path(str(params.get("dest", ""))).expanduser()
        marker = params.get("marker")
        remotes = list(params.get("remotes", []))
        syncthing = bool(params.get("syncthing"))
        if dry_run:
            return StepResult(DONE, f"would clone {name} to {dest} if absent")
        if dest.exists():
            if marker and not (dest / str(marker)).exists():
                return StepResult(
                    FAILED, f"{dest} exists but marker {marker} missing (wrong checkout?)"
                )
            note = f"{name} already present at {dest}"
            if syncthing:
                note += f"  [action: add {dest} to a Syncthing share]"
            return StepResult(DONE, note)
        if not shutil.which("git"):
            return StepResult(PENDING, "git not available; cannot clone content repo yet")
        clone_url = remotes[0] if remotes else name
        rc, out = _run(["git", "clone", clone_url, str(dest)])
        if rc != 0:
            return StepResult(FAILED, f"clone failed: {out.strip()[:300]}")
        if marker and not (dest / str(marker)).exists():
            return StepResult(FAILED, f"cloned but marker {marker} missing")
        note = f"cloned {name} to {dest}"
        if syncthing:
            note += f"  [action: add {dest} to a Syncthing share]"
        return StepResult(DONE, note)

    def seed(self, params: Mapping[str, Any], *, dry_run: bool) -> StepResult:
        cmd = [str(c) for c in params.get("cmd", [])]
        defer_ok = bool(params.get("defer_ok"))
        pretty = " ".join(cmd)
        if dry_run:
            return StepResult(DONE, f"would run seed `{pretty}`")
        if not cmd:
            return StepResult(FAILED, "seed step has an empty command")
        if shutil.which(cmd[0]) is None:
            if defer_ok:
                return StepResult(PENDING, f"seed command `{cmd[0]}` not on PATH yet (defer_ok)")
            return StepResult(FAILED, f"seed command `{cmd[0]}` not found")
        rc, out = _run(cmd)
        if rc != 0:
            return StepResult(FAILED, f"seed `{pretty}` failed: {out.strip()[:300]}")
        return StepResult(DONE, f"ran seed `{pretty}`")

    def fleet_objects(
        self, params: Mapping[str, Any], pack_dir: Path, *, dry_run: bool
    ) -> StepResult:
        objects = [str(o) for o in params.get("objects", [])]
        target_dir = self._fleet_dir()
        if dry_run:
            return StepResult(DONE, f"would write {len(objects)} fleet object(s) to {target_dir}")
        target_dir.mkdir(parents=True, exist_ok=True)
        written, unchanged = 0, 0
        for rel in objects:
            src = pack_dir / rel
            if not src.is_file():
                return StepResult(FAILED, f"fleet object template missing: {src}")
            dst = target_dir / Path(rel).name
            desired = src.read_text(encoding="utf-8")
            if dst.exists() and dst.read_text(encoding="utf-8") == desired:
                unchanged += 1
                continue
            dst.write_text(desired, encoding="utf-8")
            written += 1
        return StepResult(DONE, f"fleet objects: {written} written, {unchanged} unchanged")

    def doctor(self, params: Mapping[str, Any], pack_id: str, *, dry_run: bool) -> StepResult:
        checks = [str(c) for c in params.get("checks", [])]
        if dry_run:
            return StepResult(DONE, f"would register {len(checks)} doctor check(s)")
        reg_path = _skcapstone_home() / "doctor" / "pack-checks.json"
        reg_path.parent.mkdir(parents=True, exist_ok=True)
        import json

        registry = {}
        if reg_path.exists():
            try:
                registry = json.loads(reg_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                registry = {}
        registry[pack_id] = checks
        reg_path.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return StepResult(DONE, f"registered {len(checks)} doctor check(s) for {pack_id}")

    def emit_manifest(self, pack_id: str, pack_dir: Path, *, dry_run: bool) -> StepResult:
        """Emit the pack's manifest into the shell/Atlas modules dir.

        The signature is the trust gate for BOTH the shell and operator discovery
        (spec 2.3). capauth signing is attempted best-effort; when the signer is
        unavailable the manifest is still emitted (so the shell tab appears), but
        the note flags it unsigned: operator discovery (OPS0.3) will not load an
        unsigned manifest. Idempotent: identical bytes are a no-op.
        """
        src = pack_dir / "skworld.module.json"
        dst = self._modules_dir() / f"{pack_id}.skworld-module.json"
        if dry_run:
            return StepResult(DONE, f"would emit signed manifest to {dst}")
        if not src.is_file():
            return StepResult(FAILED, f"pack manifest missing: {src}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        payload = src.read_text(encoding="utf-8")
        signed_note = self._sign_note(payload)
        if dst.exists() and dst.read_text(encoding="utf-8") == payload:
            return StepResult(DONE, f"manifest already emitted at {dst}{signed_note}")
        dst.write_text(payload, encoding="utf-8")
        return StepResult(DONE, f"emitted manifest to {dst}{signed_note}")

    def _sign_note(self, payload: str) -> str:
        """Best-effort capauth signing status note (never fails the step)."""
        try:
            import capauth  # noqa: F401
        except Exception:  # noqa: BLE001
            return "  [UNSIGNED: capauth signer unavailable; discovery (OPS0.3) needs a signature]"
        return "  [signing via capauth is wired in OPS0.3; emitted unsigned for now]"

    # --- reverse steps ---------------------------------------------------------

    def remove_fleet_objects(self, params: Mapping[str, Any], *, dry_run: bool) -> StepResult:
        objects = [Path(str(o)).name for o in params.get("objects", [])]
        target_dir = self._fleet_dir()
        if dry_run:
            return StepResult(DONE, f"would delete {len(objects)} fleet object(s) from {target_dir}")
        removed = 0
        for name in objects:
            path = target_dir / name
            if path.exists():
                path.unlink()
                removed += 1
        return StepResult(DONE, f"deleted {removed} fleet object(s)")

    def remove_manifest(self, pack_id: str, *, dry_run: bool) -> StepResult:
        path = self._modules_dir() / f"{pack_id}.skworld-module.json"
        if dry_run:
            return StepResult(DONE, f"would remove signed manifest {path}")
        if path.exists():
            path.unlink()
            return StepResult(DONE, f"removed manifest {path}")
        return StepResult(DONE, f"manifest already absent ({path})")

    def purge_db(self, params: Mapping[str, Any], *, dry_run: bool) -> StepResult:
        # The documented rollback: DROP SCHEMA ops + ops_brain CASCADE. Guarded off
        # by default; only reached on `skos remove skbrain --purge-db`.
        if dry_run:
            return StepResult(DONE, "would DROP SCHEMA ops + ops_brain CASCADE (documented rollback)")
        if not shutil.which("docker"):
            return StepResult(FAILED, "docker not available; cannot purge ops schema")
        sql = "DROP SCHEMA IF EXISTS ops CASCADE;\nDROP SCHEMA IF EXISTS ops_brain CASCADE;\n"
        rc, out = _run(
            [
                "docker",
                "exec",
                "-i",
                self.pg_container,
                "psql",
                "-U",
                self.pg_super_user,
                "-v",
                "ON_ERROR_STOP=1",
                "-f",
                "-",
            ],
            stdin=sql,
        )
        if rc != 0:
            return StepResult(FAILED, f"purge failed: {out.strip()[:300]}")
        return StepResult(DONE, "dropped ops + ops_brain schemas")


def _run(
    cmd: list[str], *, stdin: str | None = None, env: Mapping[str, str] | None = None
) -> tuple[int, str]:
    """Run a subprocess, capturing merged stdout+stderr. Never raises on non-zero."""
    try:
        proc = subprocess.run(
            cmd,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=600,
            env=dict(env) if env is not None else None,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _gen_password(nbytes: int = 24) -> str:
    import secrets as _s

    return _s.token_urlsafe(nbytes)


__all__ = [
    "DONE",
    "PENDING",
    "FAILED",
    "SKIPPED",
    "StepResult",
    "Effects",
    "DefaultEffects",
    "resolve_package_path",
]
