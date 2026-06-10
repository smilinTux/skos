"""Tests for the codex + n8n surface scaffolds (planned, ABC-conformant)."""
import pytest

from skos.brain.entity import EntityNode, EntityType
from skos.interface.base import Surface, SurfaceNotImplementedError
from skos.interface.codex import CodexSurface
from skos.interface.n8n import N8nSurface


SCAFFOLDS = [CodexSurface, N8nSurface]


def _node():
    return EntityNode(id="agent-x", type=EntityType.agent, namespace="agents")


@pytest.mark.parametrize("cls", SCAFFOLDS)
def test_scaffold_is_surface(cls):
    assert issubclass(cls, Surface)


@pytest.mark.parametrize("cls", SCAFFOLDS)
def test_scaffold_instantiates(cls):
    # Conforms to the ABC: all abstract methods are implemented, so it constructs.
    s = cls()
    assert s.name in ("codex", "n8n")


@pytest.mark.parametrize("cls", SCAFFOLDS)
def test_scaffold_capabilities_signal_planned(cls):
    caps = cls().capabilities()
    assert caps.planned is True
    assert caps.readable is False
    assert caps.writable is False
    assert caps.listable is False


@pytest.mark.parametrize("cls", SCAFFOLDS)
def test_scaffold_read_raises_not_implemented(cls):
    with pytest.raises(SurfaceNotImplementedError, match="planned"):
        cls().read("agent-x")


@pytest.mark.parametrize("cls", SCAFFOLDS)
def test_scaffold_write_raises_not_implemented(cls):
    with pytest.raises(SurfaceNotImplementedError, match="planned"):
        cls().write(_node())


@pytest.mark.parametrize("cls", SCAFFOLDS)
def test_scaffold_list_raises_not_implemented(cls):
    with pytest.raises(SurfaceNotImplementedError, match="planned"):
        cls().list()


def test_codex_name():
    assert CodexSurface().name == "codex"


def test_n8n_name():
    assert N8nSurface().name == "n8n"
