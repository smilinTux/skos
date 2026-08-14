"""The cursor store: the only state skwatchdog owns. Advance + replay-after-crash."""
import json

import pytest

from skos.watchdog.cursor import (
    watchdog_home, cursors_dir, read_cursor, write_cursor, advance, window_since,
    DEFAULT_LOOKBACK,
)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    yield


def test_watchdog_home_and_cursors_dir_are_created():
    home = watchdog_home()
    assert home.is_dir()
    d = cursors_dir()
    assert d.is_dir()
    assert d.parent == home


def test_read_cursor_of_never_digested_source_is_none():
    assert read_cursor("fleet") is None


def test_write_then_read_round_trips():
    write_cursor("fleet", "2026-08-10T06:00:00Z")
    assert read_cursor("fleet") == "2026-08-10T06:00:00Z"


def test_advance_is_write_cursor():
    advance("scheduler", "2026-08-10T07:00:00Z")
    assert read_cursor("scheduler") == "2026-08-10T07:00:00Z"


def test_write_is_atomic_on_disk():
    write_cursor("itil", "2026-08-10T00:00:00Z")
    p = cursors_dir() / "itil.json"
    data = json.loads(p.read_text())
    assert data == {"source": "itil", "last_digested_at": "2026-08-10T00:00:00Z"}
    # no leftover temp files after a clean write
    leftovers = [f for f in cursors_dir().iterdir() if f.name.startswith(".itil")]
    assert leftovers == []


def test_corrupt_cursor_file_reads_as_none_fail_safe():
    d = cursors_dir()
    (d / "coord.json").write_text("{not json", encoding="utf-8")
    assert read_cursor("coord") is None


def test_dotted_source_names_get_their_own_file():
    write_cursor("chat.skchat", "2026-08-10T00:00:00Z")
    assert read_cursor("chat.skchat") == "2026-08-10T00:00:00Z"
    assert (cursors_dir() / "chat.skchat.json").exists()


def test_window_since_defaults_to_lookback_on_first_ever_run():
    w = window_since("fleet", now="2026-08-10T12:00:00Z", lookback=DEFAULT_LOOKBACK)
    assert w.until == "2026-08-10T12:00:00Z"
    assert w.since == "2026-08-09T12:00:00Z"


def test_window_since_uses_the_existing_cursor_when_present():
    write_cursor("fleet", "2026-08-10T03:00:00Z")
    w = window_since("fleet", now="2026-08-10T09:00:00Z")
    assert w.since == "2026-08-10T03:00:00Z"
    assert w.until == "2026-08-10T09:00:00Z"


def test_advance_after_digest_moves_the_next_window_forward():
    w1 = window_since("fleet", now="2026-08-10T09:00:00Z")
    advance("fleet", w1.until)
    w2 = window_since("fleet", now="2026-08-10T15:00:00Z")
    assert w2.since == "2026-08-10T09:00:00Z"
    assert w2.until == "2026-08-10T15:00:00Z"


def test_replay_after_crash_re_reads_the_same_window_when_cursor_never_advanced():
    """If the process dies after computing a window but before advance() is
    called (the crash-in-the-middle case the spec calls out in 6.1), the next
    run must recompute the SAME since-bound: no data loss, only a replay that
    downstream dedupes on WatchdogEvent.ref."""
    write_cursor("fleet", "2026-08-10T03:00:00Z")
    w1 = window_since("fleet", now="2026-08-10T09:00:00Z")
    # simulate a crash: advance() is never called
    w2 = window_since("fleet", now="2026-08-10T09:05:00Z")
    assert w2.since == w1.since == "2026-08-10T03:00:00Z"
    # only the "until" bound moves, because time passed before the retry
    assert w2.until != w1.until


def test_sources_have_independent_cursors():
    write_cursor("fleet", "2026-08-10T01:00:00Z")
    write_cursor("scheduler", "2026-08-10T02:00:00Z")
    assert read_cursor("fleet") == "2026-08-10T01:00:00Z"
    assert read_cursor("scheduler") == "2026-08-10T02:00:00Z"
