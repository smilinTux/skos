"""skos.brain.brain_init — Scaffold the namespaced entity-graph skeleton.

``skos brain init`` does three things:
1. Creates the entities/ directory tree under the wiki for core namespaces.
2. Writes stub _index.md files for each namespace.
3. Lays down the self-build prompt (build_prompt.md) so an agent can flesh
   out the brain on first install.

Design intent (from the spec):
  "Install step: skos brain init → lays the namespaced entity-graph skeleton
   + index stubs + the build prompt, then an agent (Claude Code) fleshes it
   on first run."
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# Core namespaces scaffolded by brain init
# ---------------------------------------------------------------------------

CORE_NAMESPACES: list[tuple[str, str]] = [
    ("skos", "skos OS foundation — packaging, profiles, capabilities, secrets"),
    ("agents", "sovereign AI agents (Lumina, Opus, Jarvis, Ava, swarm specialists)"),
    ("skills", "Claude Code skills — SOPs and domain-specific capabilities"),
    ("tools", "infrastructure tools — CLI tools, MCP tools, embed servers, datastores"),
    ("knowledge", "promoted knowledge nodes — architecture decisions, concepts, learnings"),
    ("workflows", "multi-step automated workflows across agents and services"),
    ("rules", "governance rules — guardrails, policies, and operating constraints"),
    ("projects", "active and archived projects with their state pointers"),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wiki_root() -> Path:
    env = os.environ.get("SKOS_WIKI_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path("~/clawd/wiki").expanduser().resolve()


def _build_prompt_src() -> Path:
    """Return the path to the shipped build_prompt.md."""
    return Path(__file__).parent / "build_prompt.md"


def _stub_index(namespace: str, description: str, today: str) -> str:
    return "\n".join([
        "---",
        f"namespace: {namespace}",
        "entity_count: 0",
        f"updated: {today}",
        "---",
        "",
        f"# {namespace} — entity index",
        "",
        f"> {description}",
        ">",
        "> Auto-generated stub. Run `skos brain index {namespace}` after adding entity nodes.",
        "> Agents: scan this index first; open individual node files only when needed.",
        "",
        "| id | type | lifecycle | summary |",
        "|----|------|-----------|---------|",
        "",
    ])


# ---------------------------------------------------------------------------
# scaffold()
# ---------------------------------------------------------------------------

def scaffold(wiki_root: Path | None = None) -> dict[str, Path]:
    """Scaffold the entity-graph skeleton under the wiki.

    Creates:
      - ``<wiki_root>/pages/entities/<namespace>/`` for each CORE_NAMESPACES entry
      - ``<wiki_root>/pages/entities/<namespace>/_index.md`` stub (no-overwrite)
      - ``<wiki_root>/pages/entities/build_prompt.md`` (the self-build prompt)

    Returns:
        Dict mapping namespace name → _index.md path.
    """
    root = wiki_root or _wiki_root()
    entities_dir = root / "pages" / "entities"
    today = date.today().isoformat()

    result: dict[str, Path] = {}

    for namespace, description in CORE_NAMESPACES:
        ns_dir = entities_dir / namespace
        ns_dir.mkdir(parents=True, exist_ok=True)

        index_path = ns_dir / "_index.md"
        if not index_path.exists():
            index_path.write_text(
                _stub_index(namespace, description, today),
                encoding="utf-8",
            )
        result[namespace] = index_path

    # Lay down the self-build prompt at the entities root
    prompt_src = _build_prompt_src()
    prompt_dst = entities_dir / "build_prompt.md"
    if prompt_src.exists() and not prompt_dst.exists():
        prompt_dst.write_text(prompt_src.read_text(encoding="utf-8"), encoding="utf-8")

    return result
