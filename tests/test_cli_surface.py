"""Tests for the `skos surface` CLI command family."""
import pytest
from typer.testing import CliRunner

from skos.cli import app

runner = CliRunner()


NODE_MD = (
    "---\n"
    "id: agent-lumina\n"
    "type: agent\n"
    "namespace: agents\n"
    "lifecycle_state: draft\n"
    'summary: "Queen of SKWorld."\n'
    "---\n\n## Overview\n\nLumina.\n"
)


def test_surface_list_shows_targets():
    r = runner.invoke(app, ["surface", "list"])
    assert r.exit_code == 0, r.output
    for name in ("obsidian", "claude-code", "codex", "n8n"):
        assert name in r.output


def test_surface_list_marks_planned():
    r = runner.invoke(app, ["surface", "list"])
    assert r.exit_code == 0, r.output
    # codex/n8n are planned scaffolds
    assert "planned" in r.output.lower()


def test_surface_write_then_read(tmp_path):
    src = tmp_path / "node.md"
    src.write_text(NODE_MD, encoding="utf-8")
    vault = tmp_path / "vault"

    w = runner.invoke(app, [
        "surface", "write", "obsidian", str(src), "--root", str(vault),
    ])
    assert w.exit_code == 0, w.output

    r = runner.invoke(app, [
        "surface", "read", "obsidian", "agent-lumina", "--root", str(vault),
    ])
    assert r.exit_code == 0, r.output
    assert "agent-lumina" in r.output
    assert "Lumina." in r.output


def test_surface_ls_entities(tmp_path):
    src = tmp_path / "node.md"
    src.write_text(NODE_MD, encoding="utf-8")
    vault = tmp_path / "vault"
    runner.invoke(app, ["surface", "write", "obsidian", str(src), "--root", str(vault)])

    r = runner.invoke(app, ["surface", "ls", "obsidian", "--root", str(vault)])
    assert r.exit_code == 0, r.output
    assert "agent-lumina" in r.output


def test_surface_read_missing_exits_1(tmp_path):
    r = runner.invoke(app, [
        "surface", "read", "obsidian", "ghost", "--root", str(tmp_path / "vault"),
    ])
    assert r.exit_code == 1


def test_surface_read_planned_scaffold_exits_1():
    r = runner.invoke(app, ["surface", "read", "codex", "agent-x"])
    assert r.exit_code == 1
    assert "planned" in r.output.lower()


def test_surface_unknown_name_exits_nonzero():
    r = runner.invoke(app, ["surface", "ls", "nonesuch"])
    assert r.exit_code != 0
