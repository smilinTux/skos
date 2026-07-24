"""skbackup: point-in-time backups for the unified GTD store, the cron ledger,
and the model registry (card 17660fbe; deploy-plan step 3c).

Syncthing is *replication*, not backup: a corrupt or deleted file propagates
fleet-wide. This module takes a consistent point-in-time **snapshot** of the
durable skos state, independent of Syncthing, with retention rotation and an
optional off-box copy (per the redundancy mantra: the backup must not live only
on the box it protects).

What is covered
    - the unified GTD store directory (``skos.gtd_ingest.gtd_dir()``)
    - the cron run-ledger (``$SK_CRON_LEDGER`` or ``~/.skcapstone/logs/cron-ledger.jsonl``)
    - the model registry (``skos.models.registry_path()``)

Consistency
    The snapshot is taken while holding the *same* advisory store lock every GTD
    writer takes (``skos.gtd_ingest._store_lock`` over ``<gtd_dir>/.gtd.lock``),
    so a snapshot can never capture a half-written GTD list.

Format
    Each snapshot is a single ``.tar.gz`` named
    ``skos-backup-<UTC-timestamp>.tar.gz`` with a ``MANIFEST.json`` at the root
    describing every archived source (kind, original path, per-file sha256).
    The tar is written to a temp file and ``os.replace``-d into place, so a crash
    never leaves a partial snapshot at the final name.

Restore is deliberately *staged* (extract into a scratch directory, never over
the live paths) so a drill can diff before the operator copies anything back.
See ``docs/runbooks/skbackup-restore.md``.

CLI: ``skos backup run|list|verify|restore``.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .gtd_ingest import _store_lock as _gtd_store_lock, gtd_dir

# ── snapshot naming ──────────────────────────────────────────────────────────
_PREFIX = "skos-backup-"
_SUFFIX = ".tar.gz"
_TS_FMT = "%Y%m%dT%H%M%S%fZ"          # microseconds keep names unique + sortable
_PAYLOAD = "payload"                   # tar root dir under which sources live


# ── source resolution ────────────────────────────────────────────────────────
def cron_ledger_path() -> Path:
    """Resolve the cron run-ledger: ``$SK_CRON_LEDGER`` > default. Matches the
    path ``scripts/sk-cron-run.sh`` writes to."""
    env = os.environ.get("SK_CRON_LEDGER")
    if env:
        return Path(env).expanduser()
    home = Path(os.environ.get("SKCAPSTONE_HOME", str(Path.home() / ".skcapstone")))
    return home / "logs" / "cron-ledger.jsonl"


def model_registry_path() -> Path:
    """Resolve the model registry via skos.models (``$SKMODELS_REGISTRY`` > default)."""
    from .models import registry_path
    return registry_path()


@dataclass(frozen=True)
class Source:
    """One thing to back up. ``label`` is the stable arc-root inside the tar."""
    label: str
    path: Path
    kind: str  # "dir" | "file"


def default_sources() -> list[Source]:
    """The three durable skos artifacts the deploy plan names, in a fixed order.

    Resolution is dynamic (honors ``SK_GTD_DIR``/``SK_CRON_LEDGER``/
    ``SKMODELS_REGISTRY``) so tests and cold hosts both work."""
    return [
        Source("gtd", gtd_dir(), "dir"),
        Source("cron-ledger", cron_ledger_path(), "file"),
        Source("model-registry", model_registry_path(), "file"),
    ]


def default_dest() -> Path:
    """Default local retention dir: ``$SK_BACKUP_DIR`` > ``<SKCAPSTONE_HOME>/backups/skos``."""
    env = os.environ.get("SK_BACKUP_DIR")
    if env:
        return Path(env).expanduser()
    home = Path(os.environ.get("SKCAPSTONE_HOME", str(Path.home() / ".skcapstone")))
    return home / "backups" / "skos"


# ── snapshot ─────────────────────────────────────────────────────────────────
def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _add_source(tar: tarfile.TarFile, src: Source) -> dict:
    """Add one source's bytes to the tar under ``payload/<label>/`` and return
    its manifest entry (with per-file sha256). A missing source is recorded as
    ``present: false`` and contributes no bytes: a cold host with no registry
    yet still produces a valid snapshot."""
    entry: dict = {
        "label": src.label,
        "kind": src.kind,
        "original_path": str(src.path),
        "present": src.path.exists(),
        "files": [],
    }
    if not src.path.exists():
        return entry
    arc_root = f"{_PAYLOAD}/{src.label}"
    if src.kind == "file":
        arc = f"{arc_root}/{src.path.name}"
        tar.add(str(src.path), arcname=arc)
        entry["files"].append({"arc": arc, "rel": src.path.name,
                               "sha256": _sha256(src.path)})
    else:  # dir: walk deterministically, files only (dirs implied by paths)
        for p in sorted(src.path.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(src.path).as_posix()
            arc = f"{arc_root}/{rel}"
            tar.add(str(p), arcname=arc)
            entry["files"].append({"arc": arc, "rel": rel, "sha256": _sha256(p)})
    return entry


def snapshot(dest_dir: str | os.PathLike | None = None,
             sources: list[Source] | None = None) -> Path:
    """Take a consistent point-in-time snapshot into ``dest_dir`` and return its
    path. Holds the GTD store lock across the whole read so no GTD list can be
    captured mid-write. The tar is built in a temp file and atomically renamed
    into place."""
    dest = Path(dest_dir).expanduser() if dest_dir is not None else default_dest()
    dest.mkdir(parents=True, exist_ok=True)
    srcs = sources if sources is not None else default_sources()

    created = datetime.now(timezone.utc)
    name = f"{_PREFIX}{created.strftime(_TS_FMT)}{_SUFFIX}"
    final = dest / name

    manifest: dict = {
        "tool": "skos.backup",
        "version": __version__,
        "created_at": created.isoformat(),
        "host": socket.gethostname(),
        "sources": [],
    }

    fd, tmp = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=str(dest))
    os.close(fd)
    try:
        # The GTD lock guarantees no writer is mid load-modify-save while we read.
        with _gtd_store_lock():
            with tarfile.open(tmp, "w:gz") as tar:
                for src in srcs:
                    manifest["sources"].append(_add_source(tar, src))
                man_bytes = json.dumps(manifest, indent=2).encode("utf-8")
                info = tarfile.TarInfo("MANIFEST.json")
                info.size = len(man_bytes)
                info.mtime = int(created.timestamp())
                import io
                tar.addfile(info, io.BytesIO(man_bytes))
        # fsync the temp file, then atomically publish it.
        f = os.open(tmp, os.O_RDONLY)
        try:
            os.fsync(f)
        finally:
            os.close(f)
        os.replace(tmp, str(final))
        dfd = os.open(str(dest), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return final


# ── retention ────────────────────────────────────────────────────────────────
def list_snapshots(dest_dir: str | os.PathLike | None = None) -> list[Path]:
    """All snapshots in ``dest_dir``, oldest first (timestamp name sorts lexically)."""
    dest = Path(dest_dir).expanduser() if dest_dir is not None else default_dest()
    if not dest.is_dir():
        return []
    snaps = [p for p in dest.iterdir()
             if p.is_file() and p.name.startswith(_PREFIX) and p.name.endswith(_SUFFIX)]
    return sorted(snaps, key=lambda p: p.name)


def rotate(dest_dir: str | os.PathLike | None = None, keep: int = 7) -> list[Path]:
    """Keep the ``keep`` newest snapshots, delete the rest. Returns deleted paths.
    ``keep <= 0`` is treated as ``keep=1`` (never wipe every backup by accident)."""
    keep = max(1, keep)
    snaps = list_snapshots(dest_dir)
    if len(snaps) <= keep:
        return []
    doomed = snaps[:len(snaps) - keep]
    for p in doomed:
        p.unlink()
    return doomed


# ── off-box copy ─────────────────────────────────────────────────────────────
def copy_offbox(snapshot_path: str | os.PathLike, target: str) -> None:
    """Copy a snapshot to an off-box target (the whole point: a backup on the
    same host is not a backup). ``target`` with a ``host:path`` form is rsync'd
    over ssh; otherwise it is treated as a local/mounted directory and copied.
    Raises on failure so the caller (and sk-cron-run) sees it."""
    snapshot_path = Path(snapshot_path)
    if ":" in target and not Path(target).drive:
        # remote host:path -> rsync over ssh (idempotent, resumable)
        subprocess.run(
            ["rsync", "-a", "--mkpath", str(snapshot_path), target],
            check=True,
        )
    else:
        tdir = Path(target).expanduser()
        tdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(snapshot_path), str(tdir / snapshot_path.name))


# ── verify ───────────────────────────────────────────────────────────────────
def read_manifest(snapshot_path: str | os.PathLike) -> dict:
    """Return the MANIFEST.json embedded in a snapshot."""
    with tarfile.open(str(snapshot_path), "r:gz") as tar:
        f = tar.extractfile("MANIFEST.json")
        if f is None:
            raise ValueError("snapshot has no MANIFEST.json")
        return json.loads(f.read().decode("utf-8"))


def verify(snapshot_path: str | os.PathLike) -> dict:
    """Verify a snapshot end to end: the tar is readable, every manifested member
    is present, and every sha256 matches. Returns
    ``{"ok": bool, "checked": int, "errors": [...]}``."""
    errors: list[str] = []
    checked = 0
    with tarfile.open(str(snapshot_path), "r:gz") as tar:
        try:
            man_f = tar.extractfile("MANIFEST.json")
            manifest = json.loads(man_f.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "checked": 0, "errors": [f"manifest unreadable: {e}"]}
        for src in manifest.get("sources", []):
            for fmeta in src.get("files", []):
                arc = fmeta["arc"]
                try:
                    member = tar.extractfile(arc)
                    if member is None:
                        errors.append(f"missing member: {arc}")
                        continue
                    h = hashlib.sha256(member.read()).hexdigest()
                    checked += 1
                    if h != fmeta["sha256"]:
                        errors.append(f"sha256 mismatch: {arc}")
                except KeyError:
                    errors.append(f"missing member: {arc}")
    return {"ok": not errors, "checked": checked, "errors": errors}


# ── restore ──────────────────────────────────────────────────────────────────
def restore(snapshot_path: str | os.PathLike,
            target_dir: str | os.PathLike) -> list[Path]:
    """Extract a snapshot's payload into ``target_dir`` (a *staging* directory,
    never the live paths). Layout mirrors the tar: ``<target>/<label>/...``.
    Returns the restored file paths. The operator diffs the staged tree against
    live, then copies back per the runbook."""
    target = Path(target_dir).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    restored: list[Path] = []
    with tarfile.open(str(snapshot_path), "r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            if not member.name.startswith(f"{_PAYLOAD}/"):
                continue  # skip MANIFEST.json and anything outside payload/
            rel = member.name[len(_PAYLOAD) + 1:]
            out = target / rel
            if os.path.relpath(out.resolve(), target.resolve()).startswith(".."):
                raise ValueError(f"unsafe member path: {member.name}")  # pragma: no cover
            out.parent.mkdir(parents=True, exist_ok=True)
            src_f = tar.extractfile(member)
            with open(out, "wb") as dst:
                shutil.copyfileobj(src_f, dst)
            restored.append(out)
    return restored


# ── orchestration ────────────────────────────────────────────────────────────
@dataclass
class BackupResult:
    snapshot: Path
    rotated: list[Path] = field(default_factory=list)
    offbox: str | None = None
    offbox_ok: bool = False
    verify: dict = field(default_factory=dict)


def run_backup(dest_dir: str | os.PathLike | None = None,
               keep: int = 7,
               offbox: str | None = None) -> BackupResult:
    """Full backup cycle: snapshot, self-verify, rotate to ``keep``, and (if an
    off-box target is given) copy off-box. This is what the scheduled unit calls."""
    snap = snapshot(dest_dir)
    result = BackupResult(snapshot=snap, verify=verify(snap))
    if offbox:
        result.offbox = offbox
        copy_offbox(snap, offbox)   # raises on failure -> surfaced by sk-cron-run
        result.offbox_ok = True
    result.rotated = rotate(dest_dir, keep)
    return result
