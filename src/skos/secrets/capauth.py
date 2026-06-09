"""capauth backend — sovereign PGP secret storage (stub; full impl delegates to the capauth agent)."""
from __future__ import annotations

from skos.secrets.base import SecretBackend, SecretError


class CapAuthBackend(SecretBackend):
    name = "capauth"

    def _todo(self):
        raise SecretError("capauth secret backend not yet implemented — use vault-file (default).")

    def set(self, scope, key, value): self._todo()
    def get(self, scope, key): self._todo()
    def list(self, scope=None): self._todo()
    def delete(self, scope, key): self._todo()
