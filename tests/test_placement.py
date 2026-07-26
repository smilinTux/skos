"""Tests for the storage-placement policy engine (skos.placement)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from skos import placement as pl

EXAMPLE = Path(__file__).resolve().parents[1] / "docs" / "examples" / "placement.yaml"


@pytest.fixture
def policy() -> pl.PlacementPolicy:
    return pl.load_policy(EXAMPLE)


@pytest.fixture(autouse=True)
def _isolate_catalog(tmp_path, monkeypatch):
    """Never touch a live catalog: pin it into tmp for every test."""
    monkeypatch.setenv("SKOS_BLOB_CATALOG", str(tmp_path / "blob_catalog.json"))
    yield


# ── loader ───────────────────────────────────────────────────────────────────
def test_load_example_policy(policy):
    assert policy.default == "hot"
    assert set(policy.stores) == {"hot", "cold", "archive"}
    assert policy.stores["cold"].node == "dot41"
    assert [r.name for r in policy.rules] == [
        "sensitive-hot", "large-media-cold", "archive-tagged",
    ]


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(pl.PlacementError, match="not found"):
        pl.load_policy(tmp_path / "nope.yaml")


def test_load_bad_yaml_raises(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("::: not : valid : yaml :::", encoding="utf-8")
    with pytest.raises(pl.PlacementError):
        pl.load_policy(p)


def test_default_store_must_exist(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(
        "version: 1\nstores:\n  hot: {node: n1}\ndefault: cold\n", encoding="utf-8"
    )
    with pytest.raises(pl.PlacementError, match="default store"):
        pl.load_policy(p)


def test_rule_target_must_exist(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(
        "version: 1\nstores:\n  hot: {node: n1}\n"
        "rules:\n  - {name: r1, target: ghost}\ndefault: hot\n",
        encoding="utf-8",
    )
    with pytest.raises(pl.PlacementError, match="undeclared store"):
        pl.load_policy(p)


# ── pure resolver ────────────────────────────────────────────────────────────
def test_resolve_first_match_wins_sensitive(policy):
    p = pl.resolve_placement({"tags": ["secret"], "size": 10}, policy)
    assert (p.store, p.node, p.tier, p.rule) == ("hot", "dot158", "ssd", "sensitive-hot")


def test_resolve_large_media_to_cold(policy):
    p = pl.resolve_placement(
        {"mimetype": "video/mp4", "size": 200 * 1024 * 1024}, policy
    )
    assert p.store == "cold"
    assert p.annex == "skdata-cold"
    assert p.rule == "large-media-cold"


def test_resolve_small_video_falls_through_to_default(policy):
    # video but under the 100 MiB floor -> no rule matches -> default (hot)
    p = pl.resolve_placement({"mimetype": "video/mp4", "size": 1024}, policy)
    assert p.store == "hot"
    assert p.rule == pl.DEFAULT_RULE


def test_resolve_archive_tag(policy):
    p = pl.resolve_placement({"tags": ["archive"]}, policy)
    assert p.store == "archive"
    assert p.node == "chiap08"


def test_resolve_default_when_nothing_matches(policy):
    p = pl.resolve_placement({"mimetype": "text/plain", "size": 5}, policy)
    assert p.store == "hot"
    assert p.rule == pl.DEFAULT_RULE


def test_resolve_ext_and_source_and_ranges():
    policy = pl.PlacementPolicy.model_validate(
        {
            "version": 1,
            "stores": {"a": {"node": "na"}, "b": {"node": "nb"}},
            "rules": [
                {"name": "iso-ext", "match": {"ext": "iso"}, "target": "b"},
                {"name": "from-email", "match": {"source": "email"}, "target": "b"},
                {"name": "midsize", "match": {"min_size": 100, "max_size": 200}, "target": "b"},
            ],
            "default": "a",
        }
    )
    assert pl.resolve_placement({"filename": "ubuntu.ISO"}, policy).store == "b"
    assert pl.resolve_placement({"source": "email"}, policy).store == "b"
    assert pl.resolve_placement({"size": 150}, policy).store == "b"
    assert pl.resolve_placement({"size": 250}, policy).store == "a"


def test_all_tags_requires_every_tag():
    policy = pl.PlacementPolicy.model_validate(
        {
            "version": 1,
            "stores": {"a": {"node": "na"}, "b": {"node": "nb"}},
            "rules": [{"name": "both", "match": {"all_tags": ["x", "y"]}, "target": "b"}],
            "default": "a",
        }
    )
    assert pl.resolve_placement({"tags": ["x", "y", "z"]}, policy).store == "b"
    assert pl.resolve_placement({"tags": ["x"]}, policy).store == "a"


# ── git-annex preferred-content seam ─────────────────────────────────────────
def test_preferred_content_expr_cold(policy):
    expr = pl.preferred_content_expr(policy, "cold")
    assert "mimeglob=video/*" in expr
    assert "largerthan=104857600b" in expr
    # cold is guarded against the earlier sensitive-hot rule
    assert "not" in expr


def test_preferred_content_expr_default_store_is_fallthrough(policy):
    expr = pl.preferred_content_expr(policy, "hot")
    # hot is the default; its expr must include the sensitive-hot rule and the
    # "nothing earlier matched" fallthrough guard.
    assert "metadata=tag=secret" in expr
    assert "not" in expr


def test_preferred_content_unknown_store(policy):
    with pytest.raises(pl.PlacementError):
        pl.preferred_content_expr(policy, "ghost")


# ── catalog + record_ingest_location ─────────────────────────────────────────
def test_record_and_read_back(policy):
    p = pl.resolve_placement({"tags": ["secret"]}, policy)
    row = pl.record_ingest_location(
        "blob-1", p, {"size": 42, "hash": "sha256:abc", "mimetype": "text/plain"}
    )
    assert row["blob_id"] == "blob-1"
    assert row["store"] == "hot"
    assert row["size"] == 42
    assert row["hash"] == "sha256:abc"

    back = pl.get_placement("blob-1")
    assert back is not None
    assert back["store"] == "hot"
    assert back["hash"] == "sha256:abc"


def test_get_placement_absent_is_none():
    assert pl.get_placement("nope") is None


def test_record_is_idempotent_upsert(policy):
    p = pl.resolve_placement({"tags": ["secret"]}, policy)
    pl.record_ingest_location("blob-x", p, {"size": 1})
    pl.record_ingest_location("blob-x", p, {"size": 1})
    pl.record_ingest_location("blob-x", p, {"size": 1})
    assert len([r for r in pl.list_placements() if r["blob_id"] == "blob-x"]) == 1


def test_record_upsert_updates_placement_preserves_recorded_at(policy):
    hot = pl.resolve_placement({"tags": ["secret"]}, policy)
    first = pl.record_ingest_location("blob-y", hot, {"size": 1})
    cold = pl.resolve_placement(
        {"mimetype": "video/mp4", "size": 200 * 1024 * 1024}, policy
    )
    second = pl.record_ingest_location("blob-y", cold, {"size": 2})
    rows = [r for r in pl.list_placements() if r["blob_id"] == "blob-y"]
    assert len(rows) == 1
    assert rows[0]["store"] == "cold"          # updated in place
    assert rows[0]["size"] == 2
    assert second["recorded_at"] == first["recorded_at"]  # original preserved


def test_record_requires_blob_id(policy):
    p = pl.resolve_placement({}, policy)
    with pytest.raises(pl.PlacementError):
        pl.record_ingest_location("", p, {})


def test_corrupt_catalog_is_quarantined(tmp_path, monkeypatch, policy):
    cat = tmp_path / "cat.json"
    cat.write_text("{ not a list }", encoding="utf-8")
    monkeypatch.setenv("SKOS_BLOB_CATALOG", str(cat))
    # a load-modify-save cycle over a corrupt file quarantines it, never crashes
    p = pl.resolve_placement({}, policy)
    pl.record_ingest_location("b", p, {})
    assert any(x.name.startswith("cat.json.corrupt-") for x in tmp_path.iterdir())
    assert pl.get_placement("b") is not None


# ── store_blob primitive (skos store) ────────────────────────────────────────
def test_store_blob_resolves_and_records(policy):
    row = pl.store_blob(
        "blob-store",
        {"mimetype": "video/mp4", "size": 200 * 1024 * 1024, "hash": "h1", "extra": "kept"},
        policy,
    )
    assert row["store"] == "cold"
    assert row["size"] == 200 * 1024 * 1024
    assert row["hash"] == "h1"
    assert row["meta"]["extra"] == "kept"
    assert pl.get_placement("blob-store")["store"] == "cold"


def test_store_blob_content_facts_hoisted_not_in_meta(policy):
    row = pl.store_blob("b2", {"size": 9, "hash": "hh", "source": "ingest"}, policy)
    assert row["source"] == "ingest"
    assert "size" not in row["meta"]
    assert "source" not in row["meta"]


# ── catalog path resolution ──────────────────────────────────────────────────
def test_catalog_path_env_override(tmp_path, monkeypatch):
    target = tmp_path / "sub" / "cat.json"
    monkeypatch.setenv("SKOS_BLOB_CATALOG", str(target))
    assert pl.catalog_path() == target
    assert target.parent.is_dir()  # parent created


def test_catalog_json_is_a_list_on_disk(policy, tmp_path, monkeypatch):
    cat = tmp_path / "c.json"
    monkeypatch.setenv("SKOS_BLOB_CATALOG", str(cat))
    p = pl.resolve_placement({}, policy)
    pl.record_ingest_location("b", p, {})
    assert isinstance(json.loads(cat.read_text()), list)
