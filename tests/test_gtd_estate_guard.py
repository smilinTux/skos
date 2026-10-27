"""ESTATE marker: a host never writes another estate's GTD store."""
import json

import pytest

from skos import gtd_ingest as g


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "gtd"))
    monkeypatch.setenv("SK_GTD_SPOOL_STATE", str(tmp_path / "state" / "offsets.json"))
    monkeypatch.delenv("SK_GTD_OWNER_HOST", raising=False)
    monkeypatch.delenv("SKWORLD_ESTATE", raising=False)
    monkeypatch.setenv("SK_GTD_HOST", "noroc2027")
    return tmp_path / "gtd"


def _cap(ref="r1"):
    return g.GtdCapture(text="t", source="manual", source_ref=ref)


def _estate(store, name):
    store.mkdir(parents=True, exist_ok=True)
    (store / "ESTATE").write_text(f"# which estate owns this store\n{name}\n")


def _inbox(store):
    p = store / "inbox.json"
    return json.loads(p.read_text()) if p.exists() else []


def test_no_estate_file_is_unchanged(store, monkeypatch):
    monkeypatch.setenv("SK_GTD_HOST", "chiap08")
    assert g.capture(_cap())
    assert len(_inbox(store)) == 1


def test_same_estate_by_hostname_prefix_writes(store):
    _estate(store, "nor")
    assert g.capture(_cap())
    assert len(_inbox(store)) == 1


def test_other_estate_host_is_refused(store, monkeypatch):
    _estate(store, "nor")
    monkeypatch.setenv("SK_GTD_HOST", "chiap08")
    with pytest.raises(g.EstateMismatchError):
        g.capture(_cap())
    with pytest.raises(g.EstateMismatchError):
        g.upsert(_cap("r2"))
    assert _inbox(store) == []


def test_other_estate_non_owner_cannot_spool_either(store, monkeypatch):
    _estate(store, "chi")
    (store / "OWNER").write_text("chiap08\n")
    monkeypatch.setenv("SK_GTD_HOST", "noroc2027")
    with pytest.raises(g.EstateMismatchError):
        g.capture(_cap())
    assert not (store / "spool" / "noroc2027.jsonl").exists()


def test_env_overrides_hostname_and_aliases_match(store, monkeypatch):
    _estate(store, "cakjr")             # naming-standard label == chi
    monkeypatch.setenv("SK_GTD_HOST", "ziowk01")
    monkeypatch.setenv("SKWORLD_ESTATE", "chi")
    assert g.capture(_cap())
    monkeypatch.setenv("SKWORLD_ESTATE", "chef")  # == nor
    with pytest.raises(g.EstateMismatchError):
        g.capture(_cap("r3"))


def test_unknown_host_estate_is_allowed(store, monkeypatch):
    _estate(store, "nor")
    monkeypatch.setenv("SK_GTD_HOST", "ollama")
    assert g.capture(_cap())


def test_estate_helpers(store, monkeypatch):
    assert g.store_estate(store) is None
    _estate(store, "  NOR ")
    assert g.store_estate(store) == "nor"
    monkeypatch.setenv("SK_GTD_HOST", "chiwk12")
    assert g.host_estate() == "chi"
