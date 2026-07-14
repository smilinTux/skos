from skos.autopilot.adapters.opencode import OpenCodeAdapter
from skos.autopilot.sandbox import Sandbox


def _a(**kw):
    return OpenCodeAdapter(Sandbox(), **kw)


def test_argv_default_and_with_model():
    # the prompt is NOT a positional arg: `opencode run "<msg>"` silently no-ops;
    # it is fed via stdin instead (see _stdin_for).
    assert _a()._argv("P") == ["opencode", "run", "--format", "json", "--pure"]
    assert _a(model="nvidia/x")._argv("P") == [
        "opencode", "run", "--format", "json", "--pure", "--model", "nvidia/x"]


def test_stdin_for_feeds_the_prompt():
    assert _a()._stdin_for("P") == "P"


def test_image_and_local_routing():
    a = _a(base_url="http://localhost:18780/v1", model="sk-default")
    assert a._image() == "sandbox-opencode:1" and a.name == "opencode"
    assert a._auth_env()["OPENCODE_CONFIG"] == "/cfg/opencode.json"
    assert a._auth_mounts() == []


def test_parse_defensive():
    a = _a()
    assert a._parse({"score": 5}) == {"score": 5}
    assert a._parse({"result": {"verdict": "valid"}}) == {"verdict": "valid"}
    assert a._parse({"result": "{\"passed\": true}"}) == {"passed": True}
    assert a._parse({"result": "junk"}) == {}


# Real captured `opencode run ... --format json` output: an NDJSON event stream
# whose final `text` event carries the model reply as part.text.
REAL_OPENCODE = (
    '{"type":"step_start","sessionID":"s1","part":{"type":"step-start"}}\n'
    '{"type":"text","sessionID":"s1","part":{"type":"text",'
    '"text":"{\\"verdict\\":\\"valid\\",\\"reason\\":\\"ok\\"}"}}\n')


def test_parse_real_event_stream():
    a = _a()
    assert a._parse({"result": REAL_OPENCODE}) == {"verdict": "valid", "reason": "ok"}


# opencode's first assistant text chunk is the model's direct JSON reply; it then
# agentic-loops with more chunks (rambling prose). The parser must take the FIRST
# valid-JSON reply, not the last, or the rambling clobbers the real answer.
OPENCODE_THEN_RAMBLE = (
    '{"type":"step_start","sessionID":"s1","part":{"type":"step-start"}}\n'
    '{"type":"text","sessionID":"s1","part":{"type":"text",'
    '"text":"{\\"verdict\\":\\"valid\\"}"}}\n'
    '{"type":"text","sessionID":"s1","part":{"type":"text",'
    '"text":"## rambling further exploration of the task..."}}\n')


def test_parse_takes_first_valid_json_not_last_rambling_text():
    a = _a()
    assert a._parse({"result": OPENCODE_THEN_RAMBLE}) == {"verdict": "valid"}


def test_config_injection_to_skgateway():
    import json
    from skos.autopilot.sandbox import Sandbox
    from skos.autopilot.adapters.opencode import OpenCodeAdapter
    a = OpenCodeAdapter(Sandbox(), model="ornith-big",
                        base_url="http://172.17.0.1:18780/v1", max_tokens=131072)
    # argv routes to the injected skgw provider
    assert a._argv("P") == ["opencode", "run", "--format", "json", "--pure",
                            "--model", "skgw/ornith-big"]
    assert a._auth_env() == {"OPENCODE_CONFIG": "/cfg/opencode.json"}
    cf = a._config_files()
    cfg = json.loads(cf["/cfg/opencode.json"])
    prov = cfg["provider"]["skgw"]
    assert prov["npm"] == "@ai-sdk/openai-compatible"
    assert prov["options"]["baseURL"] == "http://172.17.0.1:18780/v1"
    assert prov["models"]["ornith-big"]["limit"]["output"] == 131072
    # no base_url -> no injection, plain model
    b = OpenCodeAdapter(Sandbox(), model="opencode/deepseek-v4-flash-free")
    assert b._config_files() == {} and b._auth_env() == {}
    assert b._argv("P") == ["opencode", "run", "--format", "json", "--pure",
                            "--model", "opencode/deepseek-v4-flash-free"]
