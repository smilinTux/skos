"""Non-mutating health checks for a fresh or installed skbrain pack."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from skos.brain.ops.postgres import _connect
from skos.brain.ops.secrets import lint_tree


@dataclass(frozen=True)
class Check:
    """One machine-readable health assertion."""

    name: str
    ok: bool
    detail: str


def run_checks(*, canon: str | Path, reader_dsn: str | None,
               connect: Callable[[str], Any] = _connect) -> list[Check]:
    """Check content, secret hygiene, schema, grants, and projector population."""
    root = Path(canon).expanduser()
    checks = [Check("skbrain:content", root.is_dir(), "canon present" if root.is_dir() else "canon missing")]
    if root.is_dir():
        findings = lint_tree(root)
        checks.append(Check("skbrain:secret-lint", not findings,
                            "clean" if not findings else f"{len(findings)} redacted finding(s)"))
    else:
        checks.append(Check("skbrain:secret-lint", False, "content unavailable"))
    if not reader_dsn:
        checks.extend([Check("skbrain:schema", False, "reader DSN missing"),
                       Check("skbrain:grants", False, "reader DSN missing"),
                       Check("skbrain:projector", False, "reader DSN missing")])
        return checks
    try:
        with connect(reader_dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('ops.wiki_nodes'), to_regclass('ops.wiki_chunks'), to_regclass('ops.links')")
            schema = cur.fetchone()
            checks.append(Check("skbrain:schema", bool(schema and all(schema)), "ops relations present" if schema and all(schema) else "ops relations missing"))
            cur.execute("SELECT has_schema_privilege(current_user,'ops','USAGE'), has_table_privilege(current_user,'ops.wiki_nodes','SELECT')")
            grants = cur.fetchone()
            checks.append(Check("skbrain:grants", bool(grants and all(grants)), "reader wall valid" if grants and all(grants) else "reader grants invalid"))
            cur.execute("SELECT count(*), EXTRACT(EPOCH FROM (now()-max(updated_at))) FROM ops.wiki_nodes")
            count, age_seconds = cur.fetchone()
            fresh = int(count) > 0 and age_seconds is not None and float(age_seconds) <= 7200
            detail = f"{count} node(s); age_seconds={int(age_seconds) if age_seconds is not None else 'unknown'}"
            checks.append(Check("skbrain:projector", fresh, detail))
    except Exception as exc:  # do not include DSN or exception repr
        checks.extend([Check("skbrain:schema", False, f"database unavailable ({type(exc).__name__})"),
                       Check("skbrain:grants", False, "database unavailable"),
                       Check("skbrain:projector", False, "database unavailable")])
    return checks


def checks_json(checks: list[Check]) -> list[dict[str, object]]:
    """Serialize checks without implementation-specific objects."""
    return [asdict(item) for item in checks]
