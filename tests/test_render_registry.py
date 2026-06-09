"""Tests for skos.render registry + get_renderer."""
import pytest
from skos.render import RENDERERS, get_renderer, ComposeRenderer, SwarmRenderer, KubernetesRenderer
from skos.render.base import Renderer


def test_registry_has_compose():
    assert "compose" in RENDERERS


def test_registry_has_swarm():
    assert "swarm" in RENDERERS


def test_registry_has_kubernetes():
    assert "kubernetes" in RENDERERS


def test_all_renderers_are_renderer_instances():
    for platform, renderer in RENDERERS.items():
        assert isinstance(renderer, Renderer), f"{platform} renderer is not a Renderer"


def test_get_renderer_compose():
    r = get_renderer("compose")
    assert isinstance(r, ComposeRenderer)


def test_get_renderer_swarm():
    r = get_renderer("swarm")
    assert isinstance(r, SwarmRenderer)


def test_get_renderer_kubernetes():
    r = get_renderer("kubernetes")
    assert isinstance(r, KubernetesRenderer)


def test_get_renderer_unknown_raises():
    with pytest.raises(KeyError, match="unknown platform" if False else "Unknown platform"):
        get_renderer("nonexistent")


def test_get_renderer_error_lists_supported():
    with pytest.raises(KeyError) as exc_info:
        get_renderer("bad-platform")
    msg = str(exc_info.value)
    assert "compose" in msg or "kubernetes" in msg


def test_platform_attribute_matches_registry_key():
    for key, renderer in RENDERERS.items():
        assert renderer.platform == key
