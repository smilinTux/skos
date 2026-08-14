"""WatchdogEvent: shape, round-tripping, severity validation, fail-safe marker."""
import json

import pytest

from skos.watchdog.events import (
    WatchdogEvent, WatchdogLink, WatchdogEventError, SEVERITIES, source_unavailable,
)


def _event(**kw):
    base = dict(
        ts="2026-08-10T06:12:03Z", source="fleet", kind="ServiceCrashLoop",
        object="skchat-daemon@dot41", severity="problem",
        summary="skchat daemon on .41 restarted 4 times.",
        link=WatchdogLink(uri="skworld://skchat/ops/daemon", http="https://atlas.skworld.io/"),
        ref="fleet:dot41:2026-08-10T06:12:03Z:ServiceCrashLoop:skchat-daemon",
    )
    base.update(kw)
    return WatchdogEvent(**base)


def test_event_has_the_one_shape():
    e = _event()
    d = e.to_dict()
    assert set(d.keys()) == {"ts", "source", "kind", "object", "severity",
                              "summary", "link", "ref", "meta"}
    assert d["link"] == {"uri": "skworld://skchat/ops/daemon", "http": "https://atlas.skworld.io/"}


def test_to_dict_round_trips_through_from_dict():
    e = _event()
    d = e.to_dict()
    e2 = WatchdogEvent.from_dict(d)
    assert e2 == e


def test_to_dict_round_trips_through_json():
    e = _event(meta={"restarts": 4})
    payload = json.dumps(e.to_dict())
    reloaded = WatchdogEvent.from_dict(json.loads(payload))
    assert reloaded == e
    assert reloaded.meta == {"restarts": 4}


def test_link_defaults_to_empty_but_present():
    e = _event(link=WatchdogLink())
    d = e.to_dict()
    assert d["link"] == {"uri": "", "http": ""}


def test_link_accepts_a_plain_dict_too():
    e = WatchdogEvent(ts="t", source="s", kind="k", object="o", severity="info",
                       summary="sum", link={"uri": "u", "http": "h"})
    assert isinstance(e.link, WatchdogLink)
    assert e.link.uri == "u" and e.link.http == "h"


@pytest.mark.parametrize("severity", SEVERITIES)
def test_all_three_severities_are_valid(severity):
    e = _event(severity=severity)
    assert e.severity == severity


def test_invalid_severity_rejected_loudly():
    with pytest.raises(WatchdogEventError):
        _event(severity="critical")


def test_from_dict_tolerates_missing_link_and_meta():
    e = WatchdogEvent.from_dict({
        "ts": "t", "source": "s", "kind": "k", "object": "o",
        "severity": "info", "summary": "sum", "ref": "r",
    })
    assert e.link == WatchdogLink()
    assert e.meta == {}


def test_from_dict_defaults_missing_severity_to_info():
    e = WatchdogEvent.from_dict({"ts": "t", "source": "s", "kind": "k",
                                  "object": "o", "summary": "sum"})
    assert e.severity == "info"


def test_source_unavailable_is_notable_never_problem_or_info():
    ev = source_unavailable("fleet", ts="2026-08-10T00:00:00Z", error="timeout")
    assert ev.severity == "notable"
    assert ev.kind == "SourceUnavailable"
    assert ev.source == "fleet"
    assert ev.object == "fleet"
    assert "timeout" in ev.summary
    assert ev.ref  # stable, non-empty dedupe key
