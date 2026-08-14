"""The versioned rubric schema + loader (WD-7).

Spec: docs/specs/2026-08-10-skwatchdog-architecture.md, section 7: "Versioned
YAML in-repo (`skos/src/skos/watchdog/rubrics/<name>.yaml`): dimensions
(answered the actual question, factually grounded, correct tone per soul, no
banned punctuation, action captured to GTD when one surfaced), each 1 to 5,
an overall floor, and a threshold (default 3). Rubric changes are commits,
reviewable like code."

Two independent things are versioned here, on purpose:

  `schema`   the rubric FILE FORMAT this loader understands (currently 1).
             Bumped only when the YAML shape itself changes (a renamed or
             newly-required top-level key), never when a rubric's own
             criteria change. A file whose `schema` does not match this
             loader's SCHEMA_VERSION is refused outright rather than
             best-effort parsed, so a future shape change can never be
             silently misread as today's shape.
  `version`  the rubric's own CONTENT version, bumped every time its
             dimensions/threshold/floor change. Stamped onto every grade
             downstream (`Rubric.rubric_ref`, "<id>@v<version>") so a score
             is always interpretable against the exact rubric that produced
             it -- the card's hard rule: "A score with no rubric version
             attached is not evidence." A rubric change is therefore a NEW
             file (`lumina-replies.v2.yaml`, ...), never an in-place edit to
             an existing version's file: `lumina-replies@v1` must mean the
             same five criteria forever, even after v2 ships.

    from skos.watchdog.rubric import load_rubric
    rubric = load_rubric("lumina-replies")   # newest version on disk
    rubric.rubric_ref                        # "lumina-replies@v1"

Pure module: no model call, no network, no state. `load_rubric_file` /
`load_rubric` are the only functions that touch disk; `parse_rubric` is pure
text-in, Rubric-out, and is what tests exercise directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

#: The rubric FILE FORMAT version this loader understands. See module
#: docstring for the schema/version distinction.
SCHEMA_VERSION = 1

RUBRICS_DIR = Path(__file__).resolve().parent / "rubrics"


class RubricError(ValueError):
    """A rubric file failed to parse or validate. Callers (the grading
    adapter) let this propagate out of `collect()` exactly like any other
    adapter failure, so `collect_safe` degrades the whole 'grading' source
    to one SourceUnavailable line -- never a grade produced against a
    broken or unreadable rubric."""


@dataclass(frozen=True)
class RubricDimension:
    """One 1-to-5 axis the grader scores independently. `key` is the exact
    JSON field name the grader's strict-JSON reply must use (see
    grader.py); `prompt` is the one concrete sentence handed to the model,
    kept short and concrete on purpose (spec 7: "a rubric nobody can apply
    consistently produces noise dressed as data")."""
    key: str
    prompt: str


@dataclass(frozen=True)
class Rubric:
    """A versioned grading rubric, loaded from
    `skos/watchdog/rubrics/<id>.v<version>.yaml`."""
    id: str
    version: int
    title: str
    applies_to: str
    instructions: str
    dimensions: tuple[RubricDimension, ...]
    threshold: int = 3
    floor: int = 1

    @property
    def rubric_ref(self) -> str:
        """The "<id>@v<version>" string stamped onto every GradeResult
        downstream. This IS the evidence tag: a bare integer score means
        nothing without it."""
        return f"{self.id}@v{self.version}"

    def dimension_keys(self) -> tuple[str, ...]:
        return tuple(d.key for d in self.dimensions)


def _require(d: dict, key: str, source: str):
    if key not in d or d[key] in (None, ""):
        raise RubricError(f"{source}: missing required field {key!r}")
    return d[key]


def parse_rubric(text: str, *, source: str = "<string>") -> Rubric:
    """Parse and validate a rubric YAML document already read into `text`.

    Pure: no file I/O. Raises `RubricError` on ANY structural problem (an
    unknown schema, a missing required field, an out-of-range threshold or
    floor, a malformed dimension, a duplicate dimension key) -- never
    returns a partially-valid Rubric, so a broken rubric can never grade
    silently-wrong.
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RubricError(f"{source}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise RubricError(f"{source}: rubric document must be a YAML mapping")

    schema = data.get("schema")
    if schema != SCHEMA_VERSION:
        raise RubricError(
            f"{source}: unsupported rubric schema {schema!r}; this loader "
            f"understands schema {SCHEMA_VERSION} only")

    rid = str(_require(data, "id", source))

    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise RubricError(f"{source}: 'version' must be a positive integer")

    dims_raw = data.get("dimensions")
    if not isinstance(dims_raw, list) or not dims_raw:
        raise RubricError(f"{source}: 'dimensions' must be a non-empty list")
    dimensions: list[RubricDimension] = []
    seen_keys: set[str] = set()
    for i, dim in enumerate(dims_raw):
        if not isinstance(dim, dict):
            raise RubricError(f"{source}: dimensions[{i}] must be a mapping")
        key = str(_require(dim, "key", source))
        if key in seen_keys:
            raise RubricError(f"{source}: duplicate dimension key {key!r}")
        seen_keys.add(key)
        prompt = str(_require(dim, "prompt", source))
        dimensions.append(RubricDimension(key=key, prompt=prompt))

    threshold = data.get("threshold", 3)
    floor = data.get("floor", 1)
    for name, val in (("threshold", threshold), ("floor", floor)):
        if isinstance(val, bool) or not isinstance(val, int) or not (1 <= val <= 5):
            raise RubricError(f"{source}: '{name}' must be an integer 1..5")

    return Rubric(
        id=rid, version=version,
        title=str(data.get("title") or rid),
        applies_to=str(data.get("applies_to") or ""),
        instructions=str(data.get("instructions") or ""),
        dimensions=tuple(dimensions),
        threshold=threshold, floor=floor,
    )


def load_rubric_file(path: Path | str) -> Rubric:
    """Read + parse one rubric file. Raises `RubricError` on a missing or
    unreadable file, matching `parse_rubric`'s "never partially valid"
    contract."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise RubricError(f"{p}: cannot read rubric file: {exc}") from exc
    return parse_rubric(text, source=str(p))


def load_rubric(rubric_id: str, *, rubrics_dir: Optional[Path] = None) -> Rubric:
    """Load the highest-version file for `rubric_id` under `rubrics_dir`
    (default `skos/watchdog/rubrics/`).

    File naming convention: `<id>.v<version>.yaml`, e.g.
    `lumina-replies.v1.yaml`. Raises `RubricError` when no file matches, or
    when a matching file's own `id` field disagrees with the filename (a
    copy/paste mistake that would otherwise silently mislabel a score).
    """
    d = rubrics_dir if rubrics_dir is not None else RUBRICS_DIR
    prefix = f"{rubric_id}.v"
    candidates = sorted(Path(d).glob(f"{prefix}*.yaml"))
    if not candidates:
        raise RubricError(f"no rubric files found for id={rubric_id!r} under {d}")

    best: Optional[Rubric] = None
    for c in candidates:
        r = load_rubric_file(c)
        if r.id != rubric_id:
            raise RubricError(
                f"{c}: rubric id {r.id!r} does not match filename id {rubric_id!r}")
        if best is None or r.version > best.version:
            best = r
    assert best is not None
    return best


__all__ = [
    "Rubric", "RubricDimension", "RubricError", "SCHEMA_VERSION",
    "RUBRICS_DIR", "parse_rubric", "load_rubric_file", "load_rubric",
]
