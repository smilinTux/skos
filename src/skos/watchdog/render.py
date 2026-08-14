"""The digest renderer (WD-3): assemble_digest()'s dict, in Markdown.

Spec section 6.4 + the WD-3 card: "Render the assembled digest to markdown
plus JSON ... DEEP LINKS ARE THE POINT, not a nicety ... Every event carries
both a skworld:// uri and an https fallback. A digest line that is not
clickable has missed the card."

This module is pure: no I/O, no model call, no network. It takes the exact
dict `skos.watchdog.digest.assemble_digest` already produces (plus a
possibly LLM-rendered `headline`, WD-3's own concern) and turns it into one
Markdown document where every problem/notable line links to its `link.http`
(falling back to `link.uri` when http is empty, so a line with only a
skworld:// form still renders a working reference instead of going silent).

The five keys card C-9's Dart parser reads (date, headline, problems,
notable, info_counts) are read here exactly as WD-1 defined them; this
module never renames or restructures them, it only walks the dict to render
prose. JSON publishing (`publish.py`) writes this same dict byte-for-byte,
untouched, so the Markdown and the JSON a human/Flutter reads always agree.

Chef reads this every morning: NO em dashes (--) or en dashes (--) anywhere
in the rendered text, including inside event summaries a source produced.
`_no_banned_dashes` sanitizes every string that lands in the document.
"""
from __future__ import annotations

import re
from typing import Mapping

#: The two banned dash characters (em dash U+2014, en dash U+2013), Chef's
#: hard writing-style rule (see MEMORY.md "No em/en dashes"). Applied to
#: EVERY piece of text this module emits, including source-supplied summaries
#: and (in headline.py) model output, so a banned character can never reach
#: the artifact regardless of where it originated.
_EM_EN_SPACED = re.compile(r"\s+[—–]\s+")
_EM_EN_BARE = re.compile(r"[—–]")


def strip_banned_dashes(text: str) -> str:
    """Replace em/en dashes with plain punctuation, never just delete them.

    A dash used as a sentence break (` -- ` or ` - `, spaced) becomes a comma
    so the sentence still reads; a bare dash glued to letters (a stray glyph
    with no surrounding spaces, e.g. in a pasted title) becomes a plain
    hyphen. Never raises, never returns the input unchanged if it contained
    either character.
    """
    if not text:
        return text
    out = _EM_EN_SPACED.sub(", ", text)
    out = _EM_EN_BARE.sub("-", out)
    return out


def link_of(event: Mapping) -> str:
    """The one clickable target for an event: `link.http` (the load-bearing
    form until a resolver exists outside the Flutter shell, spec section 8),
    falling back to `link.uri` so an event with only a skworld:// form still
    renders something a human can act on rather than a dead line."""
    link = event.get("link") or {}
    return str(link.get("http") or "") or str(link.get("uri") or "")


_SEVERITY_MARK = {"problem": "!!", "notable": "*", "info": "-"}


def _render_event_line(event: Mapping) -> str:
    summary = strip_banned_dashes(str(event.get("summary") or "(no summary)"))
    mark = _SEVERITY_MARK.get(str(event.get("severity") or ""), "-")
    tags = " / ".join(
        str(event.get(k) or "") for k in ("source", "kind", "object") if event.get(k)
    )
    link = link_of(event)
    lines = [f"{mark} {summary}"]
    detail_parts = []
    if tags:
        detail_parts.append(f"({tags})")
    if link:
        detail_parts.append(f"-> {link}")
    if detail_parts:
        lines.append("  " + " ".join(detail_parts))
    return "\n".join(lines)


def _render_section(title: str, events: list) -> list[str]:
    if not events:
        return [f"## {title}", "", "- none", ""]
    out = [f"## {title} ({len(events)})", ""]
    for e in events:
        out.append(_render_event_line(e))
    out.append("")
    return out


def render_markdown(digest: Mapping) -> str:
    """Render one assembled (and headline-finalized) digest dict to Markdown.

    Pure and deterministic given its input: same digest dict, same
    Markdown, every time. Never raises on a malformed/partial digest (missing
    keys read as empty, matching the C-9 Dart parser's own defensive
    defaults) so a renderer bug can never be the reason a digest fails to
    publish.
    """
    date = str(digest.get("date") or "")
    headline = strip_banned_dashes(str(digest.get("headline") or ""))
    problems = list(digest.get("problems") or [])
    notable = list(digest.get("notable") or [])
    info_counts = dict(digest.get("info_counts") or {})
    per_source = dict(digest.get("per_source") or {})

    lines = [f"# skwatchdog digest - {date or 'unknown date'}", ""]
    if headline:
        lines += [headline, ""]

    lines += _render_section("Problems", problems)
    lines += _render_section("Notable", notable)

    total_info = sum(info_counts.values()) if info_counts else 0
    lines.append(f"## Quiet ({total_info} info event(s))")
    lines.append("")
    if info_counts:
        for src in sorted(info_counts):
            lines.append(f"- {src}: {info_counts[src]}")
    else:
        lines.append("- none")
    lines.append("")

    if per_source:
        lines.append("## Sources")
        lines.append("")
        for src in sorted(per_source):
            row = per_source[src] or {}
            ok = row.get("ok", True)
            status = "ok" if ok else "DEGRADED"
            lines.append(
                f"- {src}: {status}, {row.get('events', 0)} event(s), "
                f"cursor {row.get('cursor', '?')}"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


__all__ = ["render_markdown", "strip_banned_dashes", "link_of"]
