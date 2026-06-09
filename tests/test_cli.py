from typer.testing import CliRunner
from skos.cli import app

runner = CliRunner()


def test_path_subdir(data_root):
    r = runner.invoke(app, ["path", "apps"])
    assert r.exit_code == 0
    assert str(data_root.resolve() / "apps") in r.stdout


def test_profile_prints_local(monkeypatch):
    monkeypatch.delenv("SKOS_PROFILE", raising=False)
    r = runner.invoke(app, ["profile"])
    assert r.exit_code == 0 and "local" in r.stdout


def test_describe_valid(tmp_path, data_root):
    p = tmp_path / "app.yaml"
    p.write_text("name: x\ncapability: c\npackaging: {oci: {image: i:1}}\n")
    r = runner.invoke(app, ["describe", str(p)])
    assert r.exit_code == 0 and "x" in r.stdout
