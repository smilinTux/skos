"""skos' SKWorld module manifest (umbrella shell spec 5.2 + reconciled 2.3).

skos is a first-class SKWorld subapp and sits at the TOP of the subapp list
(Chef: "skos is TOP of the subapp list"; umbrella spec 4.4: "its manifest
registers nav position 1 regardless"). Like every first-class subapp it declares
ONE capauth-signed skworld.module.json with two facets: the UI facet lets the
shell mount skos' single-pane-of-glass surface, and the operator facet lets Atlas
watch and steer skos.

This module builds the manifest as a pure dict from the serving origin, so the
served URLs are origin-relative (they resolve against wherever the host actually
answers, avoiding host/port drift). The manifest is public discovery metadata
(no secrets) and is meant to be served unauthenticated at
/.well-known/skworld-module.json once skos grows a web surface; today skos has no
HTTP server, so the builder stands ready for that route (mirroring skchat's
webui.py and skcode's daemon.py) or for generating a static signed file for the
shell's modules.json registry.

UI facet: Grade B, the same web-embed path the umbrella spec assigns skos
("same Grade B path as skdashboard for its web UI when one exists", spec 4.4).
The shell may interim-route the manifest entry to the existing native skos
screens; a grade promotion is then a manifest edit plus a package, never a
contract change (reconciled spec 2.3).

The operator block mirrors operator_seat/skos_adapter.py in skcapstone. The two
live in separate repos, so the shared schema in sk-standards is the source of
truth; keep these two in sync when either changes. The manifest-adapter
drift-guard test (skcapstone tests/operator_seat/test_manifest_adapter_conformance.py)
asserts manifest.operator.conditions == skos_adapter.CONDITIONS exactly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

#: The manifest schema version (sk-standards manifest schema v1.1, +operator block).
SCHEMA_VERSION = "1.1"
#: The audience skos tokens are minted for.
AUDIENCE = "skos"
#: Placeholder serving origin baked into the emitted static manifest. skos has no
#: HTTP server, so the shell interim-routes this entry to the native skos screens
#: (umbrella spec 4.4). Override with a real origin once skos' web UI lands; the
#: URLs are origin-relative, so a promotion is a re-emit, never a contract change.
DEFAULT_BASE_URL = "http://127.0.0.1:7780/"


def skos_module_manifest(base_url: str) -> dict:
    """Build skos' skworld.module.json for a given serving origin.

    Args:
        base_url: The origin the host answers on (e.g. the request base URL).
            URLs in the manifest are built relative to this so they never
            hardcode a host or port.

    Returns:
        The manifest dict (UI facet + operator facet).
    """
    base = base_url.rstrip("/")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "id": "skos",
        "name": "OS",
        # UI facet: Grade B (the web-embed path per umbrella spec 4.4). Promotes
        # to Grade A by flipping grade + adding entry.flutter_package, never a
        # contract change (reconciled spec 2.3).
        "grade": "B",
        "entry": {"url": f"{base}/"},
        # nav.order 10 slots skos FIRST, ahead of chats (20) and code (30):
        # skos is the TOP of the subapp list (umbrella spec 4.4 "nav position 1").
        "nav": {"icon": "grid_view", "order": 10, "label": "OS"},
        "deeplinkPrefix": "skworld://skos/",
        "auth": {
            "audience": AUDIENCE,
            "scopes": ["skos.read"],
        },
        "memory": {"opt_in": True, "scope": "skos"},
        "health": f"{base}/health",
        # Operator facet: what Atlas's skos adapter observes and may act on.
        "operator": {
            "contractVersion": 1,
            "cli": "skos operator",
            "repos": ["skos"],
            "conditions": [
                "SchedulerAlive",
                "GtdSinkDraining",
            ],
            "proposedStandardActions": ["restart_service", "replay_errors"],
        },
    }


def _skcapstone_home() -> Path:
    """The skcapstone home dir ($SKCAPSTONE_HOME or ~/.skcapstone). The umbrella
    shell keeps its registry under here (spec 5.3)."""
    env = os.environ.get("SKCAPSTONE_HOME", "").strip()
    return Path(env).expanduser() if env else Path.home() / ".skcapstone"


def default_manifest_path() -> Path:
    """The well-known local-file location for skos' emitted, signed manifest.

    The umbrella shell's static registry (`~/.skcapstone/shell/modules.json`,
    spec 5.3) references each module's manifest by "local file or /.well-known/
    URL". skos has no HTTP surface, so it publishes the local-file form here and
    the registry points at this path.
    """
    return _skcapstone_home() / "shell" / "modules" / "skos.skworld-module.json"


def render_manifest_json(base_url: str = DEFAULT_BASE_URL) -> str:
    """Render the manifest as deterministic JSON (sorted keys, trailing newline).

    Stable bytes matter: the shell hashes and capauth-signs this file, so an
    unchanged manifest must re-emit byte-for-byte identically (idempotent diff,
    reproducible signature).
    """
    return json.dumps(skos_module_manifest(base_url), indent=2, sort_keys=True) + "\n"


def emit_manifest_file(
    base_url: str = DEFAULT_BASE_URL,
    out_path: Path | str | None = None,
) -> Path:
    """Write skos' manifest to a static file for the umbrella shell registry.

    Args:
        base_url: Serving origin baked into the origin-relative URLs (defaults to
            the localhost placeholder skos advertises until its web UI lands).
        out_path: Where to write (defaults to :func:`default_manifest_path`).

    Returns:
        The path written. Creates parent dirs as needed and writes deterministic
        bytes so re-emitting an unchanged manifest is a no-op diff.
    """
    path = Path(out_path).expanduser() if out_path else default_manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_manifest_json(base_url), encoding="utf-8")
    return path


__all__ = [
    "skos_module_manifest",
    "SCHEMA_VERSION",
    "AUDIENCE",
    "DEFAULT_BASE_URL",
    "default_manifest_path",
    "render_manifest_json",
    "emit_manifest_file",
]
