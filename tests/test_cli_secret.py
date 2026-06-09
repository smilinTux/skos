from typer.testing import CliRunner
from skos.cli import app
runner = CliRunner()


def test_secret_set_get(data_root, vault_key):
    assert runner.invoke(app, ["secret", "set", "cloud/cf_token", "abc123"]).exit_code == 0
    r = runner.invoke(app, ["secret", "get", "cloud/cf_token"])
    assert r.exit_code == 0 and "abc123" in r.stdout


def test_secret_list(data_root, vault_key):
    runner.invoke(app, ["secret", "set", "core/pgp", "k"])
    r = runner.invoke(app, ["secret", "list"])
    assert "core/pgp" in r.stdout
