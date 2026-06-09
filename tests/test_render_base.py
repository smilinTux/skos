"""Tests for skos.render.base — Renderer ABC."""
import pytest
from skos.render.base import Renderer, RenderError


def test_renderer_is_abstract():
    with pytest.raises(TypeError):
        Renderer()  # abstract method `render` not implemented


def test_renderer_subclass_must_implement_render():
    class Incomplete(Renderer):
        platform = "test"
        # missing render()

    with pytest.raises(TypeError):
        Incomplete()


def test_renderer_subclass_with_render():
    class Stub(Renderer):
        platform = "stub"

        def render(self, descriptor, profile="local"):
            return "stub manifest"

    r = Stub()
    assert r.platform == "stub"
    assert r.render(None) == "stub manifest"


def test_render_error_is_value_error():
    assert issubclass(RenderError, ValueError)
