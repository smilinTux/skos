from typer.testing import CliRunner
from skos.cli import app
runner = CliRunner()


def test_capabilities_lists_groups():
    r = runner.invoke(app, ["capabilities"])
    assert r.exit_code == 0
    for g in ("cloud", "comms", "compute", "core"):
        assert g in r.stdout
    assert "skobject" in r.stdout and "garage" in r.stdout


def test_resolve_prints_adapter():
    r = runner.invoke(app, ["resolve", "skobject", "--profile", "local"])
    assert r.exit_code == 0 and "garage" in r.stdout


def test_resolve_override():
    r = runner.invoke(app, ["resolve", "skobject", "--profile", "local", "--adapter", "seaweedfs"])
    assert r.exit_code == 0 and "seaweedfs" in r.stdout
