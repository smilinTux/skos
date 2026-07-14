import json

from skos.autopilot.adapters.pi import PiAdapter
from skos.autopilot.sandbox import Sandbox


def _a(**kw):
    return PiAdapter(Sandbox(), **kw)


def test_argv_and_image():
    a = _a()
    assert a._argv("P") == ["pi", "-p", "P", "--mode", "json", "--no-session"]
    assert a._image() == "sandbox-pi:1"
    assert a.name == "pi"


def test_local_model_routes_to_skgateway_via_injected_models_json():
    a = _a(model="ornith-big", base_url="http://localhost:18780/v1")
    assert a._argv("P") == ["pi", "-p", "P", "--mode", "json", "--no-session",
                            "--model", "skgw/ornith-big", "--api-key", "sk-local"]
    env = a._auth_env()
    assert env["PI_CODING_AGENT_DIR"] == "/agent"
    assert "OPENAI_BASE_URL" not in env            # pi ignores it; hits real OpenAI otherwise
    assert a._auth_mounts() == []                  # local: no external cred to mount
    cfg = a._config_files()
    models = json.loads(cfg["/agent/models.json"])
    skgw = models["providers"]["skgw"]
    assert skgw["api"] == "openai-completions"
    assert skgw["baseUrl"] == "http://localhost:18780/v1"
    assert skgw["compat"]["supportsDeveloperRole"] is False
    assert skgw["models"][0]["id"] == "ornith-big"
    assert skgw["models"][0]["limit"]["output"] == 131072      # generous default (ornith is uncapped)


def test_no_base_url_means_no_config_files_and_plain_argv():
    a = _a()
    assert a._config_files() == {}
    assert a._argv("P") == ["pi", "-p", "P", "--mode", "json", "--no-session"]


def test_run_timeout_defaults_to_sandbox_default_but_is_overridable():
    # pi terminates on its own (fast), so it keeps the sandbox default rather than
    # opencode's aggressive cap; the knob still lets a caller bound a long run.
    a = PiAdapter(model="ornith-tiny", base_url="http://gw:18780/v1")
    assert a.sandbox.run_timeout == 1800                       # sandbox default, uncapped
    b = PiAdapter(model="ornith-tiny", base_url="http://gw:18780/v1", run_timeout=600)
    assert b.sandbox.run_timeout == 600


def test_parse_extracts_model_reply_dict():
    a = _a()
    # already-parsed object
    assert a._parse({"verdict": "valid", "reason": "ok"}) == {"verdict": "valid", "reason": "ok"}
    # nested under result
    assert a._parse({"result": {"score": 5}}) == {"score": 5}
    # result carries a JSON string (model reply as text)
    assert a._parse({"result": "{\"verdict\": \"stale\"}"}) == {"verdict": "stale"}
    # unparseable -> empty dict, never crash
    assert a._parse({"result": "not json"}) == {}


def test_parse_event_stream_ndjson():
    a = _a()
    stream = ('{"type":"text","part":{"type":"text",'
              '"text":"{\\"score\\":5,\\"passed\\":true}"}}\n')
    assert a._parse({"result": stream}) == {"score": 5, "passed": True}


def test_parse_real_pi_event_schema():
    # pi --mode json: reply is in the assistant message_end event's content[].text
    a = _a()
    stream = (
        '{"type":"turn_start"}\n'
        '{"type":"message_end","message":{"role":"assistant","content":'
        '[{"type":"text","text":"{\\"verdict\\":\\"valid\\",\\"reason\\":\\"ok\\"}"}]}}\n'
        '{"type":"agent_end"}\n')
    assert a._parse({"result": stream}) == {"verdict": "valid", "reason": "ok"}


def test_config_files_budget_overridable():
    import json
    from skos.autopilot.sandbox import Sandbox
    from skos.autopilot.adapters.pi import PiAdapter
    a = PiAdapter(Sandbox(), model="ornith-big", base_url="http://x/v1", max_tokens=262144)
    lim = json.loads(a._config_files()["/agent/models.json"])["providers"]["skgw"]["models"][0]["limit"]
    assert lim["output"] == 262144 and lim["context"] == 262144
    # default when unset is the generous ceiling
    b = PiAdapter(Sandbox(), model="m", base_url="http://x/v1")
    assert json.loads(b._config_files()["/agent/models.json"])["providers"]["skgw"]["models"][0]["limit"]["output"] == 131072
