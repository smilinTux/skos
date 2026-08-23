"""Non-mutating health checks for a fresh or installed skbrain pack."""

from __future__ import annotations

import importlib
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from skos.brain.ops.kedb_coverage import compute_kedb_coverage
from skos.brain.ops.postgres import _connect
from skos.brain.ops.secrets import lint_tree
from skos.packs.loader import load_manifest_dict, pack_dir


@dataclass(frozen=True)
class Check:
    """One machine-readable health assertion."""

    name: str
    ok: bool
    detail: str


def _skcapstone_home() -> Path:
    """The skcapstone home dir. Mirrors every other skos module's identical
    helper (e.g. ``skos.watchdog.adapters.itil._skcapstone_home``): same env
    var, same default, so no two readers of skcapstone state can disagree."""
    return Path(os.environ.get("SKCAPSTONE_HOME", str(Path.home() / ".skcapstone"))).expanduser()


def _check_kedb(
    canon: Path, *, kedb_loader: Callable[[Path], list[str]] | None = None
) -> Check:
    """``skbrain:kedb`` -- real KedbCanonCovered coverage, doctor-shaped.

    Reuses :func:`compute_kedb_coverage` (card 8c6e05e3) so the doctor check
    and the ``operator observe`` condition can never compute two different
    answers to the same question. A doctor check has no ``Unknown`` state, so
    an ``Unknown`` coverage verdict (the authoritative fold is genuinely
    unreachable -- e.g. the optional skcapstone/skcoord sibling is absent, as
    it always is in CI) fails closed here, exactly like every other doctor
    check fails closed on missing evidence.

    ``kedb_loader`` is forwarded to :func:`compute_kedb_coverage` (tests
    inject a fake so this check is exercisable without the optional sibling
    installed); defaults to the real authoritative-fold reader.
    """
    kwargs = {"kedb_loader": kedb_loader} if kedb_loader is not None else {}
    coverage = compute_kedb_coverage(canon, **kwargs)
    return Check("skbrain:kedb", coverage.status == "True", coverage.reason)


def _check_adapter() -> Check:
    """``skbrain:adapter`` -- the manifest's declared knowledge retriever
    resolves.

    The signed manifest's ``knowledge.retriever`` (a ``module:callable`` ref,
    currently ``skos.brain.ops.read_api:build_retriever``) is exactly what
    ATLAS's manifest-driven discovery probes to decide whether SKBrain's RAG
    adapter is available (skcapstone ``operator_seat/discovery.py``,
    ``_default_knowledge_prober``: import the module, ``hasattr`` the
    attribute). This check mirrors that probe locally so a broken or renamed
    retriever ref is a red doctor check on this side, not a silent RAG
    fallback discovered only from ATLAS's side later. The ref is read from the
    manifest itself (never hardcoded here), so a manifest edit can never drift
    silently past this check.
    """
    knowledge = load_manifest_dict("skbrain").get("knowledge") or {}
    ref = knowledge.get("retriever")
    if not ref or ":" not in ref:
        return Check(
            "skbrain:adapter", False, f"manifest knowledge.retriever is malformed: {ref!r}"
        )
    mod_name, _, attr = ref.partition(":")
    try:
        mod = importlib.import_module(mod_name)
    except Exception as exc:  # noqa: BLE001 - report class only, never leak a traceback
        return Check(
            "skbrain:adapter",
            False,
            f"retriever module {mod_name!r} unimportable ({type(exc).__name__})",
        )
    if not attr or not hasattr(mod, attr):
        return Check(
            "skbrain:adapter", False, f"retriever attribute {attr!r} missing from {mod_name!r}"
        )
    return Check("skbrain:adapter", True, f"knowledge retriever {ref} resolves")


