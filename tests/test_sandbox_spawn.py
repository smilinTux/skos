import json
import pytest
from skos.autopilot.sandbox import Sandbox, LaunchSpec
from skos.autopilot.claude_code import HarnessUnavailable


def _spec():
    return LaunchSpec(name="pi", argv=["pi", "-p", "x", "--mode", "json"],
                      image="sandbox-pi:1", worktree="/tmp/wt",
                      egress_hosts=["gw.local"])


def test_spawn_disabled_raises_when_not_live():
    with pytest.raises(HarnessUnavailable):
        Sandbox(live_execution=False).spawn(_spec(), repo_remote_host="github.com", ci_host=None)


def test_spawn_runs_container_and_tears_down(monkeypatch):
    calls = []
    def fake_run(argv, **kw):
        calls.append(argv)
        class P:
            returncode = 0
            stdout = json.dumps({"result": {"ok": True}}) if argv[:2] == ["docker", "run"] else ""
            stderr = ""
        return P()
    sb = Sandbox(live_execution=True)
    monkeypatch.setattr(sb, "_ensure_capable", lambda spec: None)
    monkeypatch.setattr("skos.autopilot.sandbox.subprocess.run", fake_run)
    out = sb.spawn(_spec(), repo_remote_host="github.com", ci_host="ci.local")
    assert out == {"result": {"ok": True}}
    kinds = [c[1] for c in calls if c and c[0] == "docker"]
    assert "network" in kinds and "run" in kinds          # created a network and ran
    assert any(c[0] == "docker" and "network" in c and "rm" in c for c in calls)  # teardown
    # the proxy was started with the assembled allowlist (repo + ci + egress hosts)
    proxy_start = next(c for c in calls if c[0] == "docker" and "run" in c and "-d" in c)
    for host in ("github.com", "ci.local", "gw.local"):
        assert host in proxy_start
