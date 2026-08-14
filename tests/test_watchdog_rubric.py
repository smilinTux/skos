"""Tests for the WD-7 versioned rubric schema + loader.

`parse_rubric` is pure (text in, Rubric out or RubricError), so most cases
here build a YAML string directly rather than touching disk. A handful of
tests load the REAL shipped `lumina-replies.v1.yaml` to prove the file this
card ships actually satisfies its own schema.
"""
from __future__ import annotations

import pytest

from skos.watchdog.rubric import (
    RUBRICS_DIR, RubricError, load_rubric, load_rubric_file, parse_rubric,
)

_VALID = """
schema: 1
id: test-rubric
version: 1
title: "Test rubric"
applies_to: test.thing
threshold: 3
floor: 2
instructions: "Grade the thing."
dimensions:
  - key: dim_one
    prompt: "Is dimension one good?"
  - key: dim_two
    prompt: "Is dimension two good?"
"""


# ------------------------------------------------------------- parse_rubric

def test_parses_a_valid_rubric():
    r = parse_rubric(_VALID, source="test.yaml")
    assert r.id == "test-rubric"
    assert r.version == 1
    assert r.threshold == 3
    assert r.floor == 2
    assert r.dimension_keys() == ("dim_one", "dim_two")
    assert r.rubric_ref == "test-rubric@v1"


def test_rubric_ref_carries_id_and_version():
    r = parse_rubric(_VALID.replace("version: 1", "version: 7"), source="t")
    assert r.rubric_ref == "test-rubric@v7"


def test_defaults_apply_when_threshold_and_floor_omitted():
    text = _VALID.replace("threshold: 3\nfloor: 2\n", "")
    r = parse_rubric(text, source="t")
    assert r.threshold == 3
    assert r.floor == 1


def test_rejects_unsupported_schema_version():
    text = _VALID.replace("schema: 1", "schema: 99")
    with pytest.raises(RubricError, match="schema"):
        parse_rubric(text, source="t")


def test_rejects_missing_schema_field():
    text = _VALID.replace("schema: 1\n", "")
    with pytest.raises(RubricError, match="schema"):
        parse_rubric(text, source="t")


def test_rejects_non_mapping_document():
    with pytest.raises(RubricError, match="mapping"):
        parse_rubric("- just\n- a\n- list\n", source="t")


def test_rejects_invalid_yaml():
    with pytest.raises(RubricError, match="invalid YAML"):
        parse_rubric("{not: valid: yaml: [", source="t")


def test_rejects_missing_id():
    text = _VALID.replace("id: test-rubric\n", "")
    with pytest.raises(RubricError, match="id"):
        parse_rubric(text, source="t")


def test_rejects_non_integer_version():
    text = _VALID.replace("version: 1", 'version: "one"')
    with pytest.raises(RubricError, match="version"):
        parse_rubric(text, source="t")


def test_rejects_empty_dimensions_list():
    text = _VALID.split("dimensions:")[0] + "dimensions: []\n"
    with pytest.raises(RubricError, match="dimensions"):
        parse_rubric(text, source="t")


def test_rejects_missing_dimensions_key():
    text = _VALID.split("dimensions:")[0]
    with pytest.raises(RubricError, match="dimensions"):
        parse_rubric(text, source="t")


def test_rejects_dimension_missing_prompt():
    text = _VALID + "  - key: dim_three\n"
    with pytest.raises(RubricError, match="prompt"):
        parse_rubric(text, source="t")


def test_rejects_duplicate_dimension_keys():
    text = _VALID + '  - key: dim_one\n    prompt: "again"\n'
    with pytest.raises(RubricError, match="duplicate"):
        parse_rubric(text, source="t")


@pytest.mark.parametrize("bad", ["0", "6", '"three"'])
def test_rejects_out_of_range_threshold(bad):
    text = _VALID.replace("threshold: 3", f"threshold: {bad}")
    with pytest.raises(RubricError, match="threshold"):
        parse_rubric(text, source="t")


@pytest.mark.parametrize("bad", ["0", "6"])
def test_rejects_out_of_range_floor(bad):
    text = _VALID.replace("floor: 2", f"floor: {bad}")
    with pytest.raises(RubricError, match="floor"):
        parse_rubric(text, source="t")


# --------------------------------------------------------------- file I/O

def test_load_rubric_file_missing_raises(tmp_path):
    with pytest.raises(RubricError, match="cannot read"):
        load_rubric_file(tmp_path / "nope.yaml")


def test_load_rubric_picks_highest_version(tmp_path):
    v1 = _VALID.replace("id: test-rubric", "id: widget")
    v2 = v1.replace("version: 1", "version: 2")
    (tmp_path / "widget.v1.yaml").write_text(v1, encoding="utf-8")
    (tmp_path / "widget.v2.yaml").write_text(v2, encoding="utf-8")

    r = load_rubric("widget", rubrics_dir=tmp_path)
    assert r.version == 2
    assert r.rubric_ref == "widget@v2"


def test_load_rubric_raises_when_no_file_matches(tmp_path):
    with pytest.raises(RubricError, match="no rubric files"):
        load_rubric("nonexistent", rubrics_dir=tmp_path)


def test_load_rubric_raises_on_filename_id_mismatch(tmp_path):
    mismatched = _VALID.replace("id: test-rubric", "id: something-else")
    (tmp_path / "widget.v1.yaml").write_text(mismatched, encoding="utf-8")
    with pytest.raises(RubricError, match="does not match"):
        load_rubric("widget", rubrics_dir=tmp_path)


# --------------------------------------------------- the real shipped file

def test_shipped_lumina_replies_rubric_loads():
    r = load_rubric("lumina-replies")
    assert r.rubric_ref == "lumina-replies@v1"
    assert r.threshold == 3
    assert r.floor == 2
    assert set(r.dimension_keys()) == {
        "answered_the_question", "factually_grounded", "tone_matches_soul",
        "no_banned_punctuation", "action_captured_if_any",
    }


def test_shipped_rubric_file_lives_under_rubrics_dir():
    assert (RUBRICS_DIR / "lumina-replies.v1.yaml").is_file()


def test_shipped_rubric_contains_no_banned_dashes():
    text = (RUBRICS_DIR / "lumina-replies.v1.yaml").read_text(encoding="utf-8")
    assert "—" not in text  # em dash
    assert "–" not in text  # en dash
