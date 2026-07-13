from skos.autopilot.config import Config, config_path

SAMPLE = """
enabled: true
harness: claude-code
allowed_tools: [Read, Edit, Write, Bash, mcp__skcapstone__coord_score]
automerge_repos: [skos]
digest_chat: "chef-dm"
epic_id: "1b4ab47a"
caps:
  max_concurrent: 2
  new_tasks_per_run: 5
repo_map:
  skos:
    path: /home/cbrd21/clawd/skos
    base_branch: main
    integration_branch: autopilot/integration
    test_cmd: "pytest -q"
    ci: github-actions
    coverage_cmd: "pytest --cov --cov-report=xml"
    automerge: true
  skcapstone:
    path: /home/cbrd21/clawd/skcapstone-repos/skcapstone
    base_branch: main
    integration_branch: autopilot/integration
    test_cmd: "pytest -q"
    ci: none
"""


def test_missing_file_returns_disabled_default(tmp_path, monkeypatch):
    monkeypatch.delenv("SKOS_AUTOPILOT_CONFIG", raising=False)
    cfg = Config.load(tmp_path / "nope.yaml")
    assert cfg.enabled is False
    assert cfg.harness == "claude-code"
    assert cfg.repo_map == {}
    assert cfg.caps.max_concurrent == 3
    assert cfg.repo("anything") is None


def test_parse_and_repo_resolution(tmp_path, monkeypatch):
    p = tmp_path / "autopilot.yaml"
    p.write_text(SAMPLE)
    monkeypatch.setenv("SKOS_AUTOPILOT_CONFIG", str(p))
    assert config_path() == p
    cfg = Config.load()
    assert cfg.enabled is True and cfg.automerge_repos == ["skos"]
    assert cfg.digest_chat == "chef-dm" and cfg.epic_id == "1b4ab47a"
    assert cfg.caps.max_concurrent == 2 and cfg.caps.new_tasks_per_run == 5
    assert cfg.caps.max_usd_per_day == 25.0          # untouched default preserved
    skos = cfg.repo("skos")
    assert skos is not None and skos.name == "skos"  # name injected from the key
    assert skos.ci == "github-actions" and skos.automerge is True
    assert skos.min_diff_coverage == 0.8             # RepoSpec default, not in yaml
    assert cfg.repo("skcapstone").ci == "none"
    assert cfg.repo("unknown") is None
