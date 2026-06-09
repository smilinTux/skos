from skos import secrets


def test_default_backend_is_vault_file(data_root, vault_key):
    b = secrets.get_backend(profile="local")
    assert b.name == "vault-file"


def test_explicit_capauth_stub_raises_on_use(data_root):
    b = secrets.get_backend("capauth")
    import pytest
    with pytest.raises(secrets.SecretError):
        b.get("x", "y")
