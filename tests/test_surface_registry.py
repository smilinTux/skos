"""Tests for the skos.interface registry + get_surface()."""
import pytest

from skos.adapter import AdapterError
from skos.interface import (
    SURFACES,
    get_surface,
    Surface,
    ObsidianSurface,
    ClaudeCodeSurface,
    CodexSurface,
    N8nSurface,
)


EXPECTED = {"obsidian", "claude-code", "codex", "n8n"}


def test_registry_has_all_targets():
    assert EXPECTED.issubset(set(SURFACES))


def test_all_registered_are_surface_subclasses():
    for name, cls in SURFACES.items():
        assert issubclass(cls, Surface), f"{name} is not a Surface"
        assert cls.name == name


def test_get_surface_obsidian():
    s = get_surface("obsidian")
    assert isinstance(s, ObsidianSurface)


def test_get_surface_claude_code():
    s = get_surface("claude-code")
    assert isinstance(s, ClaudeCodeSurface)


def test_get_surface_codex_scaffold():
    assert isinstance(get_surface("codex"), CodexSurface)


def test_get_surface_n8n_scaffold():
    assert isinstance(get_surface("n8n"), N8nSurface)


def test_get_surface_unknown_raises():
    with pytest.raises(AdapterError):
        get_surface("nonexistent")


def test_get_surface_passes_kwargs(tmp_path):
    s = get_surface("obsidian", vault_root=tmp_path / "v")
    assert s.vault_root == (tmp_path / "v")
