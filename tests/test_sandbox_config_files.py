import json
import os

from skos.autopilot.sandbox import AuthMount, LaunchSpec, Sandbox


def _spec(**kw):
    base = dict(name="pi", argv=["pi", "-p", "hi"], image="sandbox-pi:1", worktree="/tmp/wt")
    base.update(kw)
    return LaunchSpec(**base)


def test_extra_mounts_are_bound_readonly_with_writable_parent_tmpfs():
    argv = Sandbox()._docker_run_argv(
        _spec(), network="n", proxy_alias="p",
        extra_mounts=[AuthMount("/h/models.json", "/agent/models.json")])
    j = " ".join(argv)
    assert "type=bind,src=/h/models.json,dst=/agent/models.json,readonly" in j
    assert "--tmpfs" in argv and "/agent:mode=1777" in argv


def test_extra_mounts_fold_alongside_auth_mounts():
    spec = _spec(auth_mounts=[AuthMount("/home/u/.claude/.credentials.json",
                                        "/home/sbx/.claude/.credentials.json")])
    argv = Sandbox()._docker_run_argv(
        spec, network="n", proxy_alias="p",
        extra_mounts=[AuthMount("/h/models.json", "/agent/models.json")])
    j = " ".join(argv)
    assert "/home/sbx/.claude/.credentials.json" in j and "readonly" in j
    assert "type=bind,src=/h/models.json,dst=/agent/models.json,readonly" in j
    assert "/agent:mode=1777" in argv


def test_spawn_writes_config_files_to_temp_dir_and_mounts_them_then_cleans_up(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        if argv[:2] == ["docker", "run"] and "--mount" in argv:
            for i, a in enumerate(argv):
                if a == "--mount" and "sbxcfg-" in argv[i + 1]:
                    src = argv[i + 1].split("src=", 1)[1].split(",dst=")[0]
                    seen["src"] = src
                    with open(src) as fh:
                        seen["content"] = fh.read()
                    seen["cfg_dir"] = os.path.dirname(src)

        class P:
            returncode = 0
            stdout = json.dumps({"result": {"ok": True}}) if argv[:2] == ["docker", "run"] else ""
            stderr = ""
        return P()

    sb = Sandbox(live_execution=True)
    monkeypatch.setattr(sb, "_ensure_capable", lambda spec: None)
    monkeypatch.setattr("skos.autopilot.sandbox.subprocess.run", fake_run)
    spec = _spec(config_files={"/agent/models.json": '{"x":1}'})
    out = sb.spawn(spec, repo_remote_host=None, ci_host=None)
    assert out == {"result": {"ok": True}}
    assert seen["content"] == '{"x":1}'
    assert not os.path.exists(seen["cfg_dir"])            # temp dir cleaned up in finally


def test_spawn_with_no_config_files_mounts_nothing_extra(monkeypatch):
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
    sb.spawn(_spec(), repo_remote_host=None, ci_host=None)
    harness_run = next(c for c in calls if c[:2] == ["docker", "run"] and "--rm" in c)
    assert "sbxcfg-" not in " ".join(harness_run)
