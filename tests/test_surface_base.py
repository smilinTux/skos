"""Tests for skos.interface.base — Surface ABC + contract types."""
import pytest

from skos.adapter import Adapter
from skos.brain.entity import EntityNode
from skos.interface.base import (
    Surface,
    SurfaceError,
    SurfaceNotImplementedError,
    SurfaceCapabilities,
)


def test_surface_is_abstract():
    with pytest.raises(TypeError):
        Surface()  # abstract methods not implemented


def test_surface_subclass_missing_methods_is_abstract():
    class Incomplete(Surface):
        name = "incomplete"
        # missing read/write/list/capabilities

    with pytest.raises(TypeError):
        Incomplete()


def test_surface_is_an_adapter():
    assert issubclass(Surface, Adapter)


def test_surface_capability_is_surface():
    assert Surface.capability == "surface"


def test_surface_error_is_runtime_error():
    assert issubclass(SurfaceError, RuntimeError)


def test_not_implemented_is_surface_error():
    assert issubclass(SurfaceNotImplementedError, SurfaceError)


def test_complete_subclass_instantiates():
    class Stub(Surface):
        name = "stub"

        def read(self, node_id):
            return None

        def write(self, node):
            return None

        def list(self):
            return []

        def capabilities(self):
            return SurfaceCapabilities(name="stub")

    s = Stub()
    assert s.name == "stub"
    assert s.list() == []


def test_surface_capabilities_defaults():
    caps = SurfaceCapabilities(name="x")
    assert caps.name == "x"
    assert caps.readable is True
    assert caps.writable is True
    assert caps.listable is True
    assert caps.planned is False


def test_surface_capabilities_planned_flag():
    caps = SurfaceCapabilities(name="y", planned=True, readable=False, writable=False)
    assert caps.planned is True
    assert caps.readable is False
