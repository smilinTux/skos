from skos.secrets.conformance import assert_secret_conforms
from skos.secrets.vault_file import VaultFileBackend


def test_vault_file_conforms(data_root, vault_key):
    assert_secret_conforms(VaultFileBackend())
