"""Smoke tests for `skos brain` CLI commands."""
import pytest
from pathlib import Path
from typer.testing import CliRunner
from skos.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# skos brain init
# ---------------------------------------------------------------------------

def test_brain_init_creates_directories(tmp_path):
    r = runner.invoke(app, ["brain", "init", "--wiki", str(tmp_path)])
    assert r.exit_code == 0, r.output
    entities_dir = tmp_path / "pages" / "entities"
    assert entities_dir.is_dir()
    # Check core namespaces exist
    for ns in ("agents", "skills", "tools", "knowledge"):
        assert (entities_dir / ns).is_dir(), f"Missing namespace dir: {ns}"
        assert (entities_dir / ns / "_index.md").exists(), f"Missing index for: {ns}"


def test_brain_init_output_contains_namespaces(tmp_path):
    r = runner.invoke(app, ["brain", "init", "--wiki", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "agents" in r.output
    assert "skills" in r.output
    assert "tools" in r.output
    assert "knowledge" in r.output


def test_brain_init_lays_build_prompt(tmp_path):
    runner.invoke(app, ["brain", "init", "--wiki", str(tmp_path)])
    prompt = tmp_path / "pages" / "entities" / "build_prompt.md"
    assert prompt.exists()
    content = prompt.read_text()
    assert "EntityNode" in content or "entity node" in content.lower()


def test_brain_init_idempotent(tmp_path):
    """Running brain init twice should not fail or corrupt anything."""
    r1 = runner.invoke(app, ["brain", "init", "--wiki", str(tmp_path)])
    r2 = runner.invoke(app, ["brain", "init", "--wiki", str(tmp_path)])
    assert r1.exit_code == 0
    assert r2.exit_code == 0


# ---------------------------------------------------------------------------
# skos brain index
# ---------------------------------------------------------------------------

def _seed_namespace(wiki_root: Path, namespace: str) -> None:
    ns_dir = wiki_root / "pages" / "entities" / namespace
    ns_dir.mkdir(parents=True, exist_ok=True)
    md = (
        "---\n"
        f"id: {namespace}-test\n"
        "type: knowledge\n"
        f"namespace: {namespace}\n"
        "lifecycle_state: draft\n"
        'summary: "Test entity."\n'
        "---\n\n## Body\n\nTest.\n"
    )
    (ns_dir / f"{namespace}-test.md").write_text(md, encoding="utf-8")


def test_brain_index_creates_index(tmp_path):
    _seed_namespace(tmp_path, "knowledge")
    r = runner.invoke(app, [
        "brain", "index", "knowledge", "--wiki", str(tmp_path)
    ])
    assert r.exit_code == 0, r.output
    index_path = tmp_path / "pages" / "entities" / "knowledge" / "_index.md"
    assert index_path.exists()
    assert "1 entities indexed" in r.output


def test_brain_index_missing_namespace(tmp_path):
    r = runner.invoke(app, [
        "brain", "index", "nonexistent", "--wiki", str(tmp_path)
    ])
    assert r.exit_code != 0


# ---------------------------------------------------------------------------
# skos brain validate
# ---------------------------------------------------------------------------

def test_brain_validate_valid_node(tmp_path):
    p = tmp_path / "agent-lumina.md"
    p.write_text(
        "---\n"
        "id: agent-lumina\n"
        "type: agent\n"
        "namespace: agents\n"
        "lifecycle_state: draft\n"
        "summary: \"Queen of SKWorld.\"\n"
        "---\n\n## Overview\n\nLumina is the primary agent.\n",
        encoding="utf-8",
    )
    r = runner.invoke(app, ["brain", "validate", str(p)])
    assert r.exit_code == 0, r.output
    assert "OK" in r.output
    assert "agent-lumina" in r.output


def test_brain_validate_invalid_node(tmp_path):
    p = tmp_path / "broken.md"
    p.write_text("# No frontmatter\n\nJust markdown.\n", encoding="utf-8")
    r = runner.invoke(app, ["brain", "validate", str(p)])
    assert r.exit_code != 0


def test_brain_validate_missing_file(tmp_path):
    r = runner.invoke(app, ["brain", "validate", str(tmp_path / "ghost.md")])
    assert r.exit_code != 0


def test_brain_validate_schema_error(tmp_path):
    p = tmp_path / "bad-schema.md"
    p.write_text(
        "---\n"
        "id: missing-type-only\n"
        "namespace: agents\n"
        "---\n",
        encoding="utf-8",
    )
    r = runner.invoke(app, ["brain", "validate", str(p)])
    assert r.exit_code != 0
