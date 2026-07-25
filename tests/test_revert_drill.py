"""Revert drill (card 681514a5): prove a change applied to durable skos state
can be rolled back to the exact pre-change known-good tree, and exercise it.

The drill and every test here operate ONLY on a scratch dir under tmp_path;
they never touch live state."""
import pytest

from skos import backup, revert_drill


# ── the drill: apply a change, revert, return to baseline byte-for-byte ───────

def test_drill_reverts_to_baseline_byte_for_byte(tmp_path):
    res = revert_drill.run_drill(tmp_path / "scratch")
    assert res.ok, f"drill failed: mismatches={res.mismatches} unexpected={res.unexpected}"
    assert res.mismatches == []
    assert res.unexpected == []
    assert res.baseline_files >= 1
    # the revert restored the deleted file and removed the added file
    assert "config.yaml" in res.reverted["restored"]
    assert any(r.endswith("new_adapter.py") for r in res.reverted["removed"])


def test_drill_target_is_scratch_only(tmp_path):
    """The drill must confine every write under the scratch dir it was given."""
    scratch = tmp_path / "scratch"
    revert_drill.run_drill(scratch)
    # everything the drill created lives under the scratch dir, nowhere else
    assert scratch.exists()
    # tmp_path holds only the scratch tree (no stray live-path writes)
    assert [p.name for p in tmp_path.iterdir()] == ["scratch"]


# ── revert_target primitive: in-place return to snapshot tree ────────────────

def test_revert_target_restores_edit_add_delete(tmp_path):
    target = tmp_path / "t"
    target.mkdir()
    (target / "a.txt").write_text("original-a")
    (target / "keep.txt").write_text("keep")
    baseline = {p.relative_to(target).as_posix(): p.read_bytes()
                for p in target.rglob("*") if p.is_file()}

    snap = backup.snapshot(tmp_path / "snaps",
                           sources=[backup.Source("t", target, "dir")])

    # a change edits a.txt, adds b.txt, deletes keep.txt
    (target / "a.txt").write_text("EDITED")
    (target / "b.txt").write_text("added")
    (target / "keep.txt").unlink()

    out = revert_target_call(snap, target)
    now = {p.relative_to(target).as_posix(): p.read_bytes()
           for p in target.rglob("*") if p.is_file()}
    assert now == baseline
    assert "b.txt" in out["removed"]
    assert "a.txt" in out["restored"] and "keep.txt" in out["restored"]


def revert_target_call(snap, target):
    return revert_drill.revert_target(snap, "t", target)


# ── negative: revert with no snapshot / wrong label is a clear error ─────────

def test_revert_missing_label_raises_clear_error(tmp_path):
    target = tmp_path / "t"
    target.mkdir()
    (target / "a.txt").write_text("x")
    snap = backup.snapshot(tmp_path / "snaps",
                           sources=[backup.Source("t", target, "dir")])
    with pytest.raises(ValueError, match="no source labeled"):
        revert_drill.revert_target(snap, "does-not-exist", target)


def test_revert_missing_snapshot_file_raises(tmp_path):
    target = tmp_path / "t"
    target.mkdir()
    missing = tmp_path / "nope.tar.gz"
    with pytest.raises((FileNotFoundError, ValueError)):
        revert_drill.revert_target(missing, "t", target)


def test_revert_missing_target_raises(tmp_path):
    target = tmp_path / "t"
    target.mkdir()
    (target / "a.txt").write_text("x")
    snap = backup.snapshot(tmp_path / "snaps",
                           sources=[backup.Source("t", target, "dir")])
    with pytest.raises(ValueError, match="does not exist"):
        revert_drill.revert_target(snap, "t", tmp_path / "absent")
