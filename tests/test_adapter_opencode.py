from skos.autopilot.adapters.opencode import OpenCodeAdapter
from skos.autopilot.sandbox import Sandbox


def _a(**kw):
    return OpenCodeAdapter(Sandbox(), **kw)


def test_argv_default_and_with_model():
    assert _a()._argv("P") == ["opencode", "run", "P", "--pure"]
    assert _a(model="ollama/qwen")._argv("P") == ["opencode", "run", "P", "--pure", "--model", "ollama/qwen"]


def test_image_and_local_routing():
    a = _a(base_url="http://localhost:18780/v1", model="sk-default")
    assert a._image() == "sandbox-opencode:1" and a.name == "opencode"
    assert a._auth_env()["OPENAI_BASE_URL"] == "http://localhost:18780/v1"
    assert a._auth_mounts() == []


def test_parse_defensive():
    a = _a()
    assert a._parse({"score": 5}) == {"score": 5}
    assert a._parse({"result": {"verdict": "valid"}}) == {"verdict": "valid"}
    assert a._parse({"result": "{\"passed\": true}"}) == {"passed": True}
    assert a._parse({"result": "junk"}) == {}
