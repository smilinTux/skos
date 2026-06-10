"""obsidian surface — read/write an Obsidian-style markdown vault.

Each EntityNode is a markdown file under ``<vault_root>/<namespace>/<id>.md``,
serialised with the same brain ``parse``/``render`` round-trip the rest of skos
uses, so an Obsidian vault and the canon wiki speak the identical EntityNode
dialect.  No Obsidian app is required — this works against any directory tree.
"""
from __future__ import annotations

import os
from pathlib import Path

from skos.brain.entity import EntityNode, ParseError, parse, render
from skos.interface.base import Surface, SurfaceCapabilities, SurfaceError


def _default_vault_root() -> Path:
    env = os.environ.get("SKOS_VAULT_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path("~/clawd/vault").expanduser().resolve()


class ObsidianSurface(Surface):
    name = "obsidian"

    def __init__(self, vault_root: str | Path | None = None):
        self.vault_root = (
            Path(vault_root) if vault_root is not None else _default_vault_root()
        )

    # -- path helpers -------------------------------------------------------

    def _node_path(self, node: EntityNode) -> Path:
        return self.vault_root / node.namespace / f"{node.id}.md"

    def _find_path(self, node_id: str) -> Path | None:
        """Locate the markdown file for *node_id* across all namespaces."""
        if not self.vault_root.is_dir():
            return None
        for md in self.vault_root.rglob(f"{node_id}.md"):
            if md.name.startswith("_"):
                continue
            return md
        return None

    # -- Surface contract ---------------------------------------------------

    def read(self, node_id: str) -> EntityNode:
        path = self._find_path(node_id)
        if path is None:
            raise SurfaceError(
                f"No entity node {node_id!r} in obsidian vault {self.vault_root}."
            )
        try:
            return parse(path.read_text(encoding="utf-8"))
        except ParseError as exc:
            raise SurfaceError(f"Failed to parse {path}: {exc}") from exc

    def write(self, node: EntityNode) -> None:
        path = self._node_path(node)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(node), encoding="utf-8")

    def list(self) -> list[str]:
        if not self.vault_root.is_dir():
            return []
        ids: list[str] = []
        for md in self.vault_root.rglob("*.md"):
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
