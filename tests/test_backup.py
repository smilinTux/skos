"""Tests for skbackup: consistent snapshot, restorable content, rotation keeps N,
off-box copy, verify, and GTD store lock respected (card 17660fbe / deploy 3c)."""
import json
import os
import tarfile
import threading
import time

import pytest

from skos import backup
from skos.gtd_ingest import GtdCapture, capture, gtd_dir


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolate every resolver at a throwaway location."""
    gdir = tmp_path / "gtd"
    ledger = tmp_path / "logs" / "cron-ledger.jsonl"
    registry = tmp_path / "models" / "registry.yaml"
    dest = tmp_path / "backups"
    ledger.parent.mkdir(parents=True)
    registry.parent.mkdir(parents=True)
    monkeypatch.setenv("SK_GTD_DIR", str(gdir))
    monkeypatch.setenv("SK_CRON_LEDGER", str(ledger))
    monkeypatch.setenv("SKMODELS_REGISTRY", str(registry))
    monkeypatch.setenv("SK_BACKUP_DIR", str(dest))
    return {"gtd": gdir, "ledger": ledger, "registry": registry, "dest": dest}


def _seed(env):
    """Populate the three sources with recognizable content."""
    capture(GtdCapture(text="pay the electric bill", source="manual",
                       source_ref="bill-1", status="next"))
    env["ledger"].write_text('{"job":"x","ok":true}\n')
    env["registry"].write_text("backends:\n  sk-default: ornith\n")


# ── snapshot + manifest ──────────────────────────────────────────────────────

def test_snapshot_creates_tarball_with_manifest(env):
    _seed(env)
    snap = backup.snapshot()
    assert snap.exists() and snap.name.startswith("skos-backup-") and snap.name.endswith(".tar.gz")
    man = backup.read_manifest(snap)
    labels = {s["label"]: s for s in man["sources"]}
    assert set(labels) == {"gtd", "cron-ledger", "model-registry"}
    assert all(v["present"] for v in labels.values())
    # every source contributed at least one archived file with a sha256
    for s in man["sources"]:
        assert s["files"] and all(f["sha256"] for f in s["files"])


def test_snapshot_leaves_no_tmp_files(env):
    _seed(env)
    backup.snapshot()
    leftovers = [p.name for p in env["dest"].iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_snapshot_tolerates_missing_source(env):
    # only the GTD store exists; ledger + registry absent
    capture(GtdCapture(text="a", source="manual", source_ref="r1"))
    env["ledger"].unlink(missing_ok=True)
    env["registry"].unlink(missing_ok=True)
    snap = backup.snapshot()
    man = backup.read_manifest(snap)
    present = {s["label"]: s["present"] for s in man["sources"]}
    assert present == {"gtd": True, "cron-ledger": False, "model-registry": False}
    assert backup.verify(snap)["ok"]


# ── restore / roundtrip ──────────────────────────────────────────────────────

def test_restore_roundtrip_matches_item_content(env, tmp_path):
    _seed(env)
    snap = backup.snapshot()
    original = (gtd_dir() / "next-actions.json").read_text()

    # simulate a corrupt/lost list, then restore from backup
    (gtd_dir() / "next-actions.json").write_text("[]")
    staging = tmp_path / "restore"
    restored = backup.restore(snap, staging)

    staged_next = staging / "gtd" / "next-actions.json"
    assert staged_next in restored
    assert staged_next.read_text() == original
    items = json.loads(staged_next.read_text())
    assert any(it["text"] == "pay the electric bill" for it in items)
    # ledger + registry also recover byte-for-byte
    assert (staging / "cron-ledger" / "cron-ledger.jsonl").read_text() == '{"job":"x","ok":true}\n'
    assert (staging / "model-registry" / "registry.yaml").read_text() == "backends:\n  sk-default: ornith\n"


def test_restore_skips_manifest_and_stays_in_target(env, tmp_path):
    _seed(env)
    snap = backup.snapshot()
    staging = tmp_path / "restore"
    restored = backup.restore(snap, staging)
    assert not (staging / "MANIFEST.json").exists()
    for p in restored:
        assert staging in p.parents


# ── rotation ─────────────────────────────────────────────────────────────────

def test_rotate_keeps_n_newest(env):
    _seed(env)
    snaps = []
    for _ in range(5):
        snaps.append(backup.snapshot())
        time.sleep(0.002)  # keep timestamp names distinct + ordered
    deleted = backup.rotate(keep=2)
    remaining = backup.list_snapshots()
    assert len(remaining) == 2
    assert len(deleted) == 3
    # the two newest survive
    assert [p.name for p in remaining] == [p.name for p in snaps[-2:]]
    # deleted are actually gone
    assert all(not p.exists() for p in deleted)


def test_rotate_noop_when_under_keep(env):
    _seed(env)
    backup.snapshot()
    assert backup.rotate(keep=7) == []


def test_rotate_never_wipes_all(env):
    _seed(env)
    backup.snapshot()
    backup.snapshot()
    backup.rotate(keep=0)          # clamped to 1
    assert len(backup.list_snapshots()) == 1


# ── verify catches corruption ────────────────────────────────────────────────

def test_verify_detects_tampering(env, tmp_path):
    _seed(env)
    snap = backup.snapshot()
    assert backup.verify(snap)["ok"]

    # rebuild the tar with one member's bytes altered -> sha256 mismatch
    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(snap, "r:gz") as src, tarfile.open(bad, "w:gz") as dst:
        for m in src.getmembers():
            f = src.extractfile(m)
            data = f.read() if f else b""
            if m.name.endswith("next-actions.json"):
                data = data + b"tampered"
                m.size = len(data)
            import io
            dst.addfile(m, io.BytesIO(data))
    res = backup.verify(bad)
    assert not res["ok"]
    assert any("mismatch" in e for e in res["errors"])


# ── off-box copy ─────────────────────────────────────────────────────────────

def test_copy_offbox_local_dir(env, tmp_path):
    _seed(env)
    snap = backup.snapshot()
    offbox = tmp_path / "offsite"
    backup.copy_offbox(snap, str(offbox))
    assert (offbox / snap.name).exists()
    assert (offbox / snap.name).read_bytes() == snap.read_bytes()


def test_run_backup_full_cycle(env, tmp_path):
    _seed(env)
    offbox = tmp_path / "offsite"
    for _ in range(3):
        backup.run_backup(keep=2, offbox=str(offbox))
        time.sleep(0.002)
    assert len(backup.list_snapshots()) == 2      # rotated
    assert len(list(offbox.iterdir())) == 3       # off-box keeps all copied
    latest = backup.list_snapshots()[-1]
    assert backup.verify(latest)["ok"]


# ── the store lock is respected ──────────────────────────────────────────────

def test_snapshot_blocks_on_held_store_lock(env):
    """A snapshot must wait for the GTD store lock: while another holder has the
    exclusive flock, snapshot() cannot proceed."""
    import fcntl

    _seed(env)
    lock_path = gtd_dir() / ".gtd.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)

    done = threading.Event()
    result = {}

    def _run():
        result["snap"] = backup.snapshot()
        done.set()

    t = threading.Thread(target=_run)
    t.start()
    try:
        # snapshot should be blocked on the lock: it must NOT finish while held
        assert not done.wait(timeout=0.4), "snapshot ignored the held store lock"
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    # once released, it completes
    assert done.wait(timeout=5)
    t.join()
    assert result["snap"].exists()


def test_snapshot_consistent_under_concurrent_writes(env):
    """Snapshots taken while writers hammer the store are always internally
    consistent: each captured next-actions.json parses as a JSON list."""
    _seed(env)
    stop = threading.Event()

    def _writer():
        i = 0
        while not stop.is_set():
            capture(GtdCapture(text=f"item {i}", source="load",
                               source_ref=f"load-{i}", status="next"))
            i += 1

    w = threading.Thread(target=_writer)
    w.start()
    try:
        for _ in range(8):
            snap = backup.snapshot()
            with tarfile.open(snap, "r:gz") as tar:
                member = tar.extractfile("payload/gtd/next-actions.json")
                data = json.loads(member.read().decode())  # must never be half-written
                assert isinstance(data, list)
    finally:
        stop.set()
        w.join()
