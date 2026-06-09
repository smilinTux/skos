"""SecretBackend — the skvault adapter family. Mirrors SKStacks v2 SKSecretBackend (get/set/list)."""
from __future__ import annotations

import abc

from skos.adapter import Adapter


class SecretError(RuntimeError):
    pass


class SecretBackend(Adapter, abc.ABC):
    capability = "skvault"

    @abc.abstractmethod
    def set(self, scope: str, key: str, value: str) -> None: ...

    @abc.abstractmethod
    def get(self, scope: str, key: str) -> str: ...

    @abc.abstractmethod
    def list(self, scope: str | None = None) -> list[str]: ...

    @abc.abstractmethod
    def delete(self, scope: str, key: str) -> None: ...
