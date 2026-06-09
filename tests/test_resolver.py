import pytest
from skos import resolver


def test_resolve_default():
    assert resolver.resolve("skobject", profile="local") == "garage"


def test_resolve_profile_override():
    # skvault overrides per profile? use a known default; profile override path:
    assert resolver.resolve("skfence", profile="local") == "traefik"


def test_explicit_override_wins():
    assert resolver.resolve("skobject", profile="local", override="seaweedfs") == "seaweedfs"


def test_override_must_be_known():
    with pytest.raises(resolver.ResolveError):
        resolver.resolve("skobject", profile="local", override="bogus")


def test_unknown_capability_raises():
    with pytest.raises(resolver.ResolveError):
        resolver.resolve("nope", profile="local")
