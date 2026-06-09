from typer.testing import CliRunner
from skos.cli import app
from skos import registry
runner = CliRunner()


def test_install_records(data_root, tmp_path, monkeypatch):
    import skos.packaging.oci as oci
    monkeypatch.setattr(oci.OciAdapter, "materialize",
                        lambda self, a: __import__("skos.packaging.base", fromlist=["InstallResult"]).InstallResult(a.name, "oci", a.packaging.oci.image, True))
    y = tmp_path / "app.yaml"
    y.write_text("name: capauth\ncapability: identity\npackaging: {oci: {image: i:1, ports: [8088]}}\n")
    r = runner.invoke(app, ["install", str(y)])
    assert r.exit_code == 0
    assert "capauth" in registry.list_installed()
