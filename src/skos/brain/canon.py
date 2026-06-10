"""skos.brain.canon — Wiki-backed entity-node persistence.

The wiki (``~/clawd/wiki``) is the single source of truth (canon).
skmem-pg is a derived *retrieval projection* of canon, not a second source.

Canonical path scheme (same as skingest, additive — no existing content touched):
    <wiki_root>/pages/entities/<namespace>/<slug>.md

skingest.canon writes nodes too; this module is additive and uses the same
paths so both write to the same locations without conflict.  The EntityNode
schema adds structure (typed edges, enums) over skingest's looser dict approach.

Public API:
    wiki_path(node, wiki_root)           → Path  (no I/O)
    write_node(node, wiki_root, commit)  → Path  (writes, optional git commit)
    read_node(path)                      → EntityNode
    promote(path, new_state, wiki_root, commit)  (in-place frontmatter update)
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from skos.brain.entity import EntityNode, LifecycleState, ParseError, parse, render

# ---------------------------------------------------------------------------
# Default wiki root: $SKOS_WIKI_ROOT → ~/clawd/wiki
# ---------------------------------------------------------------------------

import os

def _default_wiki_root() -> Path:
    env = os.environ.get("SKOS_WIKI_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path("~/clawd/wiki").expanduser().resolve()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class CanonError(RuntimeError):
    """Raised on canon I/O or git failures."""


# ---------------------------------------------------------------------------
# Path helper
# ---------------------------------------------------------------------------

def wiki_path(node: EntityNode, wiki_root: Path | None = None) -> Path:
    """Return the canonical filesystem path for *node* (no I/O).

    Path: <wiki_root>/pages/entities/<namespace>/<id>.md
    """
    root = wiki_root or _default_wiki_root()
    return root / "pages" / "entities" / node.namespace / f"{node.id}.md"


# ---------------------------------------------------------------------------
# write_node
# ---------------------------------------------------------------------------

def write_node(
    node: EntityNode,
    wiki_root: Path | None = None,
    *,
    commit: bool = False,
) -> Path:
    """Write (or overwrite) an EntityNode as a canonical Markdown file in the wiki.

    Creates parent directories as needed.

    Args:
        node:       The entity node to write.
        wiki_root:  Override for the wiki root path.
        commit:     If True, ``git add`` and ``git commit`` the file in the wiki repo.

    Returns:
        Path to the written file.

    Raises:
        CanonError: on git commit failure.
    """
    path = wiki_path(node, wiki_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(node), encoding="utf-8")
    if commit:
        root = wiki_root or _default_wiki_root()
        _git_commit(path, root, f"brain: write entity node {node.namespace}/{node.id}")
    return path


# ---------------------------------------------------------------------------
# read_node
# ---------------------------------------------------------------------------

def read_node(path: str | Path) -> EntityNode:
    """Read and parse an EntityNode from a Markdown file.

    Raises:
        FileNotFoundError: if the file does not exist.
        ParseError: if the file is not a valid EntityNode.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Entity node file not found: {p}")
    return parse(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# promote
# ---------------------------------------------------------------------------

_LIFECYCLE_RE = re.compile(r"^(lifecycle_state:\s*).*$", re.MULTILINE)

_PROMOTION_ORDER = [
    LifecycleState.draft.value,
    LifecycleState.reviewed.value,
    LifecycleState.canon.value,
]


def promote(
    path: str | Path,
    new_state: str,
    wiki_root: Path | None = None,
    *,
    commit: bool = False,
) -> None:
    """Promote an entity node's lifecycle_state in-place.

    Only forward promotions are allowed (draft → reviewed → canon).
    Downgrade attempts raise CanonError.

    Args:
        path:       Path to the entity node markdown file.
        new_state:  Target lifecycle state: "draft" | "reviewed" | "canon".
        wiki_root:  Override for the wiki root (used for git commit).
        commit:     If True, ``git add`` and ``git commit`` after the update.

    Raises:
        CanonError: on invalid state, backward promotion, or git failure.
        FileNotFoundError: if *path* does not exist.
    """
    valid = {s.value for s in LifecycleState}
    if new_state not in valid:
        raise CanonError(f"Invalid lifecycle_state {new_state!r}; must be one of {sorted(valid)}")

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Entity node file not found: {p}")

    content = p.read_text(encoding="utf-8")

    # Check current state and enforce forward-only promotion
    m = _LIFECYCLE_RE.search(content)
    if m:
        current_str = m.group(0).split(":", 1)[1].strip()
        current_idx = _PROMOTION_ORDER.index(current_str) if current_str in _PROMOTION_ORDER else -1
        new_idx = _PROMOTION_ORDER.index(new_state)
        if current_idx >= new_idx and current_str != new_state:
            raise CanonError(
                f"Cannot promote backwards: {current_str!r} → {new_state!r}. "
                f"Promotion must go draft → reviewed → canon."
            )

    updated = _LIFECYCLE_RE.sub(f"lifecycle_state: {new_state}", content)
    p.write_text(updated, encoding="utf-8")

    if commit:
        root = wiki_root or _default_wiki_root()
        _git_commit(p, root, f"brain: promote {p.stem} → {new_state}")


# ---------------------------------------------------------------------------
# Internal git helper
# ---------------------------------------------------------------------------

def _git_commit(node_path: Path, repo_root: Path, message: str) -> None:
    """Stage and commit a single file in the wiki git repo."""
    try:
        subprocess.run(
            ["git", "-C", str(repo_root), "add", str(node_path)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_root), "commit", "--no-verify", "-m", message],
            check=True, capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        raise CanonError(
            f"git commit failed: {exc.stderr.decode('utf-8', errors='replace')[:400]}"
        ) from exc
