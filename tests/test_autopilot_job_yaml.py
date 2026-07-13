"""The autopilot-daily scheduler block renders to the locked spec-section-13 YAML."""
import yaml

from skos.autopilot import config


def test_render_autopilot_job_yaml():
    block = config.render_autopilot_job_yaml()
    parsed = yaml.safe_load(block)
    assert set(parsed) == {"autopilot-daily"}
    job = parsed["autopilot-daily"]
    assert job["schedule"] == "30 6 * * *"
    assert job["type"] == "shell"
    assert job["nodes"] == ["noroc2027"]              # single writer, required (section 8)
    assert job["retries"] == 0                        # no auto-retry (could re-merge)
    assert job["catchup"] is False                    # never stack missed runs
    assert job["notify"] == "on_failure"
    cmd = job["command"]
    assert "/usr/bin/flock -n" in cmd
    assert "autopilot-daily.lock" in cmd
    assert "sk-cron-run.sh autopilot-daily" in cmd
    assert "skos autopilot run --once" in cmd
