"""Tests for the stateful order/shipment gtd-ingest adapter."""
import json

import pytest

from skos.gtd_ingest import GtdCapture, capture, gtd_dir
from skos.adapters import order as order_mod
from skos.adapters.order import OrderAdapter, classify_state, DEFAULT_STATES


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "gtd"))
    yield


def _load(name):
    p = gtd_dir() / name
    return json.loads(p.read_text()) if p.exists() else []


def _seed_battery():
    capture(GtdCapture(
        text="iPhone 13 mini battery ×2 - ordered",
        source="order", source_ref="amazon:113-5638977-2258657",
        status="waiting", context="@errand", priority="low",
        meta={"order": {"vendor": "amazon", "order_id": "113-5638977-2258657",
                        "account": "test@gmail.com", "states": DEFAULT_STATES,
                        "state": "ordered", "complete_on": "delivered"}}))


# ── classifier ───────────────────────────────────────────────────────────────
def test_classify_picks_furthest_state():
    subjects = ["Your order has shipped!", "Your package is out for delivery",
                "Order confirmed"]
    assert classify_state(subjects, DEFAULT_STATES) == "out_for_delivery"


def test_classify_none_when_no_match():
    assert classify_state(["Weekly newsletter", "Re: dinner"], DEFAULT_STATES) is None


def test_classify_never_regresses_on_old_email():
    # a stale "shipped" email present alongside "delivered" -> still delivered
    subjects = ["Your order has shipped!", "Your package was delivered"]
    assert classify_state(subjects, DEFAULT_STATES) == "delivered"


# ── stateful drain ───────────────────────────────────────────────────────────
def test_drain_advances_state_and_notifies(monkeypatch):
    _seed_battery()
    pings = []
    monkeypatch.setattr(order_mod, "_tg_text", lambda msg: pings.append(msg) or True)
    # mail returns an "out for delivery" subject for this order
    monkeypatch.setattr("skos.mail.list_threads",
                        lambda acct, q, maxn=20: [{"subject": "Your package is out for delivery"}])

    written = OrderAdapter().drain()
    assert len(written) == 1
    wf = _load("waiting-for.json")
    assert wf[0]["order"]["state"] == "out_for_delivery"
    assert len(pings) == 1 and "out for delivery" in pings[0]


def test_drain_is_quiet_when_no_change(monkeypatch):
    _seed_battery()
    pings = []
    monkeypatch.setattr(order_mod, "_tg_text", lambda msg: pings.append(msg) or True)
    # mail only ever shows the same "ordered" state
    monkeypatch.setattr("skos.mail.list_threads",
                        lambda acct, q, maxn=20: [{"subject": "Order confirmed"}])
    before = (gtd_dir() / "waiting-for.json").read_text()

    written = OrderAdapter().drain()
    assert written == []                                   # nothing advanced
    assert pings == []                                     # no notification
    assert (gtd_dir() / "waiting-for.json").read_text() == before  # no write


def test_drain_completes_and_archives_on_delivered(monkeypatch):
    _seed_battery()
    monkeypatch.setattr(order_mod, "_tg_text", lambda msg: True)
    monkeypatch.setattr("skos.mail.list_threads",
                        lambda acct, q, maxn=20: [{"subject": "Your package was delivered"}])

    OrderAdapter().drain()
    assert _load("waiting-for.json") == []
    arch = _load("archive.json")
    assert any(i["source_ref"] == "amazon:113-5638977-2258657"
               and i["status"] == "done" and i.get("completed_at") for i in arch)


# ── seed_order helper ─────────────────────────────────────────────────────────
def test_seed_order_creates_then_idempotent():
    from skos.adapters.order import seed_order
    iid, action = seed_order("113-XYZ", "you@gmail.com", eta="2026-07-05",
                             text="battery ×2")
    assert action == "created" and iid
    wf = _load("waiting-for.json")
    assert len(wf) == 1
    o = wf[0]["order"]
    assert o["order_id"] == "113-XYZ" and o["state"] == "ordered"
    assert o["complete_on"] == "delivered" and o["account"] == "you@gmail.com"
    assert wf[0]["source_ref"] == "amazon:113-XYZ"
    # second call is a no-op
    iid2, action2 = seed_order("113-XYZ", "you@gmail.com")
    assert action2 == "exists" and iid2 == iid
    assert len(_load("waiting-for.json")) == 1


# ── ornith fallback wiring (LLM mocked) ───────────────────────────────────────
def test_classify_prefers_deterministic(monkeypatch):
    from skos.adapters import order as om
    called = {"llm": False}
    monkeypatch.setattr(om, "classify_llm", lambda s, st: called.__setitem__("llm", True) or "shipped")
    # deterministic hit -> LLM must NOT be consulted
    out = om.classify(["Your order has shipped!"], DEFAULT_STATES)
    assert out == "shipped" and called["llm"] is False


def test_classify_falls_back_to_llm_on_miss(monkeypatch):
    from skos.adapters import order as om
    monkeypatch.setattr(om, "classify_llm", lambda s, st: "out_for_delivery")
    out = om.classify(["driver has your package, see it tonight"], DEFAULT_STATES)
    assert out == "out_for_delivery"


def test_classify_llm_degrades_to_none_on_gateway_error(monkeypatch):
    from skos.adapters import order as om
    # simulate the gateway being unreachable -> curl/json blows up -> None (no guess)
    monkeypatch.setattr(om.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no gateway")))
    assert om.classify_llm(["anything ambiguous"], DEFAULT_STATES) is None


def test_classify_llm_rejects_out_of_vocab(monkeypatch):
    from skos.adapters import order as om
    class _R:  # fake curl result whose model returns garbage
        stdout = '{"choices":[{"message":{"content":"maybe delivered lol"}}]}'
    monkeypatch.setattr(om.subprocess, "run", lambda *a, **k: _R())
    # "maybe_delivered_lol" is not a valid state -> None
    assert om.classify_llm(["x"], DEFAULT_STATES) is None
