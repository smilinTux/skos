"""gogcli is RESOLVED, not assumed to sit at a Homebrew path.

Three modules each hardcoded `/home/linuxbrew/.linuxbrew/bin/gog` as the
default. That is wrong three ways:

* a fresh skos install on a machine without Homebrew has no gog at all, and
  the failure is a confusing "no such file" deep inside an adapter rather
  than "gogcli is not installed";
* it pinned us to whichever tap put a binary there (here: `openclaw/tap`,
  a party deliberately evicted in April 2026, shipping v0.12.0 against
  upstream v0.37.0);
* moving gog anywhere breaks all three copies independently, which is the
  same divergent-constant shape as the GTD file-set bug (card 3df69da1).

One resolver: explicit `GOG` env override, then PATH, then the known install
locations as a last resort. PATH before the fallbacks so an operator's own
install always wins.
"""

from __future__ import annotations

import stat

import pytest

from skos import gogbin


def _fake_gog(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def test_the_env_override_wins(tmp_path, monkeypatch):
    custom = _fake_gog(tmp_path / "custom" / "gog")
    monkeypatch.setenv("GOG", str(custom))
    assert gogbin.resolve_gog() == str(custom)


def test_path_is_preferred_over_the_baked_in_fallbacks(tmp_path, monkeypatch):
    on_path = _fake_gog(tmp_path / "bin" / "gog")
    monkeypatch.delenv("GOG", raising=False)
    monkeypatch.setenv("PATH", str(on_path.parent))
    assert gogbin.resolve_gog() == str(on_path)


def test_it_falls_back_to_a_known_location_when_path_has_none(tmp_path, monkeypatch):
    fallback = _fake_gog(tmp_path / "fallbacks" / "gog")
    monkeypatch.delenv("GOG", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(gogbin, "_FALLBACKS", (str(fallback),))
    assert gogbin.resolve_gog() == str(fallback)


def test_missing_gog_raises_something_an_operator_can_act_on(tmp_path, monkeypatch):
    monkeypatch.delenv("GOG", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(gogbin, "_FALLBACKS", ())
    with pytest.raises(gogbin.GogNotInstalled) as e:
        gogbin.resolve_gog()
    msg = str(e.value)
    assert "gogcli" in msg.lower()
    assert "GOG" in msg, "the message must name the override env var"
    assert "github.com/steipete/gogcli" in msg, "and where to actually get it"


def test_resolution_is_lazy_so_import_never_explodes(tmp_path, monkeypatch):
    """Importing an adapter on a box without gog must not raise at import
    time; the error belongs at the call, with context."""
    monkeypatch.delenv("GOG", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(gogbin, "_FALLBACKS", ())
    import importlib

    from skos.adapters import calendar as cal

    importlib.reload(cal)  # must not raise


def test_gog_available_reports_without_raising(tmp_path, monkeypatch):
    monkeypatch.delenv("GOG", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(gogbin, "_FALLBACKS", ())
    assert gogbin.gog_available() is False

    found = _fake_gog(tmp_path / "b" / "gog")
    monkeypatch.setenv("GOG", str(found))
    assert gogbin.gog_available() is True
