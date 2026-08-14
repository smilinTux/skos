"""The watchdog-source adapter port: registry resolution + fail-safe collect."""
import pytest

from skos.watchdog.events import WatchdogEvent
from skos.watchdog.port import (
    WatchdogSourceAdapter, Window, AdapterRegistry, registry, collect_safe, source_ok,
)
from skos.adapter import AdapterError


def _window():
    return Window(since="2026-08-10T00:00:00Z", until="2026-08-10T06:00:00Z")


def test_registry_registers_and_resolves_an_adapter():
    local = AdapterRegistry()  # isolated registry, does not touch the module-level one

    @local.register
    class FleetAdapter(WatchdogSourceAdapter):
        name = "fleet-test"

        def collect(self, window):
            return []

    assert "fleet-test" in local.available_for("watchdog-source")
    resolved = local.lookup("watchdog-source", "fleet-test")
    assert resolved is FleetAdapter


def test_module_registry_registers_under_watchdog_source_capability():
    @registry.register
    class ItilAdapter(WatchdogSourceAdapter):
        name = "itil-port-test"

        def collect(self, window):
            return []

    assert "itil-port-test" in registry.available_for("watchdog-source")
    assert "itil-port-test" not in registry.available_for("gtd-ingest")


def test_registering_without_a_name_is_rejected():
    local = AdapterRegistry()

    class NoName(WatchdogSourceAdapter):
        pass

    with pytest.raises(AdapterError):
        local.register(NoName)


def test_lookup_of_unregistered_source_raises():
    local = AdapterRegistry()
    with pytest.raises(AdapterError):
        local.lookup("watchdog-source", "nope")


def test_collect_safe_passes_through_normal_events():
    class OkAdapter(WatchdogSourceAdapter):
        name = "ok"

        def collect(self, window):
            return [WatchdogEvent(ts=window.until, source="ok", kind="Thing",
                                   object="x", severity="info", summary="s", ref="ok:1")]

    events = collect_safe(OkAdapter(), _window())
    assert len(events) == 1
    assert events[0].source == "ok"
    assert source_ok(events, "ok") is True


def test_collect_safe_degrades_a_raising_adapter_to_one_synthetic_event():
    class BrokenAdapter(WatchdogSourceAdapter):
        name = "broken"

        def collect(self, window):
            raise RuntimeError("connection refused")

    events = collect_safe(BrokenAdapter(), _window())
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == "SourceUnavailable"
    assert ev.source == "broken"
    assert ev.severity == "notable"
    assert "connection refused" in ev.summary
    assert source_ok(events, "broken") is False


def test_collect_safe_never_raises_even_on_unexpected_exception_types():
    class WeirdAdapter(WatchdogSourceAdapter):
        name = "weird"

        def collect(self, window):
            raise KeyError("missing field")

    events = collect_safe(WeirdAdapter(), _window())
    assert len(events) == 1
    assert events[0].kind == "SourceUnavailable"


def test_collect_safe_tolerates_none_return():
    class NoneAdapter(WatchdogSourceAdapter):
        name = "none-src"

        def collect(self, window):
            return None

    assert collect_safe(NoneAdapter(), _window()) == []


def test_unimplemented_collect_raises_not_implemented():
    class Bare(WatchdogSourceAdapter):
        name = "bare"

    with pytest.raises(NotImplementedError):
        Bare().collect(_window())


def test_window_to_dict_matches_digest_window_shape():
    w = _window()
    assert w.to_dict() == {"from": "2026-08-10T00:00:00Z", "to": "2026-08-10T06:00:00Z"}
