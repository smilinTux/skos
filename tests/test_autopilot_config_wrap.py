"""Test for module-level config.load() wrapper and dry_run/dry_run_summary fields."""
from skos.autopilot import config


def test_module_load_returns_config(tmp_path, monkeypatch):
    monkeypatch.delenv("SKOS_AUTOPILOT_CONFIG", raising=False)
    cfg = config.load(tmp_path / "none.yaml")
    assert isinstance(cfg, config.Config)
    assert cfg.dry_run is True            # default is read-only
    assert cfg.dry_run_summary is False
