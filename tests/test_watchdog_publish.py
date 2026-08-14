"""publish_digest (WD-3): dated + latest/ artifacts, atomic, same pattern as
operator_seat.brief_publish."""
import json

import pytest

from skos.watchdog.publish import publish_digest, digests_dir, latest_dir


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    yield


def _digest(date="2026-08-10"):
    return {"date": date, "headline": "quiet", "problems": [], "notable": [],
            "info_counts": {}, "per_source": {}}


def test_publish_writes_dated_json_and_md():
    paths = publish_digest(_digest(), "# hello\n")
    assert paths["dated_json"].exists()
    assert paths["dated_md"].exists()
    assert paths["dated_json"].name == "2026-08-10.json"
    assert paths["dated_md"].name == "2026-08-10.md"


def test_publish_writes_latest_dir_with_fixed_names():
    paths = publish_digest(_digest(), "# hello\n")
    assert paths["latest_json"] == latest_dir() / "digest.json"
    assert paths["latest_md"] == latest_dir() / "digest.md"
    assert paths["latest_json"].exists()
    assert paths["latest_md"].exists()


def test_dated_and_latest_json_are_byte_identical():
    paths = publish_digest(_digest(), "# hello\n")
    assert paths["dated_json"].read_text() == paths["latest_json"].read_text()
    assert paths["dated_md"].read_text() == paths["latest_md"].read_text()


def test_published_json_round_trips_the_digest_dict():
    d = _digest()
    d["problems"] = [{"ts": "x", "source": "fleet", "kind": "K", "object": "o",
                      "severity": "problem", "summary": "s",
                      "link": {"uri": "skworld://a", "http": "https://b"}, "ref": "r1"}]
    paths = publish_digest(d, "# hello\n")
    loaded = json.loads(paths["latest_json"].read_text())
    assert loaded == d


def test_publish_uses_date_override_for_dated_filename():
    paths = publish_digest(_digest(date="2026-08-10"), "# hello\n", date="2026-08-09")
    assert paths["dated_json"].name == "2026-08-09.json"


def test_publish_leaves_no_leftover_temp_files():
    publish_digest(_digest(), "# hello\n")
    leftovers = [f for f in digests_dir().iterdir() if f.name.startswith(".")]
    assert leftovers == []
    leftovers_latest = [f for f in latest_dir().iterdir() if f.name.startswith(".")]
    assert leftovers_latest == []


def test_republish_overwrites_latest_atomically():
    publish_digest(_digest(), "# v1\n")
    publish_digest(_digest(), "# v2\n")
    assert latest_dir().joinpath("digest.md").read_text() == "# v2\n"


def test_digests_dir_is_under_watchdog_home():
    from skos.watchdog.cursor import watchdog_home
    assert digests_dir().parent == watchdog_home()


def test_publish_falls_back_to_local_atomic_write_when_skcoord_unavailable(monkeypatch):
    """Even without the optional skcoord sibling, publish must still work
    (falls back to the inline atomic-write copy, never a hard dependency)."""
    import builtins
    real_import = builtins.__import__

    def _blocked_import(name, *a, **kw):
        if name == "skcoord.atomic_io" or name.startswith("skcoord."):
            raise ImportError("simulated: skcoord not installed")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)
    paths = publish_digest(_digest(), "# no skcoord\n")
    assert paths["latest_md"].read_text() == "# no skcoord\n"
