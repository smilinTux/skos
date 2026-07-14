from skos.autopilot.adapters.pi import PiAdapter
from skos.autopilot.sandbox import Sandbox


def _a(**kw):
    return PiAdapter(Sandbox(), **kw)


def test_argv_and_image():
    a = _a()
    assert a._argv("P") == ["pi", "-p", "P", "--mode", "json", "--no-session"]
    assert a._image() == "sandbox-pi:1"
    assert a.name == "pi"


def test_local_model_routes_to_skgateway_with_no_external_cred():
    a = _a(model="sk-default", base_url="http://localhost:18780/v1")
    env = a._auth_env()
    assert env["OPENAI_BASE_URL"] == "http://localhost:18780/v1"
    assert env["PI_MODEL"] == "sk-default"
    assert a._auth_mounts() == []                 # local: no external cred to mount


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
