"""Tests for skos.brain.ops.kedb_coverage — KedbCanonCovered (card 8c6e05e3).

Hermetic: the authoritative KEDB loader is always injected, so nothing here
touches a real ``~/.skcapstone`` or requires the optional skcapstone/skcoord
sibling to be installed.
"""

from __future__ import annotations

from pathlib import Path

from skos.brain.ops.kedb_coverage import compute_kedb_coverage


def _write_known_error(root: Path, slug: str, kedb_ref: str | None) -> None:
    ref_line = f"  kedb: {kedb_ref}\n" if kedb_ref is not None else ""
    (root / "known-errors").mkdir(parents=True, exist_ok=True)
    (root / "known-errors" / f"{slug}.md").write_text(
        "---\n"
        f"id: {slug}\n"
        "type: known-error\n"
        f"title: {slug}\n"
        "state_refs:\n"
        f"{ref_line}"
        "---\n\nbody\n",
        encoding="utf-8",
    )


def _write_runbook(root: Path, slug: str) -> None:
    (root / "runbooks").mkdir(parents=True, exist_ok=True)
    (root / "runbooks" / f"{slug}.md").write_text(
        f"---\nid: {slug}\ntype: runbook\ntitle: {slug}\n---\n\nbody\n", encoding="utf-8"
    )


def _loader(ids: list[str]):
    return lambda home: ids


def test_full_bidirectional_coverage_is_true(tmp_path):
    canon = tmp_path / "canon"
    canon.mkdir()
    _write_known_error(canon, "ke-a", "ke-a")
    _write_known_error(canon, "ke-b", "ke-b")

    result = compute_kedb_coverage(canon, kedb_loader=_loader(["ke-a", "ke-b"]))

    assert result.status == "True"
    assert result.missing_from_canon == ()
    assert result.dangling_canon_refs == ()
    assert result.authoritative_count == 2
    assert "2/2" in result.reason


def test_authoritative_entry_missing_from_canon_is_false_and_enumerable(tmp_path):
    canon = tmp_path / "canon"
    canon.mkdir()
    _write_known_error(canon, "ke-a", "ke-a")

    result = compute_kedb_coverage(canon, kedb_loader=_loader(["ke-a", "ke-orphan"]))

    assert result.status == "False"
    assert result.missing_from_canon == ("ke-orphan",)
    assert result.dangling_canon_refs == ()
    assert "ke-orphan" in result.reason


def test_dangling_canon_ref_is_false_and_enumerable(tmp_path):
    canon = tmp_path / "canon"
    canon.mkdir()
    _write_known_error(canon, "ke-stale", "ke-does-not-exist")

    result = compute_kedb_coverage(canon, kedb_loader=_loader(["ke-a"]))

    assert result.status == "False"
    assert result.dangling_canon_refs == ("ke-stale",)
    assert "ke-a" in result.missing_from_canon
    assert "ke-stale" in result.reason


def test_canon_known_error_with_no_kedb_ref_is_dangling(tmp_path):
    canon = tmp_path / "canon"
    canon.mkdir()
    _write_known_error(canon, "ke-unlinked", None)

    result = compute_kedb_coverage(canon, kedb_loader=_loader([]))

    assert result.status == "False"
    assert result.dangling_canon_refs == ("ke-unlinked",)


def test_non_known_error_pages_are_ignored(tmp_path):
    canon = tmp_path / "canon"
    canon.mkdir()
    _write_known_error(canon, "ke-a", "ke-a")
    _write_runbook(canon, "runbook-x")

    result = compute_kedb_coverage(canon, kedb_loader=_loader(["ke-a"]))

    assert result.status == "True"
    assert result.canon_count == 1


def test_empty_authoritative_fold_is_vacuously_true(tmp_path):
    canon = tmp_path / "canon"
    canon.mkdir()

    result = compute_kedb_coverage(canon, kedb_loader=_loader([]))

    assert result.status == "True"
    assert result.authoritative_count == 0
    assert "0/0" in result.reason


def test_missing_sibling_package_is_unknown_and_says_so(tmp_path):
    def loader(home):
        raise ModuleNotFoundError("No module named 'skcapstone'", name="skcapstone")

    result = compute_kedb_coverage(tmp_path, kedb_loader=loader)

    assert result.status == "Unknown"
    assert "skcapstone" in result.reason
    assert "not installed" in result.reason


def test_uninitialized_itil_directory_is_unknown_and_says_so(tmp_path):
    def loader(home):
        raise FileNotFoundError(str(home / "coordination" / "itil" / "kedb"))

    result = compute_kedb_coverage(tmp_path, kedb_loader=loader)

    assert result.status == "Unknown"
    assert "not been initialized" in result.reason


def test_permission_denied_reading_fold_is_unknown_and_says_so(tmp_path):
    def loader(home):
        raise PermissionError("denied")

    result = compute_kedb_coverage(tmp_path, kedb_loader=loader)

    assert result.status == "Unknown"
    assert "PermissionError" in result.reason


def test_missing_canon_with_reachable_fold_is_false_not_unknown(tmp_path):
    result = compute_kedb_coverage(tmp_path / "nonexistent", kedb_loader=_loader(["ke-a"]))

    assert result.status == "False"
    assert result.authoritative_count == 1
    assert "unverified" in result.reason


def test_default_loader_is_used_when_not_injected(tmp_path, monkeypatch):
    """Without an injected loader, a missing sibling package still degrades to
    Unknown rather than raising (the real, non-hermetic default path)."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "skcapstone.itil" or name == "skcapstone":
            raise ModuleNotFoundError(f"No module named {name!r}", name="skcapstone")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = compute_kedb_coverage(tmp_path, skcapstone_home=tmp_path)
    assert result.status == "Unknown"
