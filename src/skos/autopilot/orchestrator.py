"""The harness-agnostic meta-orchestrator: phases 0-3, dry-run, kill switch,
caps, resume. Engineering executor internals live in engineering.py (Phase E);
here we only wire the phases, routing, decision queue, and guardrails.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .types import WorkItem, AssessBrief, Verdict, GateResult, DecisionItem
from .executor import EXECUTORS
from .config import Caps
from . import journal


@dataclass
class CapLedger:
    """Running token/dollar tally, checked between items."""
    caps: Caps
    tokens: int = 0
    usd: float = 0.0

    def add(self, tokens: int = 0, usd: float = 0.0) -> None:
        self.tokens += int(tokens or 0)
        self.usd += float(usd or 0.0)

    def exceeded(self) -> bool:
        return (self.tokens > self.caps.max_tokens_per_run
                or self.usd > self.caps.max_usd_per_day)


def kill_switch_active(enabled: bool) -> bool:
    """True when the run must stop cleanly: env override or disabled config."""
    if os.environ.get("SKOS_AUTOPILOT_OFF") == "1":
        return True
    return not enabled


def stable_qid(prompt: str, action_ref: str | None) -> str:
    """Deterministic 12-char decision id over (action_ref, prompt)."""
    return hashlib.sha256(f"{action_ref}|{prompt}".encode("utf-8")).hexdigest()[:12]
