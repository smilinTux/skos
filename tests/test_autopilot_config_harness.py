from skos.autopilot.config import Config


def test_harness_fields_load_from_yaml(tmp_path):
    p = tmp_path / "autopilot.yaml"
    p.write_text(
        "enabled: true\n"
        "harness: pi\n"
        "harness_model: sk-default\n"
        "harness_base_url: http://localhost:18780/v1\n"
        "live_execution: true\n"
        "mcp_endpoints: [localhost, api.anthropic.com]\n"
        "sandbox_image: sandbox-pi:2\n")
    c = Config.load(p)
    assert c.harness == "pi"
    assert c.harness_model == "sk-default"
    assert c.harness_base_url == "http://localhost:18780/v1"
    assert c.live_execution is True
    assert c.mcp_endpoints == ["localhost", "api.anthropic.com"]
    assert c.sandbox_image == "sandbox-pi:2"


def test_harness_fields_default_safely():
    c = Config()
    assert c.live_execution is False           # posture stays off by default
    assert c.harness_model is None and c.harness_base_url is None
    assert c.mcp_endpoints == [] and c.sandbox_image is None


def test_repospec_sandbox_image_optional():
    from skos.autopilot.types import RepoSpec
    r = RepoSpec("n", "/p", "main", "ap", "true", "none")
    assert r.sandbox_image is None
    r2 = RepoSpec("n", "/p", "main", "ap", "true", "none", sandbox_image="img:1")
    assert r2.sandbox_image == "img:1"
