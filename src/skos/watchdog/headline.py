"""The model-rendered headline (WD-3), with a strict no-model fallback.

Spec section 6.4: "Exactly one model call renders the headline narrative
('what mattered yesterday, in six sentences') through skgateway sk-default,
with a strict no-model fallback to a pure template so the digest NEVER fails
on a model outage. The model never invents lines: it summarizes the
already-assembled event list."

Endpoint/model come from env with the standard skos defaults, matching
`gtd_triage.py` / `adapters/order.py` exactly -- NEVER a hardcoded concrete
model:

    SKGATEWAY_URL   (default http://localhost:18780/v1)
    SKGATEWAY_MODEL (default sk-default, auto-router -> ornith)

`render_headline_llm` never raises and never returns an empty string: any
failure (timeout, connection refused, malformed response, an empty or
all-whitespace reply) degrades silently to the caller-supplied `fallback`,
which is always `skos.watchdog.digest.render_headline`'s deterministic
template output -- itself already a complete, valid headline (see that
module's docstring). A slow or down skgateway therefore costs nothing but
the model's flavor text; the digest still lands, on time, either way.

Chef reads the rendered digest every morning: no em dash / en dash anywhere
in generated output, model text included (see MEMORY.md "No em/en dashes").
`render.strip_banned_dashes` is applied to whatever the model returns before
it is ever used, so a banned character in a model reply is silently repaired
rather than shipped.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Mapping, Optional

from .render import strip_banned_dashes

SKGATEWAY_URL = os.environ.get("SKGATEWAY_URL", "http://localhost:18780/v1")
SKGATEWAY_MODEL = os.environ.get("SKGATEWAY_MODEL", "sk-default")  # auto-router -> ornith

#: Wall-clock budget for the one headline call. Short on purpose: this must
#: never be the reason a by-hand `skos watchdog digest` run (or, later,
#: WD-4's scheduled job) stalls past its window.
DEFAULT_TIMEOUT_S = 20

#: How many problem/notable lines to hand the model. The model narrates the
#: already-assembled list (spec 6.4: "the model never invents lines"); it
#: does not need every line to write six sentences, and a hard cap keeps the
#: prompt (and the token bill) bounded on a noisy day.
MAX_LINES_PER_BUCKET = 25


def _event_lines(events: list) -> list[str]:
    out = []
    for e in events[:MAX_LINES_PER_BUCKET]:
        summary = str((e or {}).get("summary") or "").strip()
        if summary:
            out.append(f"- {summary}")
    return out


def build_prompt(digest: Mapping) -> str:
    """Compose the headline prompt from an already-assembled digest dict.
    Pure and deterministic; the model sees exactly this and nothing else, so
    it can summarize but never invent a line the collector did not produce."""
    problems = _event_lines(list(digest.get("problems") or []))
    notable = _event_lines(list(digest.get("notable") or []))
    info_counts = dict(digest.get("info_counts") or {})
    total_info = sum(info_counts.values()) if info_counts else 0

    parts = [
        "You are the skwatchdog fleet narrator. Write a short headline "
        "summarizing what mattered, in six sentences or fewer, plain prose, "
        "no markdown formatting, no bullet points, no em dashes or en "
        "dashes (use commas, periods, or a plain hyphen instead). "
        "Summarize only the events listed below; do not invent anything "
        "not present in this list.",
        "",
    ]
    parts.append(f"Problems ({len(problems)}):")
    parts.extend(problems if problems else ["- none"])
    parts.append("")
    parts.append(f"Notable ({len(notable)}):")
    parts.extend(notable if notable else ["- none"])
    parts.append("")
    parts.append(f"Quiet info events: {total_info}")
    return "\n".join(parts)


def _chat_completion(prompt: str, *, timeout: float = DEFAULT_TIMEOUT_S,
                      url: str = SKGATEWAY_URL, model: str = SKGATEWAY_MODEL) -> Optional[str]:
    """One plain-text chat call to skgateway. Returns the model's reply text,
    or None on ANY failure (timeout, non-zero curl exit, unparseable
    response, empty content) -- never raises, mirrors
    `gtd_triage._chat_json` / `adapters.order.classify_llm`'s discipline of a
    single injectable low-level call point that tests monkeypatch directly
    rather than mocking subprocess."""
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 512,
        "temperature": 0.2,
    })
    try:
        r = subprocess.run(
            ["curl", "-sS", "--max-time", str(timeout), f"{url}/chat/completions",
             "-H", "Content-Type: application/json", "-d", payload],
            capture_output=True, text=True, timeout=timeout + 10)
        content = json.loads(r.stdout)["choices"][0]["message"]["content"]
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
        return content or None
    except Exception:
        return None


def render_headline_llm(digest: Mapping, *, fallback: str,
                         timeout: float = DEFAULT_TIMEOUT_S) -> str:
    """The headline WD-3 actually ships: skgateway when it answers in time,
    the deterministic template (``fallback``, always
    ``digest.render_headline``'s output) otherwise. Never raises, never
    returns an empty string when ``fallback`` is non-empty, and never lets a
    banned dash character through in either branch."""
    text = _chat_completion(build_prompt(digest), timeout=timeout)
    if not text or not text.strip():
        return strip_banned_dashes(fallback)
    return strip_banned_dashes(text.strip())


__all__ = [
    "render_headline_llm", "build_prompt", "SKGATEWAY_URL", "SKGATEWAY_MODEL",
    "DEFAULT_TIMEOUT_S",
]