def _check_cron() -> Check:
    """``skbrain:cron`` -- the pack's fleet CronJob objects are installed and
    match the shipped templates.

    Scope, stated plainly: this verifies the fleet object DECLARATIONS the
    pack's ``fleet_objects`` install step writes (``skos.packs.effects.
    Effects.fleet_objects``) are present under ``<SKCAPSTONE_HOME>/fleet/
    objects/`` and byte-identical to what this pack ships. It does NOT verify
    a scheduler is actually invoking them on schedule -- that liveness signal
    belongs to the scheduler/watchdog subsystem (``skos.watchdog``), which has
    its own adapter for exactly that. A green ``skbrain:cron`` means "the cron
    is correctly wired," not "the cron definitely fired most recently."
    """
    manifest = load_manifest_dict("skbrain")
    fleet_steps = [
        s for s in manifest.get("install", {}).get("steps", []) if s.get("kind") == "fleet_objects"
    ]
    objects = [str(o) for step in fleet_steps for o in step.get("objects", [])]
    if not objects:
        return Check("skbrain:cron", False, "manifest declares no fleet_objects step")

    source_dir = pack_dir("skbrain")
    installed_dir = _skcapstone_home() / "fleet" / "objects"
    missing: list[str] = []
    stale: list[str] = []
    for rel in objects:
        src = source_dir / rel
        dst = installed_dir / Path(rel).name
        if not dst.is_file():
            missing.append(dst.name)
            continue
        if src.is_file() and src.read_text(encoding="utf-8") != dst.read_text(encoding="utf-8"):
            stale.append(dst.name)

    if not missing and not stale:
        return Check(
            "skbrain:cron", True, f"{len(objects)} fleet cron object(s) installed and current"
        )
    detail_parts = []
    if missing:
        detail_parts.append(f"missing: {', '.join(missing)}")
    if stale:
        detail_parts.append(f"stale (differs from shipped template): {', '.join(stale)}")
    return Check("skbrain:cron", False, "; ".join(detail_parts))


def run_checks(
    *,
    canon: str | Path,
    reader_dsn: str | None,
    connect: Callable[[str], Any] = _connect,
    kedb_loader: Callable[[Path], list[str]] | None = None,
) -> list[Check]:
    """Check content, secret hygiene, KEDB coverage, retriever adapter, fleet
    cron wiring, schema, grants, and projector population -- the full set the
    signed manifest's ``doctor`` install step declares
    (``skos/packs/skbrain/skworld.module.json``)."""
    root = Path(canon).expanduser()
    checks = [
        Check(
            "skbrain:content", root.is_dir(), "canon present" if root.is_dir() else "canon missing"
        )
    ]
    if root.is_dir():
        findings = lint_tree(root)
        checks.append(
            Check(
                "skbrain:secret-lint",
                not findings,
                "clean" if not findings else f"{len(findings)} redacted finding(s)",
            )
        )
    else:
        checks.append(Check("skbrain:secret-lint", False, "content unavailable"))
    # kedb, adapter, and cron are independent of the reader DSN/database, so
    # they run unconditionally (unlike schema/grants/projector below).
    checks.append(_check_kedb(root, kedb_loader=kedb_loader))
    checks.append(_check_adapter())
    checks.append(_check_cron())
    if not reader_dsn:
        checks.extend(
            [
                Check("skbrain:schema", False, "reader DSN missing"),
                Check("skbrain:grants", False, "reader DSN missing"),
                Check("skbrain:projector", False, "reader DSN missing"),
            ]
        )
        return checks
    try:
        with connect(reader_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT to_regclass('ops.wiki_nodes'), "
                "to_regclass('ops.wiki_chunks'), to_regclass('ops.links')"
            )
            schema = cur.fetchone()
            checks.append(
                Check(
                    "skbrain:schema",
                    bool(schema and all(schema)),
                    "ops relations present" if schema and all(schema) else "ops relations missing",
                )
            )
            cur.execute(
                "SELECT has_schema_privilege(current_user,'ops','USAGE'), "
                "has_table_privilege(current_user,'ops.wiki_nodes','SELECT')"
            )
            grants = cur.fetchone()
            checks.append(
                Check(
                    "skbrain:grants",
                    bool(grants and all(grants)),
                    "reader wall valid" if grants and all(grants) else "reader grants invalid",
                )
            )
            cur.execute(
                "SELECT count(*), EXTRACT(EPOCH FROM (now()-max(updated_at))) FROM ops.wiki_nodes"
            )
            count, age_seconds = cur.fetchone()
            fresh = int(count) > 0 and age_seconds is not None and float(age_seconds) <= 7200
            age = int(age_seconds) if age_seconds is not None else "unknown"
            detail = f"{count} node(s); age_seconds={age}"
            checks.append(Check("skbrain:projector", fresh, detail))
    except Exception as exc:  # do not include DSN or exception repr
        checks.extend(
            [
                Check("skbrain:schema", False, f"database unavailable ({type(exc).__name__})"),
                Check("skbrain:grants", False, "database unavailable"),
                Check("skbrain:projector", False, "database unavailable"),
            ]
        )
    return checks


def checks_json(checks: list[Check]) -> list[dict[str, object]]:
    """Serialize checks without implementation-specific objects."""
    return [asdict(item) for item in checks]
