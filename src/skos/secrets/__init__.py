"""skvault backends + a default registry wired into the #2 resolver."""
from skos.adapter import AdapterRegistry
from skos import resolver
from skos.secrets.base import SecretBackend, SecretError
from skos.secrets.vault_file import VaultFileBackend
from skos.secrets.capauth import CapAuthBackend

REGISTRY = AdapterRegistry()
REGISTRY.register(VaultFileBackend)
REGISTRY.register(CapAuthBackend)


def get_backend(name: str | None = None, *, profile: str = "local") -> SecretBackend:
    chosen = name or resolver.resolve("skvault", profile=profile)
    return REGISTRY.lookup("skvault", chosen)()  # type: ignore[abstract]


__all__ = ["SecretBackend", "SecretError", "VaultFileBackend", "CapAuthBackend", "REGISTRY", "get_backend"]
