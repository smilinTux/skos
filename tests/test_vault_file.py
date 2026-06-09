import pytest
from skos.secrets.vault_file import VaultFileBackend
from skos.secrets.base import SecretError


def test_set_get_roundtrip(data_root, vault_key):
    b = VaultFileBackend()
    b.set("cloud", "cloudflare_dns_token", "secret-123")
    assert b.get("cloud", "cloudflare_dns_token") == "secret-123"


def test_encrypted_at_rest(data_root, vault_key):
    b = VaultFileBackend()
    b.set("cloud", "k", "PLAINTEXT_SHOULD_NOT_APPEAR")
    blob = (data_root.resolve() / "secrets" / "vault-file.enc").read_bytes()
    assert b"PLAINTEXT_SHOULD_NOT_APPEAR" not in blob


def test_list_scoped(data_root, vault_key):
    b = VaultFileBackend()
    b.set("cloud", "a", "1"); b.set("cloud", "b", "2"); b.set("core", "c", "3")
    assert sorted(b.list("cloud")) == ["cloud/a", "cloud/b"]
    assert "core/c" in b.list()


def test_get_missing_raises(data_root, vault_key):
    with pytest.raises(SecretError):
        VaultFileBackend().get("cloud", "nope")


def test_delete(data_root, vault_key):
    b = VaultFileBackend(); b.set("x", "y", "z"); b.delete("x", "y")
    with pytest.raises(SecretError):
        b.get("x", "y")
