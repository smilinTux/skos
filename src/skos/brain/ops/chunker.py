"""skos.brain.ops.chunker — heading-aware retrieval chunking (SB1.1, pure).

Inherits chunk.py's discipline (the mxbai-embed-large 512-token ceiling, empirically
~1200 chars of English with headroom, plus a small overlap) but splits on markdown
headings FIRST so a runbook's sections (Preconditions / Steps / Verify / Rollback)
stay retrievable as coherent units. Sections still over the ceiling fall back to
paragraph-greedy packing, then sentence, then a hard char split, exactly like
chunk.py. Pure: no I/O.
"""

from __future__ import annotations

import re

from skos.brain.ops.models import OpsChunk

# Ported from wiki/tools/chunk.py: ~1200 chars ~= 300 tokens, under mxbai's 512
# ceiling with headroom for URLs/punctuation that tokenize heavier than prose.
DEFAULT_MAX_CHARS = 1200
DEFAULT_OVERLAP_CHARS = 150
MIN_TAIL = 200  # fold a trailing chunk smaller than this back into the previous one

_HEADING_RE = re.compile(r"^(#{1,6})\s", re.MULTILINE)
_SENT_RE = re.compile(r"(?<=[.!?])\s+")


def _split_sections(body: str) -> list[str]:
    """Split *body* into heading-led sections (text before the first heading kept)."""
    matches = list(_HEADING_RE.finditer(body))
    if not matches:
        return [body] if body.strip() else []
    sections: list[str] = []
    # preamble before the first heading
    if matches[0].start() > 0:
        pre = body[: matches[0].start()].strip()
        if pre:
            sections.append(pre)
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        section = body[m.start() : end].strip()
        if section:
            sections.append(section)
    return sections


def _pack_units(units: list[str], max_chars: int, overlap: int) -> list[str]:
    """Greedy-pack units into <=max_chars chunks with a small tail overlap."""
    chunks: list[str] = []
    cur = ""
    for u in units:
        if not cur:
            cur = u
        elif len(cur) + len(u) + 2 <= max_chars:
            cur = cur + "\n\n" + u
        else:
            chunks.append(cur)
            tail = cur[-overlap:]
            tail = tail[tail.find(" ") + 1 :] if " " in tail else ""
            # Only seed the overlap tail if it keeps the new chunk under the
            # ceiling; otherwise start clean (the mxbai token limit is hard).
            if tail and len(tail) + 2 + len(u) <= max_chars:
                cur = (tail + "\n\n" + u).strip()
            else:
                cur = u
    if cur:
        chunks.append(cur)
    if (
        len(chunks) >= 2
        and len(chunks[-1]) < MIN_TAIL
        and len(chunks[-2]) + 2 + len(chunks[-1]) <= max_chars
    ):
        chunks[-2] = chunks[-2] + "\n\n" + chunks[-1]
        chunks.pop()
    return chunks


def _explode_oversize(text: str, max_chars: int) -> list[str]:
    """Break one oversized block into <=max_chars units: paragraphs -> sentences -> hard."""
    units: list[str] = []
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    for p in paras:
        if len(p) <= max_chars:
            units.append(p)
            continue
        buf = ""
        for sent in _SENT_RE.split(p):
            sent = sent.strip()
            if not sent:
                continue
            if len(sent) > max_chars:
                if buf:
                    units.append(buf)
                    buf = ""
                for i in range(0, len(sent), max_chars):
                    units.append(sent[i : i + max_chars])
                continue
            if len(buf) + len(sent) + 1 <= max_chars:
                buf = (buf + " " + sent).strip()
            else:
                if buf:
                    units.append(buf)
                buf = sent
        if buf:
            units.append(buf)
    return units


def chunk_body(
    body: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP_CHARS,
) -> list[str]:
    """Split a page body into retrieval chunks, each ``<= max_chars`` (headings-first).

    Returns an empty list for an empty/whitespace body. Every returned chunk is at
    or under ``max_chars`` (a single unsplittable token can be the only exception).
    """
    if not body or not body.strip():
        return []

    units: list[str] = []
    for section in _split_sections(body):
        if len(section) <= max_chars:
            units.append(section)
        else:
            units.extend(_explode_oversize(section, max_chars))

    return _pack_units(units, max_chars, overlap)


def chunk_page(
    page,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP_CHARS,
) -> list[OpsChunk]:
    """Chunk ``page.body_md`` into ordered ``OpsChunk`` rows (embedding unset)."""
    return [
        OpsChunk(node_id=page.slug, ord=i, content=content)
        for i, content in enumerate(chunk_body(page.body_md, max_chars=max_chars, overlap=overlap))
    ]
