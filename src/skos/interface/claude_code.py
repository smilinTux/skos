"""claude-code surface — the agent-facing brain surface, backed by canon.

This is the surface an agent running inside Claude Code reads and writes through.
It sits directly on the canonical wiki (``skos.brain.canon``): nodes live at
``<wiki_root>/pages/entities/<namespace>/<id>.md`` — the single source of truth.
Writing here lands a node in canon; reading pulls it back through the same
EntityNode round-trip.
"""
from __future__ import annotations

from pathlib import Path

from skos.brain import canon
from skos.brain.entity import EntityNode, ParseError, parse
from skos.interface.base import Surface, SurfaceCapabilities, SurfaceError


class ClaudeCodeSurface(Surface):
    name = "claude-code"

    def __init__(self, wiki_root: str | Path | None = None):
        self.wiki_root = Path(wiki_root) if wiki_root is not None else None

    def _entities_root(self) -> Path:
        root = self.wiki_root or canon._default_wiki_root()
        return root / "pages" / "entities"

    # -- Surface contract ---------------------------------------------------

    def read(self, node_id: str) -> EntityNode:
        ent_root = self._entities_root()
        if not ent_root.is_dir():
            raise SurfaceError(f"No entity node {node_id!r} (no canon at {ent_root}).")
        for md in ent_root.rglob(f"{node_id}.md"):
            if md.name.startswith("_"):
                continue
            try:
                return canon.read_node(md)
            except ParseError as exc:
                raise SurfaceError(f"Failed to parse {md}: {exc}") from exc
        raise SurfaceError(f"No entity node {node_id!r} in canon {ent_root}.")

    def write(self, node: EntityNode) -> None:
        canon.write_node(node, wiki_root=self.wiki_root)

    def list(self) -> list[str]:
        ent_root = self._entities_root()
        if not ent_root.is_dir():
            return []
        ids: list[str] = []
        for md in ent_root.rglob("*.md"):
            if md.name.startswith("_"):
                continue
            try:
                node = parse(md.read_text(encoding="utf-8"))
            except ParseError:
                continue
            ids.append(node.id)
        return sorted(ids)

    def capabilities(self) -> SurfaceCapabilities:
        return SurfaceCapabilities(name=self.name)
