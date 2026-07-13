"""Autopilot orchestrator: helpers, phases, run_once, dry-run, kill switch, caps, resume."""
from types import SimpleNamespace

import pytest

from skos.autopilot import orchestrator as orch
from skos.autopilot.orchestrator import Caps, CapLedger, kill_switch_active, stable_qid


def test_kill_switch_env(monkeypatch):
    monkeypatch.setenv("SKOS_AUTOPILOT_OFF", "1")
    assert kill_switch_active(enabled=True) is True


def test_kill_switch_disabled_flag(monkeypatch):
    monkeypatch.delenv("SKOS_AUTOPILOT_OFF", raising=False)
    assert kill_switch_active(enabled=False) is True
    assert kill_switch_active(enabled=True) is False


def test_cap_ledger_exceeded_on_tokens():
    led = CapLedger(Caps(max_tokens_per_run=100, max_usd_per_day=10.0))
    led.add(tokens=60, usd=1.0)
    assert led.exceeded() is False
    led.add(tokens=60, usd=1.0)
    assert led.exceeded() is True


def test_cap_ledger_exceeded_on_usd():
    led = CapLedger(Caps(max_tokens_per_run=10_000, max_usd_per_day=2.0))
    led.add(tokens=1, usd=2.5)
    assert led.exceeded() is True


def test_stable_qid_deterministic():
    a = stable_qid("Merge PR #12 for task X?", "task-x")
    b = stable_qid("Merge PR #12 for task X?", "task-x")
    c = stable_qid("Merge PR #12 for task X?", "task-y")
    assert a == b and a != c and len(a) == 12
