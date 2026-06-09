"""vault-file backend — Fernet AES, encrypted JSON blob under $SK_DATA_ROOT/secrets/."""
from __future__ import annotations

import json
import os

from cryptography.fernet import Fernet, InvalidToken

from skos import paths
from skos.secrets.base import SecretBackend, SecretError


class VaultFileBackend(SecretBackend):
    name = "vault-file"

    def _store_path(self):
        paths.ensure_tree()
        return paths.subdir("secrets") / "vault-file.enc"

    def _key(self) -> bytes:
        env = os.environ.get("SKOS_VAULT_KEY", "").strip()
        if env:
            return env.encode()
        keyfile = paths.subdir("secrets") / "master.key"
        if not keyfile.exists():
            paths.ensure_tree()
            keyfile.write_bytes(Fernet.generate_key())
            keyfile.chmod(0o600)
        return keyfile.read_bytes()

    def _load(self) -> dict:
        p = self._store_path()
        if not p.exists():
            return {}
        try:
            return json.loads(Fernet(self._key()).decrypt(p.read_bytes()))
        except InvalidToken as exc:
            raise SecretError("vault-file decrypt failed (wrong SKOS_VAULT_KEY?).") from exc

    def _save(self, data: dict) -> None:
        p = self._store_path()
        p.write_bytes(Fernet(self._key()).encrypt(json.dumps(data).encode()))
        p.chmod(0o600)

    def set(self, scope: str, key: str, value: str) -> None:
        data = self._load(); data[f"{scope}/{key}"] = value; self._save(data)

    def get(self, scope: str, key: str) -> str:
        data = self._load(); k = f"{scope}/{key}"
        if k not in data:
            raise SecretError(f"No secret {k!r} in vault-file.")
        return data[k]

    def list(self, scope: str | None = None) -> list[str]:
        keys = self._load().keys()
        return sorted(k for k in keys if scope is None or k.startswith(f"{scope}/"))

    def delete(self, scope: str, key: str) -> None:
        data = self._load(); data.pop(f"{scope}/{key}", None); self._save(data)
