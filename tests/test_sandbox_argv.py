from skos.autopilot.sandbox import Sandbox, LaunchSpec, AuthMount


def _spec(**kw):
    base = dict(name="claude-code", argv=["claude", "-p", "hi"], image="sandbox-claude:1",
                worktree="/tmp/wt", auth_mounts=[AuthMount("/home/u/.claude/.credentials.json",
                "/home/sbx/.claude/.credentials.json")], auth_env={"X": "1"},
                egress_hosts=["api.anthropic.com"])
    base.update(kw)
    return LaunchSpec(**base)


def test_docker_argv_is_hardened_and_confined():
    argv = Sandbox()._docker_run_argv(_spec(), network="sbxnet", proxy_alias="sbxproxy")
    j = " ".join(argv)
    assert argv[0:2] == ["docker", "run"]
    assert "--rm" in argv and "--network" in argv and "sbxnet" in argv
    assert "--read-only" in argv
    assert "--security-opt" in argv and "no-new-privileges" in j
    assert "--cap-drop" in argv and "ALL" in argv
    assert "--user" in argv
    assert "type=bind,src=/tmp/wt,dst=/work" in j        # worktree RW at /work
    assert "/home/sbx/.claude/.credentials.json" in j and "readonly" in j
    assert any(a.startswith("HTTPS_PROXY=") and "sbxproxy" in a for a in argv)
    assert "/var/run/docker.sock" not in j               # never mount the socket
    assert argv[-3:] == ["claude", "-p", "hi"]           # harness argv is the tail


def test_no_secret_paths_mounted():
    j = " ".join(Sandbox()._docker_run_argv(_spec(), "n", "p"))
    for secret in (".skcapstone", ".hermes", ".ssh", "skvault"):
        assert secret not in j
