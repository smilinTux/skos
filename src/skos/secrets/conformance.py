"""Conformance for SecretBackend adapters: set->get roundtrip, list, delete, missing-raises."""
from __future__ import annotations

from skos.secrets.base import SecretBackend, SecretError


def assert_secret_conforms(backend: SecretBackend) -> None:
    backend.set("conf", "k1", "v1")
    assert backend.get("conf", "k1") == "v1", "set/get roundtrip failed"
    assert "conf/k1" in backend.list("conf"), "list() missing the key"
    backend.delete("conf", "k1")
    try:
        backend.get("conf", "k1")
    except SecretError:
        return
    raise AssertionError("get() after delete should raise SecretError")
